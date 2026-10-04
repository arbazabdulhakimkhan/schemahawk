"""End-to-end pipeline tests with an injected fake source (no network)."""
from __future__ import annotations

from dataclasses import replace

from schemahawk import pipeline
from schemahawk.models import PipelineStatus
from schemahawk.store import Store

from conftest import NOW, FakeSource, make_job

JOB_DESCRIPTION = "Build ETL pipelines with Python, SQL, Azure Data Factory and Databricks."


def _patch_sources(sources):
    """Replace the source factory for the duration of one test."""
    original = pipeline.build_sources
    pipeline.build_sources = lambda s, only=None: (sources, [])
    return original


def _run(settings, jobs, *, errors=None, **kwargs):
    store = Store(None)
    original = _patch_sources([FakeSource(jobs, error=errors)])
    try:
        report = pipeline.run_discovery(settings, store=store, now=NOW,
                                        force=True, **kwargs)
    finally:
        pipeline.build_sources = original
    return report, store


def test_happy_path_counts_statuses_and_stores(settings):
    jobs = [
        make_job(url="https://e.com/1", source_job_id="1", minutes_old=15,
                 description=JOB_DESCRIPTION),
        make_job(url="https://e.com/2", source_job_id="2", minutes_old=90,
                 title="ETL Developer", company="Beta Corp",
                 description=JOB_DESCRIPTION),
        make_job(url="https://e.com/3", source_job_id="3", minutes_old=600,
                 title="Analytics Engineer", company="Gamma",
                 description=JOB_DESCRIPTION),
        make_job(url="https://e.com/4", source_job_id="4", minutes_old=None,
                 title="Data Platform Engineer", company="Delta",
                 description=JOB_DESCRIPTION),
    ]
    report, store = _run(replace(settings, db_path=None), jobs)

    assert report.discovered == 4
    assert report.unique_jobs == 4
    assert report.fresh == 1              # 15m
    assert report.recent == 1             # 90m
    assert report.old == 1                # 600m
    assert report.unknown_ts == 1         # no timestamp
    assert report.strong_candidates == 2  # FRESH + RECENT qualify
    assert store.count_jobs() == 4


def test_unknown_timestamp_is_never_strong(settings):
    jobs = [make_job(url="https://e.com/9", source_job_id="9", minutes_old=None,
                     description=JOB_DESCRIPTION)]
    report, store = _run(replace(settings, db_path=None), jobs)
    assert report.strong_candidates == 0
    assert report.unknown_ts == 1
    assert store.counts_by_status().get(PipelineStatus.UNKNOWN) == 1
    assert store.conn.execute(
        "SELECT freshness_minutes FROM jobs").fetchone()[0] is None


def test_scam_listing_is_rejected_but_still_stored(settings):
    jobs = [make_job(url="https://e.com/scam", source_job_id="scam",
                     description="Pay a registration fee of $50. " + JOB_DESCRIPTION)]
    report, store = _run(replace(settings, db_path=None), jobs)
    assert report.rejected_quality == 1
    assert report.strong_candidates == 0
    assert store.counts_by_status().get(PipelineStatus.REJECTED) == 1
    reason = store.conn.execute("SELECT rejection_reason FROM jobs").fetchone()[0]
    assert reason.startswith("quality:")


def test_ineligible_listing_is_rejected(settings):
    jobs = [make_job(url="https://e.com/visa", source_job_id="visa",
                     description="US citizenship required. " + JOB_DESCRIPTION)]
    report, _ = _run(replace(settings, db_path=None), jobs)
    assert report.rejected_ineligible == 1
    assert report.strong_candidates == 0


def test_non_data_role_is_rejected_by_relevance(settings):
    jobs = [make_job(url="https://e.com/design", source_job_id="design",
                     title="Graphic Designer", description="Photoshop and branding.")]
    report, store = _run(replace(settings, db_path=None), jobs)
    assert report.rejected_non_de == 1
    score = store.conn.execute("SELECT relevance_score FROM jobs").fetchone()[0]
    assert score < settings.min_relevance_score


