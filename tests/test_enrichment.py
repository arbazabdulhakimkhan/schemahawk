"""Route-acquisition tests (Phase 3B).

Acquisition attaches a publicly discoverable application URL to a job that was
found on some other board. The properties that matter:

1. **Configuration is the trust anchor** - only configured boards are fetched.
2. **Exact matching only** - no fuzzy score can attach the wrong company's job.
3. **Ambiguity means decline** - never resolve a collision by guessing.
4. **Precedence is Phase 3A's** - a stronger existing route is never replaced.
5. **No side effects** - nothing is submitted, fetched from job text, or posted.

The network is stubbed at ``get_json``, so the real
``board_listings``/``_greenhouse_listings``/``_lever_listings`` parse path runs
against recorded payloads rather than being bypassed.
"""
from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path

from schemahawk.enrichment import SKIPPED_NO_BOARDS, enrich_jobs
from schemahawk.models import Job
from schemahawk.routes import ApplicationRoute, classify_route
from schemahawk.sources.company_boards import CompanyBoardsSource

FIXTURES = Path(__file__).parent / "fixtures"
GREENHOUSE = json.loads((FIXTURES / "greenhouse_jobs.json").read_text(encoding="utf-8"))
LEVER = json.loads((FIXTURES / "lever_postings.json").read_text(encoding="utf-8"))

GH_TOKEN = "acme"
LV_TOKEN = "globex"
GH_URL = "https://boards.greenhouse.io/acme/jobs/900002"   # "Analytics Engineer"
LV_URL = "https://jobs.lever.co/globex/770001-freelance-data-engineer"


def job(**kwargs) -> Job:
    base = dict(source="remoteok", title="Analytics Engineer",
                company="Acme", description="", url=None)
    base.update(kwargs)
    return Job(**base)


def configured(settings, greenhouse=(), lever=()):
    return replace(settings, greenhouse_boards=tuple(greenhouse),
                   lever_boards=tuple(lever))


def stub_network(monkeypatch):
    """Serve fixture payloads from the real parse path. Records every URL."""
    seen: list[str] = []

    def fake(self, url, params=None, headers=None):
        seen.append(url)
        if "greenhouse" in url:
            return GREENHOUSE
        if "lever" in url:
            return LEVER
        raise AssertionError(f"unexpected network target: {url}")

    monkeypatch.setattr(CompanyBoardsSource, "get_json", fake)
    return seen


# --- skip when unconfigured ------------------------------------------------

def test_no_boards_means_no_network_and_a_skip(settings, monkeypatch):
    def explode(self):
        raise AssertionError("board_listings must not run when unconfigured")
    monkeypatch.setattr(CompanyBoardsSource, "board_listings", explode)

    jobs = [job()]
    report = enrich_jobs(jobs, replace(settings, greenhouse_boards=(),
                                       lever_boards=()))
    assert report.status == SKIPPED_NO_BOARDS
    assert report.enriched == 0
    assert report.boards_examined == 0
    assert jobs[0].application_url is None


# --- Greenhouse -----------------------------------------------------------

