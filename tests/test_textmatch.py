"""Shared token-boundary matching tests.

These lock in the single matching implementation used by both the relevance
scorer and (from V2) the match engine. The substring regressions below are the
reason the rule exists: they were found on live job-board data.
"""
from __future__ import annotations

import pytest

from schemahawk.textmatch import (
    matched_phrases,
    matcher_for_phrase,
    matches_any,
    plain_fragment,
    skill_fragments,
    token_matcher,
)


def test_matching_requires_a_full_token_before_and_after():
    assert token_matcher("sql").search("Strong SQL Engineer")
    assert not token_matcher("sql").search("PostgreSQL Admin")
    assert not token_matcher("sql").search("nosql engineer")


def test_ssis_does_not_match_inside_assistant():
    """Regression: live data had "ssis" inside "a**ssis**tant"."""
    assert not token_matcher("ssis").search("Executive Assistant")
    assert not token_matcher("ssis").search("Junior Payroll Assistant")
    assert token_matcher("ssis").search("SSIS Developer")


def test_regex_fragments_still_work_inside_token_matcher():
    """Callers pass regex fragments (relevance's keyword tables)."""
    assert token_matcher(r"data\s+engineer\w*").search("Senior Data Engineer")
    assert token_matcher(r"data\s+warehous\w*").search("Data Warehousing Specialist")


def test_plain_fragment_escapes_regex_metacharacters():
    """Operator text must be escaped, never interpreted as a regex."""
    assert token_matcher(plain_fragment("C++")).search("C++ developer")
    assert token_matcher(plain_fragment("Node.js")).search("Node.js Engineer")
    assert not token_matcher(plain_fragment("Node.js")).search("NodeXjs")


def test_plain_fragment_allows_flexible_internal_whitespace():
    assert token_matcher(plain_fragment("Azure Data Factory")).search(
        "Azure   Data    Factory pipelines"
    )


def test_matched_phrases_returns_input_spelling_in_order():
    text = "Azure Data Factory and Python pipelines"
    assert matched_phrases(text, ["Azure Data Factory", "Python", "Kafka"]) == [
        "Azure Data Factory",
        "Python",
    ]


def test_matched_phrases_skips_empty_entries():
    assert matched_phrases("Python developer", ["", "Python", None]) == ["Python"]


def test_matches_any_is_true_when_one_pattern_hits():
    patterns = [token_matcher("kafka"), token_matcher("snowflake")]
    assert matches_any("Snowflake and Kafka", patterns)
    assert not matches_any("Tableau only", patterns)
    assert not matches_any("anything", [])


def test_matcher_for_phrase_is_cached():
    """Profile skills are matched against every job; recompiling would be costly."""
    matcher_for_phrase.cache_clear()
    matcher_for_phrase("Azure SQL")
    first = matcher_for_phrase.cache_info()
    matcher_for_phrase("Azure SQL")
    second = matcher_for_phrase.cache_info()
    assert first.misses == 1
    assert second.hits == 1


# --- skill alias expansion --------------------------------------------------
#
# Regression guard for the bug that made 7 of the 19 declared skills
# unmatchable: "Azure Data Factory (ADF)" never matched a posting saying "ADF".

@pytest.mark.parametrize("text,skill", [
    ("ADF pipelines", "Azure Data Factory (ADF)"),
    ("Azure Data Factory pipelines", "Azure Data Factory (ADF)"),
    ("BigQuery", "Google BigQuery"),
    ("Google BigQuery", "Google BigQuery"),
    ("ETL pipelines", "ETL / ELT"),
    ("ELT pipelines", "ETL / ELT"),
    ("REST API work", "REST APIs"),
    ("Jira API client", "Jira REST API"),
    ("Alteryx Designer", "Alteryx Designer"),
    ("Tableau Server", "Tableau Server"),
    ("tableau server", "Tableau Server"),
    ("dashboard work", "Dashboard development"),
    ("data extraction role", "Data extraction and transformation"),
])
def test_skill_matches_common_spellings(text, skill):
    assert matched_phrases(text, [skill]) == [skill]


@pytest.mark.parametrize("text,skill", [
    ("bi developer", "Power BI"),       # must not match the bare "bi"
    ("postgresql", "SQL"),              # must not match inside postgresql
    ("assistant tooling", "SQL"),
    ("assay results", "Azure SQL"),     # must not match inside "assay"
])
def test_alias_expansion_never_reintroduces_substring_matches(text, skill):
    assert matched_phrases(text, [skill]) == []


def test_skill_fragments_expands_parenthetical_and_slash():
    """The parenthetical is lifted out as its own alternative."""
    assert "ADF" in skill_fragments("Azure Data Factory (ADF)")
    assert "Azure Data Factory" in skill_fragments("Azure Data Factory (ADF)")
    fragments = skill_fragments("ETL / ELT")
    assert "ETL" in fragments and "ELT" in fragments


def test_skill_fragments_keeps_the_original_phrase():
    assert skill_fragments("Snowflake")[0] == "Snowflake"


def test_skill_fragments_are_deduplicated_case_insensitively():
    fragments = skill_fragments("Tableau Server")
    assert len(fragments) == len({f.lower() for f in fragments})


def test_skill_fragments_of_blank_is_empty():
    assert skill_fragments("   ") == []
