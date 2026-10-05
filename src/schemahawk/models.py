"""Normalized data model shared by every source adapter and pipeline stage.

Rules from the blueprint that this module enforces:

- Never invent values that a source did not provide; use ``None`` instead.
- Timestamps are timezone-aware UTC ``datetime`` objects internally and are
  serialized to ISO 8601 strings only when they reach storage.
- ``posted_at_raw`` always keeps the original timestamp string exactly as the
  source returned it, so nothing is lost when parsing is imperfect.
"""
from __future__ import annotations

import re
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


# --- contract-type vocabulary ------------------------------------------------
#
# Phase 2B normalization. V1 stored only CONTRACT / PART_TIME / FULL_TIME /
# None, so three values that boards actually publish - freelance, temporary and
# internship - were silently discarded, and "permanent" was never
# distinguishable from "full-time". The same ``contract_type`` column is reused;
# no new column is introduced. ``None`` still means "the source did not say".
PERMANENT = "PERMANENT"
CONTRACT = "CONTRACT"
PART_TIME = "PART_TIME"
FULL_TIME = "FULL_TIME"
FREELANCE = "FREELANCE"
TEMPORARY = "TEMPORARY"
INTERNSHIP = "INTERNSHIP"
VOLUNTEER = "VOLUNTEER"

CONTRACT_TYPES: tuple[str, ...] = (
    PERMANENT, FULL_TIME, PART_TIME, CONTRACT, FREELANCE, TEMPORARY,
    INTERNSHIP, VOLUNTEER,
)

# Board labels -> normalized value. Source-specific spellings ("part_time",
# "Part-Time", "contractor") collapse onto one vocabulary so a stored value
# means the same thing whichever adapter produced it.
CONTRACT_TYPE_ALIASES: dict[str, str] = {
    "permanent": PERMANENT, "perm": PERMANENT, "full time": FULL_TIME,
    "full_time": FULL_TIME, "fulltime": FULL_TIME, "full-time": FULL_TIME,
    "part time": PART_TIME, "part_time": PART_TIME, "parttime": PART_TIME,
    "part-time": PART_TIME,
    "contract": CONTRACT, "contractor": CONTRACT, "contract position": CONTRACT,
    "freelance": FREELANCE, "independent contractor": FREELANCE,
    "freelancer": FREELANCE,
    "temporary": TEMPORARY, "temp": TEMPORARY,
    "internship": INTERNSHIP, "intern": INTERNSHIP, "trainee": INTERNSHIP,
    "volunteer": VOLUNTEER, "unpaid": VOLUNTEER,
}


# Compound-label fallback, most specific first. A label naming several concepts
# resolves to the one that carries the most information: "freelance contract" is
# FREELANCE, "part-time contract" is PART_TIME, "senior contract" is CONTRACT.
# Token boundaries prevent "temp" matching inside "contemporary".
_CONTRACT_FALLBACK: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"(?<!\w)(?:{fragment})(?!\w)", re.I), value)
    for fragment, value in (
        (r"freelance|freelancer|independent\s+contractor", FREELANCE),
        (r"internship|intern|trainee", INTERNSHIP),
        (r"volunteer|unpaid", VOLUNTEER),
        (r"temporary|temp", TEMPORARY),
        (r"part[\s_-]?time", PART_TIME),
        (r"full[\s_-]?time", FULL_TIME),
        (r"permanent|perm", PERMANENT),
        (r"contractor|contract", CONTRACT),
    )
)


def normalize_contract_type(raw: object) -> str | None:
    """Map a board-supplied contract label onto the normalized vocabulary.

    Returns ``None`` when the label is absent or unrecognized - silence is
    never turned into "permanent". Where a source uses a fixed-hours label
    (``full_time``) it is kept as ``FULL_TIME`` rather than being upgraded to
    ``PERMANENT``, because "permanent" is an employment relationship, not an
    hours commitment.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    key = raw.strip().lower()
    if not key:
        return None

    # Exact alias wins: it is the most precise answer when it exists.
    exact = CONTRACT_TYPE_ALIASES.get(key)
    if exact is not None:
        return exact

    # Compound labels ("temporary contract", "part-time contract",
    # "contract (freelance)") miss the alias table. The previous ad-hoc
    # substring checks in the adapters used to catch these and return
    # CONTRACT; an exact-only lookup silently returned ``None`` and threw the
    # information away. The fallback below restores that coverage while
    # preferring the more specific concept, so "part-time contract" becomes
    # PART_TIME and "contract (freelance)" becomes FREELANCE rather than both
    # collapsing into CONTRACT. Order is fixed and most-specific-first, and
    # every fragment is matched on token boundaries so "temp" never matches
    # inside "contemporary".
    for pattern, value in _CONTRACT_FALLBACK:
        if pattern.search(key):
            return value
    return None

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

    # --- normalized eligibility axes (Phase 2B) ---
    # ``work_authorization`` above is the human-readable reason shown in
    # reports. The two fields below are the machine-readable, normalized
    # values and are deliberately separate from each other:
    #   location_scope          -> geography (see eligibility.LocationScope)
    #   work_authorization_level -> work-authorization law/rule
    # Citizenship is NEVER inferred from location, and neither axis is ever
    # inferred from ``remote``. Absence of evidence stays UNKNOWN.
    location_scope: str | None = None
    work_authorization_level: str | None = None

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
