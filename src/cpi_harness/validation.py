from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal

from .models import BUSINESS_FIELDS, ExtractionAttempt, ParsedPublication, ValidationReport
from .methodology import (
    classify_methodology_evidence,
    find_methodology_signal,
    methodology_requires_interpretation,
)
from .parser import normalize_text


def _percentage_in_evidence(value: Decimal, evidence: str) -> bool:
    normalized = normalize_text(evidence)
    if value == Decimal("0.0") and re.search(r"\bunchanged\b|\bno change\b", normalized, re.IGNORECASE):
        return True
    expected = f"{value:.1f}"
    candidates = re.findall(r"-?\d+(?:\.\d+)?", normalized)
    negative_wording = re.search(r"\b(?:fell|decreased|declined|was down|was lower)\b", normalized, re.IGNORECASE)
    signed_candidates = [
        -Decimal(item) if value < 0 and Decimal(item) > 0 and negative_wording else Decimal(item)
        for item in candidates
    ]
    number_present = expected in normalized or str(value.normalize()) in normalized
    if value < 0 and negative_wording:
        number_present = number_present or f"{abs(value):.1f}" in normalized
    return any(item.quantize(Decimal("0.1")) == value for item in signed_candidates) and number_present


def _month_end(reference_month: str) -> date:
    year, month = (int(item) for item in reference_month.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def validate_extraction(
    publication: ParsedPublication, attempt: ExtractionAttempt
) -> ValidationReport:
    record = attempt.record
    evidence = attempt.evidence
    errors: list[str] = []
    warnings: list[str] = []

    if record.reference_month != publication.reference_month:
        errors.append(
            f"reference_month {record.reference_month} does not match title-derived {publication.reference_month}"
        )
    if record.release_date != publication.release_date:
        errors.append(
            f"release_date {record.release_date} does not match page-derived {publication.release_date}"
        )
    if record.headline_yoy_pct != publication.headline_yoy_pct:
        errors.append(
            f"headline_yoy_pct {record.headline_yoy_pct} does not match first-card {publication.headline_yoy_pct}"
        )
    if record.headline_yoy_change_pp != publication.headline_yoy_change_pp:
        errors.append(
            f"headline_yoy_change_pp {record.headline_yoy_change_pp} does not match deterministic parse {publication.headline_yoy_change_pp}"
        )
    if record.release_date <= _month_end(record.reference_month):
        errors.append("release_date must be later than the reference month")
    for field_name in (
        "headline_yoy_pct",
        "headline_yoy_change_pp",
        "headline_mom_pct",
        "headline_mom_sa_pct",
        "cpi_ex_gas_yoy_pct",
    ):
        value = getattr(record, field_name)
        if value is not None and not Decimal("-50") <= value <= Decimal("50"):
            errors.append(f"{field_name} is outside the allowed [-50, 50] range")

    source = normalize_text(publication.cleaned_text).casefold()
    for field_name in BUSINESS_FIELDS:
        proof = getattr(evidence, field_name)
        value = getattr(record, field_name)
        if field_name == "methodology_change_status":
            if not proof and value == "none":
                continue
            if not proof:
                errors.append(f"methodology_change_status={value} requires evidence")
                continue
            for passage in proof:
                if normalize_text(passage).casefold() not in source:
                    errors.append(
                        "methodology_change_status evidence is not an exact normalized substring of the article"
                    )
            continue
        if proof is None:
            if field_name == "cpi_ex_gas_yoy_pct" and value is None:
                continue
            errors.append(f"{field_name} has no evidence")
            continue
        if normalize_text(proof).casefold() not in source:
            errors.append(f"{field_name} evidence is not an exact normalized substring of the article")

    semantic_checks = {
        "headline_yoy_pct": r"year[- ]over[- ]year|12[- ]month",
        "headline_yoy_change_pp": r"year[- ]over[- ]year|12[- ]month",
        "headline_mom_pct": r"month[- ]over[- ]month|monthly basis|on the month",
        "headline_mom_sa_pct": r"seasonally adjusted",
        "cpi_ex_gas_yoy_pct": r"excluding (?:gas|gasoline)|without gasoline",
    }
    for field_name, pattern in semantic_checks.items():
        value = getattr(record, field_name)
        proof = getattr(evidence, field_name)
        if value is not None and proof and not re.search(pattern, normalize_text(proof), re.IGNORECASE):
            errors.append(f"{field_name} evidence lacks required semantic wording")

    for field_name in (
        "headline_yoy_pct",
        "headline_mom_pct",
        "headline_mom_sa_pct",
        "cpi_ex_gas_yoy_pct",
    ):
        value = getattr(record, field_name)
        proof = getattr(evidence, field_name)
        if value is not None and proof and not _percentage_in_evidence(value, proof):
            errors.append(f"{field_name} value is not present in its evidence")

    method_proof = evidence.methodology_change_status
    source_method_status, _ = find_methodology_signal(publication.cleaned_text.splitlines())
    if source_method_status != "none" and record.methodology_change_status != source_method_status:
        errors.append(
            "methodology_change_status article classifies as "
            f"{source_method_status}, not {record.methodology_change_status}"
        )
    if record.methodology_change_status == "none":
        if method_proof:
            errors.append("methodology_change_status=none must not carry methodology evidence")
        elif methodology_requires_interpretation(publication.cleaned_text.splitlines()):
            errors.append(
                "methodology_change_status=none is unsafe because the article contains "
                "unclassified methodology-change wording"
            )
        elif source_method_status == "none":
            warnings.append("methodology_change_status=none is supported by absence, with no quote")
    elif not method_proof:
        errors.append(f"methodology_change_status={record.methodology_change_status} requires evidence")
    else:
        proof_statuses = {
            classify_methodology_evidence(normalize_text(passage)) for passage in method_proof
        }
        proof_statuses.discard("none")
        if proof_statuses == {"effective_now", "future_announced"}:
            proof_status = "effective_now_and_future_announced"
        elif len(proof_statuses) == 1:
            proof_status = next(iter(proof_statuses))
        else:
            proof_status = "none"
        if proof_status != record.methodology_change_status:
            errors.append(
                "methodology evidence classifies as "
                f"{proof_status}, not {record.methodology_change_status}"
            )

    if record.cpi_ex_gas_yoy_pct is None and evidence.cpi_ex_gas_yoy_pct is not None:
        errors.append("cpi_ex_gas_yoy_pct=null must not carry evidence for a different measure")

    status = "valid" if not errors else "invalid"
    return ValidationReport(status=status, errors=errors, warnings=warnings)
