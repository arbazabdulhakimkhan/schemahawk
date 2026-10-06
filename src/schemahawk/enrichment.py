"""Deterministic application-route acquisition (Phase 3B).

Phase 3A can classify a route, but the boards we poll mostly publish a *board
page* that links onward. This module finds the company's genuine application
endpoint when one is publicly available, and writes it onto ``Job.application_url``
so the existing Phase 3A classifier can judge it.

Two boundaries this module never crosses:

1. **Configuration is the trust anchor.** Only ``GREENHOUSE_BOARDS`` and
   ``LEVER_BOARDS`` are ever requested. A URL discovered inside a job posting
   is data, not permission to fetch - nothing here reads ``job.description``
   for a target, and no host is inferred from a company name.
2. **Exact matching only.** A listing is attached when its normalized
   ``company|title`` key equals the job's. There is no fuzzy score: a partial
   title match could attach one company's job to another company's posting,
   which is the failure mode that matters here. When several listings share a
   key the match is ambiguous and nothing is attached.

No application is submitted, no form filled, no mail sent. The pipeline ends at
report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Job
from .normalize import company_title_key
from .routes import PRECEDENCE, ApplicationRoute, classify_route
from .sources.company_boards import CompanyBoardsSource

SKIPPED_NO_BOARDS = "skipped (no configured boards)"


@dataclass
class AcquisitionReport:
    """What acquisition attempted. Report-only; nothing is persisted here."""

    status: str = SKIPPED_NO_BOARDS
    boards_examined: int = 0
    listings_seen: int = 0
    enriched: int = 0
    ambiguous_matches: int = 0
    no_match: int = 0
    weaker_route_rejected: int = 0
    #: Application URLs this stage attached from a configured board. The set
    #: *is* the provenance record: a caller may treat a URL in here as
    #: board-supplied when classifying. Kept in memory only - no column.
    board_urls: set[str] = field(default_factory=set)
    evidence: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def note(self, subject: str, detail: str) -> None:
        self.evidence.append((subject, detail))


def _rank(route_type: ApplicationRoute) -> int:
    return PRECEDENCE.index(route_type)


def _would_improve(job: Job, candidate_url: str) -> bool:
    """True when adopting ``candidate_url`` yields a stronger route.

    Delegates the whole judgement to Phase 3A: it reclassifies the job as it
    stands, then as it would stand with the candidate, and compares using
    Phase 3A's own precedence. An existing ``OFFICIAL_APPLICATION`` therefore
    can never be downgraded to ``EXTERNAL_APPLICATION``, and this module never
    needs its own notion of route quality.
    """
    current = classify_route(job)
    previous = job.application_url
    try:
        job.application_url = candidate_url
        # The candidate came from a board the operator configured and the
        # ATS vendor verified, so it carries provenance no URL shape can.
        proposed = classify_route(job, board_provenance=True)
    finally:
        job.application_url = previous
    return _rank(proposed.route_type) < _rank(current.route_type)


def enrich_jobs(jobs: list[Job], settings) -> AcquisitionReport:
    """Attach publicly discoverable application URLs to ``jobs``.

    Returns an :class:`AcquisitionReport`. Makes no network request at all when
    no boards are configured, and never raises.
    """
    report = AcquisitionReport()

    if not (settings.greenhouse_boards or settings.lever_boards):
        return report

    source = CompanyBoardsSource(settings)
    try:
        listings, warnings = source.board_listings()
    except Exception as exc:  # noqa: BLE001 - acquisition is never fatal
        report.status = f"failed: {type(exc).__name__}: {exc}"
        return report

    report.status = "ok"
    report.warnings.extend(warnings)
    report.boards_examined = (len(settings.greenhouse_boards)
                             + len(settings.lever_boards))
    usable = [item for item in listings if item.application_url]
    report.listings_seen = len(usable)

    # One key -> its listings. Collisions are kept, not resolved: an ambiguous
    # key is a reason to decline, not a reason to pick.
    index: dict[str, list] = {}
    for listing in usable:
        key = company_title_key(listing.company, listing.title)
        if key:
            index.setdefault(key, []).append(listing)

    for job in jobs:
        if job.source == CompanyBoardsSource.name:
            # Already sourced from the board itself; nothing to acquire.
            continue
        key = company_title_key(job.company, job.title)
        if not key:
            report.no_match += 1
            continue
        candidates = index.get(key)
        if not candidates:
            report.no_match += 1
            continue
        if len(candidates) > 1:
            report.ambiguous_matches += 1
            report.note(
                job.title or "(untitled)",
                f"ambiguous: key {key!r} matched {len(candidates)} listings "
                f"across boards "
                f"{sorted({c.token for c in candidates})}; not enriched")
            continue

        listing = candidates[0]
        if not _would_improve(job, listing.application_url):
            report.weaker_route_rejected += 1
            report.note(
                job.title or "(untitled)",
                f"kept existing route: candidate from {listing.vendor}:"
                f"{listing.token} would not improve on the current route")
            continue

        job.application_url = listing.application_url
        report.enriched += 1
        report.board_urls.add(listing.application_url)
        report.note(
            job.title or "(untitled)",
            f"acquired {listing.vendor} application URL for board "
            f"{listing.token!r} via exact key {key!r}")

    return report


__all__ = ["AcquisitionReport", "enrich_jobs", "SKIPPED_NO_BOARDS"]
