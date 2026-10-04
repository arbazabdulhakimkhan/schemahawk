"""SQLite persistence for discovered jobs and pipeline run records (V1).

Design:

- stdlib ``sqlite3`` only; WAL mode + busy timeout for durability.
- ``jobs`` keeps every discovered (non-duplicate) opportunity, including the
  ones rejected downstream, so verdicts remain auditable.
- Cross-run dedup works through the indexed keys ``url_canonical``,
  ``content_hash``, ``company_title`` and ``(source, source_job_id)``.
- ``pipeline_runs`` stores one row per run, including the rendered report.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .models import Job

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_job_id TEXT,
    url TEXT,
    url_canonical TEXT,
    content_hash TEXT,
    company_title TEXT,
    title TEXT,
    company TEXT,
    description TEXT,
    location TEXT,
    remote INTEGER,
    contract_type TEXT,
    posted_at TEXT,
    posted_at_raw TEXT,
    timestamp_confidence INTEGER,
    freshness_minutes INTEGER,
    freshness_status TEXT,
    discovered_at TEXT NOT NULL,
    last_seen_at TEXT,
    times_seen INTEGER NOT NULL DEFAULT 1,
    application_url TEXT,
    recruiter_name TEXT,
    recruiter_url TEXT,
    contact_email TEXT,
    country TEXT,
    timezone TEXT,
    work_authorization TEXT,
    compensation_min REAL,
    compensation_max REAL,
    compensation_currency TEXT,
    compensation_period TEXT,
    compensation_raw TEXT,
    quality_status TEXT,
    eligibility_status TEXT,
    relevance_score INTEGER,
    rejection_reason TEXT,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_url_canonical ON jobs(url_canonical);
CREATE INDEX IF NOT EXISTS idx_jobs_content_hash ON jobs(content_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_company_title ON jobs(company_title);
CREATE INDEX IF NOT EXISTS idx_jobs_source_job_id ON jobs(source, source_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_freshness ON jobs(freshness_status);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    sources_checked INTEGER NOT NULL DEFAULT 0,
    sources_failed TEXT,
    discovered INTEGER NOT NULL DEFAULT 0,
    fresh_count INTEGER NOT NULL DEFAULT 0,
    recent_count INTEGER NOT NULL DEFAULT 0,
    old_count INTEGER NOT NULL DEFAULT 0,
    unknown_count INTEGER NOT NULL DEFAULT 0,
    duplicates_removed INTEGER NOT NULL DEFAULT 0,
    rejected_non_de INTEGER NOT NULL DEFAULT 0,
    rejected_ineligible INTEGER NOT NULL DEFAULT 0,
    rejected_quality INTEGER NOT NULL DEFAULT 0,
    suspicious INTEGER NOT NULL DEFAULT 0,
    strong_candidates INTEGER NOT NULL DEFAULT 0,
    report_text TEXT
);
"""

_INSERT_JOB = """
INSERT INTO jobs (
    id, source, source_job_id, url, url_canonical, content_hash, company_title,
    title, company, description, location, remote, contract_type, posted_at,
    posted_at_raw, timestamp_confidence, freshness_minutes, freshness_status,
    discovered_at, last_seen_at, times_seen, application_url, recruiter_name,
    recruiter_url, contact_email, country, timezone, work_authorization,
    compensation_min, compensation_max, compensation_currency,
    compensation_period, compensation_raw,
    quality_status, eligibility_status, relevance_score, rejection_reason,
    status, created_at, updated_at
) VALUES (
    :id, :source, :source_job_id, :url, :url_canonical, :content_hash, :company_title,
    :title, :company, :description, :location, :remote, :contract_type, :posted_at,
    :posted_at_raw, :timestamp_confidence, :freshness_minutes, :freshness_status,
    :discovered_at, :last_seen_at, 1, :application_url, :recruiter_name,
    :recruiter_url, :contact_email, :country, :timezone, :work_authorization,
    :compensation_min, :compensation_max, :compensation_currency,
    :compensation_period, :compensation_raw,
    :quality_status, :eligibility_status, :relevance_score, :rejection_reason,
    :status, :created_at, :updated_at
)
"""


