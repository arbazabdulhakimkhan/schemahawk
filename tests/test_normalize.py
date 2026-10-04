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

# --- compensation parsing ---------------------------------------------------
#
# Shapes taken from the live APIs, all of which used to be discarded:
#   Jobicy    salaryMin / salaryMax / salaryCurrency / salaryPeriod ("hourly")
#   RemoteOK  salary_min / salary_max          (no period published at all)
#   Remotive  salary: "$90k - $105k"           (a formatted string)

D = "$"
EUR = "\u20ac"
GBP = "\u00a3"


def test_nothing_published_yields_none_not_zero():
    assert n.parse_compensation(None) is None
    assert n.parse_compensation("") is None
    assert n.parse_compensation("   ") is None


def test_structured_jobicy_payload():
    comp = n.parse_compensation(min_value=30, max_value=30,
                                currency="USD", period="hourly")
    assert (comp.min_value, comp.max_value) == (30.0, 30.0)
    assert comp.currency == "USD"
    assert comp.period == "hour"


def test_structured_remoteok_keeps_period_unknown():
    """170000 is plainly annual, but nothing says so: never guessed."""
    comp = n.parse_compensation(min_value=170000, max_value=350000)
    assert (comp.min_value, comp.max_value) == (170000.0, 350000.0)
    assert comp.period is None
    assert comp.currency is None


def test_k_suffix_means_thousand():
    assert n.parse_compensation(f"{D}120k").min_value == 120000.0


def test_remotive_style_range_with_repeated_symbol():
    comp = n.parse_compensation(f"{D}90k - {D}105k")
    assert comp.min_value == 90000.0
    assert comp.max_value == 105000.0
    assert comp.currency == "USD"
    assert comp.raw == f"{D}90k - {D}105k"


def test_comma_thousands_separator():
    comp = n.parse_compensation(f"{D}90,000 - {D}105,000")
    assert comp.min_value == 90000.0
    assert comp.max_value == 105000.0


@pytest.mark.parametrize("text,period", [
    (f"{D}60 per hour", "hour"),
    (f"{D}60/hour", "hour"),
    (f"{EUR}45/hr", "hour"),
    (f"{GBP}400 per day", "day"),
    (f"{D}5k per month", "month"),
    (f"{D}100k per year", "year"),
])
def test_period_from_slash_and_word_forms(text, period):
    assert n.parse_compensation(text).period == period


def test_currency_from_symbol_and_code():
    assert n.parse_compensation(f"{EUR}45/hr").currency == "EUR"
    assert n.parse_compensation(f"{GBP}400/day").currency == "GBP"
    assert n.parse_compensation(f"{D}60 per hour").currency == "USD"
    assert n.parse_compensation(min_value=10, currency="eur").currency == "EUR"


def test_unknown_currency_code_is_dropped_not_guessed():
    comp = n.parse_compensation(min_value=10, currency="XYZ")
    assert comp.currency is None
    assert comp.min_value == 10.0


def test_unparseable_text_keeps_raw_but_invents_no_numbers():
    comp = n.parse_compensation("Competitive salary")
    assert comp.is_known
    assert comp.raw == "Competitive salary"
    assert comp.min_value is None and comp.max_value is None


def test_bool_is_not_treated_as_a_number():
    """bool subclasses int; True must not become a salary of 1."""
    assert n.parse_compensation(min_value=True) is None


def test_junk_numbers_are_rejected():
    assert n.parse_compensation(min_value="abc") is None
    assert n.parse_compensation(min_value="") is None


def test_empty_compensation_is_not_known():
    from schemahawk.models import Compensation
    assert Compensation().is_known is False
    assert Compensation(min_value=1).is_known is True


def test_compensation_attaches_to_the_job_model():
    from schemahawk.models import Job
    job = Job(source="x",
              compensation=n.parse_compensation(min_value=30, period="hour"))
    assert job.compensation.min_value == 30.0
    assert job.compensation.period == "hour"

