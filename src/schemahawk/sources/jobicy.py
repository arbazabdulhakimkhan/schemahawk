"""Jobicy remote-jobs API adapter.

Endpoint: https://jobicy.com/api/v2/remote-jobs

Jobicy timestamps ("pubDate") are ISO 8601 with an explicit timezone, so they
map to confidence 100. The API caps ``count`` at 50.
"""
from __future__ import annotations

from ..models import Job, normalize_contract_type
from ..normalize import clean_text, parse_compensation, parse_timestamp, strip_html
from .base import BaseSource

API_URL = "https://jobicy.com/api/v2/remote-jobs"
_MAX_COUNT = 50


class JobicySource(BaseSource):
    name = "jobicy"
    poll_every_hours = 1

    def fetch(self) -> list[Job]:
        count = min(self.settings.per_source_limit or _MAX_COUNT, _MAX_COUNT)
        payload = self.get_json(API_URL, params={"count": str(count)})
        if not isinstance(payload, dict):
            return []
        jobs: list[Job] = []
        for entry in payload.get("jobs") or []:
            job = _to_job(self, entry)
            if job is not None:
                jobs.append(job)
        return jobs


def _to_job(source: JobicySource, entry: object) -> Job | None:
    if not isinstance(entry, dict) or not entry.get("jobTitle"):
        return None
    raw = entry.get("pubDate")
    posted_at, confidence = parse_timestamp(raw) if raw else (None, None)

    types = [t.lower() for t in (entry.get("jobType") or []) if isinstance(t, str)]
    # Contract taxonomy (Phase 2B): freelance used to be collapsed into
    # CONTRACT. normalize_contract_type maps the board's own labels onto the
    # shared vocabulary so "freelance" -> FREELANCE, not CONTRACT.
    contract_type = None
    for label in types:
        contract_type = normalize_contract_type(label)
        if contract_type is not None:
            break

    geo = clean_text(entry.get("jobGeo"), 160)
    remote = True if geo and any(
        word in geo.lower() for word in ("anywhere", "worldwide")) else None

    description = strip_html(entry.get("jobDescription"),
                            max_len=source.settings.max_description_chars)
    if not description:
        description = clean_text(entry.get("jobExcerpt"), 1000)
    tags = [t for t in (entry.get("jobIndustry") or []) if isinstance(t, str)]
    if tags:
        label = ", ".join(tags[:10])
        description = (f"{description} [source tags: {label}]"
                       if description else f"[source tags: {label}]")

    return Job(
        source=source.name,
        source_job_id=str(entry.get("id")) if entry.get("id") is not None else None,
        url=clean_text(entry.get("url"), 500),
        title=clean_text(entry.get("jobTitle"), 200),
        company=clean_text(entry.get("companyName"), 120),
        description=description,
        location=geo,
        remote=remote,
        contract_type=contract_type,
        compensation=parse_compensation(
            min_value=entry.get("salaryMin"),
            max_value=entry.get("salaryMax"),
            currency=entry.get("salaryCurrency"),
            period=entry.get("salaryPeriod"),
        ),
        posted_at=posted_at,
        posted_at_raw=str(raw) if raw else None,
        timestamp_confidence=confidence,
    )