def test_source_failure_is_isolated_and_reported(settings):
    store = Store(None)
    failing = FakeSource([], error="HTTP 500 after 3 attempt(s)")
    working = FakeSource([make_job(url="https://e.com/ok", source_job_id="ok",
                                   description=JOB_DESCRIPTION)])
    working.name = "working"
    original = _patch_sources([failing, working])
    try:
        report = pipeline.run_discovery(replace(settings, db_path=None),
                                        store=store, now=NOW, force=True)
    finally:
        pipeline.build_sources = original

    assert report.status == "OK_WITH_SOURCE_FAILURES"
    assert ("fake", "HTTP 500 after 3 attempt(s)") in report.source_failures
    assert report.discovered == 1  # the healthy source still contributed
    text = report.render()
    assert "Source failures:" in text and "- fake - HTTP 500" in text


def test_duplicates_are_removed_once(settings):
    duplicate_text = "Build ETL pipelines with Python and SQL every single day."
    jobs = [
        make_job(url="https://a.com/j", source_job_id="a", source="remoteok",
                 description=duplicate_text),
        make_job(url="https://b.com/j", source_job_id="b", source="jobicy",
                 description=duplicate_text),
    ]
    report, store = _run(replace(settings, db_path=None), jobs)
    assert report.discovered == 2
    assert report.duplicates_removed == 1
    assert report.unique_jobs == 1
    assert store.count_jobs() == 1


def test_cross_run_duplicates_are_removed(settings):
    settings = replace(settings, db_path=None)
    store = Store(None)
    original = _patch_sources([FakeSource([
        make_job(url="https://e.com/cross", source_job_id="cross",
                 description=JOB_DESCRIPTION)])])
    try:
        pipeline.run_discovery(settings, store=store, now=NOW, force=True)
        repeated = make_job(url="https://e.com/cross", source_job_id="cross",
                            description=JOB_DESCRIPTION)
        pipeline.build_sources = lambda s, only=None: ([FakeSource([repeated])], [])
        report = pipeline.run_discovery(settings, store=store, now=NOW, force=True)
    finally:
        pipeline.build_sources = original

    assert report.duplicates_removed == 1
    assert report.stored_inserted == 0
    assert store.count_jobs() == 1


def test_since_and_min_score_overrides(settings):
    jobs = [make_job(url="https://e.com/s", source_job_id="s", minutes_old=45,
                     description=JOB_DESCRIPTION)]
    report, _ = _run(replace(settings, db_path=None), jobs, since_minutes=30)
    assert report.strong_candidates == 0

    report, _ = _run(replace(settings, db_path=None), jobs, min_score=101)
    assert report.strong_candidates == 0
    assert report.rejected_non_de == 1


def test_report_contains_required_sections(settings):
    jobs = [make_job(url="https://e.com/r", source_job_id="r", minutes_old=5,
                     description=JOB_DESCRIPTION)]
    report, _ = _run(replace(settings, db_path=None), jobs)
    text = report.render()
    for expected in ("SchemaHawk - Discovery Run", "Sources checked:", "Source failures:",
                     "Discovered:", "Fresh <=60m:", "Recent 1-3h:", "Old:",
                     "Unknown timestamp:", "Duplicates removed:", "Rejected:",
                     "- Non-data-engineering:", "- Ineligible:", "- Scam/quality:",
                     "Strong candidates:", "Top candidates:"):
        assert expected in text


def test_pipeline_records_a_run_row(settings):
    jobs = [make_job(url="https://e.com/run", source_job_id="run",
                     description=JOB_DESCRIPTION)]
    report, store = _run(replace(settings, db_path=None), jobs)
    assert report.run_id and store.latest_runs(1)[0]["id"] == report.run_id


def test_source_note_is_recorded_for_skipped_poll(settings):
    """A source that is not due is noted (not failed) and skipped."""
    source = FakeSource([make_job(url="https://e.com/poll", source_job_id="poll",
                                  description=JOB_DESCRIPTION)])
    source.poll_every_hours = 6
    store = Store(None)
    original = _patch_sources([source])
    try:
        report = pipeline.run_discovery(replace(settings, db_path=None), store=store,
                                       now=NOW.replace(hour=13), force=False)
    finally:
        pipeline.build_sources = original
    assert report.discovered == 0
    assert report.source_failures == []
    assert any("skipped this hour" in note for _, note in report.source_notes)

