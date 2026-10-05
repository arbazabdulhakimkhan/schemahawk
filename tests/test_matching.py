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
    ADJACENT,
    ALIGNED,
    BELOW,
    OVER_QUALIFIED,
    UNKNOWN,
    MatchResult,
    extract_requirements,
    extract_seniority,
    extract_seniority_from_description,
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
    result = match_job(
        job(description="We use Python, Snowflake, Databricks and Power Query "
                       "daily."),
        profile(skills=["Python", "Snowflake", "Databricks", "Power Query"]),
    )
    assert "Python" in result.matched_preferred
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
    result = match_job(
        job(description="PostgreSQL, Snowflake, Databricks and Tableau "
                       "Desktop required."),
        profile(skills=["SQL"]),
    )
    # The boundary rule holds: "SQL" is not satisfied by "PostgreSQL".
    assert "SQL" not in result.matched_preferred
    # PostgreSQL is now recognized as a job requirement the candidate lacks:
    # nothing matched, and the gap is reported instead of being invisible.
    assert result.matched_preferred == ()
    assert "PostgreSQL" in result.missing_required
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


def test_candidate_more_senior_than_role_is_reported_over_qualified():
    """Over-level is distinguishable from an exact fit.

    Previously this returned ALIGNED, so "Junior Payroll Assistant" and
    "Senior Data Engineer" were indistinguishable for a staff-level candidate.
    The verdict is informational; the score is unchanged (see the scoring
    tests below).
    """
    result = match_job(job(title="Junior Data Engineer"),
                       profile(skills=["Python"], seniority="staff"))
    assert result.seniority_alignment == OVER_QUALIFIED


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
    result = match_job(
        job(description="Nice to have: Power BI, Tableau Desktop, Snowflake, "
                       "Databricks."),
        profile(skills=["Python"]),
    )
    assert result.technical_score == 0
    assert set(result.missing_preferred) == {
        "Power BI", "Tableau Desktop", "Snowflake", "Databricks"}


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
        job(title="Engineer",
            description="You will use SQL, Python, Databricks and Snowflake "
                        "daily."),
        profile(total_years_experience=7, seniority="senior",
                skills=["SQL", "Python", "Databricks", "Snowflake"]),
    )
    assert result.technical_score == 100
    assert result.overall_score is None


