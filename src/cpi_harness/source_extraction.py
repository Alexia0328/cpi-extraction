from __future__ import annotations

import hashlib
import re
import time
from decimal import Decimal

from .methodology import find_methodology_signal, methodology_requires_interpretation
from .models import CPIEvidence, CPIRecord, ExtractionAttempt, ParsedPublication, UsageMetrics


FAST_PATH_ID = "deterministic-fast-path-v1"
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
YOY_PATTERN = r"\bCPI\b.*(?:year[- ]over[- ]year|year-over-year basis|12[- ]month)"
MOM_PATTERN = (
    r"(?:\bCPI\b.*(?:month over month|month-over-month|monthly basis)|"
    r"(?:month over month|month-over-month|monthly basis).*\bCPI\b)"
)
SA_PATTERN = r"(?:\bCPI\b.*seasonally adjusted|seasonally adjusted.*\bCPI\b)"
EX_GAS_PATTERN = r"\bCPI\b.*excluding gasoline|excluding gasoline.*\bCPI\b"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _sentences(lines: list[str]) -> list[str]:
    return [
        sentence.strip()
        for line in lines
        for sentence in re.split(r"(?<=[.!?])\s+", line)
        if sentence.strip()
    ]


def _signed_percentage(line: str, match: re.Match[str]) -> Decimal:
    value = Decimal(match.group(1)).quantize(Decimal("0.1"))
    if value > 0 and re.search(
        r"\b(?:fell|decreased|declined|was down|was lower)\s*$",
        line[: match.start()],
        re.IGNORECASE,
    ):
        return -value
    return value


def _first_percentage(line: str) -> Decimal | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", line)
    if match:
        return _signed_percentage(line, match)
    if re.search(r"\b(?:unchanged|no change)\b", line, re.IGNORECASE):
        return Decimal("0.0")
    return None


def _percentage_for_month(
    line: str, reference_month: str, *, permissive: bool
) -> Decimal | None:
    month = MONTH_NAMES[int(reference_month[-2:]) - 1]
    if re.search(
        rf"\b(?:unchanged|no change)\b[^%.!?;]{{0,60}}\bin\s+{month}\b",
        line,
        re.IGNORECASE,
    ):
        return Decimal("0.0")
    for match in re.finditer(r"([+-]?\d+(?:\.\d+)?)\s*%", line):
        if re.match(rf"[^%]{{0,60}}\bin\s+{month}\b", line[match.end() :], re.IGNORECASE):
            return _signed_percentage(line, match)
    current = re.search(
        r"\b(rose|increased|grew|fell|decreased|declined|slowed to|accelerated to|"
        r"was up|was down)\s+(?:by\s+|a\s+)?([+-]?\d+(?:\.\d+)?)\s*%",
        line,
        re.IGNORECASE,
    )
    if current:
        value = Decimal(current.group(2)).quantize(Decimal("0.1"))
        return -abs(value) if re.search(
            r"fell|decreased|declined|was down", current.group(1), re.IGNORECASE
        ) else value
    return _first_percentage(line) if permissive else None


def _metric(
    lines: list[str],
    pattern: str,
    reference_month: str,
    *,
    permissive: bool,
    exclude: str | None = None,
) -> tuple[str, Decimal] | None:
    for line in lines:
        if not re.search(pattern, line, re.IGNORECASE):
            continue
        if exclude and re.search(exclude, line, re.IGNORECASE):
            continue
        value = _percentage_for_month(line, reference_month, permissive=permissive)
        if value is not None:
            return line, value
    return None


def _source_fields(
    publication: ParsedPublication, *, strict: bool
) -> tuple[dict[str, object], dict[str, object]] | None:
    lines = _lines(publication.cleaned_text)
    sentences = _sentences(lines)
    reference = next((line for line in lines if line.startswith("# Consumer Price Index,")), None)
    release = next((line for line in lines if line.startswith("Released:")), None)
    headline = _metric(
        sentences, YOY_PATTERN, publication.reference_month, permissive=not strict
    )
    mom = _metric(
        sentences,
        MOM_PATTERN,
        publication.reference_month,
        permissive=not strict,
        exclude=r"seasonally adjusted",
    )
    sa = _metric(sentences, SA_PATTERN, publication.reference_month, permissive=not strict)
    ex_gas_line = next(
        (line for line in sentences if re.search(EX_GAS_PATTERN, line, re.IGNORECASE)), None
    )
    ex_gas = (
        _percentage_for_month(
            ex_gas_line, publication.reference_month, permissive=not strict
        )
        if ex_gas_line
        else None
    )
    needs_model = methodology_requires_interpretation(lines)
    if strict and (
        not all((reference, release, headline, mom, sa))
        or (ex_gas_line is not None and ex_gas is None)
        or needs_model
    ):
        return None

    record: dict[str, object] = {}
    evidence: dict[str, object] = {}
    if reference:
        evidence["reference_month"] = reference
    if release:
        evidence["release_date"] = release
    if headline:
        record.update(
            headline_yoy_pct=publication.headline_yoy_pct,
            headline_yoy_change_pp=publication.headline_yoy_change_pp,
        )
        evidence.update(headline_yoy_pct=headline[0], headline_yoy_change_pp=headline[0])
    for field, found in (("headline_mom_pct", mom), ("headline_mom_sa_pct", sa)):
        if found:
            record[field], evidence[field] = found[1], found[0]
    record["cpi_ex_gas_yoy_pct"] = ex_gas
    evidence["cpi_ex_gas_yoy_pct"] = ex_gas_line
    if not needs_model:
        status, method_lines = find_methodology_signal(lines)
        record["methodology_change_status"] = status
        evidence["methodology_change_status"] = method_lines
    return record, evidence


def reconcile_with_source(
    publication: ParsedPublication, record: CPIRecord, evidence: CPIEvidence
) -> tuple[CPIRecord, CPIEvidence]:
    source = _source_fields(publication, strict=False)
    assert source is not None
    record_updates, evidence_updates = source
    return (
        record.model_copy(update=record_updates),
        evidence.model_copy(update=evidence_updates),
    )


def deterministic_attempt(publication: ParsedPublication) -> ExtractionAttempt | None:
    started = time.perf_counter()
    source = _source_fields(publication, strict=True)
    if source is None:
        return None
    record_updates, evidence_updates = source
    record = CPIRecord(
        reference_month=publication.reference_month,
        release_date=publication.release_date,
        **record_updates,
    )
    evidence = CPIEvidence(**evidence_updates)
    cache_key = hashlib.sha256(
        f"{publication.source_sha256}|{FAST_PATH_ID}".encode("utf-8")
    ).hexdigest()
    return ExtractionAttempt(
        cache_hit=False,
        cache_key=cache_key,
        model_id=FAST_PATH_ID,
        prompt_version=FAST_PATH_ID,
        latency_ms=round((time.perf_counter() - started) * 1000),
        usage=UsageMetrics(),
        record=record,
        evidence=evidence,
    )
