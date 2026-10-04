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

_ELIGIBILITY: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bUS\b.{0,12}\bcitizens?(?:hip)?\b.{0,20}\b(?:only|required|must)\b", re.I),
        "US citizenship required",
    ),
    (re.compile(r"\bcitizenship\s+(?:is\s+)?required\b", re.I), "citizenship required"),
    (re.compile(r"\bsecurity\s+clearance\b", re.I), "security clearance required"),
    (re.compile(r"\bno\s+(?:visa\s+)?sponsorship\b", re.I), "no visa sponsorship"),
    (
        re.compile(
            r"\b(?:US|USA|U\.S\.|United States|UK|United Kingdom|Canadian|EU|European|Australian)"
            r"\s+(?:citizens|nationals|residents|persons)\s+only\b",
            re.I,
        ),
        "citizenship/residency restriction",
    ),
    (
        re.compile(
            r"\bmust\s+be\s+(?:authorized|eligible)\s+to\s+work\s+in\s+(?:the\s+)?"
            r"(?:US|USA|United States|UK|United Kingdom|Canada|Australia|EU)\b",
            re.I,
        ),
        "explicit work-authorization requirement",
    ),
)

_LOCATION_ONLY = re.compile(
    r"\b(?:US|USA|U\.S\.|United States|UK|United Kingdom|Canada|Australia|EU|Europe|India|Germany)"
    r"\s*[-\u2013]?\s+only\b",
    re.I,
)
_WORLDWIDE = re.compile(r"\b(?:worldwide|anywhere|any\s+location|global)\b", re.I)


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

    restriction = _find_restriction(text, job.location)
    if restriction is not None:
        job.eligibility_status = EligibilityStatus.RESTRICTED
        job.work_authorization = restriction
        if reject_restricted:
            job.rejection_reason = f"eligibility: {restriction}"
            job.status = PipelineStatus.REJECTED
        return job

    job.eligibility_status = (
        EligibilityStatus.ELIGIBLE
        if job.remote is True or (job.location and _WORLDWIDE.search(job.location))
        else EligibilityStatus.UNKNOWN
    )
    return job


def _find_restriction(text: str, location: str | None) -> str | None:
    for pattern, label in _ELIGIBILITY:
        if pattern.search(text):
            return label
    if location and _LOCATION_ONLY.search(location):
        return "location restricted to a single country"
    if text and _LOCATION_ONLY.search(text):
        return "location restricted to a single country"
    return None
