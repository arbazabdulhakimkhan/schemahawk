"""Shared pytest fixtures.

The pipeline is exercised through an injectable "fake" source so tests never
touch the network, and every run uses an in-memory ``:memory:`` database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from schemahawk.config import Settings
from schemahawk.models import Job
from schemahawk.sources.base import BaseSource

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=str(tmp_path / "test.db"))


class FakeSource(BaseSource):
    """A source that returns canned jobs (and can be told to fail)."""

    name = "fake"
    poll_every_hours = 1

    def __init__(self, jobs: list[Job], error: str | None = None):
        super().__init__(Settings(db_path=":memory:"))
        self._jobs = jobs
        self._error = error

    def fetch(self) -> list[Job]:
        if self._error:
            from schemahawk.sources.base import SourceError
            raise SourceError(self._error)
        return list(self._jobs)


def make_job(
    *,
    title: str = "Senior Data Engineer",
    company: str = "Acme Analytics",
    description: str = "Build ETL pipelines with Python, SQL and Airflow.",
    url: str = "https://example.com/jobs/123",
    source_job_id: str = "123",
    minutes_old: int | None = 30,
    confidence: int | None = 100,
    source: str = "fake",
    **kwargs,
) -> Job:
    """Build a Job with a deterministic timestamp relative to ``NOW``."""
    posted_at = (
        NOW - timedelta(minutes=minutes_old) if minutes_old is not None else None
    )
    return Job(
        source=source,
        source_job_id=source_job_id,
        url=url,
        title=title,
        company=company,
        description=description,
        posted_at=posted_at,
        posted_at_raw=posted_at.isoformat() if posted_at else None,
        timestamp_confidence=confidence if posted_at else None,
        **kwargs,
    )
