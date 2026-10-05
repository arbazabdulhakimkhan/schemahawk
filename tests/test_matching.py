"""Profile-aware candidate matching tests (Phase 1, part 2).

Three rules are asserted throughout:

1. **Unknown is not zero.** When the candidate or the posting says nothing, the
   component is ``None`` / ``UNKNOWN`` - never an invented 0 or assumed match.
2. **Nothing is invented.** Skills, years and seniority come only from real text.
3. **V1 relevance is separate.** Matching never overwrites ``relevance_score``.
"""
from __future__ import annotations

import pytest

from schemahawk.matching import (
    UNKNOWN,
    MatchResult,
    extract_requirements,
    extract_seniority,
    match_job,
)
from schemahawk.models import Job
from schemahawk.profile import CandidateProfile, default_profile, parse_profile
from schemahawk.relevance import score_relevance


def job(**kwargs) -> Job:
    base = dict(source="fake", title="Data Engineer", company="Acme",
                description="", location=None)
    base.update(kwargs)
    return Job(**base)


def profile(**kwargs) -> CandidateProfile:
    return parse_profile({"version": 1, **kwargs})


# --- skill matching ---------------------------------------------------------

def test_exact_skill_match():
    result = match_job(job(description="We use Python daily."),
                       profile(skills=["Python"]))
    assert result.matched_preferred == ("Python",)
    assert result.technical_score == 100


def test_case_insensitive_skill_match():
    result = match_job(job(description="We use PYTHON and Snowflake daily."),
                       profile(skills=["python", "snowflake"]))
    assert {s.lower() for s in result.matched_preferred} == {"python", "snowflake"}


def test_skill_match_respects_token_boundaries():
    """A skill must never match inside a longer word.

    "PostgreSQL" contains "SQL" but must not satisfy it. It is now recognized
    as its own skill, so the job reports a real (missing) requirement rather
    than nothing - the boundary rule is what keeps ``SQL`` out of the match.
    """
    result = match_job(job(description="PostgreSQL and Microsoft support."),
                       profile(skills=["SQL"]))
    # The boundary rule holds: "SQL" is not satisfied by "PostgreSQL".
    assert "SQL" not in result.matched_preferred
    # PostgreSQL is now recognized as a job requirement the candidate lacks:
    # nothing matched, and the gap is reported instead of being invisible.
    assert result.matched_preferred == ()
    assert result.missing_preferred == ("PostgreSQL",)
    assert result.technical_score == 0


def test_skill_alias_spellings_are_matched():
    """A posting saying "ADF" matches the declared "Azure Data Factory (ADF)"."""
    result = match_job(job(description="Build ADF pipelines."),
                       profile(skills=["Azure Data Factory (ADF)"]))
    assert result.matched_preferred == ("Azure Data Factory (ADF)",)


def test_missing_required_skill_is_reported():
    result = match_job(
        job(description="Required: Python, Snowflake."),
        profile(skills=["Python", "Snowflake"]),
    )
    assert result.missing_required == ()
    assert any("matched required" in line for line in result.explanation)


def test_missing_preferred_skill_is_reported():
    result = match_job(
        job(description="Nice to have: Tableau."),
        profile(skills=["Python"]),
    )
    assert result.matched_preferred == ()
    assert any("Missing preferred skills: Tableau" in line
               for line in result.explanation)


def test_required_skills_count_double_versus_preferred():
    """Missing a required skill hurts more than missing a preferred one.

    Both profiles match SQL; only one can also supply Airflow. The job states
    Snowflake as required and Airflow as preferred, so failing the required
    item must cost more than failing the preferred one.
    """
    strong = match_job(job(description="Required: SQL, Snowflake. Nice to have: Airflow."),
                       profile(skills=["SQL", "Snowflake", "Airflow"]))
    weak = match_job(job(description="Required: SQL, Snowflake. Nice to have: Airflow."),
                     profile(skills=["SQL", "Airflow"]))
    assert strong.technical_score == 100
    assert weak.technical_score < strong.technical_score
    assert weak.missing_required == ("Snowflake",)


def test_technical_is_unknown_when_job_lists_no_skills():
    result = match_job(job(description="Join our team."), profile(skills=["Python"]))
    assert result.technical_score is None
    assert result.overall_score is None


def test_technical_is_unknown_when_profile_declares_no_skills():
    result = match_job(job(description="We use Python."), profile())
    assert result.technical_score is None


# --- experience -------------------------------------------------------------

