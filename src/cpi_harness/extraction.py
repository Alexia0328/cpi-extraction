from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

import httpx

from .models import ExtractionAttempt, ParsedPublication, StructuredExtraction, UsageMetrics
from .source_extraction import deterministic_attempt, reconcile_with_source
from .storage import read_json, write_json_atomic


MODEL_ID = "qwen2.5:1.5b-instruct-q4_K_M"
PROMPT_VERSION = "cpi-extract-v12"
OLLAMA_BASE_URL = "http://localhost:11434"
CONTEXT_PATTERN = re.compile(
    r"^# Consumer Price Index|^Released:|\bCPI\b.*(?:year[- ]over[- ]year|12[- ]month|"
    r"month[- ]over[- ]month|monthly basis|seasonally adjusted|excluding gasoline)|"
    r"excluding gasoline.*\bCPI\b|\b(?:basket|methodolog|collect|calculation|revision policy)\w*\b|"
    r"\b(?:with|beginning with) (?:today'?s|this) release\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You extract data from one English Statistics Canada The Daily monthly Consumer Price Index release.

Use only facts stated in this release, as originally published. Do not use later revisions or outside knowledge.

Rules:
- Identify the release's reference month, not a comparison month.
- Distinguish year-over-year, unadjusted month-over-month, and seasonally adjusted month-over-month values.
- headline_yoy_change_pp is the current reference month's headline year-over-year rate minus the immediately preceding month's headline year-over-year rate stated in the same release, in percentage points. Use the displayed as-published values; positive means acceleration, negative means deceleration, and zero means unchanged.
- Preserve minus signs. Never calculate or infer a missing value; return null where the schema permits it.
- cpi_ex_gas_yoy_pct means the all-items CPI excluding gasoline, year over year. If the release does not state that measure, return both its value and evidence as JSON null; never write the word "null" as evidence.
- methodology_change_status is "effective_now" when a basket, collection method, calculation method, or revision policy starts with this release; "future_announced" when such a change is announced for later; "effective_now_and_future_announced" when both occur; otherwise "none". Routine table updates, tax/tariff treatment, and publication-schedule changes are "none". Return one exact methodology evidence passage, or JSON null for none; deterministic reconciliation may retain a second passage when both timings occur.
- Evidence must be copied verbatim from the supplied article, including the source wording and date format. Do not paraphrase, translate, convert dates to ISO, or combine fragments. Copy a complete sentence when possible. Use the headline sentence containing both the current and comparison-month YoY rates as evidence for headline_yoy_change_pp.
- Re-scan the source before returning to catch current/prior-month and adjusted/unadjusted mix-ups.
"""


class ConfigurationError(RuntimeError):
    pass


class ExtractionError(RuntimeError):
    pass


def _cache_key(source_sha256: str) -> str:
    material = f"{source_sha256}|{MODEL_ID}|{PROMPT_VERSION}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _model_context(text: str) -> str:
    return "\n\n".join(
        line.strip() for line in text.splitlines() if CONTEXT_PATTERN.search(line.strip())
    )


class OllamaCPIExtractor:
    def __init__(self, cache_dir: Path, *, client: object | None = None) -> None:
        self.cache_dir = cache_dir
        self.client = client

    def _client(self) -> object:
        if self.client is None:
            self.client = httpx.Client(
                base_url=os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL),
                timeout=httpx.Timeout(connect=2.0, read=8.0, write=5.0, pool=2.0),
            )
        return self.client

    def _request(self, body: dict) -> dict:
        try:
            response = self._client().post("/api/chat", json=body)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as exc:
            raise ConfigurationError(
                f"Ollama is not reachable at {OLLAMA_BASE_URL}; "
                f"install/start Ollama and pull {MODEL_ID}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ConfigurationError(
                    f"Ollama model {MODEL_ID} is not installed; run 'ollama pull {MODEL_ID}'"
                ) from exc
            raise ExtractionError(
                f"Ollama request failed: HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError, ValueError) as exc:
            raise ExtractionError(f"Ollama extraction request failed: {exc}") from exc

    def extract(self, publication: ParsedPublication) -> ExtractionAttempt:
        deterministic = deterministic_attempt(publication)
        if deterministic is not None:
            return deterministic

        cache_key = _cache_key(publication.source_sha256)
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            cached = ExtractionAttempt.model_validate(read_json(cache_path))
            record, evidence = reconcile_with_source(publication, cached.record, cached.evidence)
            return cached.model_copy(
                update={"cache_hit": True, "latency_ms": 0, "record": record, "evidence": evidence}
            )

        output_schema = StructuredExtraction.model_json_schema()
        request_body = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Extract the CPI record and exact evidence from this release:\n\n"
                        f"<release>\n{_model_context(publication.cleaned_text)}\n</release>"
                    ),
                },
            ],
            "stream": False,
            "format": output_schema,
            "options": {"temperature": 0, "num_predict": 512},
            "keep_alive": "10m",
        }
        started = time.perf_counter()
        payload = self._request(request_body)
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            parsed = StructuredExtraction.model_validate_json(payload["message"]["content"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractionError("Ollama response did not contain valid structured output") from exc

        record, evidence = reconcile_with_source(
            publication,
            parsed.business_record(publication.reference_month),
            parsed.evidence.business_evidence(),
        )
        attempt = ExtractionAttempt(
            cache_hit=False,
            cache_key=cache_key,
            model_id=MODEL_ID,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
            usage=UsageMetrics(
                input_tokens=int(payload.get("prompt_eval_count", 0) or 0),
                output_tokens=int(payload.get("eval_count", 0) or 0),
            ),
            record=record,
            evidence=evidence,
        )
        write_json_atomic(cache_path, attempt)
        return attempt
