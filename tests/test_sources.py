"""Source adapter tests against small, real (trimmed) API payloads.

These tests never touch the network: each adapter's HTTP helper is replaced
with the recorded fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from schemahawk.models import CONTRACT, FreshnessStatus, PART_TIME
from schemahawk.normalize import CONFIDENCE_EXACT, CONFIDENCE_EXACT_NAIVE
from schemahawk.sources import REGISTRY, build_sources
from schemahawk.sources.adzuna import AdzunaSource
from schemahawk.sources.base import SourceError
from schemahawk.sources.company_boards import CompanyBoardsSource
from schemahawk.sources.jobicy import JobicySource
from schemahawk.sources.linkedin import LinkedInSource
from schemahawk.sources.remoteok import RemoteOKSource
from schemahawk.sources.remotive import RemotiveSource
from schemahawk.sources.weworkremotely import WeWorkRemotelySource

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- RemoteOK -------------------------------------------------------------
def test_remoteok_parses_payload_and_skips_legal_notice(settings, monkeypatch):
    payload = load_json("remoteok.json")
    source = RemoteOKSource(settings)
    monkeypatch.setattr(source, "get_json",
                        lambda url, params=None, headers=None: payload)
    jobs = source.fetch()
    # Element 0 of the live payload is a legal notice, not a job.
    assert len(jobs) == len(payload) - 1
    first = jobs[0]
    assert first.source == "remoteok"
    assert first.title and first.company
    assert first.url is not None and first.url.startswith("http")
    assert first.timestamp_confidence == CONFIDENCE_EXACT
    assert first.posted_at is not None and first.posted_at.tzinfo is not None
    assert "source tags:" in (first.description or "")


def test_remoteok_handles_notice_only_payload(settings, monkeypatch):
    source = RemoteOKSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: [{"legal": "notice"}])
    assert source.fetch() == []


def test_remoteok_rejects_non_list_payload(settings, monkeypatch):
    source = RemoteOKSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: {"oops": True})
    jobs, error = source.run()
    assert jobs == [] and "JSON array" in error


# --- Remotive -------------------------------------------------------------
def test_remotive_parses_payload_and_dedupes_queries(settings, monkeypatch):
    payload = load_json("remotive.json")
    source = RemotiveSource(settings)
    monkeypatch.setattr(source, "get_json",
                        lambda url, params=None, headers=None: payload)
    jobs = source.fetch()
    ids = [job.source_job_id for job in jobs]
    assert len(ids) == len(set(ids))  # the two queries must not double-count
    assert all(job.timestamp_confidence == CONFIDENCE_EXACT_NAIVE for job in jobs)
    assert all(job.posted_at is not None and job.posted_at.tzinfo is not None
               for job in jobs)


def test_remotive_polls_every_six_hours(settings):
    assert RemotiveSource(settings).poll_every_hours == 6


def test_remotive_maps_freelance_contract_type(settings, monkeypatch):
    payload = {"jobs": [{
        "id": 1, "title": "Freelance Data Engineer", "company_name": "Acme",
        "publication_date": "2026-10-02T20:01:00", "job_type": "freelance",
        "candidate_required_location": "Worldwide",
        "url": "https://remotive.com/remote-jobs/1", "description": "<p>ETL</p>",
    }]}
    source = RemotiveSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    job = source.fetch()[0]
# --- Jobicy ---------------------------------------------------------------
def test_jobicy_parses_payload(settings, monkeypatch):
    payload = load_json("jobicy.json")
    source = JobicySource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    jobs = source.fetch()
    assert len(jobs) == len(payload["jobs"])
    assert all(job.timestamp_confidence == CONFIDENCE_EXACT for job in jobs)
    assert all(job.posted_at is not None for job in jobs)


def test_jobicy_skips_entries_without_title(settings, monkeypatch):
    source = JobicySource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: {"jobs": [{"id": 1}]})
    assert source.fetch() == []


def test_jobicy_maps_part_time(settings, monkeypatch):
    payload = {"jobs": [{"id": 5, "jobTitle": "Data Engineer", "companyName": "Acme",
                         "pubDate": "2026-10-04T05:45:40+00:00",
                         "jobType": ["Part-Time"], "jobGeo": "Anywhere",
                         "url": "https://jobicy.com/jobs/5"}]}
    source = JobicySource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    job = source.fetch()[0]
    assert job.contract_type == PART_TIME
    assert job.remote is True


# --- We Work Remotely -----------------------------------------------------
def test_wwr_parses_rss_and_splits_company(settings, monkeypatch):
    xml_text = load_text("weworkremotely.xml")
    source = WeWorkRemotelySource(settings)
    monkeypatch.setattr(source, "get_text",
                        lambda url, params=None, headers=None: xml_text)
    jobs = source.fetch()
    assert jobs
    assert all(job.company and job.title for job in jobs)
    assert all(job.timestamp_confidence == CONFIDENCE_EXACT for job in jobs)
    assert any("source tags:" in (job.description or "") for job in jobs)


def test_wwr_invalid_xml_is_a_source_error(settings, monkeypatch):
    source = WeWorkRemotelySource(settings)
    monkeypatch.setattr(source, "get_text", lambda *a, **k: "not xml at all <")
    jobs, error = source.run()
    assert jobs == [] and error is not None


def test_wwr_splits_company_and_role():
    from schemahawk.sources.weworkremotely import _split_title
    assert _split_title("Toptal: Senior Data Engineer") == ("Toptal", "Senior Data Engineer")
    company, role = _split_title("Just A Role")
    assert company is None and role == "Just A Role"


# --- Adzuna (optional keyed source) ---------------------------------------
def test_adzuna_is_skipped_without_credentials(settings):
    assert AdzunaSource.is_configured(settings) is False
    assert "ADZUNA_APP_ID" in (AdzunaSource.skip_reason(settings) or "")


def test_adzuna_parses_results(settings, monkeypatch):
    from dataclasses import replace
    configured = replace(settings, adzuna_app_id="id", adzuna_app_key="key",
                         adzuna_countries=("gb",))
    payload = {"results": [{
        "id": "999", "title": "Data Engineer", "created": "2026-10-04T06:00:00Z",
        "redirect_url": "https://www.adzuna.co.uk/jobs/999",
        "description": "Build ETL pipelines.",
        "company": {"display_name": "Acme"},
        "location": {"display_name": "London", "area": ["UK", "London"]},
        "contract_time": "full_time",
    }]}
    source = AdzunaSource(configured)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    job = source.fetch()[0]
    assert job.source_job_id == "gb-999"
    assert job.country == "UK"
    assert job.timestamp_confidence == CONFIDENCE_EXACT


def test_adzuna_reports_failure_when_every_country_fails(settings, monkeypatch):
    from dataclasses import replace
    configured = replace(settings, adzuna_app_id="id", adzuna_app_key="key",
                         adzuna_countries=("gb", "us"))
    source = AdzunaSource(configured)
    calls = {"n": 0}

    def fake(url, params=None, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SourceError("HTTP 500 server error")
        return {"results": []}

    monkeypatch.setattr(source, "get_json", fake)
    jobs, error = source.run()
    assert jobs == []
    assert error and "gb" in error
    assert source.warnings and "gb" in source.warnings[0]


def test_adzuna_keeps_going_when_one_country_fails(settings, monkeypatch):
    from dataclasses import replace
    configured = replace(settings, adzuna_app_id="id", adzuna_app_key="key",
                         adzuna_countries=("gb", "us"))
    source = AdzunaSource(configured)
    calls = {"n": 0}

    def fake(url, params=None, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SourceError("HTTP 500 server error")
        return {"results": [{"id": "1", "title": "Data Engineer",
                             "created": "2026-10-04T06:00:00Z",
                             "redirect_url": "https://adzuna.com/1",
                             "location": {"area": ["US"]},
                             "company": {"display_name": "Acme"}}]}

    monkeypatch.setattr(source, "get_json", fake)
    jobs, error = source.run()
    assert error is None
    assert len(jobs) == 1 and jobs[0].source_job_id == "us-1"
    assert source.warnings and "gb" in source.warnings[0]


def test_adzuna_never_logs_credentials(settings):
    safe = AdzunaSource(settings)._safe_url(
        "https://api.adzuna.com/v1/api/jobs/gb/search/1?app_id=SECRET&app_key=TOPSECRET")
    assert "SECRET" not in safe
    assert safe == "https://api.adzuna.com/v1/api/jobs/gb/search/1"


# --- Company boards (optional) -------------------------------------------
def test_company_boards_skipped_without_tokens(settings):
    assert CompanyBoardsSource.is_configured(settings) is False
    assert "GREENHOUSE_BOARDS" in (CompanyBoardsSource.skip_reason(settings) or "")


def test_company_boards_parses_greenhouse(settings, monkeypatch):
    from dataclasses import replace
    configured = replace(settings, greenhouse_boards=("acme",))
    payload = {"jobs": [{
        "id": 7, "title": "Data Engineer", "updated_at": "2026-10-04T06:00:00-04:00",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/7",
        "location": {"name": "Remote - US"}, "content": "&lt;p&gt;ETL&lt;/p&gt;",
    }]}
    source = CompanyBoardsSource(configured)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    job = source.fetch()[0]
    assert job.source_job_id == "gh-acme-7"
    assert job.description == "ETL"
    assert job.remote is True


def test_company_boards_isolates_a_broken_token(settings, monkeypatch):
    from dataclasses import replace
    configured = replace(settings, greenhouse_boards=("broken",),
                         lever_boards=("working",))
    source = CompanyBoardsSource(configured)

    def fake(url, params=None, headers=None):
        if "greenhouse" in url:
            raise SourceError("HTTP 404 not found")
        return [{"id": "x", "text": "Data Engineer", "createdAt": 1768474800000,
                 "categories": {"location": "Remote", "commitment": "Contract"},
                 "descriptionPlain": "ETL work"}]

    monkeypatch.setattr(source, "get_json", fake)
    jobs = source.fetch()
    assert len(jobs) == 1 and jobs[0].source_job_id == "lever-working-x"
    assert source.warnings and "broken" in source.warnings[0]


# --- LinkedIn stub --------------------------------------------------------
def test_linkedin_is_an_honest_stub(settings):
    assert LinkedInSource.is_configured(settings) is False
    assert "terms of service" in (LinkedInSource.skip_reason(settings) or "")
    jobs, error = LinkedInSource(settings).run()
    assert jobs == [] and "not supported in V1" in error


# --- registry -------------------------------------------------------------
def test_build_sources_reports_skips_as_notes(settings):
    sources, notes = build_sources(settings)
    names = {source.name for source in sources}
    assert {"remoteok", "remotive", "jobicy", "weworkremotely"} <= names
    assert {"adzuna", "company_boards"} <= {name for name, _ in notes}


def test_build_sources_unknown_name_raises(settings):
    with pytest.raises(ValueError):
        build_sources(settings, only="does-not-exist")


def test_build_sources_only_selects_one(settings):
    sources, _ = build_sources(settings, only="jobicy")
    assert [source.name for source in sources] == ["jobicy"]


def test_linkedin_never_enabled_implicitly(settings):
    sources, _ = build_sources(settings)
    assert "linkedin" not in {source.name for source in sources}


def test_registry_matches_config_defaults(settings):
    assert set(settings.enabled_sources) <= set(REGISTRY)


def test_remotive_naive_timestamp_is_fresh(settings, monkeypatch):
    """tz-naive Remotive timestamps still yield a valid freshness verdict."""
    from datetime import datetime, timezone

    from schemahawk.freshness import evaluate_freshness

    now = datetime.now(timezone.utc)
    payload = {"jobs": [{"id": 2, "title": "Data Engineer", "company_name": "Acme",
                         "publication_date": now.replace(tzinfo=None).isoformat(),
                         "url": "https://remotive.com/remote-jobs/2"}]}
    source = RemotiveSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    job = source.fetch()[0]
    evaluate_freshness(job, now=now)
    assert job.freshness_status == FreshnessStatus.FRESH
    assert 0 <= (job.freshness_minutes or 0) <= 1


# --- compensation wiring (offline; fixtures only) ---------------------------
#
# These guard the *adapter mapping*, not the parser: that a board's pay fields
# actually reach ``Job.compensation``. The parser itself is covered in
# ``test_normalize``; this is the wiring most likely to regress silently.

def test_remoteok_salary_fields_reach_compensation(settings, monkeypatch):
    payload = load_json("remoteok.json")
    source = RemoteOKSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    jobs = source.fetch()

    paid = [j for j in jobs if j.compensation and j.compensation.is_known]
    assert paid, "fixture should contain at least one job with salary_min"

    # Match the fixture's own values, so the mapping is verified end to end.
    by_id = {str(e.get("id")): e for e in payload if isinstance(e, dict)}
    for job in paid:
        entry = by_id[job.source_job_id]
        assert job.compensation.min_value == float(entry["salary_min"])
        assert job.compensation.max_value == float(entry["salary_max"])


def test_remoteok_leaves_period_unknown(settings, monkeypatch):
    """RemoteOK publishes no period, so none may be invented."""
    payload = load_json("remoteok.json")
    source = RemoteOKSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    for job in source.fetch():
        if job.compensation:
            assert job.compensation.period is None
            assert job.compensation.currency is None


def test_remoteok_job_without_salary_has_no_compensation(settings, monkeypatch):
    payload = {"id": 1, "position": "Data Engineer", "company": "Acme",
               "url": "https://remoteok.com/remote-jobs/x",
               "tags": ["sql"], "epoch": 1767225600}
    source = RemoteOKSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: [payload])
    job = source.fetch()[0]
    assert job.compensation is None


def test_remotive_salary_text_reaches_compensation(settings, monkeypatch):
    payload = load_json("remotive.json")
    source = RemotiveSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    paid = [j for j in source.fetch() if j.compensation and j.compensation.is_known]
    assert paid, "fixture should contain a job with salary text"

    for job in paid:
        assert job.compensation.min_value is not None
        assert job.compensation.max_value is not None
        # The original string is kept verbatim for auditability.
        assert job.compensation.raw


def test_remotive_salary_string_is_parsed_not_guessed(settings, monkeypatch):
    """'$20k -$35k' (no space after the dash) must still yield a range."""
    payload = {"jobs": [{"id": 9, "title": "Freelance Data Engineer",
                         "company_name": "Acme",
                         "publication_date": "2026-01-15T10:00:00",
                         "url": "https://remotive.com/remote-jobs/9",
                         "salary": "$20k -$35k", "job_type": "freelance"}]}
    source = RemotiveSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    job = source.fetch()[0]
    assert job.compensation.min_value == 20000.0
    assert job.compensation.max_value == 35000.0
    assert job.compensation.currency == "USD"
    assert job.compensation.raw == "$20k -$35k"


def test_remotive_job_without_salary_has_no_compensation(settings, monkeypatch):
    payload = {"jobs": [{"id": 10, "title": "Data Engineer",
                         "company_name": "Acme",
                         "publication_date": "2026-01-15T10:00:00",
                         "url": "https://remotive.com/remote-jobs/10"}]}
    source = RemotiveSource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    assert source.fetch()[0].compensation is None


def test_jobicy_structured_salary_is_mapped(settings, monkeypatch):
    """Synthetic payload: the recorded fixture predates salary fields."""
    payload = {"jobs": [{
        "id": 5, "jobTitle": "Contract Data Engineer", "companyName": "Acme",
        "pubDate": "2026-01-15T10:00:00", "url": "https://jobicy.com/job/5",
        "jobGeo": "Worldwide", "jobType": ["Contract"],
        "jobDescription": "Build ETL with SQL.",
        "salaryMin": 45, "salaryMax": 60, "salaryCurrency": "USD",
        "salaryPeriod": "hourly",
    }]}
    source = JobicySource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    job = source.fetch()[0]
    assert job.compensation is not None
    assert job.compensation.min_value == 45.0
    assert job.compensation.max_value == 60.0
    assert job.compensation.currency == "USD"
    assert job.compensation.period == "hour"


def test_jobicy_job_without_salary_has_no_compensation(settings, monkeypatch):
    payload = {"jobs": [{
        "id": 6, "jobTitle": "Data Engineer", "companyName": "Acme",
        "pubDate": "2026-01-15T10:00:00", "url": "https://jobicy.com/job/6",
        "jobGeo": "Worldwide", "jobDescription": "Build ETL with SQL.",
    }]}
    source = JobicySource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    assert source.fetch()[0].compensation is None


def test_jobicy_absent_salary_keys_yield_none(settings, monkeypatch):
    """Absent keys must not become zero (which would read as unpaid)."""
    payload = {"jobs": [{
        "id": 7, "jobTitle": "Data Engineer", "companyName": "Acme",
        "pubDate": "2026-01-15T10:00:00", "url": "https://jobicy.com/job/7",
        "jobDescription": "x",
    }]}
    source = JobicySource(settings)
    monkeypatch.setattr(source, "get_json", lambda *a, **k: payload)
    comp = source.fetch()[0].compensation
    assert comp is None


