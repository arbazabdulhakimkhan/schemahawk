"""Tests for text/URL/timestamp normalization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from schemahawk import normalize as n

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def test_strip_html_removes_tags_and_entities():
    raw = "<p>Build&nbsp;<b>ETL</b> pipelines &amp; more</p>"
    assert n.strip_html(raw) == "Build ETL pipelines & more"


def test_strip_html_handles_double_escaped_markup():
    raw = "&lt;p&gt;Senior Data &lt;em&gt;Engineer&lt;/em&gt;&lt;/p&gt;"
    assert n.strip_html(raw) == "Senior Data Engineer"


def test_clean_text_collapses_whitespace_and_controls():
    assert n.clean_text("  a\n\tb\x00  c ") == "a b c"
    assert n.clean_text("") is None
    assert n.clean_text(None) is None


def test_clean_text_truncates():
    out = n.clean_text("x" * 100, max_len=10)
    assert out is not None and out.startswith("xxxxxxxxxx") and out.endswith("...")


def test_canonical_url_normalizes_host_scheme_and_path():
    assert (n.canonical_url("HTTP://WWW.Example.com/Jobs/123/?utm=1#top")
            == "http://example.com/Jobs/123")


def test_canonical_url_rejects_garbage():
    assert n.canonical_url("not a url") is None
    assert n.canonical_url(None) is None


def test_jaccard_identical_and_disjoint():
    assert n.jaccard(n.token_set("data engineer sql"), n.token_set("sql data engineer")) == 1.0
    assert n.jaccard(n.token_set("alpha"), n.token_set("beta")) == 0.0
    assert n.jaccard(frozenset(), n.token_set("beta")) == 0.0


def test_content_hash_is_stable_and_order_sensitive():
    a = n.content_hash("Data Engineer", "Acme", "Build pipelines with SQL")
    b = n.content_hash("data engineer", "acme", "build pipelines with sql")
    c = n.content_hash("Data Analyst", "Acme", "Build pipelines with SQL")
    assert a == b
    assert a != c
    assert n.content_hash(None, None, None) is None


def test_company_title_key_requires_both_parts():
    assert n.company_title_key("Acme Inc.", "Data Engineer") == "acme inc|data engineer"
    assert n.company_title_key(None, "Data Engineer") is None


@pytest.mark.parametrize("raw,expected", [
    ("2026-01-15T11:00:00Z", n.CONFIDENCE_EXACT),
    ("2026-01-15T11:00:00+00:00", n.CONFIDENCE_EXACT),
    ("2026-01-15T11:00:00", n.CONFIDENCE_EXACT_NAIVE),
    ("Thu, 15 Jan 2026 11:00:00 +0000", n.CONFIDENCE_EXACT),
    (1768474800, n.CONFIDENCE_EXACT),
    ("1768474800", n.CONFIDENCE_EXACT),
    ("2 hours ago", n.CONFIDENCE_RELATIVE),
    ("45m", n.CONFIDENCE_RELATIVE),
    ("just now", n.CONFIDENCE_RELATIVE),
    ("today", n.CONFIDENCE_COARSE),
    ("2026-01-15", n.CONFIDENCE_DATE_ONLY),
    ("whenever", n.CONFIDENCE_NONE),
])
def test_parse_timestamp_confidence(raw, expected):
    _, confidence = n.parse_timestamp(raw, now=NOW)
    assert confidence == expected


def test_parse_timestamp_relative_is_relative_to_now():
    posted_at, confidence = n.parse_timestamp("2h ago", now=NOW)
    assert confidence == n.CONFIDENCE_RELATIVE
    assert posted_at == NOW - timedelta(hours=2)


def test_parse_timestamp_coarse_returns_no_datetime():
    posted_at, confidence = n.parse_timestamp("today", now=NOW)
    assert posted_at is None and confidence == n.CONFIDENCE_COARSE
    posted_at, confidence = n.parse_timestamp("2026-01-15", now=NOW)
    assert posted_at is None and confidence == n.CONFIDENCE_DATE_ONLY


def test_parse_timestamp_naive_is_assumed_utc():
    posted_at, _ = n.parse_timestamp("2026-01-15T11:00:00", now=NOW)
    assert posted_at == datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)


def test_parse_timestamp_epoch_milliseconds():
    posted_at, confidence = n.parse_timestamp(1768474800000, now=NOW)
    assert confidence == n.CONFIDENCE_EXACT
    assert posted_at is not None and posted_at.year == 2026


def test_parse_timestamp_empty_and_none():
    assert n.parse_timestamp(None) == (None, None)
    assert n.parse_timestamp("") == (None, None)
