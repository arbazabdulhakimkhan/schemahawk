"""Quality and eligibility filtering (rule-based, deliberately conservative).

Policy:

- Hard scam indicators               -> quality REJECTED (with a reason)
- Borderline signals                 -> quality SUSPICIOUS (kept, never "strong")
- Explicit eligibility restrictions  -> eligibility RESTRICTED (rejected by
  default via ``Settings.reject_restricted``)
- Missing information                -> UNKNOWN, never auto-rejected

Descriptions are untrusted data: they are only ever pattern-matched here and
stored as text elsewhere.
"""
from __future__ import annotations

import re

from .eligibility import EligibilityProfile, LocationScope, WorkAuthorization, assess
from .models import EligibilityStatus, Job, PipelineStatus, QualityStatus

_HARD_SCAM: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:registration|application|processing|training)\s+fee\b", re.I),
        "fee requested to apply/register",
    ),
    (re.compile(r"\bpay\s+to\s+apply\b", re.I), "pay-to-apply language"),
    (
        re.compile(
            r"\b(?:send|transfer|deposit|pay)\b.{0,60}"
            r"\b(?:crypto(?:currency)?|bitcoin|btc|usdt|ethereum|gift\s?card|wire\s+transfer)\b",
            re.I,
        ),
        "payment/deposit request",
    ),
    (re.compile(r"\bgift\s?card\b", re.I), "gift card mentioned"),
    (re.compile(r"\bwire\s+transfer\b", re.I), "wire transfer mentioned"),
    (
        re.compile(
            r"\bearn\b.{0,40}(?:\$\s?\d{3,}|\b\d{3,}\s?(?:usd|eur)\b).{0,30}"
            r"\b(?:per\s+day|daily|a\s+day|each\s+day)\b",
            re.I,
        ),
        "unrealistic earnings claim",
    ),
)

_SOFT_SUSPICIOUS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bno\s+experience\b.{0,80}\b(?:earn|income|\$|salary)\b", re.I),
        "no-experience earnings pitch",
    ),
    (re.compile(r"\b(?:daily|instant)\s+(?:payout|payment|withdrawal)\b", re.I), "daily payout promise"),
    (re.compile(r"\bsign\s?up\s+bonus\b", re.I), "signup bonus"),
)

# The eligibility patterns moved to ``eligibility.assess()`` in Phase 2B. The
# old hand-rolled set here only knew nine countries and no EU work-authorization
# phrasing, so postings such as "EU candidates only" or "must be authorized to
# work in Germany" silently evaluated to UNKNOWN. The two mappings below turn the
# normalized enums back into the human-readable reasons that appear in reports.
_AUTHORIZATION_REASON: dict[WorkAuthorization, str | None] = {
    WorkAuthorization.UNKNOWN: None,
    WorkAuthorization.NOT_STATED: None,
    WorkAuthorization.OPEN: None,
    WorkAuthorization.WORK_AUTHORIZATION_REQUIRED: "explicit work-authorization requirement",
    WorkAuthorization.CITIZENSHIP_REQUIRED: "citizenship required",
}

_SCOPE_REASON: dict[LocationScope, str | None] = {
    LocationScope.UNKNOWN: None,
    # Only an *explicit* worldwide statement means worldwide. "Remote" does not.
    LocationScope.WORLDWIDE: None,
    LocationScope.COUNTRY_RESTRICTED: "location restricted to a single country",
    LocationScope.REGION_RESTRICTED: "location restricted to a region",
    LocationScope.TIMEZONE_RESTRICTED: "timezone-restricted working hours",
}


def _searchable(job: Job) -> str:
    return " \n ".join(
        part for part in (job.title, job.location, job.description) if part
    )


def classify(job: Job, *, reject_restricted: bool = True) -> Job:
    """Assign quality/eligibility verdicts (and rejection status) to ``job``."""
    text = _searchable(job)

    for pattern, label in _HARD_SCAM:
        if pattern.search(text):
            job.quality_status = QualityStatus.REJECTED
            job.eligibility_status = job.eligibility_status or EligibilityStatus.UNKNOWN
            job.rejection_reason = f"quality: {label}"
            job.status = PipelineStatus.REJECTED
            return job

    job.quality_status = QualityStatus.OK
    for pattern, label in _SOFT_SUSPICIOUS:
        if pattern.search(text):
            job.quality_status = QualityStatus.SUSPICIOUS
            job.work_authorization = None
            break

    profile = assess(job)
    job.location_scope = profile.scope.name
    job.work_authorization_level = profile.work_authorization.name

    reason = _restriction_reason(profile)
    if reason is not None:
        job.eligibility_status = EligibilityStatus.RESTRICTED
        job.work_authorization = reason
        if reject_restricted:
            job.rejection_reason = f"eligibility: {reason}"
            job.status = PipelineStatus.REJECTED
        return job

    # Only an explicitly worldwide posting counts as eligible. Previously this
    # branch fired for any remote job, which claimed eligibility from evidence
    # that does not exist -- 84% of remote postings never say "worldwide".
    job.eligibility_status = (
        EligibilityStatus.ELIGIBLE
        if profile.scope is LocationScope.WORLDWIDE
        else EligibilityStatus.UNKNOWN
    )
    return job


def _restriction_reason(profile: EligibilityProfile) -> str | None:
    """Human-readable restriction reason, or None when nothing is restricted.

    The work-authorization axis wins when present because it is the stronger
    constraint: "US citizens only" is more informative than "US only".
    """
    reason = _AUTHORIZATION_REASON.get(profile.work_authorization)
    if reason is None:
        reason = _SCOPE_REASON.get(profile.scope)
    if reason is None and profile.work_authorization is WorkAuthorization.OPEN:
        return None
    return reason