def test_unknown_candidate_experience():
    """No years declared => UNKNOWN, never an assumed match or a zero."""
    result = match_job(job(description="5+ years of experience required."),
                       profile(skills=["Python"]))
    assert result.experience_match == UNKNOWN
    assert result.experience_gap_years is None


def test_experience_gap_is_reported():
    result = match_job(job(description="5+ years of experience required."),
                       profile(skills=["Python"], total_years_experience=2))
    assert result.experience_gap_years == 3
    assert result.experience_match == "BELOW"
    assert any("gap of 3 year" in line for line in result.explanation)


def test_experience_meets_requirement():
    result = match_job(job(description="5+ years of experience required."),
                       profile(skills=["Python"], total_years_experience=8))
    assert result.experience_match == "NO_GAP"
    assert result.experience_gap_years == 0


def test_experience_is_unknown_when_job_states_no_requirement():
    result = match_job(job(description="Great team."),
                       profile(skills=["Python"], total_years_experience=8))
    assert result.experience_score is None


# --- seniority --------------------------------------------------------------

@pytest.mark.parametrize("word,expected", [
    ("Senior Data Engineer", "senior"),
    ("Junior Analyst", "junior"),
    ("Staff Engineer", "staff"),
    ("Data Platform Lead", "lead"),
])
def test_seniority_extraction(word, expected):
    assert extract_seniority(word) == expected


def test_seniority_alignment_when_equal():
    result = match_job(job(title="Senior Data Engineer"),
                       profile(skills=["Python"], seniority="senior"))
    assert result.seniority_alignment == "ALIGNED"


def test_candidate_more_senior_than_role_is_aligned():
    result = match_job(job(title="Junior Data Engineer"),
                       profile(skills=["Python"], seniority="staff"))
    assert result.seniority_alignment == "ALIGNED"


def test_candidate_below_seniority_is_flagged():
    result = match_job(job(title="Principal Data Engineer"),
                       profile(skills=["Python"], seniority="junior"))
    assert result.seniority_alignment == "BELOW"


def test_seniority_unknown_when_profile_omits_it():
    result = match_job(job(title="Senior Data Engineer"), profile(skills=["Python"]))
    assert result.seniority_alignment == UNKNOWN


# --- contract / remote / eligibility ---------------------------------------

def test_contract_fit_when_types_overlap():
    result = match_job(job(title="Contract Data Engineer"),
                       profile(skills=["Python"], preferred_contract_types=["contract"]))
    assert result.contract_fit == "FIT"
    assert result.contract_score == 100


def test_contract_mismatch():
    result = match_job(job(title="Freelance Data Engineer"),
                       profile(skills=["Python"], preferred_contract_types=["part_time"]))
    assert result.contract_fit == "MISMATCH"
    assert result.contract_score == 0


def test_contract_unknown_without_preference_or_signal():
    result = match_job(job(description="Data Engineer wanted."),
                       profile(skills=["Python"]))
    assert result.contract_score is None
    assert result.contract_fit == UNKNOWN


def test_remote_preference_matches_remote_job():
    result = match_job(job(description="Fully remote position."),
                       profile(skills=["Python"], remote_preference="remote-only"))
    assert result.contract_score == 100

# --- empty / malformed profile ---------------------------------------------

def test_empty_profile_yields_unknown_not_zero():
    """The headline guarantee: unknown information must never become a 0."""
    result = match_job(job(description="Required: Python, Snowflake, SQL. 5+ years."),
                       default_profile())
    assert result.technical_score is None
    assert result.experience_score is None
    assert result.contract_score is None
    assert result.eligibility_score is None
    assert result.overall_score is None
    assert result.is_fully_unknown


def test_empty_profile_explanation_says_unknown():
    result = match_job(job(description="Required: Python."), default_profile())
    joined = " ".join(result.explanation)
    assert "unknown" in joined.lower()
    assert "not specified" in joined.lower()


def test_malformed_profile_raises_rather_than_returning_partial_state():
    from schemahawk.profile import ProfileError
    with pytest.raises(ProfileError):
        parse_profile({"skills": [{"name": "SQL", "years": "eight"}]})


# --- no invented facts ------------------------------------------------------

def test_undeclared_skills_are_never_credited():
    """A skill the candidate never declared cannot count as matched."""
    result = match_job(job(description="Required: Rust and Elixir."),
                       profile(skills=["Python"]))
    assert result.matched_required == ()
    assert result.technical_score is None