def test_greenhouse_exact_key_attaches_application_url(settings, monkeypatch):
    stub_network(monkeypatch)
    jobs = [job()]
    report = enrich_jobs(jobs, configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 1
    assert jobs[0].application_url == GH_URL
    assert "exact key" in report.evidence[0][1]


def test_greenhouse_wrong_company_is_never_matched(settings, monkeypatch):
    """A configured Acme board must not enrich a Vanta job."""
    stub_network(monkeypatch)
    jobs = [job(company="Vanta")]
    report = enrich_jobs(jobs, configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 0
    assert report.no_match == 1
    assert jobs[0].application_url is None


def test_greenhouse_wrong_title_is_never_matched(settings, monkeypatch):
    stub_network(monkeypatch)
    jobs = [job(title="Head of Cheese")]
    report = enrich_jobs(jobs, configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 0
    assert jobs[0].application_url is None


def test_greenhouse_duplicate_title_is_ambiguous_and_declined(settings,
                                                             monkeypatch):
    """Two listings share a key: decline rather than pick one."""
    stub_network(monkeypatch)
    jobs = [job(title="Senior Data Engineer")]
    report = enrich_jobs(jobs, configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 0
    assert report.ambiguous_matches == 1
    assert jobs[0].application_url is None
    detail = report.evidence[0][1]
    assert "ambiguous" in detail and "matched 2 listings" in detail
    # Evidence explains the ambiguity without dumping listing bodies.
    assert "<p>" not in detail


def test_greenhouse_match_is_case_and_punctuation_insensitive(settings,
                                                              monkeypatch):
    """company_title_key normalizes both sides identically."""
    stub_network(monkeypatch)
    jobs = [job(title="analytics   engineer", company="ACME")]
    report = enrich_jobs(jobs, configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 1


def test_board_sourced_jobs_are_skipped(settings, monkeypatch):
    """A job already from the board has its own route; nothing to acquire."""
    stub_network(monkeypatch)
    jobs = [job(source="company_boards")]
    report = enrich_jobs(jobs, configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 0
    assert jobs[0].application_url is None


# --- Lever ----------------------------------------------------------------

def test_lever_exact_key_attaches_application_url(settings, monkeypatch):
    stub_network(monkeypatch)
    jobs = [job(title="Freelance Data Engineer", company="Globex")]
    report = enrich_jobs(jobs, configured(settings, lever=[LV_TOKEN]))
    assert report.enriched == 1
    assert jobs[0].application_url == LV_URL


def test_lever_wrong_company_is_never_matched(settings, monkeypatch):
    stub_network(monkeypatch)
    jobs = [job(title="Lead Analytics Engineer", company="Initech")]
    report = enrich_jobs(jobs, configured(settings, lever=[LV_TOKEN]))
    assert report.enriched == 0
    assert jobs[0].application_url is None


def test_only_configured_boards_are_fetched(settings, monkeypatch):
    """An unconfigured board must never be requested."""
    seen = stub_network(monkeypatch)
    enrich_jobs([job()], configured(settings, greenhouse=[GH_TOKEN]))
    assert seen == [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
    ]


def test_unconfigured_board_token_is_never_requested(settings, monkeypatch):
    """Configuring 'acme' must not pull 'vanta'."""
    seen = stub_network(monkeypatch)
    enrich_jobs([job(company="Vanta")], configured(settings, greenhouse=[GH_TOKEN]))
    assert all("vanta" not in url for url in seen)


# --- precedence -----------------------------------------------------------

def test_existing_official_route_is_not_downgraded(settings, monkeypatch):
    stub_network(monkeypatch)
    strong = job(url="https://careers.acme.com/jobs/900002")
    assert classify_route(strong).route_type is (
        ApplicationRoute.OFFICIAL_APPLICATION)
    report = enrich_jobs([strong], configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 0
    assert report.weaker_route_rejected == 1
    assert strong.application_url is None


def test_board_posting_route_is_upgraded(settings, monkeypatch):
    stub_network(monkeypatch)
    weak = job(url="https://remoteok.com/remote-jobs/acme-analytics-1")
    assert classify_route(weak).route_type is ApplicationRoute.JOB_POSTING_ONLY
    report = enrich_jobs([weak], configured(settings, greenhouse=[GH_TOKEN]))
    assert report.enriched == 1
    assert classify_route(weak).route_type is ApplicationRoute.EXTERNAL_APPLICATION


def test_precedence_is_phase3as_not_duplicated():
    from schemahawk.enrichment import _rank
    from schemahawk.routes import PRECEDENCE
    assert _rank(ApplicationRoute.OFFICIAL_APPLICATION) == 0
    assert _rank(ApplicationRoute.UNKNOWN) == len(PRECEDENCE) - 1


# --- security -------------------------------------------------------------

def test_url_from_description_is_never_fetched(settings, monkeypatch):
    """A URL in posting text is data, not permission to fetch."""
    seen = stub_network(monkeypatch)
    hostile = job(
        description="Apply now at https://evil.example.com/steal?x=1",
        url="https://remoteok.com/remote-jobs/acme-analytics-1")
    enrich_jobs([hostile], configured(settings, greenhouse=[GH_TOKEN]))
    assert not any("evil.example.com" in url for url in seen)
    assert hostile.application_url != "https://evil.example.com/steal?x=1"


def test_enrichment_makes_no_outbound_action():
    """Static check: no HTTP client, no POST, no mail, no form submission."""
    source = Path(inspect.getfile(enrich_jobs)).read_text(encoding="utf-8")
    for forbidden in ("smtplib", "socket", "subprocess", "urlopen",
                      "requests.post", "os.system", "eval(", "exec("):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"post", "put", "patch", "delete"}, \
                node.func.attr


def test_credentials_are_never_used_or_stored(settings, monkeypatch):
    """Public board configuration needs no secrets."""
    stub_network(monkeypatch)
    jobs = [job()]
    enrich_jobs(jobs, configured(settings, greenhouse=[GH_TOKEN]))
    for acquired in jobs:
        assert acquired.contact_email is None
        assert acquired.recruiter_url is None
