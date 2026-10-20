from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


BUSINESS_FIELDS: Final[tuple[str, ...]] = (
    "reference_month",
    "release_date",
    "headline_yoy_pct",
    "headline_yoy_change_pp",
    "headline_mom_pct",
    "headline_mom_sa_pct",
    "cpi_ex_gas_yoy_pct",
    "methodology_change_status",
)

MethodologyChangeStatus = Literal[
    "effective_now",
    "future_announced",
    "effective_now_and_future_announced",
    "none",
]


def canonical_reference_month(value: str) -> str:
    match = re.fullmatch(r"([A-Za-z]+) (\d{4})", value.strip())
    if match:
        try:
            month = datetime.strptime(match.group(1), "%B").month
            return f"{int(match.group(2)):04d}-{month:02d}"
        except ValueError:
            pass
    return value.strip()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CPIRecord(StrictModel):
    reference_month: str
    release_date: date
    headline_yoy_pct: Decimal
    headline_yoy_change_pp: Decimal
    headline_mom_pct: Decimal
    headline_mom_sa_pct: Decimal
    cpi_ex_gas_yoy_pct: Decimal | None
    methodology_change_status: MethodologyChangeStatus

    @field_validator("reference_month")
    @classmethod
    def valid_reference_month(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("reference_month must be YYYY-MM")
        return value

    @field_validator(
        "headline_yoy_pct",
        "headline_yoy_change_pp",
        "headline_mom_pct",
        "headline_mom_sa_pct",
        "cpi_ex_gas_yoy_pct",
        mode="before",
    )
    @classmethod
    def one_decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.1"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("percentage must be a decimal number") from exc


class CPIEvidence(StrictModel):
    reference_month: str
    release_date: str
    headline_yoy_pct: str
    headline_yoy_change_pp: str
    headline_mom_pct: str
    headline_mom_sa_pct: str
    cpi_ex_gas_yoy_pct: str | None
    methodology_change_status: list[str]

    @field_validator("cpi_ex_gas_yoy_pct", mode="before")
    @classmethod
    def null_ex_gas_evidence(cls, value: Any) -> str | None:
        if isinstance(value, str) and value.strip().casefold() in {"null", "none", ""}:
            return None
        return value

    @field_validator("methodology_change_status", mode="before")
    @classmethod
    def null_methodology_evidence(cls, value: Any) -> list[str]:
        if isinstance(value, str) and value.strip().casefold() in {"null", "none", ""}:
            return []
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class LLMCPIRecord(StrictModel):
    """API-facing schema; converted to Decimal-backed CPIRecord after parsing."""

    reference_month: str
    release_date: date
    headline_yoy_pct: float
    headline_yoy_change_pp: float
    headline_mom_pct: float
    headline_mom_sa_pct: float
    cpi_ex_gas_yoy_pct: float | None
    methodology_change_status: MethodologyChangeStatus


class LLMCPIEvidence(StrictModel):
    """Small-model-facing evidence schema; deterministic reconciliation can add both method signals."""

    reference_month: str
    release_date: str
    headline_yoy_pct: str
    headline_yoy_change_pp: str
    headline_mom_pct: str
    headline_mom_sa_pct: str
    cpi_ex_gas_yoy_pct: str | None
    methodology_change_status: str | None

    @field_validator("cpi_ex_gas_yoy_pct", "methodology_change_status", mode="before")
    @classmethod
    def null_evidence(cls, value: Any) -> str | None:
        if isinstance(value, str) and value.strip().casefold() in {"null", "none", ""}:
            return None
        return value

    def business_evidence(self) -> CPIEvidence:
        payload = self.model_dump()
        passage = payload["methodology_change_status"]
        payload["methodology_change_status"] = [passage] if passage else []
        return CPIEvidence.model_validate(payload)


class StructuredExtraction(StrictModel):
    record: LLMCPIRecord
    evidence: LLMCPIEvidence

    def business_record(self, reference_month_fallback: str | None = None) -> CPIRecord:
        payload = self.record.model_dump()
        payload["reference_month"] = canonical_reference_month(payload["reference_month"])
        if reference_month_fallback and re.fullmatch(r"[A-Za-z]+", payload["reference_month"]):
            payload["reference_month"] = reference_month_fallback
        return CPIRecord.model_validate(payload)


class UsageMetrics(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ExtractionAttempt(StrictModel):
    cache_hit: bool
    cache_key: str
    model_id: str
    prompt_version: str
    latency_ms: int
    usage: UsageMetrics
    record: CPIRecord
    evidence: CPIEvidence


class ValidationReport(StrictModel):
    status: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.status == "valid" and not self.errors


class SourceMetadata(StrictModel):
    url: str
    fetched_at: datetime
    sha256: str
    published_at: datetime | None = None
    discovery_lag_seconds: int | None = None


class ProcessingMetadata(StrictModel):
    completed_at: datetime
    method: str
    prompt_version: str
    latency_ms: int
    slo_ms: int
    within_slo: bool
    cache_hit: bool
    input_tokens: int
    output_tokens: int
    validation: str
    warnings: list[str] = Field(default_factory=list)


class OutputEnvelope(StrictModel):
    schema_version: Literal["2"] = "2"
    source: SourceMetadata
    record: CPIRecord
    evidence: CPIEvidence
    processing: ProcessingMetadata


class FeedEntry(StrictModel):
    title: str
    url: str
    updated_at: datetime


@dataclass(frozen=True)
class FetchResult:
    url: str
    content: bytes
    fetched_at: datetime
    source_sha256: str
    cache_hit: bool
    attempts: int
    cache_path: Path


class ParsedPublication(StrictModel):
    source_url: str
    fetched_at: datetime
    source_sha256: str
    title: str
    reference_month: str
    release_date: date
    headline_yoy_pct: Decimal
    headline_yoy_change_pp: Decimal
    cleaned_text: str


class EvaluationCase(StrictModel):
    case_id: str
    source_url: str
    gold: CPIRecord
    notes: list[str] = Field(default_factory=list)


class CaseScore(StrictModel):
    case_id: str
    strict_pass: bool
    timely_pass: bool
    utility_pass: bool
    validation_pass: bool
    processing_latency_ms: int | None
    correct_fields: int
    total_fields: int = 8
    field_results: dict[str, bool]


class EvaluationSummary(StrictModel):
    mode: str
    processing_slo_ms: int
    utility_records_correct: int
    utility_records_total: int
    utility_accuracy_pct: float
    strict_records_correct: int
    strict_records_total: int
    strict_accuracy_pct: float
    fields_correct: int
    fields_total: int
    field_accuracy_pct: float
    cases: list[CaseScore]
