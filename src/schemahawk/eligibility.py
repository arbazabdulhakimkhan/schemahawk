"""Normalized eligibility and requirement extraction (Phase 2B, deterministic).

Two independent axes, never collapsed into one:

- **Location scope** - where the employer will hire.
- **Work authorization** - what status the worker must already hold.

They are separate because "US citizens only" (citizenship) and "must be
authorized to work in the US" (existing authorization) are different
constraints, and conflating them loses that distinction.

Security rule, enforced everywhere in this module:

    **absence of evidence = UNKNOWN**

Nothing here infers a value from silence. In particular:

- "remote" is **not** "worldwide" - measured on 116 live postings, 88 mention
  remote but only 17 say worldwide, so 74 would be wrongly upgraded;
- a location is **not** a work authorization (a Brazil-based role does not make
  the worker authorized to work in Brazil);
- a timezone is **not** a country (``EST`` does not imply United States);
- silence is **not** "no requirement" - no education, language or authorization
  requirement is ever inferred from a posting that does not mention one.

Every verdict carries the text that produced it in ``evidence``, so a stored
decision can always be traced back to a phrase in the posting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from .models import Job


class LocationScope(IntEnum):
    """Where the employer is willing to hire. ``UNKNOWN`` is the default."""

    UNKNOWN = 0
    WORLDWIDE = 1           # only when stated outright
    COUNTRY_RESTRICTED = 2  # one or more named countries
    REGION_RESTRICTED = 3   # a named multi-country region
    TIMEZONE_RESTRICTED = 4  # constrained by hours rather than place


class WorkAuthorization(IntEnum):
    """What status the worker must already hold. Never inferred from location."""

    UNKNOWN = 0                 # the posting says nothing either way
    NOT_STATED = 1              # alias of UNKNOWN, kept for readable reporting
    OPEN = 2                    # employer states sponsorship is available
    WORK_AUTHORIZATION_REQUIRED = 3   # "must be authorized to work in X"
    CITIZENSHIP_REQUIRED = 4    # "US citizens only"
    CLEARANCE_REQUIRED = 5      # "security clearance required"


# --- vocabulary -------------------------------------------------------------

# Countries named in postings, normalized to a stable key.
_COUNTRIES: dict[str, str] = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states": "US",
    "united states of america": "US", "america": "US",
    "uk": "UK", "united kingdom": "UK", "great britain": "UK", "britain": "UK",
    "england": "UK", "scotland": "UK", "wales": "UK",
    "ireland": "IE", "germany": "DE", "france": "FR", "spain": "ES",
    "italy": "IT", "netherlands": "NL", "poland": "PL", "portugal": "PT",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "australia": "AU", "new zealand": "NZ", "canada": "CA", "india": "IN",
    "singapore": "SG", "japan": "JP", "brazil": "BR", "mexico": "MX",
    "south africa": "ZA", "israel": "IL", "switzerland": "CH",
}

# Regions are separate from countries: "EU candidates only" is not a country.
_REGIONS: dict[str, str] = {
    "eu": "EU", "european union": "EU", "europe": "EU",
    "eea": "EEA", "emea": "EMEA", "apac": "APAC", "asia pacific": "APAC",
    "latam": "LATAM", "latin america": "LATAM",
    "nordics": "NORDICS", "benelux": "BENELUX", "dach": "DACH",
}

_COUNTRY_ALT = "|".join(sorted((re.escape(k) for k in _COUNTRIES), key=len, reverse=True))
_REGION_ALT = "|".join(sorted((re.escape(k) for k in _REGIONS), key=len, reverse=True))
_PLACE_ALT = rf"(?:{_COUNTRY_ALT}|{_REGION_ALT})"

# --- patterns ---------------------------------------------------------------

# "worldwide", "anywhere in the world", "globally", "any location". Nothing
# looser: "remote" alone must never reach this.
_WORLDWIDE = re.compile(
    r"\b(?:world\s*-?\s*wide|globally|anywhere\s+in\s+the\s+world|"
    r"any\s+location|any\s+country|no\s+location\s+restrictions?)\b",
    re.I,
)

# "... citizens only" -> citizenship, strictly stronger than authorization.
_CITIZENSHIP = re.compile(
    rf"\b(?:{_COUNTRY_ALT})\s+(?:citizens|nationals)\s+only\b"
    r"|\bcitizenship\s+(?:is\s+)?required\b"
    r"|\bmust\s+be\s+(?:a\s+)?citizen\b",
    re.I,
)

# "authorized to work in X" -> existing authorization, NOT citizenship.
_AUTHORIZED = re.compile(
    r"\b(?:must\s+be\s+(?:legally\s+)?(?:authorized|authorised|eligible)\s+to\s+work\b"
    r"|\bhave\s+the\s+(?:right|legal\s+right)\s+to\s+work\b"
    r"|\bwork\s+authorization\s+(?:is\s+)?required\b"
    r"|\brequire[sd]?\s+(?:work\s+)?(?:authorization|authorisation)\b)",
    re.I,
)
# The place half, kept separate so a bare "authorized" with no place stays useful.
_AUTHORIZED_PLACE = re.compile(
    rf"\bto\s+work\s+(?:legally\s+)?in\s+(?:the\s+)?{_PLACE_ALT}\b", re.I)

# The employer volunteering sponsorship is the only positive "open" signal.
_OPEN = re.compile(
    r"\b(?:sponsorship\s+(?:is\s+)?available|we\s+(?:can|will)\s+sponsor|"
    r"visa\s+sponsorship\s+(?:is\s+)?available|no\s+visa\s+required)\b", re.I)
_NO_SPONSORSHIP = re.compile(
    r"\bno\s+(?:visa\s+)?sponsorship\b"
    r"|\bunable\s+to\s+sponsor\b"
    # "we cannot offer sponsorship" and "sponsorship is not available" are the
    # common ways a posting declines sponsorship. Missing them would report a
    # real restriction as UNKNOWN and lose it entirely.
    r"|\bcannot\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship\b"
    r"|\b(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:not\s+available|unavailable)\b"
    r"|\bdo\s+not\s+(?:offer|provide)\s+sponsorship\b",
    re.I,
)
_CLEARANCE = re.compile(r"\bsecurity\s+clearance\b|\bclearance\s+required\b", re.I)

# "US only" / "Germany-based" / "residents of the EU" / "EU candidates only" ->
# a place restriction. Real postings insert a noun ("EU *candidates* only",
# "US *residents* only"), so an optional noun is allowed between place and
# "only" - without it, "EU candidates only" would be missed entirely.
_PLACE_ONLY = re.compile(
    rf"\b{_PLACE_ALT}\s+(?:[a-z]{{1,12}}\s+)?only\b"
    rf"|\bonly\s+(?:the\s+)?{_PLACE_ALT}\b"
    rf"|\b(?:based|located)\s+in\s+(?:the\s+)?{_PLACE_ALT}\b"
    rf"|\bresidents?\s+of\s+(?:the\s+)?{_PLACE_ALT}\b",
    re.I,
)

# An hours requirement, never a place requirement. "EST" must not imply US.
# The named zone and the word "hours" must be near each other but may appear in
# either order: postings write both "EST business hours" and "Hours are 9-5 EST".
# A bare "business hours" is NOT a timezone signal and is deliberately absent.
_TZ_NAME = (
    r"UTC\s*[+\u2212-]\s*\d{1,2}"
    r"|(?:UTC|GMT)"
    r"|(?:Eastern|Central|Mountain|Pacific)\s+(?:Standard\s+|Daylight\s+)?Time"
    r"|(?:EST|EDT|CET|CEST|PST|PDT|MST|MDT|AEST|AEDT|IST)"
)
_TIMEZONE = re.compile(
    # overlap <-> hours, in either order ("4 hours of overlap with the EMEA team")
    r"\boverlap\w*[^.]{0,60}\bhours?\b"
    r"|\bhours?\b[^.]{0,40}\boverlap\w*\b"
    # named zone <-> hours, in either order ("EST business hours", "hours 9-5 EST")
    r"|\b(?:" + _TZ_NAME + r")\b[^.]{0,30}\bhours?\b"
    r"|\bhours?\b[^.]{0,40}\b(?:" + _TZ_NAME + r")\b"
    # an explicit UTC offset is unambiguous on its own
    r"|\bUTC\s*[+\u2212-]\s*\d{1,2}\b",
    re.I,
)

# Remote is NOT a scope signal; it is recorded separately so that "remote" can
# never be mistaken for "worldwide".
_REMOTE = re.compile(
    r"\b(?:fully?\s+remote|remote|work\s+from\s+home|anywhere|distributed)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_ONSITE = re.compile(
    r"\b(?:on[- ]site|in[- ]office|onsite|in[- ]person)\b"
    # A hybrid posting still says "2 days in office", so this phrase must not
    # outrank an explicit "hybrid" elsewhere in the same posting.
    r"|\b\d+\s+days?\s+(?:in\s+office|on[- ]site)\b",
    re.I,
)


# --- result -----------------------------------------------------------------

@dataclass(frozen=True)
class EligibilityProfile:
    """Normalized, explainable eligibility for one job.

    Every field is optional; ``UNKNOWN`` means the posting did not say. The
    ``evidence`` tuple holds the exact phrases that produced each verdict, so a
    stored decision is always traceable to source text.
    """

    scope: LocationScope = LocationScope.UNKNOWN
    work_authorization: WorkAuthorization = WorkAuthorization.UNKNOWN
    remote_mode: str | None = None          # "remote" | "hybrid" | "onsite" | None
    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def is_worldwide(self) -> bool:
        return self.scope is LocationScope.WORLDWIDE

    @property
    def is_restricted(self) -> bool:
        """True only when a restriction was actually stated."""
        return (self.scope in (LocationScope.COUNTRY_RESTRICTED,
                               LocationScope.REGION_RESTRICTED,
                               LocationScope.TIMEZONE_RESTRICTED)
                or self.work_authorization in (
                    WorkAuthorization.WORK_AUTHORIZATION_REQUIRED,
                    WorkAuthorization.CITIZENSHIP_REQUIRED,
                    WorkAuthorization.CLEARANCE_REQUIRED))

    @property
    def restriction_reason(self) -> str | None:
        """A short human-readable reason, or ``None`` when nothing was stated."""
        if self.work_authorization is WorkAuthorization.CITIZENSHIP_REQUIRED:
            return "citizenship required"
        if self.work_authorization is WorkAuthorization.CLEARANCE_REQUIRED:
            return "security clearance required"
        if self.work_authorization is WorkAuthorization.WORK_AUTHORIZATION_REQUIRED:
            where = ", ".join(self.countries or self.regions) or "the stated country"
            return f"must be authorized to work in {where}"
        if self.scope is LocationScope.COUNTRY_RESTRICTED:
            return f"restricted to {', '.join(self.countries)}"
        if self.scope is LocationScope.REGION_RESTRICTED:
            return f"restricted to {', '.join(self.regions)}"
        if self.scope is LocationScope.TIMEZONE_RESTRICTED:
            return "timezone-restricted working hours"
        return None


def _norm_place(word: str) -> tuple[str, str | None]:
    """Map a matched place token to ``("country"|"region", key)``."""
    key = word.lower().strip().rstrip(",.")
    if key in _COUNTRIES:
        return "country", _COUNTRIES[key]
    if key in _REGIONS:
        return "region", _REGIONS[key]
    return "", None


def _named_places(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Countries and regions named anywhere in ``text``."""
    countries: list[str] = []
    regions: list[str] = []
    # Alternation is ordered longest-first by the caller, so "united kingdom"
    # wins over "uk" and "european union" over "eu".
    for token in re.findall(rf"\b(?:{_COUNTRY_ALT}|{_REGION_ALT})\b", text, re.I):
        kind, key = _norm_place(token)
        if kind == "country" and key not in countries:
            countries.append(key)
        elif kind == "region" and key not in regions:
            regions.append(key)
    return tuple(countries), tuple(regions)


