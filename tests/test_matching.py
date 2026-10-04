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
    """A skill must never match inside a longer word."""
    result = match_job(job(description="PostgreSQL and Microsoft support."),
                       profile(skills=["SQL"]))
    assert result.matched_preferred == ()
    assert result.technical_score is None


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