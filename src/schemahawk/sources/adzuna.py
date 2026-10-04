"""Adzuna official API adapter (OPTIONAL - requires free app_id + app_key).

Create credentials at https://developer.adzuna.com/ and set
``ADZUNA_APP_ID`` / ``ADZUNA_APP_KEY``.

Polling budget: each run issues one query per configured country. With the
default 8 countries and a 4-hour poll interval this stays far below Adzuna's
free-tier daily limit. Countries that fail are isolated and reported as
warnings; the remaining countries still return results.
"""
from __future__ import annotations

from ..models import FULL_TIME, PART_TIME, Job
from ..normalize import clean_text, parse_timestamp
from .base import BaseSource, SourceError

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
WHAT = "data engineer"
RESULTS_PER_PAGE = 20
MAX_DAYS_OLD = 1


class AdzunaSource(BaseSource):
    name = "adzuna"
    poll_every_hours = 4

    @classmethod
    def is_configured(cls, settings) -> bool:
        return bool(settings.adzuna_app_id and settings.adzuna_app_key)

    @classmethod
    def skip_reason(cls, settings) -> str | None:
        return "skipped: set ADZUNA_APP_ID and ADZUNA_APP_KEY to enable"

    def fetch(self) -> list[Job]:
        if not self.is_configured(self.settings):
            raise SourceError(self.skip_reason(self.settings) or "not configured")

        jobs: list[Job] = []
        seen: set[str] = set()
        for country in self.settings.adzuna_countries:
            try:
                jobs.extend(self._country(country, seen))
            except SourceError as exc:
                self.warnings.append(f"{country}: {exc}")
        if not jobs and self.warnings:
            raise SourceError("; ".join(self.warnings))
        return jobs

    def _country(self, country: str, seen: set[str]) -> list[Job]:
        payload = self.get_json(
            API_URL.format(country=country),
            params={
                "app_id": self.settings.adzuna_app_id,
                "app_key": self.settings.adzuna_app_key,
                "results_per_page": str(RESULTS_PER_PAGE),
                "what": WHAT,
                "max_days_old": str(MAX_DAYS_OLD),
                "content-type": "application/json",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError("unexpected payload")
        out: list[Job] = []
        for entry in payload.get("results") or []:
            if not isinstance(entry, dict) or entry.get("id") is None:
                continue
            key = f"{country}-{entry['id']}"
            if key in seen:
                continue
            seen.add(key)
            out.append(self._to_job(entry, country))
        return out

    def _to_job(self, entry: dict, country: str) -> Job:
        raw = entry.get("created")
        posted_at, confidence = parse_timestamp(raw) if raw else (None, None)

        company = entry.get("company") or {}
        location = entry.get("location") or {}
        area = location.get("area") or []
        country_name = clean_text(area[0], 80) if area else country.upper()

        contract_time = str(entry.get("contract_time") or "").strip().lower()
        if contract_time == "part_time":
            contract_type = PART_TIME
        elif contract_time == "full_time":
            contract_type = FULL_TIME
        else:
            contract_type = None

        return Job(
            source=self.name,
            source_job_id=f"{country}-{entry.get('id')}",
            url=clean_text(entry.get("redirect_url"), 500),
            title=clean_text(entry.get("title"), 200),
            company=clean_text(company.get("display_name"), 120),
            description=clean_text(entry.get("description"),
                                   self.settings.max_description_chars),
            location=clean_text(location.get("display_name"), 200),
            remote=None,
            contract_type=contract_type,
            posted_at=posted_at,
            posted_at_raw=str(raw) if raw else None,
            timestamp_confidence=confidence,
            country=country_name,
        )
