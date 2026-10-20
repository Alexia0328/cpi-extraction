from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import urljoin

from lxml import etree

from .fetch import canonicalize_url
from .models import FeedEntry


CPI_TITLE_RE = re.compile(
    r"^Consumer Price Index, (January|February|March|April|May|June|July|August|September|October|November|December) \d{4}$"
)
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def discover_latest_cpi(feed_xml: bytes) -> FeedEntry:
    try:
        root = etree.fromstring(feed_xml)
    except etree.XMLSyntaxError as exc:
        raise ValueError("invalid Atom feed XML") from exc

    candidates: list[FeedEntry] = []
    for entry in root.xpath("//atom:entry", namespaces=ATOM_NS):
        title_nodes = entry.xpath("./atom:title", namespaces=ATOM_NS)
        title = " ".join("".join(title_nodes[0].itertext()).split()) if title_nodes else ""
        if not CPI_TITLE_RE.fullmatch(title):
            continue
        links = entry.xpath("./atom:link[@href]", namespaces=ATOM_NS)
        updated_nodes = entry.xpath("./atom:updated/text()", namespaces=ATOM_NS)
        if not links or not updated_nodes:
            continue
        try:
            updated = datetime.fromisoformat(updated_nodes[0].replace("Z", "+00:00"))
        except ValueError:
            continue
        candidates.append(
            FeedEntry(
                title=title,
                url=canonicalize_url(links[0].get("href"), require_english_html=True),
                updated_at=updated,
            )
        )

    if not candidates:
        raise ValueError("feed contains no exact monthly CPI release entries")
    return max(candidates, key=lambda item: item.updated_at)


def discover_cpi_by_month(schedule_json: bytes, reference_month: str) -> FeedEntry:
    match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", reference_month)
    if not match:
        raise ValueError("reference month must be YYYY-MM")
    target = f"{MONTH_NAMES[int(match.group(2)) - 1]} {match.group(1)}"
    try:
        items = json.loads(schedule_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Statistics Canada release schedule JSON") from exc
    if not isinstance(items, list):
        raise ValueError("invalid Statistics Canada release schedule JSON")

    matches = [
        item for item in items
        if isinstance(item, dict)
        and str(item.get("title", "")).strip() == "Consumer Price Index"
        and " ".join(str(item.get("description", "")).split()) == target
    ]
    released = [item for item in matches if str(item.get("url", "")).strip()]
    if not released:
        raise ValueError(f"no published CPI release found for {reference_month}")
    if len(released) != 1:
        raise ValueError(f"multiple CPI releases found for {reference_month}")

    item = released[0]
    try:
        published_at = datetime.fromisoformat(str(item["date"])).replace(tzinfo=UTC)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"CPI schedule entry for {reference_month} has an invalid date") from exc
    return FeedEntry(
        title=f"Consumer Price Index, {target}",
        url=canonicalize_url(
            urljoin("https://www.statcan.gc.ca", str(item["url"])),
            require_english_html=True,
        ),
        updated_at=published_at,
    )
