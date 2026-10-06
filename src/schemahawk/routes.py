"""Deterministic application-route classification (Phase 3A).

This module answers one question: *if a candidate wanted to apply to this job,
what legitimate, usable route exists?* It is classification only.

Hard boundaries - nothing in this module performs an outward action:

- no HTTP request is made (no HEAD/GET probe of an application URL)
- no email is sent, no application submitted, no message sent
- no account is created and no CAPTCHA/MFA is touched
- no contact address is invented, and one found in job text is never persisted

Why this matters: a job description is attacker-controllable text. A URL or
address scraped out of it must never be promoted to a trusted endpoint on the
strength of its own contents. Trust comes from provenance (which source
supplied the field, and whether its host matches a company we already know),
never from what the page says about itself.

Confidence vocabulary (deliberately coarse - three states, no false precision):

- ``HIGH``   provenance proves the classification
- ``MEDIUM`` structure of the URL indicates it, provenance does not prove it
- ``LOW``    we found something but cannot classify it confidently
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from urllib.parse import urlsplit, urlunsplit

from .models import Job


class ApplicationRoute(IntEnum):
    """How a candidate could legitimately reach a hiring team.

    ``UNKNOWN`` and ``NO_ROUTE`` are different on purpose. Most postings carry
    a URL we cannot confidently classify, which is ``UNKNOWN``. ``NO_ROUTE``
    asserts something stronger: the posting was found to be informational
    only. Collapsing them would overstate certainty in both directions.
    """

    UNKNOWN = 0
    NO_ROUTE = 1
    JOB_POSTING_ONLY = 2
    EXTERNAL_APPLICATION = 3
    FREELANCE_PLATFORM = 4
    EMAIL_APPLICATION = 5
    RECRUITER_CONTACT = 6
    COMPANY_CAREERS = 7
    OFFICIAL_APPLICATION = 8


class RouteConfidence(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


#: Only these routes let a candidate act without leaving a public page first.
#: An ``EXTERNAL_APPLICATION`` may sit behind a login or CAPTCHA, and this
#: module never fetches it to find out, so it is not claimed to be actionable.
#:
#: ``RECRUITER_CONTACT`` is deliberately excluded. A LinkedIn profile proves a
#: contact surface exists; it does not prove the person is the hiring contact
#: for this specific job. It can be promoted only once stronger provenance
#: exists, so by default it is information rather than an instruction.
DIRECTLY_ACTIONABLE: frozenset[ApplicationRoute] = frozenset({
    ApplicationRoute.OFFICIAL_APPLICATION,
    ApplicationRoute.FREELANCE_PLATFORM,
    ApplicationRoute.EMAIL_APPLICATION,
})

#: Quality levels in the order the official-first policy prefers them.
#: Index is the tie-break rank when a job offers more than one route.
PRECEDENCE: tuple[ApplicationRoute, ...] = (
    ApplicationRoute.OFFICIAL_APPLICATION,
    ApplicationRoute.COMPANY_CAREERS,
    ApplicationRoute.FREELANCE_PLATFORM,
    ApplicationRoute.RECRUITER_CONTACT,
    ApplicationRoute.EMAIL_APPLICATION,
    ApplicationRoute.EXTERNAL_APPLICATION,
    ApplicationRoute.JOB_POSTING_ONLY,
    ApplicationRoute.NO_ROUTE,
    ApplicationRoute.UNKNOWN,
)


@dataclass(frozen=True)
class ApplicationRouteInfo:
    """One classified route. Immutable; nothing here performs an action."""

    route_type: ApplicationRoute = ApplicationRoute.UNKNOWN
    url: str | None = None
    source: str | None = None
    confidence: RouteConfidence = RouteConfidence.LOW
    is_official: bool = False
    requires_auth: bool | None = None
    directly_actionable: bool = False
    evidence: str = ""
    discovered_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.discovered_at is None:
            object.__setattr__(self, "discovered_at",
                               datetime.now(timezone.utc))

    @property
    def is_usable(self) -> bool:
        """True when a route exists and needs no authenticated detour."""
        return (self.directly_actionable
                and self.confidence is not RouteConfidence.LOW)


# --- URL validation ---------------------------------------------------------

_SCHEME = re.compile(r"^https?://", re.I)

#: Query parameters that carry campaign attribution rather than routing
#: information. Stripped for comparison only - the stored URL keeps whatever
#: the source gave us, so no evidence is lost.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gh_src", "gh_jid", "ref", "referrer", "source", "fbclid",
    "gclid", "trk", "trkCampaign", "originalSubdomain", "lipi",
})

#: Hosts that are never an official company application endpoint.
_SOCIAL_HOSTS = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "t.me", "telegram.me", "wa.me", "whatsapp.com",
    "reddit.com", "tiktok.com", "pinterest.com", "medium.com",
    "github.com", "gitlab.com", "bitbucket.org",
)

#: Path fragments that indicate an applicant-tracking system rather than a
#: company-owned careers page.
_ATS_MARKERS = (
    "/jobs/", "/careers/", "/vacancy", "/vacancies", "/opening",
    "/position", "/opportunity", "/requisition", "/job-search",
    "/search-jobs", "/jobsearch", "/talent-pool", "/apply",
)

#: Host fragments identifying well-known ATS / job-board vendors. Presence of
#: one means the endpoint is a real application form, but hosted by a third
#: party - which is NOT the same as company-owned.
_ATS_HOSTS = (
    "greenhouse.io", "lever.co", "myworkdayjobs.com", "workdayjobs.com",
    "ashbyhq.com", "smartrecruiters.com", "workable.com", "smartapply",
    "jobvite.com", "icims.com", "taleo.net", "successfactors.com",
    "bamboohr.com", "recruitee.com", "teamtailor.com", "personio.de",
    "personio.com", "jobstack.com", "jobs.jobvite.com",
)

#: Freelance marketplaces with a proposal flow, as opposed to a job board.
_FREELANCE_HOSTS = (
    "upwork.com", "freelancer.com", "contra.com", "toptal.com",
    "guru.com", "peopleperhour.com", "malt.com", "freelancer.de",
)


def _normalise_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _strip_tracking(url: str) -> str:
    """Remove campaign parameters. Pure local string work - never a request."""
    parts = urlsplit(url)
    kept = [pair for pair in parts.query.split("&") if pair
            and pair.split("=", 1)[0].lower() not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       "&".join(kept), parts.fragment))


def is_http_url(value: str | None) -> bool:
    """True when ``value`` parses as an absolute http(s) URL with a host."""
    if not value or not isinstance(value, str):
        return False
    if not _SCHEME.match(value.strip()):
        return False
    parts = urlsplit(value.strip())
    return bool(parts.hostname) and "." in parts.hostname


def is_insecure(value: str | None) -> bool:
    """True for a plain-http URL. Never upgraded - we do not fetch these."""
    return bool(value) and value.strip().lower().startswith("http://")


def is_social(url: str) -> bool:
    host = _normalise_host(url)
    return any(host == s or host.endswith("." + s) for s in _SOCIAL_HOSTS)


def is_ats(url: str) -> bool:
    """True when the URL sits on a known applicant-tracking vendor's domain.

    Host-based only, and deliberately so. An earlier version also matched path
    fragments, which mislabelled every job board as an ATS
    (``jobicy.com/jobs/154514-...``); a later one matched a ``/detail/<id>``
    shape, which would classify *any* domain with that path as an ATS. URL
    shape can never establish provenance. Custom-domain boards such as
    ``careers.datadoghq.com`` are handled instead by the caller asserting
    ``board_provenance``, because there the trust comes from the board the
    operator configured, not from the path.
    """
    host = _normalise_host(url)
    return any(h in host for h in _ATS_HOSTS)

def has_application_path(url: str) -> bool:
    """True when the path looks like a specific posting rather than a hub.

    Used only to separate "an official careers page" from "an official
    application endpoint" once the host has already been tied to the company.
    Never used to decide that a URL belongs to an ATS.
    """
    path = urlsplit(url).path.lower()
    return any(m in path for m in _ATS_MARKERS)


def is_freelance_platform(url: str) -> bool:
    host = _normalise_host(url)
    return any(host == h or host.endswith("." + h) for h in _FREELANCE_HOSTS)


def looks_like_homepage(url: str) -> bool:
    """A bare company homepage is not an application route.

    Deliberately shallow: ``/about`` counts as a homepage, ``/jobs/1234``
    does not. Anything ambiguous is left for the caller to treat as UNKNOWN
    rather than being force-fitted here.
    """
    path = urlsplit(url).path.strip("/")
    return path in {"", "about", "about-us", "contact", "en", "index.html"}


def domain_tokens(company: str | None) -> tuple[str, ...]:
    """Lower-case tokens from a company name, for host matching.

    "Acme Data Inc." -> ("acme", "data"). Used only to compare against a host
    the *source* supplied. Never used to construct a URL.
    """
    if not company:
        return ()
    noise = {"inc", "llc", "ltd", "gmbh", "corp", "corporation", "co",
             "company", "the", "and", "group", "plc", "sa", "bv", "ag"}
    parts = re.findall(r"[a-z0-9]+", company.lower())
    return tuple(p for p in parts if len(p) > 2 and p not in noise)


#: Path segments that mark a careers/ATS area. A segment on its own means a
#: hub ("/careers"); one followed by another segment means a specific posting.
_CAREERS_SEGMENTS = frozenset({
    "careers", "jobs", "job", "vacancy", "vacancies", "opening", "openings",
    "position", "positions", "opportunity", "opportunities", "requisition",
    "apply", "search-jobs", "jobsearch", "talent-pool",
})


def careers_marker(url: str) -> str | None:
    """First careers/ATS path segment, or ``None``.

    ``/careers`` -> ``"careers"``; ``/about`` -> ``None``; ``/`` -> ``None``.
    """
    for segment in urlsplit(url).path.strip("/").split("/"):
        if segment and segment.lower() in _CAREERS_SEGMENTS:
            return segment.lower()
    return None


def application_specificity(url: str) -> str | None:
    """A careers marker *plus* something naming one posting, or ``None``.

    This is the application-specific evidence that official provenance alone
    cannot supply: ``/jobs/123`` and ``/careers/data-engineer`` qualify, while
    ``/careers`` (a hub) and ``/about`` (not careers at all) do not.
    """
    segments = [s for s in urlsplit(url).path.strip("/").split("/") if s]
    for index, segment in enumerate(segments):
        if segment.lower() not in _CAREERS_SEGMENTS:
            continue
        if index + 1 < len(segments):
            return f"{segment.lower()}/{segments[index + 1]}"
        return None
    return None

def _host_matches_company(url: str, company: str | None) -> bool:
    """True when the host looks like the company's own domain.

    Conservative on purpose: requires the company's most distinctive token to
    appear in the registrable host. A mismatch yields UNKNOWN rather than a
    guess that the host belongs to somebody else.
    """
    tokens = domain_tokens(company)
    if not tokens:
        return False
    host = _normalise_host(url)
    bare = host.rsplit(".", 1)[0]
    parts = set(re.split(r"[.\-]", bare))
    return any(token in parts for token in tokens)


# --- email shape (never used as a route on its own) -------------------------

# Shape check only. An address inside a job description is untrusted text, so
# a match is recorded as evidence and is never promoted to a stored contact.
# The trailing guard deliberately excludes "." : an address at the end of a
# sentence ("...email hr@acme.com.") is the common case, and excluding the
# period made every one of them unmatchable. Any residual punctuation is
# trimmed below instead.
_EMAIL_SHAPE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}(?![\w-])"
)


def find_email_in_text(text: str | None) -> str | None:
    """Return an address *shaped* like an email, or ``None``.

    Used for classification and evidence only. Callers must not persist the
    result as a trusted contact: it came from attacker-controllable text.
    """
    if not text:
        return None
    for match in _EMAIL_SHAPE.finditer(text):
        candidate = match.group(0).rstrip(".")
        # Reject the common obfuscations that are really not addresses.
        if candidate.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
            continue
        return candidate
    return None


# --- classification ---------------------------------------------------------

#: Sources whose ``application_url`` is an ATS endpoint chosen by the
#: operator via ``GREENHOUSE_BOARDS`` / ``LEVER_BOARDS``. The board list is
#: configuration, not job text, so the endpoint's provenance is trustworthy -
#: though the company still owns it behind the ATS vendor's domain.
_ATS_SOURCE_NAMES = frozenset({"company_boards"})


def _route(route_type, url, source, confidence, is_official, *, evidence,
           requires_auth=None) -> ApplicationRouteInfo:
    return ApplicationRouteInfo(
        route_type=route_type,
        url=url,
        source=source,
        confidence=confidence,
        is_official=is_official,
        requires_auth=requires_auth,
        directly_actionable=route_type in DIRECTLY_ACTIONABLE,
        evidence=evidence,
    )


def _from_url(url: str, job: Job, *, source: str | None,
                board_provenance: bool = False) -> ApplicationRouteInfo | None:
    """Classify one candidate URL. ``None`` when it is not usable at all."""
    if not is_http_url(url):
        return None

    clean = _strip_tracking(url.strip())
    host = _normalise_host(clean)
    origin = f"{urlsplit(clean).scheme}://{host}"

    if is_social(clean):
        # A social profile is a contact surface at best, never an application.
        return _route(ApplicationRoute.RECRUITER_CONTACT, clean, source,
                      RouteConfidence.MEDIUM, is_official=False,
evidence=f"social profile host {host}")

    if is_freelance_platform(clean):
        return _route(ApplicationRoute.FREELANCE_PLATFORM, clean, source,
                      RouteConfidence.HIGH, is_official=False,
evidence=f"freelance marketplace host {host}")

    if _host_matches_company(clean, job.company):
        # Provenance: the host is the company's own domain.
        insecure = is_insecure(clean)
        suffix = " (http only, not upgraded)" if insecure else ""
        # Official provenance alone is not enough: a company's own domain can
        # host an about page, a press page, anything. OFFICIAL_APPLICATION also
        # requires application-specific evidence - a careers marker plus a
        # segment naming one posting ("/jobs/123", "/careers/data-engineer").
        specific = application_specificity(clean)
        if specific:
            return _route(ApplicationRoute.OFFICIAL_APPLICATION, clean, source,
                          RouteConfidence.HIGH, is_official=True,
                          evidence=f"official host {host} names a specific "
                                   f"posting ({specific}){suffix}")
        marker = careers_marker(clean)
        if marker:
            return _route(ApplicationRoute.COMPANY_CAREERS, origin, source,
                          RouteConfidence.HIGH, is_official=True,
                          evidence=f"official host {host}, but /{marker} is a "
                                   f"hubs page with no specific posting{suffix}")
        return _route(ApplicationRoute.UNKNOWN, None, source,
                      RouteConfidence.LOW, is_official=False,
                      evidence=f"official host {host}, but the path is neither "
                               f"an application nor a careers page{suffix}")

    if source in _ATS_SOURCE_NAMES or board_provenance:
        # Trusted provenance: this URL came from a board the operator
        # explicitly configured, whose vendor endpoint self-identifies the
        # company. That is strictly stronger evidence than any URL shape, and
        # it is what makes a custom-domain board such as
        # ``careers.datadoghq.com/detail/8144607`` recognisable without
        # guessing that ``datadoghq`` means ``Datadog``.
        #
        # It is still EXTERNAL_APPLICATION and never OFFICIAL_APPLICATION:
        # we know the board is legitimate, but nothing here ties the host to
        # the company, so company ownership is not claimed.
        return _route(ApplicationRoute.EXTERNAL_APPLICATION, clean, source,
                      RouteConfidence.HIGH, is_official=False,
                      evidence="application URL from an operator-configured "
                               "board, verified by the ATS vendor endpoint")

    if is_ats(clean):
        return _route(ApplicationRoute.EXTERNAL_APPLICATION, clean, source,
                      RouteConfidence.MEDIUM, is_official=False,
                      evidence=f"ATS-shaped URL on host {host}")

    if looks_like_homepage(clean):
        return _route(ApplicationRoute.COMPANY_CAREERS, origin, source,
                      RouteConfidence.LOW, is_official=False,
                      evidence=f"host {host} serves only a top-level page")

    return _route(ApplicationRoute.JOB_POSTING_ONLY, clean, source,
                  RouteConfidence.MEDIUM, is_official=False,
                  evidence=f"host {host} is a job board posting page")


def classify_route(job: Job, *,
                  board_provenance: bool = False) -> ApplicationRouteInfo:
    """Classify the best available application route for ``job``.

    Official-first policy: an official company endpoint always outranks a
    careers page, which outranks a marketplace, which outranks an external
    ATS, which outranks a board posting. Nothing is invented - when no route
    can be established the result is ``UNKNOWN`` with the reason recorded.

    ``board_provenance`` asserts that ``job.application_url`` was supplied by a
    board the operator explicitly configured, whose vendor endpoint
    self-identifies the owning company. It applies to ``application_url``
    only - never to ``job.url``, which is a posting page of unknown origin. It is an *input*, deliberately not a
    property of the result: ``ApplicationRouteInfo`` stays unchanged, and the
    caller must say where the URL came from rather than letting the classifier
    guess from the URL. Defaults to ``False`` so every existing caller keeps
    the URL-only behaviour.
    """
    candidates: list[ApplicationRouteInfo] = []

    # 1. A recruiter the source named explicitly outranks an inferred URL.
    if job.recruiter_url and is_http_url(job.recruiter_url):
        candidates.append(_route(
            ApplicationRoute.RECRUITER_CONTACT,
            _strip_tracking(job.recruiter_url.strip()), job.source,
            RouteConfidence.HIGH,
            is_official=bool(job.recruiter_name),
            evidence="source supplied a recruiter URL",
        ))

    # 2. The source's own application field, when it supplied one.
    if job.application_url and is_http_url(job.application_url):
        classified = _from_url(job.application_url, job, source=job.source,
                                board_provenance=board_provenance)
        if classified is not None:
            candidates.append(classified)

    # 3. Only then the posting URL, which is often just a board page.
    if job.url and is_http_url(job.url):
        classified = _from_url(job.url, job, source=job.source)
        if classified is not None:
            candidates.append(classified)

    if candidates:
        best = min(candidates, key=lambda c: PRECEDENCE.index(c.route_type))
        return best

    # An address in the description is untrusted text: recorded as evidence so
    # a human can see it exists, but never returned as a usable route.
    found = find_email_in_text(job.description)
    if found:
        return _route(
            ApplicationRoute.UNKNOWN, None, job.source, RouteConfidence.LOW,
            is_official=False,
            evidence=f"description mentions an email-shaped string "
                     f"({found.split('@')[1] if '@' in found else '?'}); "
                     f"untrusted text, not promoted to a route",
        )

    if job.url:
        return _route(ApplicationRoute.UNKNOWN, None, job.source,
                      RouteConfidence.LOW, is_official=False,
                      evidence=f"URL present but not usable: {job.url[:120]}")

    return _route(ApplicationRoute.UNKNOWN, None, job.source,
                  RouteConfidence.LOW, is_official=False,
                  evidence="no URL, no application_url and no recruiter_url")


__all__ = [
    "ApplicationRoute",
    "ApplicationRouteInfo",
    "RouteConfidence",
    "PRECEDENCE",
    "DIRECTLY_ACTIONABLE",
    "classify_route",
    "find_email_in_text",
    "is_http_url",
    "is_insecure",
    "is_social",
    "is_ats",
    "has_application_path",
    "careers_marker",
    "application_specificity",
    "is_freelance_platform",
    "looks_like_homepage",
    "domain_tokens",
]
