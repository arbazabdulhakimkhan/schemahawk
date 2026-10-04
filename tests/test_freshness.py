"""Freshness table tests - the core V1 guarantee.

15m -> FRESH, 59m -> FRESH, 61m -> RECENT, 180m -> RECENT, 181m -> OLD,
unknown/untrusted -> UNKNOWN (never fresh).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemahawk.freshness import evaluate_freshness
from schemahawk.models import FreshnessStatus, Job
from schemahawk.normalize import CONFIDENCE_DATE_ONLY, CONFIDENCE_EXACT, CONFIDENCE_RELATIVE

from conftest import make_job

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("minutes,expected", [
    (0, FreshnessStatus.FRESH),
    (15, FreshnessStatus.FRESH),
    (59, FreshnessStatus.FRESH),
    (60, FreshnessStatus.FRESH),
    (61, FreshnessStatus.RECENT),
    (120, FreshnessStatus.RECENT),
    (180, FreshnessStatus.RECENT),
    (181, FreshnessStatus.OLD),
    (1440, FreshnessStatus.OLD),
])
def test_freshness_buckets(minutes, expected):
    job = make_job(minutes_old=minutes)
    evaluate_freshness(job, now=NOW)
    assert job.freshness_status == expected
    assert job.freshness_minutes == minutes


def test_unknown_when_no_timestamp():
    job = make_job(minutes_old=None)
    evaluate_freshness(job, now=NOW)
    assert job.freshness_status == FreshnessStatus.UNKNOWN
    assert job.freshness_minutes is None


def test_unknown_when_confidence_too_low():
    """A date-only timestamp must never be reported as fresh."""
    job = Job(source="fake", title="Data Engineer", url="https://e.com/1",
              posted_at=None, timestamp_confidence=CONFIDENCE_DATE_ONLY)
    evaluate_freshness(job, now=NOW)
    assert job.freshness_status == FreshnessStatus.UNKNOWN
    assert job.freshness_minutes is None


def test_relative_confidence_minutes_are_accepted():
    job = make_job(minutes_old=10, confidence=CONFIDENCE_RELATIVE)
    evaluate_freshness(job, now=NOW)
    assert job.freshness_minutes == 10
    assert job.freshness_status == FreshnessStatus.FRESH


def test_future_timestamp_is_clamped_to_zero():
    from datetime import timedelta
    job = Job(source="fake", title="Data Engineer", url="https://e.com/2",
              posted_at=NOW + timedelta(minutes=5), timestamp_confidence=CONFIDENCE_EXACT)
    evaluate_freshness(job, now=NOW)
    assert job.freshness_minutes == 0
    assert job.freshness_status == FreshnessStatus.FRESH


def test_thresholds_are_configurable():
    job = make_job(minutes_old=45)
    evaluate_freshness(job, now=NOW, fresh_max_minutes=30, recent_max_minutes=60)
    assert job.freshness_status == FreshnessStatus.RECENT
