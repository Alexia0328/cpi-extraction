from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .extraction import ConfigurationError, OllamaCPIExtractor
from .models import (
    BUSINESS_FIELDS,
    CPIRecord,
    CaseScore,
    EvaluationCase,
    EvaluationSummary,
    FetchResult,
)
from .parser import parse_publication
from .pipeline import PROCESSING_SLO_MS, HarnessPaths
from .storage import write_bytes_atomic, write_json_atomic
from .validation import validate_extraction


class EvaluationError(RuntimeError):
    pass


def _write_eval_quarantine(root: Path, case: EvaluationCase, payload: dict) -> Path:
    path = root / "outputs" / "quarantine" / f"eval-{case.case_id}.json"
    write_json_atomic(path, payload)
    return path


def load_cases(path: Path) -> list[EvaluationCase]:
    if not path.exists():
        raise EvaluationError(f"evaluation cases file not found: {path}")
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(EvaluationCase.model_validate_json(line))
        except Exception as exc:
            raise EvaluationError(f"invalid case on line {line_number}: {exc}") from exc
    if len(cases) < 10:
        raise EvaluationError(f"expected at least 10 frozen cases, found {len(cases)}")
    return cases


def load_predictions(path: Path) -> dict[str, dict]:
    if not path.exists():
        raise EvaluationError(
            f"cached predictions not found: {path}; run 'python main.py eval --live' once and freeze the resulting predictions"
        )
    predictions: dict[str, dict] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            predictions[item["case_id"]] = item
        except (json.JSONDecodeError, KeyError) as exc:
            raise EvaluationError(f"invalid prediction on line {line_number}") from exc
    return predictions