# Columns added after the first release. Kept in one place so the migration and
# the CREATE TABLE above stay in step; every entry must be nullable with a
# default so existing rows keep working.
_ADDED_COLUMNS: dict[str, str] = {
    "compensation_min": "REAL",
    "compensation_max": "REAL",
    "compensation_currency": "TEXT",
    "compensation_period": "TEXT",
    "compensation_raw": "TEXT",
}


def iso_utc(value: datetime | None) -> str | None:
    """Serialize a datetime as an ISO 8601 string in UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _apply(job: Job, row: sqlite3.Row) -> None:
    """Copy stored discovery metadata back onto the Job (id, discovered_at)."""
    job.id = row["id"]
    if row["discovered_at"]:
        job.discovered_at = datetime.fromisoformat(row["discovered_at"])


class Store:
    """Thin wrapper around a SQLite database (or an in-memory one)."""

    def __init__(self, path: str | None, *, read_only: bool = False):
        self.path = path
        self.read_only = read_only
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        if read_only and path:
            # Read-only handle for dry runs: never creates or mutates the file.
            self.conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        else:
            self.conn = sqlite3.connect(path or ":memory:")
        self.conn.row_factory = sqlite3.Row
        if path and not read_only:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            self.conn.executescript(_SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        ``CREATE TABLE IF NOT EXISTS`` silently leaves an older table alone, so
        without this an existing ``data/schemahawk.db`` would keep working until
        an INSERT referenced a column that was never added. Every statement is
        additive and idempotent, and runs in a transaction so a failure cannot
        leave a half-migrated file.
        """
        existing = {row["name"] for row in
                    self.conn.execute("PRAGMA table_info(jobs)")}
        if not existing:
            return
        for column, decl in _ADDED_COLUMNS.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {decl}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- cross-run dedup support -----------------------------------------
    def existing_keys(self) -> dict[str, set]:
        """Return the dedup keys already persisted by previous runs."""
        urls = {row[0] for row in self.conn.execute(
            "SELECT url_canonical FROM jobs WHERE url_canonical IS NOT NULL")}
        source_ids = {(row[0], row[1]) for row in self.conn.execute(
            "SELECT source, source_job_id FROM jobs WHERE source_job_id IS NOT NULL")}
        hashes = {row[0] for row in self.conn.execute(
            "SELECT content_hash FROM jobs WHERE content_hash IS NOT NULL")}
        company_titles = {row[0] for row in self.conn.execute(
            "SELECT company_title FROM jobs WHERE company_title IS NOT NULL")}
        return {
            "urls": urls,
            "source_ids": source_ids,
            "hashes": hashes,
            "company_titles": company_titles,
        }

    # --- writes -----------------------------------------------------------
    def upsert_jobs(self, jobs: Iterable[Job]) -> tuple[int, int]:
        """Insert new jobs / refresh known jobs. Returns ``(inserted, updated)``."""
        if self.read_only:
            raise RuntimeError("cannot write to a read-only store")
        now = iso_utc(datetime.now(timezone.utc))
        inserted = updated = 0
        cursor = self.conn.cursor()
        for job in jobs:
            existing = None
            if job.url_canonical:
                existing = cursor.execute(
                    "SELECT id, times_seen, discovered_at FROM jobs WHERE url_canonical = ?",
                    (job.url_canonical,),
                ).fetchone()
            if existing is None and not job.url and job.content_hash:
                existing = cursor.execute(
                    "SELECT id, times_seen, discovered_at FROM jobs"
                    " WHERE content_hash = ? AND url IS NULL",
                    (job.content_hash,),
                ).fetchone()

            if existing is None:
                row = _job_row(job, now)
                job.id = row["id"]
                cursor.execute(_INSERT_JOB, row)
                inserted += 1
            else:
                _apply(job, existing)
                cursor.execute(
                    """
                    UPDATE jobs SET
                        last_seen_at = :last_seen_at,
                        times_seen = :times_seen,
                        freshness_minutes = :freshness_minutes,
                        freshness_status = :freshness_status,
                        quality_status = :quality_status,
                        eligibility_status = :eligibility_status,
                        relevance_score = :relevance_score,
                        rejection_reason = :rejection_reason,
                        status = :status,
                        updated_at = :updated_at
                    WHERE id = :id
                    """,
                    {
                        "id": existing["id"],
                        "last_seen_at": now,
                        "times_seen": int(existing["times_seen"] or 1) + 1,
                        "freshness_minutes": job.freshness_minutes,
                        "freshness_status": job.freshness_status,
                        "quality_status": job.quality_status,
                        "eligibility_status": job.eligibility_status,
                        "relevance_score": job.relevance_score,
                        "rejection_reason": job.rejection_reason,
                        "status": job.status,
                        "updated_at": now,
                    },
                )
                updated += 1
        self.conn.commit()
        return inserted, updated

    def record_run(self, report) -> int:
        """Persist a pipeline run summary; returns the run id."""
        if self.read_only:
            raise RuntimeError("cannot write to a read-only store")
        failures = "; ".join(f"{name}: {reason}" for name, reason in report.source_failures)
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO pipeline_runs (
                started_at, completed_at, status, sources_checked, sources_failed,
                discovered, fresh_count, recent_count, old_count, unknown_count,
                duplicates_removed, rejected_non_de, rejected_ineligible,
                rejected_quality, suspicious, strong_candidates, report_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                iso_utc(report.started_at),
                iso_utc(report.completed_at),
                report.status,
                report.sources_checked,
                failures or None,
                report.discovered,
                report.fresh,
                report.recent,
                report.old,
                report.unknown_ts,
                report.duplicates_removed,
                report.rejected_non_de,
                report.rejected_ineligible,
                report.rejected_quality,
                report.suspicious,
                report.strong_candidates,
                report.render(),
            ),
        )
        self.conn.commit()
        run_id = int(cursor.lastrowid or 0)
        report.run_id = run_id
        return run_id

    # --- reads ------------------------------------------------------------
    def count_jobs(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        return {row[0]: int(row[1]) for row in rows}

    def counts_by_source(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT source, COUNT(*) FROM jobs GROUP BY source")
        return {row[0]: int(row[1]) for row in rows}

    def latest_runs(self, limit: int = 5) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?", (limit,)))


def _comp(job: Job, field: str):
    """One compensation field, or ``None`` when the source published no pay.

    Reading through the dataclass keeps the five columns in lockstep with
    :class:`~schemahawk.models.Compensation`; a missing object yields ``None``
    rather than a zero that would read as "unpaid".
    """
    comp = getattr(job, "compensation", None)
    return getattr(comp, field, None) if comp is not None else None


def _job_row(job: Job, now: str) -> dict:
    """Flatten a Job into the column dict used by ``_INSERT_JOB``."""
    return {
        "id": job.id or uuid.uuid4().hex,
        "source": job.source,
        "source_job_id": job.source_job_id,
        "url": job.url,
        "url_canonical": job.url_canonical,
        "content_hash": job.content_hash,
        "company_title": job.company_title,
        "title": job.title,
        "company": job.company,
        "description": job.description,
        "location": job.location,
        "remote": None if job.remote is None else int(job.remote),
        "contract_type": job.contract_type,
        "posted_at": iso_utc(job.posted_at),
        "posted_at_raw": job.posted_at_raw,
        "timestamp_confidence": job.timestamp_confidence,
        "freshness_minutes": job.freshness_minutes,
        "freshness_status": job.freshness_status,
        "discovered_at": iso_utc(job.discovered_at),
        "last_seen_at": now,
        "application_url": job.application_url,
        "recruiter_name": job.recruiter_name,
        "recruiter_url": job.recruiter_url,
        "contact_email": job.contact_email,
        "country": job.country,
        "timezone": job.timezone,
        "work_authorization": job.work_authorization,
        "compensation_min": _comp(job, "min_value"),
        "compensation_max": _comp(job, "max_value"),
        "compensation_currency": _comp(job, "currency"),
        "compensation_period": _comp(job, "period"),
        "compensation_raw": _comp(job, "raw"),
        "quality_status": job.quality_status,
        "eligibility_status": job.eligibility_status,
        "relevance_score": job.relevance_score,
        "rejection_reason": job.rejection_reason,
        "status": job.status,
        "created_at": now,
        "updated_at": now,
    }



