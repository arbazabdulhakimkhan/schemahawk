"""Normalized eligibility tests (Phase 2B).

The governing rule is *absence of evidence = UNKNOWN*. Most of this file
exists to prove that nothing is inferred: not worldwide from "remote", not
citizenship from a country, not authorization from a location.
"""
from __future__ import annotations

import pytest

from schemahawk.eligibility import (
    EligibilityProfile,
    LocationScope,
    WorkAuthorization,
    assess,
    timezone_constraint,
)
from schemahawk.models import (
    CONTRACT,
    CONTRACT_TYPES,
    CONTRACT_TYPE_ALIASES,
    FREELANCE,
    FULL_TIME,
    INTERNSHIP,
    PART_TIME,
    PERMANENT,
    TEMPORARY,
    Job,
    EligibilityStatus,
    normalize_contract_type,
)
from schemahawk.quality import classify

from conftest import make_job

# --- timezone labelling --------------------------------------------------
# The zone may be written before or after "hours", spelled out or abbreviated.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Must work during EST business hours.", "EST"),
        ("Hours are 9-5 EST.", "EST"),
        ("Working hours are 9-5 Central Time.", "Central Time"),
        ("Working hours must overlap with US Eastern Time.", "Eastern Time"),
        ("UTC+2 timezone, 3 days onsite.", "UTC+2"),
        ("Needs at least 4 hours of overlap with the EMEA team.", "overlapping hours"),
    ],
)
def test_timezone_constraint_names_the_zone(text, expected):
    assert timezone_constraint(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Flexible hours, any timezone.",
        "We offer standard business hours.",
        "Skills overlap between teams matters.",
        "40 hours per week with excellent benefits.",
        "Remote role, work from anywhere.",
        "Any timezone is fine.",
        "",
    ],
)
def test_timezone_constraint_never_invents_a_zone(text):
    assert timezone_constraint(text) is None


def test_timezone_never_implies_a_country_for_scope():
    """EST must not turn a timezone constraint into a country restriction."""
    p = assess(make_job(description="Must work during EST business hours."))
    assert p.scope is LocationScope.TIMEZONE_RESTRICTED
    assert p.countries == ()
    assert p.regions == ()


# --- classify() wiring --------------------------------------------------
def test_classify_does_not_call_a_bare_remote_job_eligible():
    """Regression: remote alone used to be enough to claim ELIGIBLE.

    Claiming eligibility from evidence that does not exist is the exact failure
    the two-axis model exists to prevent, so a remote posting with no stated
    scope must land on UNKNOWN.
    """
    job = make_job(remote=True)
    classify(job)
    assert job.eligibility_status == EligibilityStatus.UNKNOWN
    assert job.location_scope == LocationScope.UNKNOWN.name


@pytest.mark.parametrize(
    "description",
    [
        "EU candidates only.",
        "Must be authorized to work in Germany.",
        "US citizens only.",
    ],
)
def test_classify_detects_restrictions_the_old_patterns_missed(description):
    """These all silently evaluated to UNKNOWN under the old nine-country set."""
    job = make_job(description=description)
    classify(job)
    assert job.eligibility_status == EligibilityStatus.RESTRICTED
    assert job.work_authorization is not None


def test_classify_persists_both_axes_on_the_job():
    job = make_job(description="US citizens only.")
    classify(job)
    assert job.location_scope == LocationScope.COUNTRY_RESTRICTED.name
    assert job.work_authorization_level == WorkAuthorization.CITIZENSHIP_REQUIRED.name


def job(description: str = "", *, title="Data Engineer", location=None) -> Job:
    return Job(source="t", title=title, company="C",
               description=description, location=location)


# --- the six required distinctions ----------------------------------------

def test_us_only_is_country_restricted_without_authorization():
    p = assess(job("US only."))
    assert p.scope is LocationScope.COUNTRY_RESTRICTED
    assert p.countries == ("US",)
    assert p.work_authorization is WorkAuthorization.UNKNOWN


