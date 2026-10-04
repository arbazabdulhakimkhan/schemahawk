"""Normalized data model shared by every source adapter and pipeline stage.

Rules from the blueprint that this module enforces:

- Never invent values that a source did not provide; use ``None`` instead.
- Timestamps are timezone-aware UTC ``datetime`` objects internally and are
  serialized to ISO 8601 strings only when they reach storage.
- ``posted_at_raw`` always keeps the original timestamp string exactly as the
  source returned it, so nothing is lost when parsing is imperfect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


class FreshnessStatus:
    """Minute-level freshness verdicts (see ``freshness.evaluate_freshness``)."""

    FRESH = "FRESH"
    RECENT = "RECENT"
    OLD = "OLD"
    UNKNOWN = "UNKNOWN"


class QualityStatus:
    """Quality verdicts produced by ``quality.classify``."""

    OK = "OK"
    SUSPICIOUS = "SUSPICIOUS"
    REJECTED = "REJECTED"


class EligibilityStatus:
    """Eligibility verdicts produced by ``quality.classify``."""

    ELIGIBLE = "ELIGIBLE"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


class PipelineStatus:
    """Values stored in the ``jobs.status`` column."""

    DISCOVERED = "DISCOVERED"
    FRESH = "FRESH"
    RECENT = "RECENT"
    OLD = "OLD"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    MATCHED = "MATCHED"


# Contract-type vocabulary (kept small and explicit; None means "unknown").
CONTRACT = "CONTRACT"
PART_TIME = "PART_TIME"
FULL_TIME = "FULL_TIME"

# --- compensation vocabulary -----------------------------------------------
# Several job boards publish pay, and several report it differently. These
# constants keep one vocabulary in one place; ``None`` always means "the source
# did not say", never "zero" and never "unpaid".
RATE_PERIODS: tuple[str, ...] = ("hour", "day", "month", "year", "project")


@dataclass(frozen=True)
class Compensation:
    """Pay a source published, normalized across differing board formats.

    Every field is optional because boards differ: some publish a min/max pair,
    some a single number, some only a formatted string. Nothing is estimated - a
    missing ``period`` stays ``None`` rather than being defaulted, because
    "170000" means hourly in one feed and annually in another.
    """

    min_value: float | None = None
    max_value: float | None = None
    currency: str | None = None
    period: str | None = None
    raw: str | None = None

    @property
    def is_known(self) -> bool:
        """True when any pay value was actually published."""
        return any(v is not None for v in (self.min_value, self.max_value, self.raw))


@dataclass
class Job:
    """A single normalized job opportunity.

    Every field may legitimately be ``None`` when the source did not provide
    the value. ``url_canonical``, ``content_hash`` and ``company_title`` are
    pipeline-computed deduplication keys (see ``normalize.attach_keys``), and
    are never sourced from a job board.
    """

    # --- identity / source ---
    source: str
    id: str | None = None
    source_job_id: str | None = None
    url: str | None = None

    # --- core content ---
    title: str | None = None
    company: str | None = None
    description: str | None = None
    location: str | None = None
    remote: bool | None = None
    contract_type: str | None = None
    compensation: Compensation | None = None

    # --- timing ---
    posted_at: datetime | None = None
    posted_at_raw: str | None = None
    timestamp_confidence: int | None = None
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # --- application route / contact (mostly V4; captured when offered) ---
    application_url: str | None = None
    recruiter_name: str | None = None
    recruiter_url: str | None = None
    contact_email: str | None = None

    # --- extra context when the source provides it ---
    country: str | None = None
    timezone: str | None = None
    work_authorization: str | None = None

    # --- pipeline-computed dedup keys (not from the source) ---
    url_canonical: str | None = None
    content_hash: str | None = None
    company_title: str | None = None

    # --- pipeline verdicts ---
    freshness_minutes: int | None = None
    freshness_status: str | None = None
    quality_status: str | None = None
    eligibility_status: str | None = None
    relevance_score: int | None = None
    rejection_reason: str | None = None
    status: str = PipelineStatus.DISCOVERED


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime; naive datetimes are assumed to be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
