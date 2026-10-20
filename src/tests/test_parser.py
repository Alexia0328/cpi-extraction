from dataclasses import replace

import pytest

from src.cpi_harness.fetch import SourceError
from src.cpi_harness.parser import parse_publication


def test_parses_supported_cpi_page(fetch_result):
    publication = parse_publication(fetch_result)
    assert publication.reference_month == "2026-07"
    assert str(publication.headline_yoy_pct) == "3.0"
    assert str(publication.headline_yoy_change_pp) == "0.2"
    assert "Note to readers" in publication.cleaned_text
    assert "This must be removed" not in publication.cleaned_text


def test_wrong_page_structure_fails_closed(fetch_result):
    broken = replace(fetch_result, content=fetch_result.content.replace(b'id="wb-cont"', b'id="wrong"'))
    with pytest.raises(SourceError, match="h1#wb-cont"):
        parse_publication(broken)
