"""RemoteOK public API adapter.

Endpoint: https://remoteok.com/api  (returns a JSON array whose first element
is a legal notice that must be skipped).

Attribution is required by RemoteOK's terms - see the README "Data sources"
section. Timestamps come as an epoch integer and/or an ISO string; both are
exact and timezone-aware, so they map to confidence 100.
"""
from __future__ import annotations

from ..models import CONTRACT, FULL_TIME, PART_TIME, Job
from ..normalize import clean_text, parse_compensation, parse_timestamp, strip_html
from .base import BaseSource, SourceError

API_URL = "https://remoteok.com/api"
_FREELANCE = ("freelance", "contract", "contractor")


class RemoteOKSource(BaseSource):
    name = "remoteok"
    poll_every_hours = 1

    def fetch(self) -> list[Job]:
        payload = self.get_json(API_URL)
        if not isinstance(payload, list):
            raise SourceError("unexpected RemoteOK payload (expected a JSON array)")

        limit = self.settings.per_source_limit or None
        jobs: list[Job] = []
        for entry in payload:
            # Skip the legal-notice element and anything malformed.
            if not isinstance(entry, dict) or "position" not in entry or "id" not in entry:
                continue

            posted_at, confidence = self._timestamp(entry)
            tags = [t for t in (entry.get("tags") or []) if isinstance(t, str)]
            title = clean_text(entry.get("position"), 200)
            description = strip_html(entry.get("description"),
                                     max_len=self.settings.max_description_chars)
            if tags:
                description = _append_tags(description, tags)

            jobs.append(Job(
                source=self.name,
                source_job_id=str(entry.get("id")),
                url=clean_text(entry.get("url"), 500) or _slug_url(entry),
                title=title,
                company=clean_text(entry.get("company"), 120),
                description=description,
                location=_location_from_tags(tags),
                remote=True,  # RemoteOK only lists remote roles
                contract_type=_contract_from_tags(tags, title or ""),
                compensation=parse_compensation(
                    min_value=entry.get("salary_min"),
                    max_value=entry.get("salary_max"),
                ),
                posted_at=posted_at,
                posted_at_raw=_raw_timestamp(entry),
                timestamp_confidence=confidence,
                application_url=clean_text(entry.get("apply_url"), 500),
            ))
            if limit is not None and len(jobs) >= limit:
                break
        return jobs

    @staticmethod
    def _timestamp(entry: dict) -> tuple[object, int | None]:
        if entry.get("date"):
            return parse_timestamp(entry["date"])
        if entry.get("epoch"):
            return parse_timestamp(int(entry["epoch"]))
        return None, None


def _raw_timestamp(entry: dict) -> str | None:
    if entry.get("date"):
        return str(entry["date"])
    if entry.get("epoch"):
        return str(entry["epoch"])
    return None


def _slug_url(entry: dict) -> str | None:
    slug = entry.get("slug")
    return f"https://remoteok.com/remote-jobs/{slug}" if slug else None


def _location_from_tags(tags: list[str]) -> str | None:
    lowered = {tag.lower() for tag in tags}
    if lowered & {"anywhere", "worldwide", "remote"}:
        return "Worldwide"
    return None


def _contract_from_tags(tags: list[str], title: str) -> str | None:
    joined = " ".join([*tags, title]).lower()
    if any(word in joined for word in _FREELANCE):
        return CONTRACT
    if "part time" in joined or "part-time" in joined:
        return PART_TIME
    if "full time" in joined or "full-time" in joined:
        return FULL_TIME
    return None


def _append_tags(description: str | None, tags: list[str]) -> str:
    label = ", ".join(tags[:15])
    return f"{description} [source tags: {label}]" if description else f"[source tags: {label}]"
