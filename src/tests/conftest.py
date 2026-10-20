from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.cpi_harness.models import (
    CPIEvidence,
    CPIRecord,
    ExtractionAttempt,
    FetchResult,
    ParsedPublication,
    UsageMetrics,
)


SAMPLE_URL = "https://www.statcan.gc.ca/daily-quotidien/260817/dq260817a-eng.htm"
SAMPLE_HTML = b"""<!doctype html>
<html><body><main class="container">
<h1 id="wb-cont">Consumer Price Index, July 2026</h1>
<section>
  <p class="sd-release-date">Released: 2026-08-17</p>
  <div class="release_nav">Previous release</div>
  <div class="tabpanels"><div id="panel1-0">Consumer Price Index July 2026 3.0% (year-over-year change)</div></div>
  <h2>Consumer prices rise in July</h2>
  <p>The Consumer Price Index (CPI) rose 3.0% year over year in July, up from 2.8% in June.</p>
  <p>On a monthly basis, the CPI rose 0.5% in July.</p>
  <p>On a seasonally adjusted monthly basis, the CPI rose 0.3% in July.</p>
  <p>Excluding gasoline, the CPI rose 2.2% year over year.</p>
  <p>This paragraph supplies enough realistic body text for the fail-closed minimum article-length check. It discusses several current-month and prior-month comparisons without changing any values used in the test record.</p>
  <h2>Note to readers</h2>
  <p>No qualifying basket, collection, calculation, or revision-policy change begins with this release.</p>
  <h2>Next release</h2>
  <p>The Consumer Price Index for August will be released on September 14, 2026.</p>
  <h2>Products</h2><p>This must be removed.</p>
</section></main></body></html>"""


@pytest.fixture
def fetch_result(tmp_path: Path) -> FetchResult:
    return FetchResult(
        url=SAMPLE_URL,
        content=SAMPLE_HTML,
        fetched_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        source_sha256=hashlib.sha256(SAMPLE_HTML).hexdigest(),
        cache_hit=False,
        attempts=1,
        cache_path=tmp_path / "sample.bin",
    )


@pytest.fixture
def publication() -> ParsedPublication:
    cleaned = "\n\n".join(
        [
            "# Consumer Price Index, July 2026",
            "Released: 2026-08-17",
            "The Consumer Price Index (CPI) rose 3.0% year over year in July, up from 2.8% in June.",
            "On a monthly basis, the CPI rose 0.5% in July.",
            "On a seasonally adjusted monthly basis, the CPI rose 0.3% in July.",
            "Excluding gasoline, the CPI rose 2.2% year over year.",
            "No qualifying basket, collection, calculation, or revision-policy change begins with this release.",
            "## Next release",
            "The Consumer Price Index for August will be released on September 14, 2026.",
        ]
    )
    return ParsedPublication(
        source_url=SAMPLE_URL,
        fetched_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        source_sha256="a" * 64,
        title="Consumer Price Index, July 2026",
        reference_month="2026-07",
        release_date=date(2026, 8, 17),
        headline_yoy_pct=Decimal("3.0"),
        headline_yoy_change_pp=Decimal("0.2"),
        cleaned_text=cleaned,
    )


@pytest.fixture
def valid_attempt() -> ExtractionAttempt:
    record = CPIRecord(
        reference_month="2026-07",
        release_date=date(2026, 8, 17),
        headline_yoy_pct="3.0",
        headline_yoy_change_pp="0.2",
        headline_mom_pct="0.5",
        headline_mom_sa_pct="0.3",
        cpi_ex_gas_yoy_pct="2.2",
        methodology_change_status="none",
    )
    evidence = CPIEvidence(
        reference_month="Consumer Price Index, July 2026",
        release_date="Released: 2026-08-17",
        headline_yoy_pct="The Consumer Price Index (CPI) rose 3.0% year over year in July, up from 2.8% in June.",
        headline_yoy_change_pp="The Consumer Price Index (CPI) rose 3.0% year over year in July, up from 2.8% in June.",
        headline_mom_pct="On a monthly basis, the CPI rose 0.5% in July.",
        headline_mom_sa_pct="On a seasonally adjusted monthly basis, the CPI rose 0.3% in July.",
        cpi_ex_gas_yoy_pct="Excluding gasoline, the CPI rose 2.2% year over year.",
        methodology_change_status=[],
    )
    return ExtractionAttempt(
        cache_hit=False,
        cache_key="key",
        model_id="test-model",
        prompt_version="test-prompt",
        latency_ms=10,
        usage=UsageMetrics(),
        record=record,
        evidence=evidence,
    )
