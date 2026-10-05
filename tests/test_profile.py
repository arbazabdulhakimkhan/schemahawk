"""Candidate profile loading, validation and privacy tests.

Two themes run through this file:

1. **Never inferred.** Absent values stay ``None``/``[]``. A profile is the only
   source of candidate facts; the loader must not invent any.
2. **Never observable.** ``summary()`` must expose counts only, so a private
   profile cannot leak through logs, reports or CI artifacts.
"""
from __future__ import annotations

import pytest

from schemahawk.config import Settings
from schemahawk.profile import (
    PROFILE_VERSION,
    SENIORITY_BANDS,
    SOURCE_DEFAULT,
    SOURCE_EXAMPLE,
    SOURCE_FILE,
    ProfileError,
    default_profile,
    example_profile,
    load_private_profile,
    load_profile,
    load_profile_or_default,
    parse_profile,
    profile_from_settings,
)

MINIMAL = "version: 1\n"


def write(tmp_path, text, name="profile.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- defaults ---------------------------------------------------------------

def test_default_profile_is_empty_and_populated_false():
    profile = default_profile()
    assert profile.source == SOURCE_DEFAULT
    assert profile.skills == ()
    assert profile.total_years_experience is None
    assert profile.is_populated is False


def test_missing_profile_file_falls_back_to_empty_default(tmp_path):
    profile = load_profile_or_default(tmp_path / "absent.yaml")
    assert profile.source == SOURCE_DEFAULT
    assert profile.is_populated is False


def test_loading_a_missing_file_raises(tmp_path):
    with pytest.raises(ProfileError, match="no profile file"):
        load_profile(tmp_path / "absent.yaml")


# --- loading ----------------------------------------------------------------

def test_minimal_profile_loads(tmp_path):
    profile = load_profile(write(tmp_path, MINIMAL))
    assert profile.version == PROFILE_VERSION
    assert profile.source == "file"
    assert profile.skills == ()


def test_full_profile_is_parsed(tmp_path):
    profile = load_profile(write(tmp_path, """
version: 1
candidate:
  name: "Test Person"
  contact_email: "test@example.com"
total_years_experience: 8
seniority: "senior"
availability: "2 weeks notice"
timezone: "UTC+0"
work_authorization: "citizen"
skills:
  - name: "SQL"
    category: "database"
    proficiency: "advanced"
    years: 6
    last_used: "2025-06"
  - name: "Python"
preferred_contract_types: ["contract", "freelance"]
preferred_locations: ["Germany", "UK"]
remote_preference: "remote-only"
preferred_timezones: ["UTC+0"]
preferred_rate:
  currency: "EUR"
  min: 80
  max: 110
  period: "hour"
employment_history:
  - company: "Example Ltd"
    title: "Data Engineer"
projects:
  - name: "Example project"
certifications:
  - name: "Example cert"
education:
  - institution: "Example University"
languages:
  - name: "English"
    proficiency: "native"
"""))
    assert profile.name == "Test Person"
    assert profile.total_years_experience == 8
    assert profile.seniority == "senior"
    assert profile.availability == "2 weeks notice"
    assert profile.skill_names == ("SQL", "Python")
    assert profile.skills[0].proficiency == "advanced"
    assert profile.skills[0].years == 6
    assert profile.skills[0].last_used == "2025-06"
    assert profile.skills[0].category == "database"
    assert profile.skills[1].proficiency is None       # not inferred
    assert profile.skills[1].years is None
    assert profile.preferred_contract_types == ("contract", "freelance")
    assert profile.preferred_locations == ("Germany", "UK")
    assert profile.remote_preference == "remote-only"
    assert profile.preferred_rate["period"] == "hour"
    assert profile.employment_history[0]["company"] == "Example Ltd"
    assert profile.education[0]["institution"] == "Example University"
    assert profile.is_populated is True


def test_skill_may_be_a_bare_string():
    assert parse_profile({"skills": ["Python", "SQL"]}).skill_names == ("Python", "SQL")


def test_empty_skill_name_is_rejected():
    with pytest.raises(ProfileError, match="name"):
        parse_profile({"skills": [{"name": "   "}]})


def test_skill_without_name_is_rejected():
    with pytest.raises(ProfileError, match="name"):
        parse_profile({"skills": [{"proficiency": "advanced"}]})


# --- never inferred ---------------------------------------------------------

def test_absent_fields_stay_none_rather_than_being_guessed():
    profile = parse_profile({"version": 1, "skills": ["Python"]})
    assert profile.total_years_experience is None
    assert profile.seniority is None
    assert profile.availability is None
    assert profile.work_authorization is None
    assert profile.preferred_rate is None
    assert profile.employment_history == ()
    # A skill name alone must not conjure proficiency or years.
    assert profile.skills[0].proficiency is None
    assert profile.skills[0].years is None
    assert profile.skills[0].last_used is None


def test_zero_years_is_kept_rather_than_treated_as_missing():
    assert parse_profile({"total_years_experience": 0}).total_years_experience == 0


# --- validation -------------------------------------------------------------

def test_wrong_scalar_type_is_rejected():
    with pytest.raises(ProfileError, match="total_years_experience"):
        parse_profile({"total_years_experience": "eight"})


def test_negative_years_are_rejected():
    with pytest.raises(ProfileError, match="negative"):
        parse_profile({"skills": [{"name": "SQL", "years": -3}]})


def test_list_expected_given_string_is_rejected():
    with pytest.raises(ProfileError, match="preferred_locations"):
        parse_profile({"preferred_locations": "Germany"})


def test_mapping_expected_given_list_is_rejected():
    with pytest.raises(ProfileError, match="preferred_rate"):
        parse_profile({"preferred_rate": [80]})


def test_candidate_must_be_a_mapping():
    with pytest.raises(ProfileError, match="candidate"):
        parse_profile({"candidate": "Test Person"})


def test_non_mapping_document_is_rejected():
    with pytest.raises(ProfileError, match="mapping"):
        parse_profile(["not", "a", "mapping"])


def test_future_version_is_rejected():
    with pytest.raises(ProfileError, match="newer than supported"):
        parse_profile({"version": PROFILE_VERSION + 1})


def test_non_integer_version_is_rejected():
    with pytest.raises(ProfileError, match="version"):
        parse_profile({"version": "1"})

# --- malformed YAML and safety ---------------------------------------------

def test_malformed_yaml_raises_without_echoing_the_body(tmp_path):
    """Error messages must not print private profile content."""
    private = "secret-skill-name-should-not-appear"
    path = write(tmp_path, f"skills:\n  - name: {private}\n   bad indent: [unclosed\n")
    with pytest.raises(ProfileError) as excinfo:
        load_profile(path)
    assert "invalid YAML" in str(excinfo.value)
    assert private not in str(excinfo.value)


def test_hostile_yaml_tag_cannot_construct_objects(tmp_path):
    """safe_load must refuse arbitrary Python object construction."""
    path = write(tmp_path, 'profile: !!python/object/apply:os.system ["echo pwned"]\n')
    with pytest.raises(ProfileError, match="invalid YAML"):
        load_profile(path)


def test_directory_path_is_rejected(tmp_path):
    with pytest.raises(ProfileError, match="directory"):
        load_profile(tmp_path)


def test_empty_file_yields_empty_profile(tmp_path):
    profile = load_profile(write(tmp_path, ""))
    assert profile.is_populated is False


# --- privacy ----------------------------------------------------------------

def test_summary_exposes_counts_only(tmp_path):
    """The central privacy guarantee: no skill names in any reportable output."""
    profile = load_profile(write(tmp_path, """
candidate:
  name: "Private Name"
  contact_email: "private@example.com"
total_years_experience: 8
skills:
  - name: "Very Secret Skill"
preferred_rate:
  currency: "EUR"
  min: 80
employment_history:
  - company: "Very Secret Employer"
"""))
    rendered = " ".join(f"{k} {v}" for k, v in profile.summary().items())
    for secret in ("Very Secret Skill", "Private Name", "private@example.com",
                   "Very Secret Employer"):
        assert secret not in rendered


def test_summary_reports_years_but_not_rates(tmp_path):
    profile = load_profile(write(tmp_path, """
total_years_experience: 8
preferred_rate:
  currency: "EUR"
  min: 80
  max: 110
"""))
    summary = profile.summary()
    assert summary["years experience"] == "8"
    assert summary["rate"] == "set"
    # Assert against profile *content* values only. The `path` key is
    # documented filesystem metadata (not profile data) and can contain
    # digits from pytest's numbered tmp dirs (e.g. "pytest-80"), which
    # used to make this assertion fail intermittently.
    content = " ".join(v for k, v in summary.items() if k != "path")
    assert "80" not in content


def test_summary_marks_unspecified_values(tmp_path):
    summary = load_profile(write(tmp_path, MINIMAL)).summary()
    assert summary["years experience"] == "not specified"
    assert summary["seniority"] == "not specified"
    assert summary["rate"] == "not specified"


# --- committed example template ---------------------------------------------

def test_committed_example_template_is_valid():
    profile = example_profile()
    assert profile.source == SOURCE_EXAMPLE
    assert profile.version == PROFILE_VERSION
    assert profile.is_populated is True   # it demonstrates the fields


def test_example_template_contains_no_contact_details():
    """The committed template must stay generic and safe to publish."""
    assert example_profile().contact_email is None


# --- settings integration ---------------------------------------------------

def test_settings_expose_a_profile_path():
    assert Settings().candidate_profile_path == "config/profile.yaml"


def test_profile_from_settings_uses_the_configured_path(tmp_path):
    path = write(tmp_path, "skills:\n  - Python\n", name="custom.yaml")
    profile = profile_from_settings(Settings(candidate_profile_path=str(path)))
    assert profile.skill_names == ("Python",)


def test_profile_from_settings_defaults_when_absent(tmp_path):
    settings = Settings(candidate_profile_path=str(tmp_path / "nope.yaml"))
    assert profile_from_settings(settings).source == SOURCE_DEFAULT



# --- forward compatibility --------------------------------------------------

def test_unknown_keys_are_preserved_not_rejected():
    """The schema must be able to grow without breaking older profiles."""
    profile = parse_profile({"version": 1, "brand_new_field": {"a": 1}})
    assert profile.extras == {"brand_new_field": {"a": 1}}


def test_known_keys_do_not_leak_into_extras():
    assert parse_profile({"version": 1, "seniority": "senior"}).extras == {}
# --- profile source disambiguation (Phase 2C hardening) -------------------
# The three concepts must not be confusable:
#   load_profile(path)      an explicitly requested file
#   load_private_profile()  the operator's default local profile
#   default_profile()       a guaranteed-empty profile
# These previously overlapped: load_profile() with no argument silently read
# config/profile.yaml on a developer machine and raised ProfileError in CI, so
# the same call behaved differently in two places.


def test_load_profile_without_a_path_raises_instead_of_guessing():
    """No implicit fallback to DEFAULT_PROFILE_PATH."""
    with pytest.raises(ProfileError) as excinfo:
        load_profile()
    message = str(excinfo.value)
    assert "explicit path" in message
    # the error must point at the alternatives rather than just failing
    assert "load_private_profile" in message
    assert "default_profile" in message


def test_load_profile_with_explicit_path_still_works(tmp_path):
    profile = load_profile(write(tmp_path, MINIMAL))
    assert profile.source == SOURCE_FILE
    assert profile.skills == ()


def test_default_profile_is_empty_even_when_a_private_profile_exists():
    """The anti-leak guard: a private file on disk must not reach a caller
    that asked for an empty profile."""
    empty = default_profile()
    assert empty.source == SOURCE_DEFAULT
    assert empty.skills == ()
    assert empty.total_years_experience is None
    assert empty.seniority is None
    # this repository checkout does contain a private profile; assert the empty
    # profile did not pick any of it up
    private = load_private_profile()
    if private.skills:
        assert empty.skills != private.skills


def test_default_profile_ignores_a_named_path(tmp_path):
    assert default_profile().skills == ()
    assert load_profile(write(tmp_path, MINIMAL)).skills == ()


def test_load_private_profile_is_the_only_default_resolver(tmp_path):
    """The settings-facing resolver is explicit about the private file."""
    resolved = load_private_profile()
    assert resolved.source in (SOURCE_FILE, SOURCE_DEFAULT)
    # and it must never raise for a missing file
    assert load_private_profile(tmp_path / "absent.yaml").source == SOURCE_DEFAULT


def test_or_default_returns_empty_when_the_file_is_absent(tmp_path):
    fallback = load_profile_or_default(tmp_path / "absent.yaml")
    assert fallback.source == SOURCE_DEFAULT
    assert fallback.skills == ()


def test_profile_api_surface_exports_all_three_concepts():
    from schemahawk.profile import __all__ as exported

    assert "load_profile" in exported
    assert "load_private_profile" in exported
    assert "default_profile" in exported
# --- seniority validation (Phase 2C hardening) --------------------------
# An unrecognised band used to flow straight into matching, where
# _SENIORITY_RANK.get() returned None and the comparison became UNKNOWN - a typo
# was indistinguishable from "the operator declined to state a seniority", and
# it silently moved match scores.


@pytest.mark.parametrize("band", SENIORITY_BANDS)
def test_valid_seniority_bands_are_accepted(band):
    assert parse_profile({"version": 1, "seniority": band}).seniority == band


@pytest.mark.parametrize("value", ["Senior", "PRINCIPAL", "  senior  "])
def test_seniority_is_case_and_whitespace_normalised(value):
    assert parse_profile({"version": 1, "seniority": value}).seniority == value.strip().lower()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_absent_seniority_stays_unknown(value):
    assert parse_profile({"version": 1, "seniority": value}).seniority is None


def test_seniority_absent_entirely_is_allowed():
    assert parse_profile({"version": 1}).seniority is None


@pytest.mark.parametrize(
    "value",
    ["expert", "Sr.", "senior/lead", "expert-level", "mid level", "director", "8"],
)
def test_invalid_seniority_fails_loudly(value):
    """Failing loudly beats a silent UNKNOWN, which reads as 'not stated'."""
    with pytest.raises(ProfileError) as excinfo:
        parse_profile({"version": 1, "seniority": value})
    message = str(excinfo.value)
    assert "seniority" in message
    # the message must tell the operator what is acceptable
    for band in SENIORITY_BANDS:
        assert band in message


def test_invalid_seniority_error_never_echoes_other_profile_fields():
    """The error names the bad value only, never the rest of the profile."""
    with pytest.raises(ProfileError) as excinfo:
        parse_profile({
            "version": 1,
            "seniority": "expert",
            "contact_email": "someone@example.com",
            "preferred_rate": "900/day",
        })
    assert "someone@example.com" not in str(excinfo.value)
    assert "900/day" not in str(excinfo.value)


def test_seniority_bands_are_the_documented_set():
    assert SENIORITY_BANDS == ("junior", "mid", "senior", "staff", "lead", "principal")
