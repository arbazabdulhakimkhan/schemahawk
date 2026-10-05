"""Company careers boards via the public Greenhouse and Lever APIs (OPTIONAL).

These endpoints are the same ones the companies' own job pages use, so nothing
is scraped through a login wall.

Activate with environment variables::

    GREENHOUSE_BOARDS=cloudflare,vanta
    LEVER_BOARDS=leverdemo

Individual boards are isolated: a broken token produces a warning and the
other boards still return results.

Phase 3B refactor: :meth:`CompanyBoardsSource.board_listings` is the single
place that knows these API shapes. The source adapter uses it to *create*
jobs, and :mod:`schemahawk.enrichment` uses it to *enrich* jobs discovered
through other boards. One HTTP path, two consumers - no second client.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import CONTRACT, FULL_TIME, PART_TIME, Job
from ..normalize import clean_text, parse_timestamp, strip_html
from .base import BaseSource, SourceError

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{token}"

GREENHOUSE = "greenhouse"
LEVER = "lever"




@dataclass(frozen=True)
class BoardListing:
    """One public posting on an explicitly configured board.

    Only the fields the adapter needs to build a Job and the fields matching
    needs. ``description`` is retained because the source adapter must keep
    publishing full jobs; the enricher never reads it, and nothing about a
    listing body is persisted or reported.
    """

    vendor: str
    token: str
    company: str
    title: str
    application_url: str
    location: str | None = None
    source_job_id: str | None = None
    description: str | None = None
    posted_at_raw: str | None = None
    commitment: str | None = None


def company_from_token(token: str) -> str:
    """Display name for a board token.

    The token is the trust anchor, so the company is derived from the operator's
    configuration and never from posting text.
    """
    return token.replace("-", " ").replace("_", " ").strip()


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
        try:
            listings, warnings = self.board_listings()
        except Exception as exc:  # noqa: BLE001 - never fatal for the run
            raise SourceError(f"{type(exc).__name__}: {exc}") from exc
        self.warnings.extend(warnings)
        jobs = [self._to_job(listing) for listing in listings]
        if not jobs and self.warnings:
            raise SourceError("; ".join(self.warnings))
        return jobs

    # --- Phase 3B: shared listing fetch ------------------------------------
    def board_listings(self) -> tuple[list[BoardListing], list[str]]:
        """Public listings from every explicitly configured board.

        Returns ``(listings, warnings)``. Never raises: a broken board is
        reported and the remaining boards still contribute.

        This is the only path Phase 3B uses to obtain external listings, and it
        only ever requests operator-configured board tokens.
        """
        listings: list[BoardListing] = []
        warnings: list[str] = []
        for kind, tokens in ((GREENHOUSE, self.settings.greenhouse_boards),
                             (LEVER, self.settings.lever_boards)):
            for raw in tokens:
                token = raw.strip()
                if not token:
                    continue
                try:
                    if kind == GREENHOUSE:
                        listings.extend(self._greenhouse_listings(token))
                    else:
                        listings.extend(self._lever_listings(token))
                except SourceError as exc:
                    warnings.append(f"{kind}:{token}: {exc}")
                except Exception as exc:  # noqa: BLE001 - isolate each board
                    warnings.append(
                        f"{kind}:{token}: {type(exc).__name__}: {exc}")
        return listings, warnings

    def _greenhouse_listings(self, token: str) -> list[BoardListing]:
        payload = self.get_json(GREENHOUSE_URL.format(token=token),
                                params={"content": "true"})
        if not isinstance(payload, dict):
            raise SourceError("unexpected payload")
        company = company_from_token(token)
        out: list[BoardListing] = []
        for entry in payload.get("jobs") or []:
            if not isinstance(entry, dict) or not entry.get("title"):
                continue
            url = clean_text(entry.get("absolute_url"), 500)
            out.append(BoardListing(
                vendor=GREENHOUSE,
                token=token,
                company=company,
                title=clean_text(entry.get("title"), 200) or "",
                application_url=url,
                location=clean_text((entry.get("location") or {}).get("name"),
                                    200),
                source_job_id=f"gh-{token}-{entry.get('id')}",
                description=strip_html(
                    entry.get("content"),
                    max_len=self.settings.max_description_chars),
                posted_at_raw=entry.get("first_published") or entry.get("updated_at"),
            ))
        return out

    def _lever_listings(self, token: str) -> list[BoardListing]:
        payload = self.get_json(LEVER_URL.format(token=token),
                                params={"mode": "json"})
        if not isinstance(payload, list):
            raise SourceError("unexpected payload")
        company = company_from_token(token)
        out: list[BoardListing] = []
        for entry in payload:
            if not isinstance(entry, dict) or not entry.get("text"):
                continue
            url = (clean_text(entry.get("applyUrl"), 500)
                   or f"https://jobs.lever.co/{token}")
            categories = entry.get("categories") or {}
            out.append(BoardListing(
                vendor=LEVER,
                token=token,
                company=company,
                title=clean_text(entry.get("text"), 200) or "",
                application_url=url,
                location=clean_text(categories.get("location"), 200),
                source_job_id=f"lever-{token}-{entry.get('id')}",
                description=strip_html(
                    entry.get("description") or entry.get("descriptionPlain"),
                    max_len=self.settings.max_description_chars),
                posted_at_raw=entry.get("createdAt"),
                commitment=categories.get("commitment"),
            ))
        return out

    # --- adapter ----------------------------------------------------------
    def _to_job(self, listing: BoardListing) -> Job:
        raw = listing.posted_at_raw
        posted_at, confidence = parse_timestamp(raw) if raw else (None, None)
        contract_type = None
        if listing.commitment:
            commitment = str(listing.commitment).lower()
            if "contract" in commitment or "freelance" in commitment:
                contract_type = CONTRACT
            elif "part-time" in commitment:
                contract_type = PART_TIME
            elif "full-time" in commitment:
                contract_type = FULL_TIME
        location = listing.location
        return Job(
            source=self.name,
            source_job_id=listing.source_job_id,
            url=listing.application_url,
            title=listing.title,
            company=listing.company,
            description=listing.description,
            location=location,
            remote=True if location and "remote" in location.lower() else None,
            contract_type=contract_type,
            posted_at=posted_at,
            posted_at_raw=str(raw) if raw else None,
            timestamp_confidence=confidence,
            application_url=listing.application_url,
        )
