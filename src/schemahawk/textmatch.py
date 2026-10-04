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


def token_matcher(fragment: str) -> re.Pattern[str]:
    """Compile a regex fragment so it only matches whole tokens."""
    return re.compile(rf"(?<!\w)(?:{fragment})(?!\w)", re.I)


def plain_fragment(text: str) -> str:
    """Turn arbitrary operator text into a token-boundary regex fragment."""
    return r"\s+".join(re.escape(word) for word in text.split())


@lru_cache(maxsize=1024)
def matcher_for_phrase(phrase: str) -> re.Pattern[str]:
    """Compile an operator-supplied phrase (e.g. a skill name) into a matcher.

    Cached: profile skills are matched against every job in a run, so the same
    few hundred phrases would otherwise be recompiled thousands of times.
    """
    return token_matcher(plain_fragment(phrase))


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
    "matcher_for_phrase",
    "matches_any",
    "matched_phrases",
]
