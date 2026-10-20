from pathlib import Path

from src.cpi_harness.evaluation import evaluate_offline, score_predictions
from src.cpi_harness.models import EvaluationCase


def test_score_requires_correct_valid_and_timely_record(valid_attempt):
    case = EvaluationCase(case_id="case", source_url="https://example.invalid", gold=valid_attempt.record)
    prediction = {
        "case": {
            "record": valid_attempt.record.model_dump(mode="json"),
            "validation_pass": True,
            "processing_latency_ms": 1000,
        }
    }
    assert score_predictions([case], prediction, mode="test").utility_records_correct == 1
    prediction["case"]["processing_latency_ms"] = 10_001
    assert score_predictions([case], prediction, mode="test").utility_records_correct == 0


def test_offline_eval_reproduces_frozen_result():
    summary = evaluate_offline(Path.cwd())
    assert summary.utility_records_correct == 10
    assert summary.strict_records_correct == 10
    assert summary.fields_correct == 80
