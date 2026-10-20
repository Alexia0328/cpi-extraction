from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .discovery import discover_cpi_by_month, discover_latest_cpi
from .extraction import OllamaCPIExtractor
from .fetch import HttpFetcher, SourceError, canonicalize_url
from .models import OutputEnvelope, ProcessingMetadata, SourceMetadata
from .parser import parse_publication
from .storage import read_json, write_json_atomic
from .validation import validate_extraction


FEED_URL = "https://www150.statcan.gc.ca/n1/rss/dai-quo/18-eng.atom"
SCHEDULE_URL = "https://www150.statcan.gc.ca/n1/dai-quo/ssi/homepage/schedule-key_indicators-eng.json"
PROCESSING_SLO_MS = 10_000
OUTPUT_SCHEMA_VERSION = "2"


class ValidationFailure(RuntimeError):
    def __init__(self, message: str, quarantine_path: Path) -> None:
        super().__init__(message)
        self.quarantine_path = quarantine_path


@dataclass(frozen=True)
class HarnessPaths:
    root: Path

    @property
    def http_cache(self) -> Path:
        return self.root / ".cache" / "http"

    @property
    def llm_cache(self) -> Path:
        return self.root / ".cache" / "llm"

    @property
    def records(self) -> Path:
        return self.root / "outputs" / "records"

    @property
    def quarantine(self) -> Path:
        return self.root / "outputs" / "quarantine"


@dataclass(frozen=True)
class PipelineResult:
    status: str
    path: Path
    envelope: OutputEnvelope


def _url_key(url: str) -> str:
    material = f"runtime-output-v{OUTPUT_SCHEMA_VERSION}|{url}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str), flush=True)


def run_url(
    url: str,
    paths: HarnessPaths,
    *,
    refresh: bool = False,
    fetcher: HttpFetcher | None = None,
    extractor: OllamaCPIExtractor | None = None,
    source_published_at: datetime | None = None,
) -> PipelineResult:
    run_started_at = datetime.now(UTC)
    timer = time.perf_counter()
    canonical = canonicalize_url(url, require_english_html=True)
    index_path = paths.records / "by-url" / f"{_url_key(canonical)}.json"
    if index_path.exists() and not refresh:
        envelope = OutputEnvelope.model_validate(read_json(index_path))
        _log(
            "run_skipped",
            reason="already_processed",
            source_url=canonical,
            path=str(index_path),
            method=envelope.processing.method,
            validation=envelope.processing.validation,
        )
        return PipelineResult(status="skipped", path=index_path, envelope=envelope)

    own_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher(paths.http_cache)
    extractor = extractor or OllamaCPIExtractor(paths.llm_cache)
    try:
        fetched = fetcher.fetch(canonical, refresh=refresh, require_english_html=True)
    finally:
        if own_fetcher:
            fetcher.close()
    publication = parse_publication(fetched)
    _log(
        "source_parsed",
        source_url=canonical,
        reference_month=publication.reference_month,
        cache_hit=fetched.cache_hit,
        fetch_attempts=fetched.attempts,
    )

    extraction = extractor.extract(publication)
    report = validate_extraction(publication, extraction)

    run_completed_at = datetime.now(UTC)
    processing_latency_ms = round((time.perf_counter() - timer) * 1000)
    within_processing_slo = processing_latency_ms <= PROCESSING_SLO_MS
    if report.valid and not within_processing_slo:
        report = report.model_copy(
            update={
                "status": "invalid",
                "errors": [
                    *report.errors,
                    f"processing latency {processing_latency_ms} ms exceeded {PROCESSING_SLO_MS} ms SLO",
                ],
            }
        )
    envelope = OutputEnvelope(
        source=SourceMetadata(
            url=canonical,
            fetched_at=publication.fetched_at,
            sha256=publication.source_sha256,
            published_at=source_published_at,
            discovery_lag_seconds=(
                max(0, round((run_started_at - source_published_at).total_seconds()))
                if source_published_at
                else None
            ),
        ),
        record=extraction.record,
        evidence=extraction.evidence,
        processing=ProcessingMetadata(
            completed_at=run_completed_at,
            method=extraction.model_id,
            prompt_version=extraction.prompt_version,
            latency_ms=processing_latency_ms,
            slo_ms=PROCESSING_SLO_MS,
            within_slo=within_processing_slo,
            cache_hit=extraction.cache_hit,
            input_tokens=extraction.usage.input_tokens,
            output_tokens=extraction.usage.output_tokens,
            validation=report.status,
            warnings=report.warnings,
        ),
    )
    if not report.valid:
        quarantine_path = paths.quarantine / f"{publication.reference_month}-{_url_key(canonical)[:10]}.json"
        write_json_atomic(
            quarantine_path,
            {
                "output": envelope.model_dump(mode="json", exclude_none=True),
                "errors": report.errors,
                "extraction": extraction.model_dump(mode="json"),
            },
        )
        _log("record_quarantined", path=str(quarantine_path), errors=report.errors)
        raise ValidationFailure("record failed validation", quarantine_path)

    record_path = paths.records / f"{publication.reference_month}.json"
    payload = envelope.model_dump(mode="json", exclude_none=True)
    write_json_atomic(record_path, payload)
    write_json_atomic(index_path, payload)
    _log(
        "record_saved",
        path=str(record_path),
        method=extraction.model_id,
        model_cache_hit=extraction.cache_hit,
        processing_latency_ms=processing_latency_ms,
        validation=report.status,
    )
    return PipelineResult(status="saved", path=record_path, envelope=envelope)


def run_latest(
    paths: HarnessPaths,
    *,
    refresh: bool = False,
    fetcher: HttpFetcher | None = None,
    extractor: OllamaCPIExtractor | None = None,
) -> PipelineResult:
    own_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher(paths.http_cache)
    try:
        feed = fetcher.fetch(FEED_URL, refresh=True)
        entry = discover_latest_cpi(feed.content)
        _log("latest_discovered", title=entry.title, source_url=entry.url)
        return run_url(
            entry.url,
            paths,
            refresh=refresh,
            fetcher=fetcher,
            extractor=extractor,
            source_published_at=entry.updated_at,
        )
    finally:
        if own_fetcher:
            fetcher.close()


def run_month(
    reference_month: str,
    paths: HarnessPaths,
    *,
    refresh: bool = False,
    fetcher: HttpFetcher | None = None,
    extractor: OllamaCPIExtractor | None = None,
) -> PipelineResult:
    own_fetcher = fetcher is None
    fetcher = fetcher or HttpFetcher(paths.http_cache)
    try:
        schedule = fetcher.fetch(SCHEDULE_URL, refresh=refresh)
        try:
            entry = discover_cpi_by_month(schedule.content, reference_month)
        except ValueError as exc:
            raise SourceError(str(exc)) from exc
        _log("month_discovered", reference_month=reference_month, source_url=entry.url)
        return run_url(
            entry.url,
            paths,
            refresh=refresh,
            fetcher=fetcher,
            extractor=extractor,
        )
    finally:
        if own_fetcher:
            fetcher.close()
