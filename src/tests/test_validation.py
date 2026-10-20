from decimal import Decimal

from src.cpi_harness.validation import validate_extraction


def test_valid_record_passes(publication, valid_attempt):
    assert validate_extraction(publication, valid_attempt).valid


def test_headline_card_mismatch_is_rejected(publication, valid_attempt):
    record = valid_attempt.record.model_copy(update={"headline_yoy_pct": Decimal("9.9")})
    report = validate_extraction(publication, valid_attempt.model_copy(update={"record": record}))
    assert any("first-card" in error for error in report.errors)


def test_adjusted_value_needs_adjusted_evidence(publication, valid_attempt):
    evidence = valid_attempt.evidence.model_copy(
        update={"headline_mom_sa_pct": "On a monthly basis, the CPI rose 0.5% in July."}
    )
    record = valid_attempt.record.model_copy(update={"headline_mom_sa_pct": Decimal("0.5")})
    report = validate_extraction(
        publication, valid_attempt.model_copy(update={"record": record, "evidence": evidence})
    )
    assert report.errors


def test_methodology_change_cannot_validate_as_none(publication, valid_attempt):
    sentence = "With this release, a new data source has been incorporated for the fuel oil index."
    publication = publication.model_copy(
        update={"cleaned_text": publication.cleaned_text + "\n\n" + sentence}
    )
    report = validate_extraction(publication, valid_attempt)
    assert any("effective_now" in error for error in report.errors)