def test_two_known_components_do_emit_an_overall_score():
    result = match_job(
        job(title="Senior Engineer",
            description="You will use SQL, Python, Databricks and Snowflake "
                        "daily."),
        profile(total_years_experience=7, seniority="senior",
                skills=["SQL", "Python", "Databricks", "Snowflake"]),
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
# --- seniority verdicts (OVER_QUALIFIED) ---------------------------------
# Over-level used to collapse into ALIGNED. It is now its own verdict, but it
# is *informational only*: the score must be identical to an exact fit, because
# postings state a minimum competence bar and not a ceiling.

_BANDS = ("principal", "staff", "lead", "senior", "mid", "junior")


@pytest.mark.parametrize(
    ("candidate", "job", "expected"),
    [
        # exact fits stay ALIGNED
        ("senior", "senior", ALIGNED),
        ("junior", "junior", ALIGNED),
        ("principal", "principal", ALIGNED),
        # any band above the role is OVER_QUALIFIED
        ("senior", "junior", OVER_QUALIFIED),
        ("senior", "mid", OVER_QUALIFIED),
        ("staff", "senior", OVER_QUALIFIED),
        ("principal", "senior", OVER_QUALIFIED),
        ("principal", "junior", OVER_QUALIFIED),
        # one band under the role is ADJACENT
        ("mid", "senior", ADJACENT),
        ("junior", "mid", ADJACENT),
        # two or more bands under is BELOW
        ("junior", "senior", BELOW),
        ("junior", "principal", BELOW),
        ("mid", "staff", BELOW),
        # either side unstated is UNKNOWN
        ("senior", None, UNKNOWN),
        (None, "senior", UNKNOWN),
        ("senior", "expert", UNKNOWN),
    ],
)
def test_seniority_verdict_matrix(candidate, job, expected):
    from schemahawk.matching import _score_seniority

    assert _score_seniority(job, candidate) == expected


def test_over_qualified_scores_identically_to_aligned():
    """The informational verdict must not move the number.

    If being above the band lowered the score, a junior title would rank
    *worse* for a senior candidate than for a junior one.
    """
    common = dict(skills=["Python"], total_years_experience=7)
    exact = match_job(job(title="Senior Data Engineer", description="Use Python."),
                      profile(seniority="senior", **common))
    over = match_job(job(title="Junior Data Engineer", description="Use Python."),
                     profile(seniority="senior", **common))

    assert exact.seniority_alignment == ALIGNED
    assert over.seniority_alignment == OVER_QUALIFIED
    assert exact.experience_score == over.experience_score == 100
    assert exact.overall_score == over.overall_score


def test_over_qualified_is_never_a_rejection():
    result = match_job(job(title="Junior Payroll Assistant"),
                       profile(skills=["Python"], seniority="staff"))
    assert result.eligibility_conflict is None
    assert result.seniority_alignment == OVER_QUALIFIED


def test_over_qualified_explanation_reads_as_a_word_not_a_constant():
    result = match_job(job(title="Junior Data Engineer"),
                       profile(skills=["Python"], seniority="staff"))
    line = [l for l in result.explanation if l.startswith("Seniority:")][0]
    assert "over-qualified" in line
    assert "OVER_QUALIFIED" not in line
    assert "_" not in line.split("(")[0]


def test_seniority_score_is_monotonic_in_candidate_level():
    """A more senior candidate never scores worse for the same posting."""
    scores = []
    for level in _BANDS:
        result = match_job(job(title="Senior Data Engineer", description="Use Python."),
                           profile(skills=["Python"], seniority=level))
        scores.append((level, result.experience_score))
    assert scores[0][1] >= scores[1][1] >= scores[2][1] >= scores[3][1] \
        >= scores[4][1] >= scores[5][1]


def test_seniority_verdict_constants_are_exported():
    from schemahawk import matching

    for name in ("ALIGNED", "OVER_QUALIFIED", "ADJACENT", "BELOW", "UNKNOWN"):
        assert name in matching.__all__
        assert getattr(matching, name) == name
# --- evidence-gated seniority extraction ---------------------------------
# Body copy mentions "lead", "staff", "senior" and "principal" constantly
# without describing the role's level. An ungated scan fired on 42 of 171 live
# postings and not one of them described the role.

_ALL_BANDS = ("junior", "mid", "senior", "staff", "lead", "principal")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Junior Data Engineer", "junior"),
        ("Mid-level Data Engineer", "mid"),
        ("Senior Data Engineer", "senior"),
        ("Staff Data Engineer", "staff"),
        ("Lead Data Engineer", "lead"),
        ("Principal Data Engineer", "principal"),
        ("Sr. Analyst", "senior"),
        ("Data Engineer", None),
    ],
)
def test_seniority_is_extracted_from_the_title(title, expected):
    assert extract_seniority(title) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # hiring cue
        ("We are looking for a senior data engineer.", "senior"),
        ("Seeking staff engineers for our team.", "staff"),
        ("We need junior developers.", "junior"),
        ("Seeking a lead for our platform team.", "lead"),
        # years-of-experience phrase
        ("5+ years in a senior engineering role.", "senior"),
        ("Requires 3+ years of experience as a principal engineer.", "principal"),
        ("5+ years in a mid-level role.", "mid"),
        # hyphenated level
        ("This is a senior-level position.", "senior"),
        ("Entry-level role available.", "junior"),
        ("Mid-level Data Analyst wanted.", "mid"),
    ],
)
def test_explicit_description_evidence_sets_seniority(text, expected):
    assert extract_seniority_from_description(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "You will lead a team of analysts.",
        "We have senior leadership.",
        "Work with staff members daily.",
        "Located near principal office.",
        "Engage senior stakeholders.",
        "Work with senior engineers.",
        "Our staff is global.",
        "The principal reason is cost.",
        "You will be leading the roadmap.",
        "Lead client conversations daily.",
        "Interact with all levels of staff.",
    ],
)
def test_ambiguous_description_mentions_do_not_set_seniority(text):
    """Every one of these produced a wrong band before the evidence gate."""
    assert extract_seniority_from_description(text) is None


@pytest.mark.parametrize("band", _ALL_BANDS)
def test_every_band_is_reachable_from_both_tiers(band):
    """Each band must be detectable from a title and from explicit evidence."""
    from schemahawk.matching import _SENIORITY_RANK

    title_map = {
        "junior": "Junior Data Engineer",
        "mid": "Mid-level Data Engineer",
        "senior": "Senior Data Engineer",
        "staff": "Staff Data Engineer",
        "lead": "Lead Data Engineer",
        "principal": "Principal Data Engineer",
    }
    assert band in _SENIORITY_RANK
    assert extract_seniority(title_map[band]) == band