def _remote_mode(text: str) -> str | None:
    """Working arrangement only - never a geographic scope.

    An explicit "hybrid" outranks "2 days in office", because a hybrid posting
    legitimately contains both. Only a posting with no hybrid wording and a
    bare office phrase is treated as onsite.
    """
    if _HYBRID.search(text):
        return "hybrid"
    if _ONSITE.search(text):
        return "onsite"
    if _REMOTE.search(text):
        return "remote"
    return None


def assess(job: Job) -> EligibilityProfile:
    """Deterministically derive eligibility from a job posting.

    Order matters: authorization verdicts are read before place restrictions so
    that "US citizens only" reports *citizenship* rather than being flattened
    into a generic place restriction. ``remote`` never contributes to scope.
    """
    text = " \n ".join(part for part in (job.title, job.location, job.description)
                       if part)
    evidence: list[str] = []

    def grab(pattern: re.Pattern[str]) -> re.Match[str] | None:
        match = pattern.search(text)
        if match:
            evidence.append(match.group(0).strip())
        return match

    # --- work authorization (strongest signal first) ---
    authz = WorkAuthorization.UNKNOWN
    if grab(_CITIZENSHIP):
        authz = WorkAuthorization.CITIZENSHIP_REQUIRED
    elif grab(_CLEARANCE):
        authz = WorkAuthorization.CLEARANCE_REQUIRED
    elif grab(_AUTHORIZED) or grab(_NO_SPONSORSHIP):
        # "no sponsorship available" means an existing right is required.
        authz = WorkAuthorization.WORK_AUTHORIZATION_REQUIRED
    elif grab(_OPEN):
        authz = WorkAuthorization.OPEN

    countries, regions = _named_places(text)

    # --- location scope ---
    scope = LocationScope.UNKNOWN
    marker = grab(_WORLDWIDE)
    if marker is not None:
        scope = LocationScope.WORLDWIDE
    elif _PLACE_ONLY.search(text) or _AUTHORIZED_PLACE.search(text):
        if regions and not countries:
            scope = LocationScope.REGION_RESTRICTED
        elif countries:
            scope = LocationScope.COUNTRY_RESTRICTED
        else:
            scope = LocationScope.REGION_RESTRICTED
    else:
        tz = grab(_TIMEZONE)
        if tz is not None:
            scope = LocationScope.TIMEZONE_RESTRICTED

    return EligibilityProfile(
        scope=scope,
        work_authorization=authz,
        remote_mode=_remote_mode(text),
        countries=countries,
        regions=regions,
        evidence=tuple(dict.fromkeys(evidence)),   # de-duplicated, order kept
    )


