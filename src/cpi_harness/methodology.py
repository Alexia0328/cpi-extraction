from __future__ import annotations

import re
from collections.abc import Iterable

from .models import MethodologyChangeStatus


QUALIFYING = re.compile(
    r"basket|collection (?:method|approach|period|frequency|of prices)|"
    r"prices .*?(?:will be|are now being|now being) collected|calculation method|"
    r"(?:will be|are|is|now) calculated based on|calculated using .*method|"
    r"responsibility for the calculation|revision policy|"
    r"(?:enhanced|new|updated) methodolog(?:y|ies)|new (?:alternative )?data sources?|"
    r"new data source has been incorporated",
    re.IGNORECASE,
)
EFFECTIVE_NOW = re.compile(
    r"with (?:today's|this) release|beginning with this release|starting with this release|"
    r"(?:has been |is )?updated with the release|effective (?:with|as of) this release|"
    r"(?:is|are) now calculated",
    re.IGNORECASE,
)
FUTURE_ANNOUNCED = re.compile(
    r"\bwill\b|beginning with (?:the )?(?:next )?release of|upcoming|next release|"
    r"future release|as of the .* release",
    re.IGNORECASE,
)
NEGATED_CHANGE = re.compile(
    r"\b(?:no|without)\s+(?:qualifying\s+)?(?:methodology|methodological|basket|"
    r"collection|calculation|revision)[^.]{0,80}\bchange",
    re.IGNORECASE,
)
METHODOLOGY_SUBJECT = re.compile(
    r"methodolog(?:y|ies)|data sources?|basket weights?|"
    r"(?:collection|calculation) (?:method|approach|procedure|process)|revision policy",
    re.IGNORECASE,
)
CHANGE_LANGUAGE = re.compile(
    r"\b(?:new|enhanced|updated?|revised?|changes?|different(?:ly)?|"
    r"incorporat(?:e|ed|ing)|implement(?:ed|ing)?|introduc(?:e|ed|ing)|"
    r"replac(?:e|ed|ing)|overhaul(?:ed|ing)?|adopt(?:ed|ing)?|transition(?:ed|ing)?|"
    r"switch(?:ed|ing)?|planned|plans?)\b",
    re.IGNORECASE,
)
CURRENT_TIMING = re.compile(
    r"with (?:today's|this) release|beginning with this release|starting with this release|"
    r"\b(?:now|currently|began|starts?|takes? effect|in effect|entered use)\b|"
    r"\b(?:has|have) been (?:implemented|incorporated|adopted|introduced)\b|"
    r"\b(?:is|are) now (?:implemented|incorporated|adopted|introduced|used)\b",
    re.IGNORECASE,
)
FUTURE_TIMING = re.compile(
    r"\b(?:will|planned|plans?|later|upcoming|future|forthcoming|intends?|scheduled|"
    r"coming months?|next release)\b",
    re.IGNORECASE,
)


def classify_methodology_text(text: str) -> MethodologyChangeStatus:
    if NEGATED_CHANGE.search(text) or not QUALIFYING.search(text):
        return "none"
    if EFFECTIVE_NOW.search(text):
        return "effective_now"
    if FUTURE_ANNOUNCED.search(text):
        return "future_announced"
    return "none"


def classify_methodology_evidence(text: str) -> MethodologyChangeStatus:
    """Classify exact model evidence; use broader timing only for unfamiliar wording."""
    strict = classify_methodology_text(text)
    if strict != "none" or NEGATED_CHANGE.search(text):
        return strict
    if not (METHODOLOGY_SUBJECT.search(text) and CHANGE_LANGUAGE.search(text)):
        return "none"
    effective = bool(CURRENT_TIMING.search(text))
    future = bool(FUTURE_TIMING.search(text))
    if effective and future:
        return "effective_now_and_future_announced"
    if effective:
        return "effective_now"
    if future:
        return "future_announced"
    return "none"


def find_methodology_signal(
    lines: Iterable[str],
) -> tuple[MethodologyChangeStatus, list[str]]:
    effective: str | None = None
    future: str | None = None
    for line in lines:
        status = classify_methodology_text(line)
        if status == "effective_now" and effective is None:
            effective = line
        if status == "future_announced" and future is None:
            future = line
    if effective is not None and future is not None:
        return "effective_now_and_future_announced", [effective, future]
    if effective is not None:
        return "effective_now", [effective]
    if future is not None:
        return "future_announced", [future]
    return "none", []


def methodology_requires_interpretation(lines: Iterable[str]) -> bool:
    """Return true only when change wording exists but the strict rules cannot label it."""
    material = list(lines)
    if find_methodology_signal(material)[0] != "none":
        return False
    return any(classify_methodology_evidence(line) != "none" for line in material)