def test_description_is_consulted_only_when_the_title_is_silent():
    """A title band always wins; body copy cannot override it."""
    titled = extract_requirements(
        job(title="Junior Data Engineer", description="We are looking for a senior engineer."),
        profile(),
    )
    assert titled.seniority == "junior"

    untitled = extract_requirements(
        job(title="Data Engineer", description="We are looking for a senior engineer."),
        profile(),
    )
    assert untitled.seniority == "senior"


def test_body_copy_never_overrides_a_silent_title():
    ambiguous = extract_requirements(
        job(title="Data Engineer", description="You will lead a team of analysts."),
        profile(),
    )
    assert ambiguous.seniority is None


def test_over_qualified_band_survives_the_gated_extraction():
    """A junior title stays detected, so over-qualification is still reported."""
    result = match_job(job(title="Junior Data Engineer"),
                       profile(skills=["Python"], seniority="staff"))
    assert result.seniority_alignment == OVER_QUALIFIED
    assert result.experience_score == 100
# --- contract duration (Phase 2C) ---------------------------------------
# A duration only counts when its sentence is contract-flavoured. The ungated
# "N months" scan fired on 6 of 171 live postings and got one right: "reached
# unicorn status in 9 months" and "in the first 12 months" are company history
# and onboarding milestones, not contract lengths.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6-month contract", 6.0),
        ("6 month contract", 6.0),
        ("contract for 6 months", 6.0),
        ("12-month contract", 12.0),
        ("12 month contract", 12.0),
        ("18 months initially", 18.0),
        ("a 24-month contract", 24.0),
        ("1 month contract", 1.0),          # singular
    ],
)
def test_plain_contract_durations_are_detected(text, expected):
    duration = extract_requirements(job(description=text), profile()).contract_duration
    assert duration is not None
    assert duration.min_months == expected
    assert duration.max_months == expected
    assert duration.is_range is False


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("3-6 month contract", 3.0, 6.0),
        ("3 to 6 month contract", 3.0, 6.0),
        ("between 3 and 6 months contract", 3.0, 6.0),
        ("2\u20134 month term", 2.0, 4.0),   # en dash
    ],
)
def test_ranges_are_preserved_not_collapsed(text, low, high):
    duration = extract_requirements(job(description=text), profile()).contract_duration
    assert duration is not None
    assert (duration.min_months, duration.max_months) == (low, high)
    assert duration.is_range is True
    assert duration.label() == f"{low:g}-{high:g} months"


def test_upper_bound_only_is_not_filled_in():
    duration = extract_requirements(
        job(description="up to 24 months contract"), profile()
    ).contract_duration
    assert duration is not None
    assert duration.max_months == 24.0
    assert duration.min_months is None
    assert duration.label() == "up to 24 months"


def test_lower_bound_only_is_not_filled_in():
    duration = extract_requirements(
        job(description="minimum 6 months contract"), profile()
    ).contract_duration
    assert duration is not None
    assert duration.min_months == 6.0
    assert duration.max_months is None
    assert duration.label() == "at least 6 months"


@pytest.mark.parametrize(
    ("text", "months"),
    [("12 week contract", 2.8), ("4-week assignment", 0.9), ("6 weeks contract", 1.4)],
)
def test_week_durations_are_normalised_to_months(text, months):
    """Values are comparable; the source wording stays in ``unit``/``raw``."""
    duration = extract_requirements(job(description=text), profile()).contract_duration
    assert duration is not None
    assert duration.min_months == months
    assert duration.unit == "weeks"
    assert duration.raw


@pytest.mark.parametrize(
    "text",
    [
        "Company founded 18 months ago.",
        "The pilot ran 36 months.",
        "We served 24 months of uptime.",
        "Served 4 months on probation.",
        "A 12 months notice period applies.",
        "Your scorecard at 90 days and 6 months.",
        "Within 6 months, you'll own a key project.",   # "project" is not enough
        "In The First 12 Months.",
        "Full-time permanent role.",
        "Salary 90000 per annum.",
        "3+ years of experience.",
        "35 hours per week.",
        "We have 200 employees.",
    ],
)
def test_misleading_numeric_phrases_yield_no_duration(text):
    """Each of these returned a number before the evidence gate."""
    assert extract_requirements(job(description=text), profile()).contract_duration is None


