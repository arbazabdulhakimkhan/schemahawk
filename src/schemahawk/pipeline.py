"""V1 discovery pipeline orchestrator.

Stage order (each stage is a small, unit-testable function):

    sources -> normalize -> freshness -> dedupe (in-run + cross-run)
    -> quality/eligibility -> relevance -> status assignment -> store -> report

Invariants:

- One broken source never kills a run; failures are recorded in the report.
- Freshness is always computed from an explicit UTC ``now``.
- An UNKNOWN timestamp is never treated as fresh.
- Duplicates are never stored twice; rejections are stored with a reason.
- V1 performs no outbound side effects (no applications, no messages).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Settings
from .dedupe import dedupe
from .freshness import evaluate_freshness
from .models import (
    EligibilityStatus,
    FreshnessStatus,
    Job,
    PipelineStatus,
    QualityStatus,
)
from .matching import match_job
from .normalize import attach_keys, clean_text
from .profile import CandidateProfile, profile_from_settings
from .quality import classify
from .relevance import score_relevance
from .report import RunReport
from .sources import build_sources
from .store import Store

log = logging.getLogger(__name__)

MAX_STRONG_IN_REPORT = 20


def run_discovery(
    settings: Settings,
    *,
    only_source: str | None = None,
    force: bool = False,
    since_minutes: int | None = None,
    min_score: int | None = None,
    store: Store | None = None,
    known_keys: dict[str, set] | None = None,
    now: datetime | None = None,
    profile: CandidateProfile | None = None,
) -> RunReport:
    """Run the full V1 discovery pipeline and return a :class:`RunReport`."""
    now = now or datetime.now(timezone.utc)
    threshold = settings.min_relevance_score if min_score is None else min_score
    owned_store = store is None
    store = store or Store(settings.db_path)

    report = RunReport(started_at=now)
    if owned_store:
        report.db_path = settings.db_path

    try:
        report = _discover(settings, report, store, now=now, only_source=only_source,
                           force=force, since_minutes=since_minutes,
                           threshold=threshold, known_keys=known_keys,
                           profile=profile)
    finally:
        if owned_store:
            store.close()
    return report


def _discover(settings, report, store, *, now, only_source, force, since_minutes,
              threshold, known_keys, profile=None) -> RunReport:
    """Inner pipeline body: keeps ``run_discovery`` focused on store lifetime.

    ``profile`` is optional: when it is not supplied the candidate-match layer
    is skipped entirely and the run behaves exactly as V1 did.
    """
    if profile is None:
        profile = profile_from_settings(settings)
    sources, notes = build_sources(settings, only=only_source)
    for name, note in notes:
        report.note(name, note)

    report.sources_checked = len(sources)
    collected: list[Job] = []
    for source in sources:
        if not force and not source.due(now):
            report.note(source.name,
                        f"skipped this hour (polls every {source.poll_every_hours}h)")
            continue
        jobs, error = source.run()
        for warning in source.warnings:
            report.note(source.name, warning)
        if error is not None:
            report.fail(source.name, error)
            log.warning("source %s failed: %s", source.name, error)
            continue
        log.info("source %s returned %s job(s)", source.name, len(jobs))
        collected.extend(jobs)

    report.discovered = len(collected)

    for job in collected:
        _prepare(job, settings, now)

    known = known_keys if known_keys is not None else store.existing_keys()
    unique, duplicates = dedupe(
        collected,
        known_urls=known.get("urls", ()),
        known_source_ids=known.get("source_ids", ()),
        known_hashes=known.get("hashes", ()),
        known_company_titles=known.get("company_titles", ()),
    )
    report.duplicates_removed = len(duplicates)
    report.unique_jobs = len(unique)

    strong: list[Job] = []
    for job in unique:
        classify(job, reject_restricted=settings.reject_restricted)
        score_relevance(job, settings.relevance_extra_keywords)
        _assign_status(job, threshold=threshold, since_minutes=since_minutes,
                       recent_max=settings.recent_max_minutes)
        report.freshness_bucket(job)
        if job.status == PipelineStatus.REJECTED:
            _count_rejection(report, job)
        elif job.quality_status == QualityStatus.SUSPICIOUS:
            report.suspicious += 1
        if job.status == PipelineStatus.MATCHED:
            strong.append(job)

    strong.sort(key=_strong_sort_key)
    report.strong = strong[:MAX_STRONG_IN_REPORT]
    report.strong_candidates = len(strong)

    # Candidate matching is additive and always runs on the strong candidates
    # only. It never alters relevance_score, quality or status, and an empty
    # profile simply yields UNKNOWN components instead of numbers.
    report.matches = [(job, match_job(job, profile)) for job in strong]

    report.stored_inserted, report.stored_updated = store.upsert_jobs(unique)
    report.completed_at = datetime.now(timezone.utc)
    report.status = "OK" if not report.source_failures else "OK_WITH_SOURCE_FAILURES"
    store.record_run(report)
    return report


def _prepare(job: Job, settings: Settings, now: datetime) -> None:
    """Defensive clean + dedup keys + freshness verdict for one job."""
    job.title = clean_text(job.title, 200)
    job.company = clean_text(job.company, 120)
    job.location = clean_text(job.location, 200)
    job.description = clean_text(job.description, settings.max_description_chars)
    attach_keys(job)
    evaluate_freshness(
        job,
        now=now,
        fresh_max_minutes=settings.fresh_max_minutes,
        recent_max_minutes=settings.recent_max_minutes,
    )


def _assign_status(job: Job, *, threshold: int, since_minutes: int | None,
                   recent_max: int) -> None:
    """Set ``job.status`` from relevance + freshness + quality verdicts."""
    if job.status == PipelineStatus.REJECTED:
        return

    score = job.relevance_score or 0
    if score < threshold:
        job.status = PipelineStatus.REJECTED
        job.rejection_reason = f"relevance {score} below threshold {threshold}"
        return

    window = recent_max if since_minutes is None else since_minutes
    qualifies = (
        job.freshness_minutes is not None
        and job.freshness_minutes <= window
        and job.quality_status != QualityStatus.REJECTED
        and job.eligibility_status != EligibilityStatus.RESTRICTED
    )
    job.status = (
        PipelineStatus.MATCHED
        if qualifies
        else (job.freshness_status or PipelineStatus.UNKNOWN)
    )


def _count_rejection(report: RunReport, job: Job) -> None:
    reason = job.rejection_reason or ""
    if reason.startswith("quality:"):
        report.rejected_quality += 1
    elif reason.startswith("eligibility:"):
        report.rejected_ineligible += 1
    else:
        report.rejected_non_de += 1


def _strong_sort_key(job: Job) -> tuple[int, int]:
    """Freshest first, then highest relevance score."""
    minutes = job.freshness_minutes if job.freshness_minutes is not None else 10**9
    return (minutes, -(job.relevance_score or 0))


