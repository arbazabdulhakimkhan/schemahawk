"""Application-route classification tests (Phase 3A).

Two rules dominate:

1. **Provenance, not self-description.** A job description is untrusted text.
   Nothing inside it can promote itself to a trusted application endpoint; a
   URL becomes official only when its host matches a company we already know.
2. **No side effects.** Classification is pure: this module must never fetch a
   URL, send mail, or contact anyone. A test asserts that.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from schemahawk.models import Job
from schemahawk.routes import (
    ApplicationRoute,
    ApplicationRouteInfo,
    PRECEDENCE,
    RouteConfidence,
    application_specificity,
    careers_marker,
    application_specificity,
    careers_marker,
    classify_route,
    domain_tokens,
    find_email_in_text,
    has_application_path,
    is_ats,
    is_freelance_platform,
    is_http_url,
    is_social,
)


def job(**kwargs) -> Job:
    base = dict(source="fake", title="Data Engineer", company="Acme",
                description="", url=None)
    base.update(kwargs)
    return Job(**base)


# --- route types -----------------------------------------------------------

def test_official_application_needs_provenance_and_specificity():
    result = classify_route(job(url="https://careers.acme.com/jobs/123",
                                company="Acme Corp"))
    assert result.route_type is ApplicationRoute.OFFICIAL_APPLICATION
    assert result.is_official is True
    assert result.confidence is RouteConfidence.HIGH
    assert result.directly_actionable is True


def test_role_path_under_careers_is_official():
    result = classify_route(
        job(url="https://acme.com/careers/data-engineer", company="Acme"))
    assert result.route_type is ApplicationRoute.OFFICIAL_APPLICATION
    assert result.directly_actionable is True


def test_careers_hub_is_not_an_official_application():
    """/careers names no specific posting, so it is a hub, not a form."""
    result = classify_route(job(url="https://acme.com/careers", company="Acme"))
    assert result.route_type is ApplicationRoute.COMPANY_CAREERS
    assert result.is_official is True
    assert result.directly_actionable is False


def test_official_host_without_an_application_path_is_unknown():
    """Official provenance alone proves nothing about what the page offers."""
    for path in ("/about", "/", "/news/2026"):
        result = classify_route(job(url="https://acme.com" + path,
                                    company="Acme"))
        assert result.route_type is ApplicationRoute.UNKNOWN, path
        assert result.url is None, path


def test_careers_marker_and_specificity_helpers():
    assert careers_marker("https://acme.com/careers") == "careers"
    assert careers_marker("https://acme.com/about") is None
    assert careers_marker("https://acme.com/") is None
    assert application_specificity("https://acme.com/jobs/123") == "jobs/123"
    assert application_specificity(
        "https://acme.com/careers/data-eng") == "careers/data-eng"
    assert application_specificity("https://acme.com/careers") is None
    assert application_specificity("https://acme.com/about") is None


def test_external_application_for_a_known_ats_vendor():
    result = classify_route(job(url="https://boards.greenhouse.io/acme/jobs/9",
                                company="Acme"))
    assert result.route_type is ApplicationRoute.EXTERNAL_APPLICATION
    # Third-party hosted does not mean company-owned.
    assert result.is_official is False


def test_operator_configured_board_is_high_confidence():
    result = classify_route(job(
        source="company_boards",
        url="https://boards.greenhouse.io/x/jobs/1", company="Acme"))
    assert result.route_type is ApplicationRoute.EXTERNAL_APPLICATION
    assert result.confidence is RouteConfidence.HIGH


def test_freelance_platform_route_is_actionable():
    result = classify_route(job(url="https://www.upwork.com/jobs/abc",
                                company="Acme"))
    assert result.route_type is ApplicationRoute.FREELANCE_PLATFORM
    assert result.directly_actionable is True


def test_social_profile_counts_as_a_contact_not_an_application():
    result = classify_route(job(url="https://www.linkedin.com/in/someone",
                                company="Acme"))
    assert result.route_type is ApplicationRoute.RECRUITER_CONTACT
    # The path identifies *which* profile; it must survive classification.
    assert result.url.endswith("/in/someone")


def test_job_posting_only_for_a_board_page():
    result = classify_route(job(source="remoteok",
                                url="https://remoteok.com/remote-jobs/x-1",
                                company="Acme"))
    assert result.route_type is ApplicationRoute.JOB_POSTING_ONLY
    assert result.is_official is False


def test_a_board_path_does_not_make_a_board_an_ats():
    """/jobs/ is a path, not proof of ATS vendor provenance."""
    result = classify_route(job(source="jobicy",
                                url="https://jobicy.com/jobs/154514-x",
                                company="Acme"))
    assert result.route_type is ApplicationRoute.JOB_POSTING_ONLY


def test_email_route_is_never_promoted_from_description_text():
    """Untrusted text can mention an address; it cannot become a route."""
    result = classify_route(job(description="Send your CV to hr@acme.com."))
    assert result.route_type is ApplicationRoute.UNKNOWN
    assert result.url is None
    assert result.directly_actionable is False
    # The host is still mentioned, so a human can see what was found.
    assert "acme.com" in result.evidence


def test_recruiter_route_from_a_source_supplied_url():
    result = classify_route(job(recruiter_url="https://example.com/jane",
                                recruiter_name="Jane", company="Acme"))
    assert result.route_type is ApplicationRoute.RECRUITER_CONTACT
    assert result.confidence is RouteConfidence.HIGH


def test_no_route_when_nothing_is_present():
    result = classify_route(job())
    assert result.route_type is ApplicationRoute.UNKNOWN
    assert result.url is None
    assert "no URL" in result.evidence


# --- URL validation --------------------------------------------------------

def test_malformed_url_is_unknown_not_a_guess():
    for bad in ("not a url", "javascript:alert(1)", "data:text/html,x",
                "ftp://acme.com/x", "//acme.com/jobs/1", ""):
        result = classify_route(job(url=bad, company="Acme"))
        assert result.route_type is ApplicationRoute.UNKNOWN, bad


def test_non_http_schemes_are_rejected():
    assert not is_http_url("javascript:alert(1)")
    assert not is_http_url("data:text/html,x")
    assert not is_http_url("mailto:a@b.com")
    assert is_http_url("https://acme.com/jobs/1")


def test_tracking_parameters_are_stripped_for_comparison():
    result = classify_route(job(
        url="https://boards.greenhouse.io/a/jobs/1?utm_source=x&gh_jid=7&ref=z",
        company="Acme"))
    assert "utm_source" not in (result.url or "")
    assert "gh_jid" not in (result.url or "")
    assert "/jobs/1" in (result.url or "")


def test_insecure_url_is_recorded_and_never_upgraded():
    result = classify_route(job(url="http://careers.acme.com/jobs/1",
                                company="Acme"))
    assert result.url.startswith("http://")
    assert "http only" in result.evidence


def test_social_and_ats_and_freelance_detection():
    assert is_social("https://www.linkedin.com/in/x")
    assert is_social("https://github.com/someone")
    assert not is_social("https://careers.acme.com/jobs/1")
    assert is_ats("https://boards.greenhouse.io/a/jobs/1")
    assert is_ats("https://acme.wd5.myworkdayjobs.com/en-US/x")
    assert not is_ats("https://jobicy.com/jobs/1")
    assert is_freelance_platform("https://www.upwork.com/jobs/x")
    assert not is_freelance_platform("https://acme.com/jobs/1")


def test_application_path_separates_official_page_from_official_form():
    assert has_application_path("https://careers.acme.com/jobs/1")
    assert not has_application_path("https://acme.com/")


def test_domain_tokens_strip_legal_suffixes():
    assert domain_tokens("Acme Data Inc.") == ("acme", "data")
    assert domain_tokens(None) == ()


# --- precedence ------------------------------------------------------------

def test_official_route_wins_over_board_posting():
    result = classify_route(job(
        source="remoteok",
        url="https://remoteok.com/remote-jobs/x",
        application_url="https://careers.acme.com/jobs/5",
        company="Acme"))
    assert result.route_type is ApplicationRoute.OFFICIAL_APPLICATION
    assert result.url == "https://careers.acme.com/jobs/5"


def test_official_route_wins_over_recruiter_route():
    result = classify_route(job(
        recruiter_url="https://linkedin.com/in/jane",
        application_url="https://acme.com/jobs/9",
        recruiter_name="Jane", company="Acme"))
    assert result.route_type is ApplicationRoute.OFFICIAL_APPLICATION


def test_precedence_is_ordered_official_first():
    assert PRECEDENCE[0] is ApplicationRoute.OFFICIAL_APPLICATION
    assert PRECEDENCE[-1] is ApplicationRoute.UNKNOWN
    ranks = [PRECEDENCE.index(r) for r in
             (ApplicationRoute.COMPANY_CAREERS,
              ApplicationRoute.FREELANCE_PLATFORM,
              ApplicationRoute.RECRUITER_CONTACT,
              ApplicationRoute.EXTERNAL_APPLICATION,
              ApplicationRoute.JOB_POSTING_ONLY)]
    assert ranks == sorted(ranks)


# --- security --------------------------------------------------------------

def test_classification_performs_no_outbound_action():
    """The module must not import a network client at all."""
    source = Path(inspect.getfile(classify_route)).read_text(encoding="utf-8")
    for forbidden in ("requests", "httpx", "urllib.request", "smtplib",
                      "socket", "urlopen", "subprocess"):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"urlopen", "get", "post", "send_mail"}


def test_no_contact_is_ever_invented():
    result = classify_route(job())
    assert result.url is None
    assert classify_route(job(description="no address here")).url is None


def test_email_shape_check_rejects_image_extensions():
    assert find_email_in_text("write to a@b.com") == "a@b.com"
    assert find_email_in_text("logo@2x.png") is None
    assert find_email_in_text(None) is None


def test_route_info_is_immutable_and_stamped():
    info = classify_route(job(url="https://acme.com/jobs/1", company="Acme"))
    assert isinstance(info, ApplicationRouteInfo)
    assert info.discovered_at is not None
    try:
        info.route_type = ApplicationRoute.NO_ROUTE
    except Exception:
        return
    raise AssertionError("ApplicationRouteInfo must be frozen")


# --- Phase 3A revision: auth is tri-state and recruiter is not actionable --

def test_requires_auth_is_unverified_for_every_route():
    """Phase 3A never fetches a URL, so authentication is never established.

    Inferring it from URL shape was removed deliberately: a "/jobs/" path says
    nothing about whether a login stands in front of it.
    """
    for url, company in [
        ("https://acme.com/jobs/1", "Acme"),
        ("https://boards.greenhouse.io/a/jobs/1", "Acme"),
        ("https://www.upwork.com/jobs/x", "Acme"),
        ("https://www.linkedin.com/in/jane", "Acme"),
        ("https://remoteok.com/remote-jobs/x-1", "Acme"),
    ]:
        result = classify_route(job(url=url, company=company))
        assert result.requires_auth is None, url


def test_operator_board_is_also_unverified_for_auth():
    result = classify_route(job(source="company_boards",
                                url="https://boards.greenhouse.io/x/jobs/1",
                                company="Acme"))
    assert result.requires_auth is None


def test_recruiter_route_is_not_actionable():
    """A profile proves a contact surface, not that it is the hiring party."""
    social = classify_route(job(url="https://www.linkedin.com/in/jane",
                                company="Acme"))
    assert social.route_type is ApplicationRoute.RECRUITER_CONTACT
    assert social.directly_actionable is False
    assert social.is_usable is False

    supplied = classify_route(job(recruiter_url="https://example.com/jane",
                                  recruiter_name="Jane", company="Acme"))
    assert supplied.route_type is ApplicationRoute.RECRUITER_CONTACT
    assert supplied.directly_actionable is False


def test_recruiter_contact_is_still_reported_with_its_url():
    """Non-actionable must not mean invisible."""
    result = classify_route(job(url="https://www.linkedin.com/in/jane",
                                company="Acme"))
    assert result.url == "https://www.linkedin.com/in/jane"
    assert result.evidence


def test_actionable_set_excludes_recruiter_and_includes_official():
    from schemahawk.routes import DIRECTLY_ACTIONABLE
    assert ApplicationRoute.RECRUITER_CONTACT not in DIRECTLY_ACTIONABLE
    assert ApplicationRoute.OFFICIAL_APPLICATION in DIRECTLY_ACTIONABLE
    assert ApplicationRoute.FREELANCE_PLATFORM in DIRECTLY_ACTIONABLE


def test_is_usable_requires_both_actionability_and_confidence():
    official = classify_route(job(url="https://acme.com/jobs/1", company="Acme"))
    assert official.is_usable is True
    unknown = classify_route(job(url="https://acme.com/about", company="Acme"))
    assert unknown.directly_actionable is False
    assert unknown.is_usable is False
