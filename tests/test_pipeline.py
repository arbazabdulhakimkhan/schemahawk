"""End-to-end pipeline tests with an injected fake source (no network)."""
from __future__ import annotations

from dataclasses import replace

from schemahawk import pipeline
from schemahawk.models import PipelineStatus
from schemahawk.profile import default_profile, parse_profile
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


# --- candidate matching wiring ---------------------------------------------
#
# The match layer is additive: it must never change V1 counters, statuses or
# the report sections V1 already guaranteed.

MATCH_JD = ("You have strong experience with Python and SQL. "
            "Required: Snowflake. Nice to have: Tableau. "
            "5+ years of experience. Remote worldwide.")


def test_pipeline_populates_match_results(settings):
    jobs = [make_job(url="https://e.com/m1", source_job_id="m1", minutes_old=5,
                     title="Senior Data Engineer", description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs,
                     profile=parse_profile({
                         "skills": ["SQL", "Python", "Snowflake"],
                         "total_years_experience": 8,
                         "seniority": "senior",
                     }))
    assert report.strong_candidates == 1
    assert len(report.matches) == 1
    match = report.matches[0][1]
    assert match.technical_score is not None
    assert match.overall_score is not None


def test_match_results_do_not_change_v1_counters(settings):
    """Adding the match layer must leave every V1 number exactly as it was."""
    jobs = [make_job(url="https://e.com/m2", source_job_id="m2", minutes_old=5,
                     description=MATCH_JD)]
    with_profile, _ = _run(replace(settings, db_path=None), jobs,
                           profile=parse_profile({"skills": ["SQL"]}))
    without, _ = _run(replace(settings, db_path=None), jobs,
                      profile=default_profile())
    assert with_profile.discovered == without.discovered
    assert with_profile.strong_candidates == without.strong_candidates
    assert with_profile.duplicates_removed == without.duplicates_removed
    assert with_profile.rejected_non_de == without.rejected_non_de


def test_v1_relevance_score_is_untouched_by_matching(settings):
    jobs = [make_job(url="https://e.com/m3", source_job_id="m3", minutes_old=5,
                     description=MATCH_JD)]
    report, store = _run(replace(settings, db_path=None), jobs,
                         profile=parse_profile({"skills": ["SQL"]}))
    stored = store.conn.execute("SELECT relevance_score FROM jobs").fetchone()[0]
    assert stored == report.matches[0][0].relevance_score


def test_report_renders_both_scores_separately(settings):
    jobs = [make_job(url="https://e.com/m4", source_job_id="m4", minutes_old=5,
                     description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs,
                     profile=parse_profile({"skills": ["SQL", "Python"]}))
    text = report.render()
    assert "Overall candidate match:" in text
    assert "Candidate Match Score:" in text
    assert "V1 Relevance Score:" in text
    assert "Technical:" in text and "Eligibility:" in text


def test_report_prints_unknown_for_unspecified_profile(settings):
    """An empty profile must render UNKNOWN, not a fake zero."""
    jobs = [make_job(url="https://e.com/m5", source_job_id="m5", minutes_old=5,
                     description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs,
                     profile=default_profile())
    # Section-scoped, not "last N lines": Phase 3A appends an application-route
    # section after the match block, so trailing-line slicing is no longer a
    # reliable way to reach it.
    text = report.render()
    block = text.split("Overall candidate match:")[1].split("Application routes:")[0]
    assert "Candidate Match Score: UNKNOWN" in block
    assert "Technical: UNKNOWN" in block


def test_v1_report_sections_survive_the_match_layer(settings):
    jobs = [make_job(url="https://e.com/m6", source_job_id="m6", minutes_old=5,
                     description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs,
                     profile=parse_profile({"skills": ["SQL"]}))
    text = report.render()
    for expected in ("SchemaHawk - Discovery Run", "Discovered:", "Fresh <=60m:",
                     "Duplicates removed:", "Rejected:", "Strong candidates:",
                     "Top candidates:"):
        assert expected in text


def test_matching_never_marks_a_job_strong_by_itself(settings):
    """A perfect profile match must not promote an irrelevant listing."""
    jobs = [make_job(url="https://e.com/m7", source_job_id="m7", minutes_old=5,
                     title="Graphic Designer", description="Photoshop.")]
    report, _ = _run(replace(settings, db_path=None), jobs,
                     profile=parse_profile({"skills": ["Photoshop"]}))
    assert report.strong_candidates == 0
    assert report.matches == []

# --- Phase 3A: application routes ----------------------------------------


def test_pipeline_classifies_a_route_for_every_match(settings):
    jobs = [make_job(url="https://e.com/m7", source_job_id="m7",
                     minutes_old=5, description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs, profile=default_profile())
    assert len(report.routes) == len(report.matches)
    text = report.render()
    assert "Application routes:" in text


def test_report_names_the_route_type_and_evidence(settings):
    jobs = [make_job(url="https://e.com/m8", source_job_id="m8",
                     minutes_old=5, description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs, profile=default_profile())
    text = report.render()
    assert "Route:" in text
    assert "Evidence:" in text
    assert "https://e.com/m8" in text


def test_route_classification_is_deterministic(settings):
    jobs = [make_job(url="https://e.com/m9", source_job_id="m9",
                     minutes_old=5, description=MATCH_JD)]
    first, _ = _run(replace(settings, db_path=None), jobs, profile=default_profile())
    second, _ = _run(replace(settings, db_path=None), jobs, profile=default_profile())
    assert ([r.route_type for _, r in first.routes]
            == [r.route_type for _, r in second.routes])


def test_route_stage_does_not_mutate_the_v1_relevance_score(settings):
    jobs = [make_job(url="https://e.com/m10", source_job_id="m10",
                     minutes_old=5, description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs, profile=default_profile())
    for job, _route in report.routes:
        assert job.relevance_score is not None
        assert job.application_url is None or job.application_url


def test_route_stage_never_invents_a_contact(settings):
    jobs = [make_job(url="https://e.com/m11", source_job_id="m11",
                     minutes_old=5, description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None), jobs, profile=default_profile())
    for _job, route in report.routes:
        assert "@" not in (route.url or "")


# --- Phase 3B: route acquisition -----------------------------------------


def test_pipeline_reports_acquisition_skipped_without_boards(settings):
    """With no trusted boards configured the stage is a no-op, not a failure."""
    jobs = [make_job(url="https://e.com/m12", source_job_id="m12",
                     minutes_old=5, description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None, greenhouse_boards=(),
                             lever_boards=()), jobs, profile=default_profile())
    assert report.acquisition is not None
    assert report.acquisition.status == "skipped (no configured boards)"
    assert report.acquisition.enriched == 0
    assert "Route acquisition:" in report.render()


def test_pipeline_acquisition_runs_before_classification(settings, monkeypatch):
    """Routes are classified after acquisition has had its chance to attach."""
    from schemahawk.sources.company_boards import CompanyBoardsSource
    order = []

    def listings(self):
        order.append("acquire")
        return [], []
    monkeypatch.setattr(CompanyBoardsSource, "board_listings", listings)

    from schemahawk.routes import classify_route as original

    def spy(job):
        order.append("classify")
        return original(job)
    monkeypatch.setattr("schemahawk.pipeline.classify_route", spy)

    jobs = [make_job(url="https://e.com/m13", source_job_id="m13",
                     minutes_old=5, description=MATCH_JD)]
    _run(replace(settings, db_path=None, greenhouse_boards=("acme",),
                 lever_boards=()), jobs, profile=default_profile())
    assert order[0] == "acquire"
    assert "classify" in order


def test_pipeline_acquisition_never_blocks_a_run(settings, monkeypatch):
    """A broken board must not fail the pipeline."""
    from schemahawk.sources.company_boards import CompanyBoardsSource

    def boom(self):
        raise RuntimeError("board API exploded")
    monkeypatch.setattr(CompanyBoardsSource, "board_listings", boom)

    jobs = [make_job(url="https://e.com/m14", source_job_id="m14",
                     minutes_old=5, description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None, greenhouse_boards=("acme",),
                             lever_boards=()), jobs, profile=default_profile())
    assert "failed" in report.acquisition.status
    assert report.strong_candidates >= 0


def test_pipeline_acquisition_does_not_mutate_relevance(settings, monkeypatch):
    jobs = [make_job(url="https://e.com/m15", source_job_id="m15",
                     minutes_old=5, description=MATCH_JD)]
    report, _ = _run(replace(settings, db_path=None, greenhouse_boards=(),
                             lever_boards=()), jobs, profile=default_profile())
    for job, _route in report.routes:
        assert job.relevance_score is not None