def test_us_citizens_only_is_citizenship():
    p = assess(job("US citizens only."))
    assert p.work_authorization is WorkAuthorization.CITIZENSHIP_REQUIRED


def test_must_be_authorized_is_authorization_not_citizenship():
    p = assess(job("You must be authorized to work in the US."))
    assert p.work_authorization is WorkAuthorization.WORK_AUTHORIZATION_REQUIRED
    assert p.work_authorization is not WorkAuthorization.CITIZENSHIP_REQUIRED
    assert p.scope is LocationScope.COUNTRY_RESTRICTED


def test_worldwide_requires_explicit_wording():
    p = assess(job("This role is open worldwide."))
    assert p.scope is LocationScope.WORLDWIDE


def test_remote_alone_is_not_worldwide():
    p = assess(job("Fully remote position."))
    assert p.scope is LocationScope.UNKNOWN
    assert p.remote_mode == "remote"


def test_no_eligibility_information_stays_unknown():
    p = assess(job("We need a data engineer."))
    assert p.scope is LocationScope.UNKNOWN
    assert p.work_authorization is WorkAuthorization.UNKNOWN
    assert p.is_restricted is False
    assert p.restriction_reason is None


# --- never infer ------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Fully remote position.",
    "Remote-first company.",
    "Work from home.",
    "Distributed team across the globe.",
])
def test_remote_wording_never_becomes_worldwide(text):
    assert assess(job(text)).scope is not LocationScope.WORLDWIDE


def test_location_never_implies_work_authorization():
    """A Brazil-based role does not make the worker authorized in Brazil."""
    p = assess(job("Data Engineer", location="Brazil"))
    assert p.work_authorization is WorkAuthorization.UNKNOWN


def test_timezone_never_implies_a_country():
    """EST must not be read as United States residence."""
    p = assess(job("You must overlap with US Eastern hours."))
    assert p.scope is LocationScope.TIMEZONE_RESTRICTED
    assert "United States" not in p.countries


def test_silence_never_becomes_open():
    assert assess(job("Great team, flexible hours.")).work_authorization \
        is not WorkAuthorization.OPEN


# --- work authorization values ---------------------------------------------

def test_sponsorship_available_is_open():
    p = assess(job("Visa sponsorship available."))
    assert p.work_authorization is WorkAuthorization.OPEN


def test_no_sponsorship_means_authorization_required():
    p = assess(job("We cannot offer visa sponsorship."))
    assert p.work_authorization is WorkAuthorization.WORK_AUTHORIZATION_REQUIRED


def test_clearance_is_its_own_verdict():
    p = assess(job("Security clearance required."))
    assert p.work_authorization is WorkAuthorization.CLEARANCE_REQUIRED


# --- regions vs countries ---------------------------------------------------

def test_eu_candidates_only_is_region_restricted():
    p = assess(job("EU candidates only."))
    assert p.scope is LocationScope.REGION_RESTRICTED
    assert p.regions == ("EU",)
    assert p.countries == ()


def test_remote_plus_eu_only_is_not_worldwide():
    p = assess(job("Remote role. EU candidates only."))
    assert p.scope is LocationScope.REGION_RESTRICTED
    assert p.remote_mode == "remote"


def test_authorized_in_germany_is_country_restricted():
    p = assess(job("Applicants must be authorized to work in Germany."))
    assert p.scope is LocationScope.COUNTRY_RESTRICTED
    assert p.countries == ("DE",)
    assert p.work_authorization is WorkAuthorization.WORK_AUTHORIZATION_REQUIRED


# --- remote mode -----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Fully remote position.", "remote"),
    ("Hybrid position, 2 days in office.", "hybrid"),
    ("This is an on-site role.", "onsite"),
])
def test_remote_mode_is_recorded_separately(text, expected):
    assert assess(job(text)).remote_mode == expected


# --- evidence ---------------------------------------------------------------

def test_evidence_records_the_source_phrase():
    p = assess(job("US citizens only."))
    assert p.evidence
    assert any("citizen" in e.lower() for e in p.evidence)