def test_explanation_never_claims_undeclared_experience():
    result = match_job(job(description="5+ years required."),
                       profile(skills=["Python"]))
    joined = " ".join(result.explanation).lower()
    assert "unknown" in joined
    assert "meets requirement" not in joined


# --- separation from V1 relevance ------------------------------------------

def test_matching_does_not_touch_the_v1_relevance_score():
    the_job = job(title="Senior Data Engineer", description="Python and SQL required.")
    score_relevance(the_job)
    before = the_job.relevance_score
    match_job(the_job, profile(skills=["Python"]))
    assert the_job.relevance_score == before


def test_v1_relevance_still_scores_when_no_profile_exists():
    the_job = job(title="Senior Data Engineer", description="Python, SQL, Databricks")
    assert score_relevance(the_job) >= 60
    result = match_job(the_job, default_profile())
    assert result.overall_score is None      # no match opinion without a profile


# --- requirements extraction ------------------------------------------------

def test_extract_required_and_preferred_skills():
    reqs = extract_requirements(
        job(description="You have strong Python experience. Nice to have: Tableau."),
        profile(skills=["Python", "Tableau Desktop"]),
    )
    assert "Python" in reqs.required_skills
    # "Tableau" in the posting is reported in the vocabulary's canonical form.
    assert "Tableau Desktop" in reqs.preferred_skills


# --- experience boundary conditions ----------------------------------------


@pytest.mark.parametrize("years,required,score,gap", [
    (5, "5+ years required", 100, 0),      # exactly meets
    (20, "5+ years required", 100, 0),     # far above
    (2, "5+ years required", 40, 3),       # slightly below
    (1, "10+ years required", 0, 9),       # significantly below
])
def test_experience_scoring_boundaries(years, required, score, gap):
    result = match_job(job(description=required),
                       profile(skills=["Python"], total_years_experience=years))
    assert result.experience_score == score
    assert result.experience_gap_years == gap
    assert result.experience_match == ("NO_GAP" if gap == 0 else "BELOW")


def test_slightly_below_requirement_is_partial_not_zero():
    """A 3-year gap must still leave partial credit, not a zero."""
    result = match_job(job(description="5+ years required."),
                       profile(skills=["Python"], total_years_experience=2))
    assert result.experience_score == 40
    assert result.experience_score > 0


def test_significantly_below_requirement_floors_at_zero():
    result = match_job(job(description="10+ years required."),
                       profile(skills=["Python"], total_years_experience=1))
    assert result.experience_score == 0


def test_explanation_states_the_actual_gap():
    result = match_job(job(description="5+ years required."),
                       profile(skills=["Python"], total_years_experience=2))
    assert any("gap of 3 year" in line for line in result.explanation)


# --- skills missing in full -------------------------------------------------
#
# Only skills inside JOB_SKILL_VOCABULARY can be extracted at all: a posting
# asking for a language we do not track is invisible to the extractor, by
# design. These tests therefore use recognised skills.


def test_all_required_skills_missing_scores_zero():
    result = match_job(job(description="Required: Snowflake, Databricks."),
                       profile(skills=["Python"]))
    assert result.technical_score == 0
    assert result.matched_required == ()
    assert set(result.missing_required) == {"Snowflake", "Databricks"}


def test_all_preferred_skills_missing_scores_zero():
    result = match_job(job(description="Nice to have: Power BI, Tableau Desktop."),
                       profile(skills=["Python"]))
    assert result.technical_score == 0
    assert set(result.missing_preferred) == {"Power BI", "Tableau Desktop"}


def test_partial_required_coverage_halves_the_score():
    result = match_job(job(description="Required: Snowflake, Python."),
                       profile(skills=["Python"]))
    assert result.technical_score == 50
    assert result.matched_required == ("Python",)
    assert result.missing_required == ("Snowflake",)


def test_unknown_vocabulary_skills_are_not_fabricated_into_missing():
    """An untracked skill must never be invented into the missing list."""
    result = match_job(job(description="Required: Rust and Elixir."),
                       profile(skills=["Python"]))
    assert result.missing_required == ()
    assert "Rust" not in result.explanation
    assert "Elixir" not in result.explanation

def test_location_match_when_posting_is_in_preferred_list():
    result = match_job(job(location="Germany, Remote"),
                       profile(skills=["Python"], preferred_locations=["Germany"]))
    assert result.contract_score == 100
    assert result.contract_fit == "FIT"


