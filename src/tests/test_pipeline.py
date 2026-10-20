import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.cpi_harness.pipeline import HarnessPaths, ValidationFailure, run_url


class FakeFetcher:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def fetch(self, *args, **kwargs):
        self.calls += 1
        return self.result


class FakeExtractor:
    def __init__(self, attempt):
        self.attempt = attempt
        self.calls = 0

    def extract(self, publication):
        self.calls += 1
        return self.attempt


def test_pipeline_saves_and_skips_duplicate_url(tmp_path: Path, fetch_result, valid_attempt):
    fetcher = FakeFetcher(fetch_result)
    extractor = FakeExtractor(valid_attempt)
    paths = HarnessPaths(tmp_path)
    first = run_url(fetch_result.url, paths, fetcher=fetcher, extractor=extractor)
    second = run_url(fetch_result.url, paths, fetcher=fetcher, extractor=extractor)
    assert first.status == "saved"
    assert second.status == "skipped"
    assert fetcher.calls == extractor.calls == 1


def test_invalid_candidate_is_quarantined_once(tmp_path: Path, fetch_result, valid_attempt):
    record = valid_attempt.record.model_copy(update={"headline_yoy_pct": Decimal("9.9")})
    extractor = FakeExtractor(valid_attempt.model_copy(update={"record": record}))
    with pytest.raises(ValidationFailure) as raised:
        run_url(
            fetch_result.url,
            HarnessPaths(tmp_path),
            fetcher=FakeFetcher(fetch_result),
            extractor=extractor,
        )
    assert extractor.calls == 1
    payload = json.loads(raised.value.quarantine_path.read_text(encoding="utf-8"))
    assert payload["output"]["processing"]["validation"] == "invalid"