def test_restriction_reason_is_human_readable():
    assert "authorized" in (assess(
        job("Must be authorized to work in Germany.")).restriction_reason or "")


# --- defaults ---------------------------------------------------------------

def test_default_profile_is_unknown_everywhere():
    p = EligibilityProfile()
    assert p.scope is LocationScope.UNKNOWN
    assert p.work_authorization is WorkAuthorization.UNKNOWN
    assert p.is_restricted is False


def test_enum_ordering_keeps_unknown_first():
    """UNKNOWN must be the falsy default so it cannot be mistaken for a value."""
    assert int(LocationScope.UNKNOWN) == 0
    assert int(WorkAuthorization.UNKNOWN) == 0


# --- contract-type normalization ------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("full_time", FULL_TIME),
    ("Full-Time", FULL_TIME),
    ("part_time", PART_TIME),
    ("Part-Time", PART_TIME),
    ("freelance", FREELANCE),
    ("independent contractor", FREELANCE),
    ("freelancer", FREELANCE),
    ("contract", CONTRACT),
    ("contractor", CONTRACT),
    ("permanent", PERMANENT),
    ("internship", INTERNSHIP),
    ("intern", INTERNSHIP),
    ("temporary", TEMPORARY),
    ("temp", TEMPORARY),
])
def test_board_labels_map_to_the_normalized_vocabulary(raw, expected):
    assert normalize_contract_type(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "mystery", 42])
def test_unknown_contract_label_is_never_guessed(raw):
    """Silence must not become PERMANENT or any other positive value."""
    assert normalize_contract_type(raw) is None


def test_freelance_is_no_longer_collapsed_into_contract():
    """The distinction V1 lost: both exist and differ."""
    assert normalize_contract_type("freelance") == FREELANCE
    assert normalize_contract_type("contract") == CONTRACT
    assert normalize_contract_type("freelance") != normalize_contract_type("contract")


def test_full_time_is_not_upgraded_to_permanent():
    """'Permanent' is an employment relationship, not an hours commitment."""
    assert normalize_contract_type("full_time") == FULL_TIME
    assert normalize_contract_type("full_time") != PERMANENT


def test_every_normalized_value_is_declared():
    assert set(CONTRACT_TYPE_ALIASES.values()) <= set(CONTRACT_TYPES)


# --- compound labels (regression) --------------------------------------
# The V1 adapters used substring matching, so "temporary contract" and
# "part-time contract" both became CONTRACT. An exact-key-only lookup silently
# returned None for them and threw away information the pipeline used to store.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("temporary contract", TEMPORARY),
        ("part-time contract", PART_TIME),
        ("senior contract", CONTRACT),
        ("contract (freelance)", FREELANCE),
        ("freelance contract", FREELANCE),
        ("Independent Contractor", FREELANCE),
        ("temp position", TEMPORARY),
        ("fixed-term contract", CONTRACT),
    ],
)
def test_compound_labels_are_not_lost(raw, expected):
    """A compound label must never degrade to None.

    Where the label names a more specific concept than "contract", the more
    specific one wins: a part-time contract is PART_TIME, not CONTRACT.
    """
    assert normalize_contract_type(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "contemporary role",       # must not match the "temp" fragment
        "permanently remote",      # must not match the "perm" fragment
        "internationally focused", # must not match the "intern" fragment
        "mystery",
    ],
)
def test_compound_fallback_uses_token_boundaries(raw):
    """Substring matches would turn these into positive contract types."""
    assert normalize_contract_type(raw) is None


@pytest.mark.parametrize(
    "label",
    ["temporary contract", "part-time contract", "senior contract", "freelance"],
)
def test_compound_labels_survive_the_jobicy_adapter(label, settings):
    """End-to-end: the adapter must store a value, not None."""
    from schemahawk.sources.jobicy import JobicySource, _to_job

    job = _to_job(JobicySource(settings), {"jobId": "x", "jobTitle": "Data Engineer",
                                          "url": "u", "jobType": [label]})
    assert job is not None
    assert job.contract_type is not None
    assert job.contract_type in CONTRACT_TYPES