def test_location_mismatch_when_posting_is_elsewhere():
    result = match_job(job(location="Brazil"),
                       profile(skills=["Python"], preferred_locations=["Germany"]))
    assert result.contract_score == 0
    assert result.contract_fit == "MISMATCH"


@pytest.mark.parametrize("location", [
    "Worldwide", "Anywhere", "Remote - Worldwide", "Global team",
])
def test_worldwide_posting_satisfies_any_location_preference(location):
    result = match_job(job(location=location),
                       profile(skills=["Python"], preferred_locations=["Germany"]))
    assert result.contract_score == 100


def test_no_geographic_inference_is_made():
    """"Europe" must not be assumed to include "Germany"."""
    result = match_job(job(location="Europe"),
                       profile(skills=["Python"], preferred_locations=["Germany"]))
    assert result.contract_score == 0
    assert result.contract_fit == "MISMATCH"


def test_location_is_unknown_when_the_posting_states_none():
    result = match_job(job(location=None, description="Build ETL with SQL."),
                       profile(skills=["Python"], preferred_locations=["Germany"]))
    assert result.contract_score is None
    assert result.contract_fit == UNKNOWN


def test_location_is_unknown_when_candidate_states_no_preference():
    """An empty preferred_locations must never count as a pass."""
    result = match_job(job(location="Brazil"),
                       profile(skills=["Python"], preferred_locations=[]))
    assert result.contract_score is None
    assert result.contract_fit == UNKNOWN


def test_blank_location_is_a_mismatch_not_a_pass():
    result = match_job(job(location="   "),
                       profile(skills=["Python"], preferred_locations=["Germany"]))
    assert result.contract_score == 0


def test_location_check_combines_with_contract_type_checks():
    """Each stated dimension contributes; the score is their mean."""
    result = match_job(job(title="Contract Data Engineer", location="Germany"),
                       profile(skills=["Python"],
                               preferred_contract_types=["contract"],
                               preferred_locations=["Germany"]))
    assert result.contract_score == 100   # both checks pass

    mixed = match_job(job(title="Contract Data Engineer", location="Brazil"),
                      profile(skills=["Python"],
                              preferred_contract_types=["contract"],
                              preferred_locations=["Germany"]))
    assert mixed.contract_score == 50      # one of two checks
    assert mixed.contract_fit == "MISMATCH"


def test_extract_contract_and_remote():
    reqs = extract_requirements(job(description="Freelance, fully remote role."),
                                profile())
    assert reqs.remote_requirement == "remote"
    assert "freelance" in reqs.contract_types


def test_extract_seniority_from_title():
    reqs = extract_requirements(job(title="Senior Analytics Engineer"), profile())
    assert reqs.seniority == "senior"


# --- determinism ------------------------------------------------------------

def test_matching_is_deterministic():
    the_job = job(description="Required: Python and SQL. 5+ years.")
    prof = profile(skills=["Python", "SQL"], total_years_experience=8)
    assert match_job(the_job, prof) == match_job(the_job, prof)


def test_match_result_defaults_are_all_unknown():
    empty = MatchResult()
    assert empty.overall_score is None
    assert empty.seniority_alignment == UNKNOWN
    assert empty.explanation == ()

def test_onsite_job_conflicts_with_remote_only():
    result = match_job(job(description="This is an on-site role."),
                       profile(skills=["Python"], remote_preference="remote-only"))
    assert result.contract_score == 0


def test_eligibility_is_unknown_when_job_states_nothing():
    """Absence of a restriction is not proof of eligibility."""
    result = match_job(job(description="We need Python."), profile(skills=["Python"]))
    assert result.eligibility_verdict == UNKNOWN
    assert result.eligibility_score is None


def test_eligibility_restriction_is_zero():
    result = match_job(job(description="Python. US citizenship required."),
                       profile(skills=["Python"]))
    assert result.eligibility_score == 0
    assert result.eligibility_verdict == "MISMATCH"


def test_technical_is_unknown_when_job_lists_no_skills():
    result = match_job(job(description="Join our team."), profile(skills=["Python"]))
    assert result.technical_score is None
    assert result.overall_score is None


def test_technical_is_unknown_when_profile_declares_no_skills():
    result = match_job(job(description="We use Python."), profile())
    assert result.technical_score is None
# --- P0: requirement silence must not score (Phase 2C) --------------------
# Before this, a posting that states nothing left exactly one knowable
# component, and renormalizing over that single component returned a perfect
# 100. Silence is not a match.