def test_customer_engagement_is_not_a_contract_cue():
    """A real posting used 'engagement' in the marketing sense.

    "Strong, measurable engagement and results across the healthcare
    partnerships ... in the first 12 months" - onboarding milestones, not a
    contract length. Only "contract" supports a duration here.
    """
    text = ("What Success Looks Like In The First 12 Months "
            "A growing roster of signed employer accounts. Strong, measurable "
            "engagement and results across the healthcare partnerships.")
    assert extract_requirements(job(description=text), profile()).contract_duration is None


def test_relative_dates_are_never_calculated():
    """No start date means no safe duration - never invent one."""
    for text in ("Contract through December.",
                 "Contract until end of year.",
                 "Contract for the remainder of 2026."):
        assert extract_requirements(job(description=text), profile()).contract_duration is None


def test_missing_duration_is_none_not_zero():
    duration = extract_requirements(
        job(description="We build data pipelines."), profile()
    ).contract_duration
    assert duration is None


def test_real_fixture_case_six_month_contract():
    """Verbatim from a live posting: 'Growth Marketer (6-Month Contract)'."""
    duration = extract_requirements(
        job(title="Growth Marketer (6-Month Contract)",
            description="Location: Remote. Great team."),
        profile(),
    ).contract_duration
    assert duration is not None
    assert duration.label() == "6 months"


def test_duration_is_absent_for_a_permanent_role():
    duration = extract_requirements(
        job(title="Senior Data Engineer",
            description="Permanent, full-time role. 40 hours per week."),
        profile(),
    ).contract_duration
    assert duration is None


def test_longest_stated_duration_wins_across_sentences():
    duration = extract_requirements(
        job(description="This is a 6 month contract. The initial term is 12 months."),
        profile(),
    ).contract_duration
    assert duration is not None
    assert duration.max_months == 12.0


def test_contract_duration_is_not_persisted_or_scored():
    """It is extraction-only: nothing scores it and no column stores it."""
    from schemahawk.matching import MatchResult

    fields = set(MatchResult.__dataclass_fields__)
    assert not any("duration" in name for name in fields)
# --- duration unit conversion policy ------------------------------------
# Non-month units are converted and therefore rounded. A converted value must
# never be presented as an exact contractual duration, so it carries
# ``approximate=True`` and a "~" in its label.


@pytest.mark.parametrize(
    ("weeks", "months"),
    [(1, 0.2), (2, 0.5), (4, 0.9), (6, 1.4), (8, 1.8),
     (12, 2.8), (16, 3.7), (26, 6.0), (52, 12.0)],
)
def test_weeks_to_months_conversion_is_exact_and_pinned(weeks, months):
    from schemahawk.matching import _DAYS_PER_MONTH, _to_months

    assert _DAYS_PER_MONTH == 30.44
    assert _to_months(weeks, "weeks") == months


def test_conversion_basis_is_documented_and_not_inlined():
    """The 30.44 basis must be a named constant so it cannot drift."""
    from schemahawk.matching import _DAYS_PER_MONTH

    assert isinstance(_DAYS_PER_MONTH, float)
    assert 30.0 < _DAYS_PER_MONTH < 31.0


def test_week_conversion_is_flagged_approximate():
    """12 weeks is really 2.7595 months; 2.8 must not read as a stated figure."""
    duration = extract_requirements(
        job(description="12 week contract"), profile()
    ).contract_duration
    assert duration is not None
    assert duration.min_months == 2.8
    assert duration.approximate is True
    assert duration.unit == "weeks"
    assert duration.label().startswith("~")


def test_month_durations_are_not_approximate():
    duration = extract_requirements(
        job(description="6-month contract"), profile()
    ).contract_duration
    assert duration is not None
    assert duration.approximate is False
    assert duration.label() == "6 months"


def test_rounded_value_is_never_equal_to_a_stated_month_figure():
    """12 weeks rounds to 2.8 months, which no posting would state as 2.8."""
    converted = _to_months_for_test(12)
    assert converted == 2.8
    assert 12 * 7.0 / 30.44 != 2.8      # the conversion is genuinely lossy


def test_conversion_is_deterministic_across_repeated_calls():
    from schemahawk.matching import _to_months

    values = {_to_months(12, "weeks") for _ in range(50)}
    assert values == {2.8}