def load_pinned_fixture(root: Path, case: EvaluationCase) -> FetchResult:
    manifest_path = root / "eval" / "fixtures" / "manifest.json"
    if not manifest_path.exists():
        raise EvaluationError(f"pinned fixture manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        entry = manifest["cases"][case.case_id]
        fixture_path = root / entry["fixture_path"]
        expected_sha = entry["source_sha256"]
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationError(f"invalid fixture manifest entry for {case.case_id}") from exc
    content = fixture_path.read_bytes()
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != expected_sha:
        raise EvaluationError(
            f"fixture hash mismatch for {case.case_id}: expected {expected_sha}, got {actual_sha}"
        )
    if entry["source_url"] != case.source_url:
        raise EvaluationError(f"fixture URL mismatch for {case.case_id}")
    return FetchResult(
        url=case.source_url,
        content=content,
        fetched_at=fetched_at,
        source_sha256=actual_sha,
        cache_hit=True,
        attempts=0,
        cache_path=fixture_path,
    )


def score_predictions(
    cases: Iterable[EvaluationCase], predictions: dict[str, dict], *, mode: str
) -> EvaluationSummary:
    scores: list[CaseScore] = []
    for case in cases:
        prediction = predictions.get(case.case_id)
        field_results = {field: False for field in BUSINESS_FIELDS}
        validation_pass = bool(prediction and prediction.get("validation_pass"))
        processing_latency_ms = prediction.get("processing_latency_ms") if prediction else None
        timely_pass = (
            isinstance(processing_latency_ms, int)
            and processing_latency_ms <= PROCESSING_SLO_MS
        )
        if prediction and prediction.get("record") is not None:
            try:
                record = CPIRecord.model_validate(prediction["record"])
            except Exception:
                record = None
            if record is not None:
                for field in BUSINESS_FIELDS:
                    field_results[field] = getattr(record, field) == getattr(case.gold, field)
        correct = sum(field_results.values())
        strict_pass = validation_pass and correct == len(BUSINESS_FIELDS)
        scores.append(
            CaseScore(
                case_id=case.case_id,
                strict_pass=strict_pass,
                timely_pass=timely_pass,
                utility_pass=strict_pass and timely_pass,
                validation_pass=validation_pass,
                processing_latency_ms=processing_latency_ms,
                correct_fields=correct,
                field_results=field_results,
            )
        )
    utility_correct = sum(item.utility_pass for item in scores)
    strict_correct = sum(item.strict_pass for item in scores)
    fields_correct = sum(item.correct_fields for item in scores)
    fields_total = len(scores) * len(BUSINESS_FIELDS)
    return EvaluationSummary(
        mode=mode,
        processing_slo_ms=PROCESSING_SLO_MS,
        utility_records_correct=utility_correct,
        utility_records_total=len(scores),
        utility_accuracy_pct=round(100 * utility_correct / len(scores), 1),
        strict_records_correct=strict_correct,
        strict_records_total=len(scores),
        strict_accuracy_pct=round(100 * strict_correct / len(scores), 1),
        fields_correct=fields_correct,
        fields_total=fields_total,
        field_accuracy_pct=round(100 * fields_correct / fields_total, 1),
        cases=scores,
    )


def evaluate_offline(root: Path) -> EvaluationSummary:
    cases = load_cases(root / "eval" / "cases.jsonl")
    predictions = load_predictions(root / "eval" / "predictions.jsonl")
    summary = score_predictions(cases, predictions, mode="offline")
    write_json_atomic(root / "eval" / "results" / "offline.json", summary)
    return summary


def evaluate_live(root: Path) -> EvaluationSummary:
    cases = load_cases(root / "eval" / "cases.jsonl")
    paths = HarnessPaths(root)
    extractor = OllamaCPIExtractor(paths.llm_cache)
    prediction_rows: list[dict] = []
    for case in cases:
        started = time.perf_counter()
        try:
            publication = parse_publication(load_pinned_fixture(root, case))
            extraction = extractor.extract(publication)
            report = validate_extraction(publication, extraction)
            row = {
                "case_id": case.case_id,
                "source_sha256": publication.source_sha256,
                "record": extraction.record.model_dump(mode="json"),
                "validation_pass": report.valid,
                "validation_errors": report.errors,
                "processing_latency_ms": round((time.perf_counter() - started) * 1000),
                "method": extraction.model_id,
                "input_tokens": extraction.usage.input_tokens,
                "output_tokens": extraction.usage.output_tokens,
            }
            prediction_rows.append(row)
            if not report.valid:
                _write_eval_quarantine(
                    root,
                    case,
                    {
                        "kind": "evaluation_validation_failure",
                        "case_id": case.case_id,
                        "source_url": publication.source_url,
                        "source_sha256": publication.source_sha256,
                        "gold": case.gold.model_dump(mode="json"),
                        "prediction": row,
                        "extraction": extraction.model_dump(mode="json"),
                        "validation": report.model_dump(mode="json"),
                    },
                )
        except ConfigurationError:
            raise
        except Exception as exc:
            row = {
                "case_id": case.case_id,
                "source_url": case.source_url,
                "record": None,
                "validation_pass": False,
                "validation_errors": [f"live evaluation error: {type(exc).__name__}: {exc}"],
                "processing_latency_ms": round((time.perf_counter() - started) * 1000),
            }
            prediction_rows.append(row)
            _write_eval_quarantine(
                root,
                case,
                {
                    "kind": "evaluation_runtime_failure",
                    "case_id": case.case_id,
                    "source_url": case.source_url,
                    "gold": case.gold.model_dump(mode="json"),
                    "prediction": row,
                },
            )

    live_path = root / "eval" / "predictions.live.jsonl"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in prediction_rows) + "\n"
    write_bytes_atomic(live_path, payload.encode("utf-8"))
    baseline_path = root / "eval" / "predictions.baseline.jsonl"
    if not baseline_path.exists():
        write_bytes_atomic(baseline_path, payload.encode("utf-8"))
    write_bytes_atomic(root / "eval" / "predictions.jsonl", payload.encode("utf-8"))
    predictions = {row["case_id"]: row for row in prediction_rows}
    summary = score_predictions(cases, predictions, mode="live")
    write_json_atomic(root / "eval" / "results" / "live.json", summary)
    return summary
