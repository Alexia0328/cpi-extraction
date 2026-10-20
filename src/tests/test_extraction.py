from decimal import Decimal
from pathlib import Path

from src.cpi_harness.extraction import OllamaCPIExtractor
from src.cpi_harness.models import LLMCPIEvidence, LLMCPIRecord, StructuredExtraction
from src.cpi_harness.source_extraction import reconcile_with_source
from src.cpi_harness.validation import validate_extraction


class FakeResponse:
    def __init__(self, parsed):
        self.payload = {
            "message": {"content": parsed.model_dump_json()},
            "prompt_eval_count": 1000,
            "eval_count": 200,
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = 0
        self.request = None

    def post(self, path, *, json):
        self.calls += 1
        self.request = json
        return FakeResponse(self.parsed)


def test_clear_source_uses_fast_path(publication, tmp_path: Path):
    publication = publication.model_copy(
        update={
            "cleaned_text": publication.cleaned_text.replace(
                "Excluding gasoline, the CPI rose 2.2% year over year.",
                "Excluding gasoline, the CPI rose 2.2% year over year in July.",
            )
        }
    )
    result = OllamaCPIExtractor(tmp_path).extract(publication)
    assert result.model_id == "deterministic-fast-path-v1"
    assert result.record.cpi_ex_gas_yoy_pct == Decimal("2.2")


def test_uncertain_wording_uses_cached_structured_model(publication, valid_attempt, tmp_path: Path):
    sentence = "A revised methodology for cellular services is planned for later this year."
    publication = publication.model_copy(
        update={"cleaned_text": publication.cleaned_text + "\n\n" + sentence}
    )
    parsed = StructuredExtraction(
        record=LLMCPIRecord.model_validate(
            valid_attempt.record.model_dump() | {"methodology_change_status": "future_announced"}
        ),
        evidence=LLMCPIEvidence.model_validate(
            valid_attempt.evidence.model_dump(exclude={"methodology_change_status"})
            | {"methodology_change_status": sentence}
        ),
    )
    client = FakeClient(parsed)
    extractor = OllamaCPIExtractor(tmp_path, client=client)
    first = extractor.extract(publication)
    second = extractor.extract(publication)
    assert client.calls == 1
    assert second.cache_hit
    assert validate_extraction(publication, first).valid
    assert "Return JSON matching this schema" not in client.request["messages"][1]["content"]


def test_reconciliation_prevents_comparison_month_leakage(publication, valid_attempt):
    sentence = "On a monthly basis, the CPI was unchanged in January, following a 0.3% decline in December."
    publication = publication.model_copy(
        update={"reference_month": "2024-01", "cleaned_text": sentence}
    )
    record = valid_attempt.record.model_copy(update={"headline_mom_pct": "0.3"})
    evidence = valid_attempt.evidence.model_copy(update={"headline_mom_pct": sentence})
    reconciled, _ = reconcile_with_source(publication, record, evidence)
    assert reconciled.headline_mom_pct == 0
