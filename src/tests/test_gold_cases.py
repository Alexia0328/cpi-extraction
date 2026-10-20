from pathlib import Path

from src.cpi_harness.evaluation import load_cases, load_pinned_fixture
from src.cpi_harness.parser import parse_publication


def test_gold_cases_are_complete_and_bound_to_pinned_pages():
    root = Path.cwd()
    cases = load_cases(root / "eval" / "cases.jsonl")
    assert len(cases) == 10
    assert sum(len(case.gold.model_dump()) for case in cases) == 80
    for case in cases:
        publication = parse_publication(load_pinned_fixture(root, case))
        assert publication.reference_month == case.gold.reference_month
        assert publication.release_date == case.gold.release_date
        assert publication.headline_yoy_pct == case.gold.headline_yoy_pct
