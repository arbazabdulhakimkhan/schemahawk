"""Freshness validation — the core constraint of V1.

Invariants (from the blueprint):

- Freshness is always ``now_utc - posted_at``; it never depends on when a
  cron job happened to fire.
- An UNKNOWN timestamp is never treated as fresh, and timestamps are never
  fabricated, guessed or rounded up into the fresh window.
- Relative timestamps ("2h ago") are trusted only with reduced confidence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import FreshnessStatus, Job, as_utc
from .normalize import CONFIDENCE_RELATIVE

# Only exact (100/95) and relative (90) timestamps can support minute-level
# freshness. Coarse/date-only values stay UNKNOWN.
_MIN_TRUSTED_CONFIDENCE = CONFIDENCE_RELATIVE


def evaluate_freshness(
    job: Job,
    *,
    now: datetime | None = None,
    fresh_max_minutes: int = 60,
    recent_max_minutes: int = 180,
) -> Job:
    """Set ``freshness_minutes`` / ``freshness_status`` on the job in place."""
    now = as_utc(now or datetime.now(timezone.utc))

    trusted = job.posted_at is not None and (
        job.timestamp_confidence is None or job.timestamp_confidence >= _MIN_TRUSTED_CONFIDENCE
    )
    if not trusted:
        job.freshness_minutes = None
        job.freshness_status = FreshnessStatus.UNKNOWN
        return job

    minutes = (now - as_utc(job.posted_at)).total_seconds() / 60.0
    if minutes < 0:  # tolerate small clock skew between source and runner
        minutes = 0.0
    job.freshness_minutes = int(round(minutes))

    if job.freshness_minutes <= fresh_max_minutes:
        job.freshness_status = FreshnessStatus.FRESH
    elif job.freshness_minutes <= recent_max_minutes:
        job.freshness_status = FreshnessStatus.RECENT
    else:
        job.freshness_status = FreshnessStatus.OLD
    return job