def test_absent_experience_requirement_is_unknown_not_satisfied():
    """Candidate declares years; posting asks for none. That is not evidence."""
    result = match_job(
        job(title="Data Engineer", description="Great team."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.experience_match == UNKNOWN
    assert "job states no years requirement" in " ".join(result.explanation)


def test_explicit_experience_requirement_met_scores_full():
    result = match_job(
        job(description="We use SQL. Requires 5+ years of experience."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.experience_match != UNKNOWN
    assert result.experience_gap_years == 0.0
    assert result.experience_score == 100


def test_explicit_experience_requirement_failed_scores_zero():
    result = match_job(
        job(description="We use SQL. Requires 12+ years of experience."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.experience_gap_years == 5.0
    assert result.experience_score == 0


def test_single_known_component_yields_no_overall_score():
    """Only technical is knowable; the total must not masquerade as a verdict."""
    result = match_job(
        job(title="Engineer", description="You will use SQL daily."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.technical_score == 100
    assert result.overall_score is None


def test_two_known_components_do_emit_an_overall_score():
    result = match_job(
        job(title="Senior Engineer", description="You will use SQL daily."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.technical_score == 100
    assert result.experience_score == 100
    assert result.overall_score == 100


def test_totally_silent_posting_has_no_overall_score():
    result = match_job(
        job(title="Data Engineer", description="Great team."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.overall_score is None
    assert result.is_fully_unknown


def test_matching_never_changes_the_v1_relevance_score():
    from schemahawk.relevance import score_relevance

    subject = job(description="We use SQL, Python and Airflow.")
    subject_profile = profile(total_years_experience=7, seniority="senior",
                              skills=["SQL", "Python"])
    before = score_relevance(subject)
    match_job(subject, subject_profile)
    assert score_relevance(subject) == before
# --- year parsing (Phase 2C hardening) -----------------------------------
# A range must resolve to its upper bound. The regex used to leave the range
# non-capturing, so "3-5 years" returned 3 - the *lower* bound - even though
# the docstring promised the maximum, quietly understating every ranged
# requirement in the lenient direction.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5+ years of experience", 5.0),
        ("at least 7 years", 7.0),
        ("10+ years", 10.0),
        ("1 year", 1.0),
        ("3-5 years", 5.0),
        ("3 to 5 years", 5.0),
        ("2\u20134 years", 4.0),          # en dash
        ("at least 3-5 years", 5.0),
        ("0-5 years", 5.0),
        ("3-5 years, 8+ years", 8.0),   # maximum across mentions
        ("99 years", 99.0),
    ],
)
def test_experience_requirements_parse_to_the_highest_stated_demand(text, expected):
    assert extract_requirements(job(description=text), profile()).min_years_experience == expected


@pytest.mark.parametrize(
    "text",
    [
        "0 years experience",
        "0 years of experience required",
        "100 years",                    # does not fit the pattern -> unknown
        "no experience required",
        "Great team, apply now.",
    ],
)
def test_unrealistic_or_absent_experience_stays_unknown(text):
    assert extract_requirements(job(description=text), profile()).min_years_experience is None


def test_zero_years_requirement_does_not_award_a_perfect_experience_score():
    """Regression: '0 years' parsed to 0.0, which is a trivially-met demand.

    ``gap = max(0, 0 - years)`` is 0, so the component scored 100 for a posting
    that required nothing - the same silence-inflation class as the P0 fix.
    """
    result = match_job(
        job(description="We use SQL. 0 years experience required."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.experience_gap_years is None
    assert "job states no years requirement" in " ".join(result.explanation)


def test_ranged_requirement_reports_the_upper_bound_as_the_gap():
    result = match_job(
        job(description="We use SQL. 3-5 years of experience required."),
        profile(total_years_experience=7, seniority="senior", skills=["SQL"]),
    )
    assert result.experience_gap_years == 0.0
    assert result.experience_score == 100


def test_ranged_requirement_is_stricter_than_the_lower_bound():
    """A 3-4 year candidate does not satisfy '3-5 years'; the gap is 1."""
    result = match_job(
        job(description="We use SQL. 3-5 years of experience required."),
        profile(total_years_experience=4, seniority="senior", skills=["SQL"]),
    )
    assert result.experience_gap_years == 1.0
    assert result.experience_score == 80


def test_absurd_year_count_is_never_fabricated_into_a_requirement():
    assert extract_requirements(
        job(description="We need 100 years of experience."),
        profile(),
    ).min_years_experience is None