def test_days_are_not_parsed_and_stay_unknown():
    """No day-based duration exists in 171 live postings.

    Every mention is a false candidate - "90 days and 6 months" (scorecard),
    "20 days of paid time off" (holiday), "within the first 30 days of
    employment" (onboarding), "3 days per week" (office schedule). Adding a
    days rule would need evidence first.
    """
    for text in ("90-day contract", "90 day contract",
                 "Within the first 30 days of employment",
                 "20 days of paid time off"):
        assert extract_requirements(
            job(description=text), profile()
        ).contract_duration is None


def _to_months_for_test(weeks):
    from schemahawk.matching import _to_months

    return _to_months(weeks, "weeks")
# --- Phase 2D: ranking & decision quality -----------------------------------
#
# Measured on 171 live postings, the technical ratio divided by whatever the
# posting happened to name. One lone "Python" gave a perfect 100, which put
# "QA Tester Entry Level" and "Developer Advocate" above every genuine Data
# Engineering role. These tests pin the evidence floor and the relevance gate.


def test_single_named_skill_yields_no_technical_score():
    """One matched skill out of one possible is an anecdote, not a 100%."""
    result = match_job(
        job(description="We use Python daily."),
        profile(skills=["Python"]),
    )
    assert result.technical_score is None
    # The evidence is still reported, only the number is withheld.
    assert result.matched_preferred == ("Python",)


def test_thin_evidence_never_reports_a_perfect_technical_score():
    """Whatever the posting names, too little of it cannot produce a 100."""
    for description, skills in [
        ("Nice to have: Python.", ["Python"]),
        ("Python and Snowflake.", ["Python", "Snowflake"]),
        ("Python, Snowflake and Databricks.", ["Python", "Snowflake", "Databricks"]),
    ]:
        result = match_job(job(description=description), profile(skills=skills))
        assert result.technical_score is None, description


def test_sufficient_evidence_still_scores():
    """The floor withholds a number only when the evidence is too thin."""
    result = match_job(
        job(description="We use Python, Snowflake, Databricks and Power Query."),
        profile(skills=["Python", "Snowflake", "Databricks", "Power Query"]),
    )
    assert result.technical_score == 100


def test_relevance_gate_suppresses_overall_score():
    """A well-matched posting of the wrong kind gets no confident verdict."""
    posting = job(
        title="QA Tester Entry Level",
        description="We use Python, Snowflake, Databricks and Power Query.",
    )
    candidate = profile(total_years_experience=7, seniority="senior",
                        skills=["Python", "Snowflake", "Databricks", "Power Query"])
    assert match_job(posting, candidate).overall_score is not None
    assert match_job(posting, candidate, min_relevance=60).overall_score is None


def test_relevance_gate_keeps_a_relevant_posting_scored():
    posting = job(
        title="Senior Data Engineer",
        description="We use Python, Snowflake, Databricks and Power Query.",
    )
    candidate = profile(total_years_experience=7, seniority="senior",
                        skills=["Python", "Snowflake", "Databricks", "Power Query"])
    result = match_job(posting, candidate, min_relevance=60)
    assert result.overall_score is not None


def test_relevance_gate_is_opt_in():
    """Passing a threshold is what engages the gate; omitting it does nothing."""
    posting = job(
        title="QA Tester Entry Level",
        description="We use Python, Snowflake, Databricks and Power Query.",
    )
    candidate = profile(total_years_experience=7, seniority="senior",
                        skills=["Python", "Snowflake", "Databricks", "Power Query"])
    assert match_job(posting, candidate).overall_score is not None
    assert match_job(posting, candidate, min_relevance=60).overall_score is None


def test_relevance_gate_leaves_components_visible():
    """Gating hides the verdict, not the evidence behind it."""
    posting = job(
        title="QA Tester Entry Level",
        description="We use Python, Snowflake, Databricks and Power Query.",
    )
    candidate = profile(total_years_experience=7, seniority="senior",
                        skills=["Python", "Snowflake", "Databricks", "Power Query"])
    result = match_job(posting, candidate, min_relevance=60)
    assert result.overall_score is None
    # The title itself names "QA", so the candidate does not match everything;
    # the point is that a number is still reported rather than gated away.
    assert result.technical_score is not None
    assert len(result.explanation) > 0


def test_gate_never_writes_to_the_v1_relevance_score():
    posting = job(
        title="QA Tester Entry Level",
        description="We use Python, Snowflake, Databricks and Power Query.",
    )
    candidate = profile(total_years_experience=7, seniority="senior",
                        skills=["Python", "Snowflake", "Databricks", "Power Query"])
    match_job(posting, candidate, min_relevance=60)
    assert posting.relevance_score is None