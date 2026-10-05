"""Remotive public API adapter.

Endpoint: https://remotive.com/api/remote-jobs

Remotive's terms of use ask for light polling, so this adapter polls at most
every 6 hours (``poll_every_hours = 6``) instead of hourly, and issues at most
two search queries per poll.

Timestamps are ISO 8601 without a timezone; the API documents UTC, so they map
to confidence 95 (exact but tz-naive).
"""
from __future__ import annotations

from ..models import Job, normalize_contract_type
from ..normalize import clean_text, parse_compensation, parse_timestamp, strip_html
from .base import BaseSource

API_URL = "https://remotive.com/api/remote-jobs"
QUERIES = ("data engineer", "etl engineer")
_MAX_LIMIT = 100


class RemotiveSource(BaseSource):
    name = "remotive"
    poll_every_hours = 6

    def fetch(self) -> list[Job]:
        limit = min(self.settings.per_source_limit or 50, _MAX_LIMIT)
        jobs: list[Job] = []
        seen: set[str] = set()
        for query in QUERIES:
            payload = self.get_json(
                API_URL,
                params={"search": query, "limit": str(limit)},
            )
            if not isinstance(payload, dict):
                continue
            for entry in payload.get("jobs") or []:
                job = _to_job(self, entry)
                if job is None or (job.source_job_id and job.source_job_id in seen):
                    continue
                if job.source_job_id:
                    seen.add(job.source_job_id)
                jobs.append(job)
        return jobs


def _to_job(source: RemotiveSource, entry: object) -> Job | None:
    if not isinstance(entry, dict) or not entry.get("title"):
        return None
    raw = entry.get("publication_date")
    posted_at, confidence = parse_timestamp(raw) if raw else (None, None)

    # Remotive publishes its own ``job_type``; normalize it so FREELANCE is no
    # longer collapsed into CONTRACT (which loses a real distinction).
    contract_type = normalize_contract_type(entry.get("job_type"))

    location = clean_text(entry.get("candidate_required_location"), 160)
    remote = True if location and "worldwide" in location.lower() else (
        True if location and "anywhere" in location.lower() else None)

    description = strip_html(entry.get("description"),
                            max_len=source.settings.max_description_chars)
    tags = [t for t in (entry.get("tags") or []) if isinstance(t, str)]
    if tags:
        label = ", ".join(tags[:15])
        description = (f"{description} [source tags: {label}]"
                       if description else f"[source tags: {label}]")

    return Job(
        source=source.name,
        source_job_id=str(entry.get("id")) if entry.get("id") is not None else None,
        url=clean_text(entry.get("url"), 500),
        title=clean_text(entry.get("title"), 200),
        company=clean_text(entry.get("company_name"), 120),
        description=description,
        location=location,
        remote=remote,
        contract_type=contract_type,
        compensation=parse_compensation(entry.get("salary")),
        posted_at=posted_at,
        posted_at_raw=str(raw) if raw else None,
        timestamp_confidence=confidence,
    )
