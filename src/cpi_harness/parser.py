from __future__ import annotations

import copy
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal

from lxml import etree, html

from .discovery import CPI_TITLE_RE
from .fetch import SourceError
from .models import FetchResult, ParsedPublication


MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("−", "-").replace("–", "-")
    return " ".join(value.split())


def _parse_title_month(title: str) -> str:
    match = re.fullmatch(r"Consumer Price Index, ([A-Za-z]+) (\d{4})", title)
    if not match or match.group(1) not in MONTHS:
        raise SourceError("page title is not a supported monthly CPI release")
    return f"{int(match.group(2)):04d}-{MONTHS[match.group(1)]:02d}"


def _parse_release_date(section: etree._Element) -> date:
    nodes = section.xpath(
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' sd-release-date ')]"
    )
    text = normalize_text("".join(nodes[0].itertext())) if nodes else ""
    match = re.search(r"Released:\s*(\d{4}-\d{2}-\d{2})", text)
    if not match:
        raise SourceError("release date is missing or malformed")
    return date.fromisoformat(match.group(1))


def _headline_card_yoy(section: etree._Element) -> Decimal:
    cards = section.xpath(".//*[@id='panel1-0']")
    if not cards:
        cards = section.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), ' tab-pane ')][1]"
        )
    if not cards:
        raise SourceError("first official headline card is missing")
    card = cards[0]
    growth_nodes = card.xpath(
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' sd-indicator-growth ')]"
    )
    detail_nodes = card.xpath(
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' sd-indicator-growth-details ')]"
    )
    if growth_nodes and detail_nodes:
        detail = normalize_text("".join(detail_nodes[0].itertext()))
        if re.search(r"12[- ]month change|year[- ]over[- ]year", detail, re.IGNORECASE):
            value_match = re.search(
                r"-?\d+(?:\.\d+)?", normalize_text("".join(growth_nodes[0].itertext()))
            )
            if value_match:
                value = Decimal(value_match.group())
                images = growth_nodes[0].xpath(".//img[@alt]")
                if images and images[0].get("alt", "").casefold() in {"decrease", "down"}:
                    value = -abs(value)
                return value.quantize(Decimal("0.1"))

    card_text = normalize_text("".join(card.itertext()))
    patterns = (
        r"(-?\d+(?:\.\d+)?)\s*%\s*\(?year[- ]over[- ]year",
        r"(-?\d+(?:\.\d+)?)\s*%\s*\(?12[- ]month change",
        r"year[- ]over[- ]year[^\d-]*(-?\d+(?:\.\d+)?)\s*%",
    )
    for pattern in patterns:
        match = re.search(pattern, card_text, flags=re.IGNORECASE)
        if match:
            return Decimal(match.group(1)).quantize(Decimal("0.1"))
    raise SourceError("headline year-over-year value is missing from the first card")


def _clean_article(section: etree._Element, title: str, release_date: date) -> str:
    article = copy.deepcopy(section)
    removal_xpath = (
        ".//script | .//style | .//figure | .//svg | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' release_nav ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' tabpanels ')] | "
        ".//*[@role='tablist'] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' sd-thumbnail ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' gc-chart ')]"
    )
    for node in article.xpath(removal_xpath):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)

    blocks: list[str] = [f"# {title}", f"Released: {release_date.isoformat()}"]
    seen: set[str] = set(blocks)
    stop = False
    for node in article.xpath(".//h2 | .//h3 | .//h4 | .//p | .//li | .//th | .//td"):
        text = normalize_text("".join(node.itertext()))
        if not text:
            continue
        if node.tag in {"h2", "h3", "h4"} and text.casefold() in {
            "products",
            "contact information",
        }:
            stop = True
        if stop:
            break
        rendered = f"## {text}" if node.tag in {"h2", "h3", "h4"} else text
        if rendered not in seen:
            seen.add(rendered)
            blocks.append(rendered)
    cleaned = "\n\n".join(blocks)
    if len(normalize_text(cleaned)) < 300:
        raise SourceError("CPI article body is unexpectedly empty")
    return cleaned


def _headline_yoy_change_pp(cleaned_text: str, current_yoy: Decimal) -> Decimal:
    for raw_line in cleaned_text.splitlines():
        line = normalize_text(raw_line)
        if not re.search(r"\bCPI\b.*(?:year[- ]over[- ]year|12[- ]month)", line, re.IGNORECASE):
            continue
        percentages = list(re.finditer(r"([+-]?\d+(?:\.\d+)?)\s*%", line))
        if len(percentages) >= 2:
            previous_match = percentages[1]
            previous = Decimal(previous_match.group(1))
            nearby = line[max(0, previous_match.start() - 25) : previous_match.end() + 15]
            if re.search(r"\b(?:fell|declined|decreased)\b|%\s*(?:decline|decrease)\b", nearby, re.IGNORECASE):
                previous = -abs(previous)
            return (current_yoy - previous).quantize(Decimal("0.1"))
        if percentages and re.search(r"\b(?:matching|same as|unchanged from)\b", line, re.IGNORECASE):
            return Decimal("0.0")
    raise SourceError("headline year-over-year comparison is missing or malformed")


def parse_publication(fetch: FetchResult) -> ParsedPublication:
    try:
        document = html.fromstring(fetch.content.decode("utf-8"))
    except (UnicodeDecodeError, etree.ParserError, ValueError) as exc:
        raise SourceError("source is not parseable HTML") from exc

    headings = document.xpath("//h1[@id='wb-cont']")
    if len(headings) != 1:
        raise SourceError("expected exactly one h1#wb-cont")
    title = normalize_text("".join(headings[0].itertext()))
    if not CPI_TITLE_RE.fullmatch(title):
        raise SourceError("h1#wb-cont is not an exact monthly CPI title")
    siblings = headings[0].xpath(
        "following-sibling::section["
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' sd-release-date ')] "
        "and .//*[@id='panel1-0']][1]"
    )
    if not siblings:
        raise SourceError("CPI article section is missing after h1#wb-cont")
    section = siblings[0]
    release_date = _parse_release_date(section)
    cleaned = _clean_article(section, title, release_date)
    headline_yoy = _headline_card_yoy(section)
    return ParsedPublication(
        source_url=fetch.url,
        fetched_at=fetch.fetched_at,
        source_sha256=fetch.source_sha256,
        title=title,
        reference_month=_parse_title_month(title),
        release_date=release_date,
        headline_yoy_pct=headline_yoy,
        headline_yoy_change_pp=_headline_yoy_change_pp(cleaned, headline_yoy),
        cleaned_text=cleaned,
    )
