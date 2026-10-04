"""Multi-signal, conservative deduplication.

Signals, strongest first:

1. canonical URL equality
2. ``source`` + ``source_job_id`` equality
3. content hash (normalized title + company + description head)
4. normalized company + title equality
5. similarity: same company AND title-Jaccard >= 0.8 AND description-Jaccard
   >= 0.5 (only evaluated when both descriptions exist)

Genuinely different openings at the same company are never merged. Cross-run
dedup uses the key sets already stored in the database (see ``store.Store``).
"""
from __future__ import annotations

from collections.abc import Iterable

from .models import Job
from .normalize import jaccard, token_set


def _company_key(job: Job) -> str:
    return (job.company_title or "").split("|", 1)[0]


def dedupe(
    jobs: Iterable[Job],
    *,
    known_urls: Iterable[str] = (),
    known_source_ids: Iterable[tuple[str, str]] = (),
    known_hashes: Iterable[str] = (),
    known_company_titles: Iterable[str] = (),
    title_threshold: float = 0.8,
    desc_threshold: float = 0.5,
) -> tuple[list[Job], list[tuple[Job, str]]]:
    """Split ``jobs`` into ``(unique, duplicates)``.

    ``duplicates`` is a list of ``(job, reason)`` pairs so the run report can
    explain exactly why something was dropped. The ``known_*`` arguments carry
    keys already persisted by previous runs.
    """
    seen_urls = set(known_urls)
    seen_source_ids = set(known_source_ids)
    seen_hashes = set(known_hashes)
    seen_company_titles = set(known_company_titles)

    unique: list[Job] = []
    accepted: list[Job] = []
    duplicates: list[tuple[Job, str]] = []

    for job in jobs:
        reason: str | None = None
        if job.url_canonical and job.url_canonical in seen_urls:
            reason = "same canonical URL"
        elif job.source_job_id and (job.source, job.source_job_id) in seen_source_ids:
            reason = "same source job id"
        elif job.content_hash and job.content_hash in seen_hashes:
            reason = "same content hash"
        elif job.company_title and job.company_title in seen_company_titles:
            reason = "same company+title"

        if reason is None and job.company_title:
            reason = _similar_duplicate(job, accepted, title_threshold, desc_threshold)

        if reason is not None:
            duplicates.append((job, reason))
            continue

        unique.append(job)
        accepted.append(job)
        if job.url_canonical:
            seen_urls.add(job.url_canonical)
        if job.source_job_id:
            seen_source_ids.add((job.source, job.source_job_id))
        if job.content_hash:
            seen_hashes.add(job.content_hash)
        if job.company_title:
            seen_company_titles.add(job.company_title)

    return unique, duplicates


def _similar_duplicate(
    job: Job,
    accepted: list[Job],
    title_threshold: float,
    desc_threshold: float,
) -> str | None:
    title_tokens = token_set(job.title)
    if not title_tokens:
        return None
    job_company = _company_key(job)
    desc_tokens = token_set(job.description)

    for other in accepted:
        if job_company and _company_key(other) and _company_key(other) != job_company:
            continue
        if jaccard(title_tokens, token_set(other.title)) < title_threshold:
            continue
        if other.description and job.description:
            if jaccard(desc_tokens, token_set(other.description)) < desc_threshold:
                continue
        return f"near-duplicate of {other.source} listing ({other.title!r})"
    return None