def timezone_constraint(text: str) -> str | None:
    """The specific timezone named by ``text``, or ``None`` when none is stated.

    Separated from :func:`assess` so ``matching`` can report *which* zone was
    required rather than an arbitrary sentence fragment. Returns the narrowest
    useful label: an explicit ``UTC+2`` offset, a spelled-out zone
    (``Eastern Time``), an abbreviation (``EST``), or ``"overlapping hours"``
    when the posting constrains hours without naming a zone.
    """
    if not text:
        return None
    hit = _TIMEZONE.search(text)
    if hit is None:
        return None

    # The match may be the "overlap ... hours" branch, which does not itself
    # contain a zone name ("hours must overlap with US Eastern Time"). Widen to
    # the surrounding sentence so the named zone is still reported.
    start, end = hit.span()
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right = text.find(".", end)
    right = len(text) if right == -1 else right + 1
    evidence = text[left:right]
    evidence = evidence.strip() or hit.group(0)

    offset = re.search(r"\bUTC\s*[+\u2212-]\s*\d{1,2}\b", evidence, re.I)
    if offset:
        return re.sub(r"\s+", "", offset.group(0)).replace("\u2212", "-").upper()
    spelled = re.search(r"\b(?:Eastern|Central|Mountain|Pacific)\s+Time\b", evidence, re.I)
    if spelled:
        return spelled.group(0).title()
    abbrev = re.search(
        r"\b(?:EST|EDT|CET|CEST|PST|PDT|MST|MDT|AEST|AEDT|IST|UTC|GMT)\b", evidence
    )
    if abbrev:
        return abbrev.group(0).upper()
    return "overlapping hours"


__all__ = [
    "LocationScope",
    "WorkAuthorization",
    "EligibilityProfile",
    "assess",
    "timezone_constraint",
]
