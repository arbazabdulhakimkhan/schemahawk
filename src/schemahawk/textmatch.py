"""Shared, token-boundary text matching.

Extracted so the deterministic relevance scorer (V1) and the match engine (V2)
share exactly one matching implementation. Fragments are matched on token
boundaries - ``(?<!\\w)`` before and ``(?!\\w)`` after - so substrings never create
false positives. This was added after live data showed ``ssis`` matching inside
"a**ssis**tant" and ``sql`` matching inside "postgre**sql**".

The module is pure and dependency-free so it is trivially unit-testable.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

# Structural patterns used by ``skill_fragments`` (kept private: operators write
# skill names, not regular expressions).
_PAREN = re.compile(r"\s*\(([^)]*)\)")
_SLASH = re.compile(r"\s+/\s+")


def token_matcher(fragment: str) -> re.Pattern[str]:
    """Compile a regex fragment so it only matches whole tokens."""
    return re.compile(rf"(?<!\w)(?:{fragment})(?!\w)", re.I)


def plain_fragment(text: str) -> str:
    """Turn arbitrary operator text into a token-boundary regex fragment."""
    return r"\s+".join(re.escape(word) for word in text.split())


# Vendor short-forms a job posting is likely to use instead of the long name.
# Kept explicit rather than generated from the trailing token: a generated rule
# matched "bi developer" against "Power BI" and would invent matches. Each entry
# is a deliberate, reviewable decision.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "azure data factory (adf)": ("azure data factory", "adf"),
    "google bigquery": ("bigquery",),
    "alteryx designer": ("alteryx",),
    "power bi": ("powerbi",),
    "tableau desktop": ("tableau",),
    "tableau server": ("tableau server",),
    "rest apis": ("rest api",),
    "jira rest api": ("jira api", "jira rest api"),
    "etl / elt": ("etl", "elt"),
    "data quality / qa": ("data quality", "qa"),
    "data extraction and transformation": ("data extraction", "etl"),
    "dashboard development": ("dashboard",),
}


def skill_fragments(phrase: str) -> list[str]:
    """Expand a skill phrase into the spellings a posting might really use.

    Three purely structural sources, applied to the phrase as written:

    1. a parenthetical becomes its own alternative ("ADF" in "Azure Data
       Factory (ADF)"), and so does each slash-separated part ("ETL" in
       "ETL / ELT");
    2. curated vendor short-forms from :data:`SKILL_ALIASES`, keyed by the
       normalized phrase;
    3. the phrase itself.

    Without this, a literal match on "Azure Data Factory (ADF)" silently fails
    against the far more common "ADF", which would make 7 of the 19 declared
    skills unmatchable. Matching stays on token boundaries, so "SQL" still does
    not match "postgresql" and "bi developer" does not match "Power BI".
    """
    text = phrase.strip()
    if not text:
        return []

    # Structural expansion, independent of the alias table.
    base = _PAREN.sub("", text).strip()
    candidates: list[str] = [base]
    if base:
        candidates += [part.strip() for part in _SLASH.split(base) if part.strip()]
    candidates += [part.strip() for part in _PAREN.findall(text) for part in _SLASH.split(part) if part.strip()]
    candidates += list(SKILL_ALIASES.get(text.lower(), ()))

    # De-duplicate case-insensitively, keeping the caller's spelling.
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate.lower() not in seen:
            seen.add(candidate.lower())
            unique.append(candidate)
    return unique


@lru_cache(maxsize=1024)
def matcher_for_phrase(phrase: str) -> re.Pattern[str]:
    """Compile an operator-supplied phrase (e.g. a skill name) into a matcher.

    Uses :func:`skill_fragments` so "Azure Data Factory (ADF)" also matches a
    posting that says only "ADF". Cached because profile skills are matched
    against every job in a run, so the same few hundred phrases would otherwise
    be recompiled thousands of times.
    """
    fragments = skill_fragments(phrase) or [phrase]
    return token_matcher("|".join(re.escape(part) for part in fragments))


def matches_any(text: str, matchers: Iterable[re.Pattern[str]]) -> bool:
    """True when any matcher searches ``text`` successfully."""
    return any(pattern.search(text) for pattern in matchers)


def matched_phrases(
    text: str, phrases: Iterable[str]
) -> list[str]:
    """Return the phrases (verbatim) whose token-boundary form matches ``text``.

    Phrases are compared case-insensitively but returned exactly as supplied,
    so reasons read the way the operator wrote the profile.
    """
    return [
        phrase for phrase in phrases
        if phrase and matcher_for_phrase(phrase).search(text)
    ]


__all__ = [
    "token_matcher",
    "plain_fragment",
    "skill_fragments",
    "matcher_for_phrase",
    "matches_any",
    "matched_phrases",
    "SKILL_ALIASES",
]
