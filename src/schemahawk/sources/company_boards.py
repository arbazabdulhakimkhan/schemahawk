"""Company careers boards via the public Greenhouse and Lever APIs (OPTIONAL).

These endpoints are the same ones the companies' own job pages use, so nothing
is scraped through a login wall.

Activate with environment variables::

    GREENHOUSE_BOARDS=cloudflare,vanta
    LEVER_BOARDS=leverdemo

Individual boards are isolated: a broken token produces a warning and the
other boards still return results.
"""
from __future__ import annotations

from ..models import CONTRACT, FULL_TIME, PART_TIME, Job
from ..normalize import clean_text, parse_timestamp, strip_html
from .base import BaseSource, SourceError

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{token}"


class CompanyBoardsSource(BaseSource):
    name = "company_boards"
    poll_every_hours = 1

    @classmethod
    def is_configured(cls, settings) -> bool:
        return bool(settings.greenhouse_boards or settings.lever_boards)

    @classmethod
    def skip_reason(cls, settings) -> str | None:
        return "skipped: set GREENHOUSE_BOARDS and/or LEVER_BOARDS to enable"

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for token in self.settings.greenhouse_boards:
            jobs.extend(self._board(token.strip(), self._greenhouse))
        for token in self.settings.lever_boards:
            jobs.extend(self._board(token.strip(), self._lever))
        if not jobs and self.warnings:
            raise SourceError("; ".join(self.warnings))
        return jobs

    def _board(self, token: str, loader) -> list[Job]:
        if not token:
            return []
        try:
            return loader(token)
        except SourceError as exc:
            self.warnings.append(f"{token}: {exc}")
        except Exception as exc:  # noqa: BLE001 - isolate each board
            self.warnings.append(f"{token}: {type(exc).__name__}: {exc}")
        return []

    # --- Greenhouse -------------------------------------------------------
    def _greenhouse(self, token: str) -> list[Job]:
        payload = self.get_json(GREENHOUSE_URL.format(token=token),
                                params={"content": "true"})
        if not isinstance(payload, dict):
            raise SourceError("unexpected payload")
        out: list[Job] = []
        for entry in payload.get("jobs") or []:
            if not isinstance(entry, dict) or not entry.get("title"):
                continue
            raw = entry.get("first_published") or entry.get("updated_at")
            posted_at, confidence = parse_timestamp(raw) if raw else (None, None)
            location = clean_text((entry.get("location") or {}).get("name"), 200)
            out.append(Job(
                source=self.name,
                source_job_id=f"gh-{token}-{entry.get('id')}",
                url=clean_text(entry.get("absolute_url"), 500),
                title=clean_text(entry.get("title"), 200),
                company=clean_text(token.replace("-", " "), 120),
                description=strip_html(entry.get("content"),
                                       max_len=self.settings.max_description_chars),
                location=location,
                remote=True if location and "remote" in location.lower() else None,
                contract_type=None,
                posted_at=posted_at,
                posted_at_raw=str(raw) if raw else None,
                timestamp_confidence=confidence,
                application_url=clean_text(entry.get("absolute_url"), 500),
            ))
        return out

    # --- Lever ------------------------------------------------------------
    def _lever(self, token: str) -> list[Job]:
        payload = self.get_json(LEVER_URL.format(token=token), params={"mode": "json"})
        if not isinstance(payload, list):
            raise SourceError("unexpected payload")
        out: list[Job] = []
        for entry in payload:
            if not isinstance(entry, dict) or not entry.get("text"):
                continue
            categories = entry.get("categories") or {}
            commitment = str(categories.get("commitment") or "").lower()
            if "contract" in commitment or "freelance" in commitment:
                contract_type = CONTRACT
            elif "part-time" in commitment:
                contract_type = PART_TIME
            elif "full-time" in commitment:
                contract_type = FULL_TIME
            else:
                contract_type = None

            raw = entry.get("createdAt")
            posted_at, confidence = parse_timestamp(raw) if raw else (None, None)
            location = clean_text(categories.get("location"), 200)
            url = clean_text(entry.get("applyUrl"), 500) or f"https://jobs.lever.co/{token}"
            out.append(Job(
                source=self.name,
                source_job_id=f"lever-{token}-{entry.get('id')}",
                url=url,
                title=clean_text(entry.get("text"), 200),
                company=clean_text(token.replace("-", " "), 120),
                description=strip_html(
                    entry.get("description") or entry.get("descriptionPlain"),
                    max_len=self.settings.max_description_chars),
                location=location,
                remote=True if location and "remote" in location.lower() else None,
                contract_type=contract_type,
                posted_at=posted_at,
                posted_at_raw=str(raw) if raw else None,
                timestamp_confidence=confidence,
                application_url=url,
            ))
        return out
