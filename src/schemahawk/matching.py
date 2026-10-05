"""Profile-aware candidate matching (Phase 1, part 2).

This is deliberately **separate from** the V1 relevance scorer:

- ``relevance.score_relevance`` answers *"is this a Data Engineering-type job,
  and does the listing look relevant at all?"* It is keyword-driven, profile
  unaware, and stores its integer in ``Job.relevance_score``.
- ``matching.match_job`` answers *"how well does this job match MY profile?"*
  It is profile-aware, explains itself, and returns a ``MatchResult``.

Neither replaces the other, and the V1 score is never overwritten.

Core rule: **unknown is not zero.** Every component returns ``None`` when it
cannot be determined from real evidence. A candidate who has not declared their
years of experience gets ``experience_match = None`` ("unknown"), never an
assumed match and never a zero that would drag the score down. The overall
score is renormalized over the components that *are* knowable, so one unknown
component neither fabricates data nor unfairly penalizes the candidate.

All text from job boards is untrusted data: it is only ever pattern-matched
here, and never executed, evaluated or used to build commands.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Job
from .profile import CandidateProfile
from .eligibility import LocationScope
from .skills import DEFAULT_VOCABULARY, alias_map, canonical_names, vocabulary_for
from .textmatch import matched_phrases, matcher_for_phrase

# Backwards-compatible name: extraction still scans a flat tuple of canonical
# skill names. The definitions now live in ``skills`` so the vocabulary can carry
# categories and aliases without changing this module.
JOB_SKILL_VOCABULARY: tuple[str, ...] = canonical_names(DEFAULT_VOCABULARY)

# --- component weights (candidate match only; unrelated to V1 relevance) -----
W_TECHNICAL = 0.50
W_EXPERIENCE = 0.25      # experience + seniority
W_CONTRACT = 0.15        # contract type / remote / location
W_ELIGIBILITY = 0.10

# Verdicts
UNKNOWN = "UNKNOWN"
ALIGNED = "ALIGNED"
BELOW = "BELOW"
NO_GAP = "NO_GAP"
FIT = "FIT"
MISMATCH = "MISMATCH"
NOT_APPLICABLE = "N/A"

# --- deterministic extraction patterns --------------------------------------
# "5+ years", "3-5 years", "at least 7 years of experience"
_YEARS = re.compile(
    r"\b(?:at\s+least\s+|minimum\s+|min\.?\s*)?(\d{1,2})\s*(?:\+|plus)?\s*"
    r"(?:(?:-|to|\u2013)\s*\d{1,2}\s*)?(?:years?|yrs?)\b",
    re.I,
)
_REQUIRED_HINT = re.compile(
    r"\b(?:required|must\s+have|you\s+have|strong\s+experience|proven|essential)\b",
    re.I,
)
_PREFERRED_HINT = re.compile(
    r"\b(?:nice\s+to\s+have|preferred|bonus|desirable|plus|ideally|good\s+to\s+have)\b",
    re.I,
)

_SENIORITY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("principal", ("principal", "distinguished", "fellow")),
    ("staff", ("staff",)),
    ("lead", ("lead", r"team\s+lead", r"tech\s+lead")),
    ("senior", ("senior", r"sr\.?", "snr")),
    ("mid", ("mid[- ]level", "intermediate", "midweight")),
    ("junior", ("junior", r"jr\.?", "entry[- ]level", "graduate")),
)
_SENIORITY_RANK = {name: index for index, (name, _) in enumerate(_SENIORITY_TERMS)}
# The table is ordered most senior first, so a *larger* rank means *less*
# senior. Comparisons below rely on that direction; the reverse bit a test.
_MOST_SENIOR = 0
_LEAST_SENIOR = len(_SENIORITY_TERMS) - 1

_CONTRACT_TERMS: tuple[str, ...] = (
    "freelance", "contract", "independent contractor", "temporary", "temp",
    "part[- ]time", "part time", "project[- ]based", "project based",
    "contract[- ]to[- ]hire", "fixed[- ]price", "hourly",
)
_REMOTE_TERMS = re.compile(
    r"\b(?:fully?\s+remote|remote|work\s+from\s+home|anywhere|worldwide|"
    r"wfh|distributed)\b",
    re.I,
)
_ONSITE_TERMS = re.compile(r"\b(?:on[- ]site|in[- ]office|onsite)\b", re.I)
_HYBRID_TERMS = re.compile(r"\bhybrid\b", re.I)

# Eligibility restrictions (reuse quality.py's verdicts where possible, but the
# job text is re-scanned here for an explainable per-component answer).
_ELIGIBILITY_RESTRICTION = re.compile(
    r"(?:\b(?:US|USA|U\.S\.|United States|UK|United Kingdom|Canadian|EU|European|"
    r"Australian)\s+(?:citizens|nationals|residents|persons)\s+only\b"
    r"|\bUS\b.{0,12}\bcitizens?(?:hip)?\b.{0,20}\b(?:only|required|must)\b"
    r"|\bcitizenship\s+(?:is\s+)?required\b"
    r"|\bsecurity\s+clearance\b"
    r"|\bno\s+(?:visa\s+)?sponsorship\b"
    r"|\bmust\s+be\s+(?:authorized|eligible)\s+to\s+work\s+in\b)",
    re.I,
)


@dataclass(frozen=True)
class JobRequirements:
    """Structured facts extracted deterministically from a job posting.

    Every field is optional: a posting that says nothing about years or
    seniority yields ``None`` rather than a guess.
    """

    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    min_years_experience: float | None = None
    seniority: str | None = None
    contract_types: tuple[str, ...] = ()
    remote_requirement: str | None = None
    location_restriction: str | None = None

    # --- Phase 2B additions -------------------------------------------------
    # All are optional and default to "not stated". Silence is never turned into
    # "no requirement": an absent education or language field means the posting
    # did not mention one, not that none is needed.
    contract_duration_months: float | None = None
    education_level: str | None = None      # bachelors | masters | doctorate | equivalent_experience
    languages_required: tuple[str, ...] = ()
    timezone_requirement: str | None = None

    @property
    def has_skill_signal(self) -> bool:
        """True when the posting names any skill at all."""
        return bool(self.required_skills or self.preferred_skills)


@dataclass(frozen=True)
class MatchResult:
    """Profile-aware match outcome for one job.

    Numeric components are ``None`` when unknown (never 0-as-unknown); strings
    carry an explicit verdict so a report can print "UNKNOWN" instead of a
    misleading value.
    """

    # --- numeric components (None = unknown) ---
    technical_score: int | None = None
    experience_score: int | None = None
    contract_score: int | None = None
    eligibility_score: int | None = None
    overall_score: int | None = None

    # --- verdicts ---
    seniority_alignment: str = UNKNOWN
    experience_match: str = UNKNOWN
    contract_fit: str = UNKNOWN
    eligibility_verdict: str = UNKNOWN

    # --- evidence ---
    matched_required: tuple[str, ...] = ()
    matched_preferred: tuple[str, ...] = ()
    missing_required: tuple[str, ...] = ()
    missing_preferred: tuple[str, ...] = ()
    experience_gap_years: float | None = None
    explanation: tuple[str, ...] = ()

    @property
    def is_fully_unknown(self) -> bool:
        return self.overall_score is None
    r"|\bUS\b.{0,12}\bcitizens?(?:hip)?\b.{0,20}\b(?:only|required|must)\b"
    r"|\bcitizenship\s+(?:is\s+)?required\b"
# --- extraction -------------------------------------------------------------

def _searchable(job: Job) -> str:
    """All free text on a job, used only for pattern matching."""
    return " \n ".join(
        part for part in (job.title, job.location, job.description) if part
    )


def extract_seniority(text: str) -> str | None:
    """Seniority implied by the wording, or ``None`` when absent."""
    for name, terms in _SENIORITY_TERMS:
        for term in terms:
            if re.search(rf"(?<!\w)(?:{term})(?!\w)", text, re.I):
                return name
    return None


def extract_requirements(job: Job, profile: CandidateProfile) -> JobRequirements:
    """Extract structured requirements from a job posting, deterministically.

    Skills are found by scanning the posting against :data:`JOB_SKILL_VOCABULARY`
    - deliberately independent of the candidate's profile, so a skill the
    candidate lacks is still *discovered* and can be reported as missing. They
    are split into required/preferred by nearby hint wording; a skill named
    without a hint is treated as preferred, the conservative reading of an
    unlabelled mention.

    ``profile`` is accepted for symmetry and future use; it does not narrow the
    scan.
    """
    text = _searchable(job)

    min_years = _extract_years(text)
    seniority = extract_seniority(job.title or "") or extract_seniority(text)

    contract_types = tuple(
        term for term in _CONTRACT_TERMS
        if re.search(rf"(?<!\w)(?:{term})(?!\w)", text, re.I)
    )

    remote = _extract_remote(text)

    # Each canonical name *and* each alias is searched, then reported under its
    # canonical name. Searching canonical names alone would miss a posting that
    # only says "s3" or "k8s", which is the common case.
    vocabulary = vocabulary_for(profile)
    aliases = alias_map(vocabulary)
    phrases = canonical_names(vocabulary) + tuple(aliases)

    required: list[str] = []
    preferred: list[str] = []
    for sentence in _sentences(text):
        found = matched_phrases(sentence, phrases)
        if not found:
            continue
        is_required = bool(_REQUIRED_HINT.search(sentence))
        for phrase in found:
            # An alias hit is reported under the canonical skill name.
            skill = aliases.get(phrase.lower(), phrase)
            if is_required:
                if skill not in required:
                    required.append(skill)
            elif skill not in preferred and skill not in required:
                preferred.append(skill)

    return JobRequirements(
        required_skills=tuple(required),
        preferred_skills=tuple(preferred),
        min_years_experience=min_years,
        seniority=seniority,
        contract_types=contract_types,
        remote_requirement=remote,
        location_restriction=(job.location or None),
        contract_duration_months=_extract_duration(text),
        education_level=_extract_education(text),
        languages_required=_extract_languages(text),
        timezone_requirement=_timezone_requirement(job),
    )


# Skills a posting can ask for, independent of what any candidate declares.
# Extraction scans the posting against this vocabulary; the profile is then
# compared against the result. Extracting only the profile's own skills (an
# earlier version) made a missing skill undetectable by construction.
#
# The vocabulary itself - canonical names, aliases and categories - lives in
# ``schemahawk.skills``. ``JOB_SKILL_VOCABULARY`` above is the flat view kept
# for backwards compatibility; a second definition here would silently shadow it.

# Sentence-ish chunks. The trailing lookaround keeps a final token that ends at
# the end of the text matchable: splitting "use Snowflake." on the period leaves
# "Snowflake" with no trailing boundary, and ``(?!\w)`` needs one.
_SENTENCE = re.compile(r"[^\n.;|]*\w[^\n.;|]*")


def _sentences(text: str) -> list[str]:
    """Split posting text into sentence-ish chunks for hint-context matching."""
    return [chunk for chunk in (part.strip() for part in _SENTENCE.findall(text)) if chunk]


def _extract_years(text: str) -> float | None:
    """The largest 'N+ years' requirement, or ``None`` when unstated.

    Takes the maximum because a range like "3-5 years" is satisfied by the
    upper bound; using the maximum keeps the requirement honest (highest stated
    demand) rather than optimistic.
    """
    years: list[float] = []
    for raw in _YEARS.findall(text):
        value = re.sub(r"[^\d]", "", raw)
        if value:
            years.append(float(value))
    return max(years) if years else None


def _extract_remote(text: str) -> str | None:
    if _HYBRID_TERMS.search(text):
        return "hybrid"
    if _ONSITE_TERMS.search(text):
        return "onsite"
    if _REMOTE_TERMS.search(text):
        return "remote"
    return None


# --- Phase 2B: duration / education / language ------------------------------
#
# Timezone requirements are derived from the eligibility pass rather than
# re-patterned, so the ``TIMEZONE_RESTRICTED`` scope and this field can never
# disagree with each other.
def _timezone_requirement(job: Job) -> str | None:
    """The timezone the posting constrains work to, or ``None`` when unstated.

    Derived from the eligibility pass rather than re-patterned, so the
    ``TIMEZONE_RESTRICTED`` scope and this field can never disagree. Only an
    explicit constraint is reported: "flexible hours, any timezone" stays
    ``None`` rather than becoming a requirement that does not exist.
    """
    from .eligibility import assess as _assess
    from .eligibility import timezone_constraint as _zone

    if _assess(job).scope is not LocationScope.TIMEZONE_RESTRICTED:
        return None
    return _zone(_searchable(job))

# These patterns are deliberately narrow. Live job text contains "degree of
# autonomy", "master modern approaches" and "master setup", so a bare "degree"
# or "master" must never be treated as an education requirement.
_DURATION_MONTHS = re.compile(
    r"\b(?:for\s+|initial\s+|term\s+of\s+)?(\d{1,2})\s*[- ]?\s*months?\b"
    r"|\b(?:initial\s+)?(\d{1,3})\s*[- ]?\s*(?:week|wks)s?\s+(?:contract|term|assignment)\b",
    re.I,
)

# "degree" only counts when it is a qualification: "... degree", "degree in X",
# "bachelor's degree". "degree of autonomy" must not match.
_EDUCATION = (
    ("doctorate", re.compile(
        r"\b(?:ph\.?d|doctorate|doctoral)\b[^.]{0,30}\b(?:required|degree)\b"
        r"|\b(?:required|preferred)\b[^.]{0,30}\b(?:ph\.?d|doctorate)\b", re.I)),
    ("masters", re.compile(
        r"\bmaster(?:'s|')?\s+(?:degree|of\s+science|of\s+arts)\b"
        r"|\bmsc\b[^.]{0,25}\bdegree\b"
        r"|\b(?:required|preferred)\b[^.]{0,40}\bmaster(?:'s|')?\s+degree\b", re.I)),
    ("bachelors", re.compile(
        r"\bbachelor(?:'s|')?\s+(?:degree|of\s+science|of\s+arts)\b"
        r"|\bb\.?s\.?c\b"
        r"|\b(?:required|preferred)\b[^.]{0,40}\bbachelor(?:'s|')?\b", re.I)),
    # "degree or equivalent practical experience" - a real, common hedge.
    ("equivalent_experience", re.compile(
        r"\bdegree\b[^.]{0,30}\bor\s+equivalent\b", re.I)),
)

# Languages named as a requirement. "English" only counts with a requirement
# or proficiency cue nearby.
_LANGUAGES: tuple[str, ...] = (
    "English", "German", "French", "Spanish", "Italian", "Portuguese",
    "Dutch", "Polish", "Swedish", "Danish", "Norwegian", "Finnish",
    "Hindi", "Mandarin", "Chinese", "Japanese", "Korean", "Arabic",
    "Russian", "Ukrainian", "Turkish",
)
_LANGUAGE_CUE = re.compile(
    r"\brequired\b|\bpreferred\b|\bmust\b|\bfluent\b|\bnative\b|\bproficient\b"
    r"|\bworking\s+knowledge\b|\bintermediate\b|\badvanced\b",
    re.I,
)


def _extract_duration(text: str) -> float | None:
    """Contract length in months, or ``None`` when no duration is stated."""
    best: float | None = None
    for match in _DURATION_MONTHS.finditer(text):
        months = match.group(1)
        weeks = match.group(2)
        if months:
            value = float(months)
        elif weeks:
            value = float(weeks) * 7.0 / 30.44      # weeks -> months, rounded later
        else:
            continue
        if best is None or value > best:
            best = value
    return round(best, 1) if best is not None else None


def _extract_education(text: str) -> str | None:
    """Highest qualification level stated, or ``None``.

    Ordered so a doctorate beats a masters beats a bachelors, and an
    "equivalent experience" hedge is only reported when it appears.
    """
    for level, pattern in _EDUCATION:
        if pattern.search(text):
            return level
    return None


def _extract_languages(text: str) -> tuple[str, ...]:
    """Languages named alongside a requirement/proficiency cue.

    A bare mention ("our team speaks Spanish") is not a requirement, so the
    cue must appear in the same clause.
    """
    found: list[str] = []
    for clause in _sentences(text):
        if not _LANGUAGE_CUE.search(clause):
            continue
        for language in _LANGUAGES:
            if re.search(rf"(?<!\w){re.escape(language)}(?!\w)", clause, re.I):
                if language not in found:
                    found.append(language)
    return tuple(found)


# --- scoring ---------------------------------------------------------------

def _satisfies(job_skills: tuple[str, ...], profile_skills: tuple[str, ...]) -> tuple[str, ...]:
    """Job skills the candidate covers, keeping the posting's spelling.

    A declared skill satisfies a requested one when the job skill appears
    alongside it, using the shared alias-aware matcher - so "ADF" in a posting
    satisfies a declared "Azure Data Factory (ADF)", and casing never matters.
    """
    covered: list[str] = []
    for skill in job_skills:
        haystack = skill
        if any(matcher_for_phrase(declared).search(haystack)
               for declared in profile_skills):
            covered.append(skill)
    return tuple(covered)


def _score_technical(req: JobRequirements, profile: CandidateProfile) -> tuple[
        int | None, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Skill coverage: required skills weigh double.

    Returns ``(score, matched_required, matched_preferred,
    missing_required, missing_preferred)``. Score is ``None`` when the posting
    names no skills at all *or* the profile declares none - there is nothing to
    measure, so no opinion is offered.

    A skill the candidate does not declare can never be credited: it lands in
    ``missing_required``/``missing_preferred`` and lowers the score, rather than
    being silently dropped and inflating it.
    """
    if not profile.skills or not req.has_skill_signal:
        return None, (), (), req.required_skills, req.preferred_skills

    # Compare the two lists case-insensitively, but keep the posting's own
    # spelling in the returned evidence. ``matched_phrases`` gives the alias-
    # aware, token-boundary match in the other direction; here we ask which
    # *job* skills are satisfied by something the candidate declared.
    matched_req = _satisfies(req.required_skills, profile.skill_names)
    matched_pref = _satisfies(req.preferred_skills, profile.skill_names)
    matched_req_set = {s.lower() for s in matched_req}
    matched_pref_set = {s.lower() for s in matched_pref}
    missing_req = tuple(s for s in req.required_skills if s.lower() not in matched_req_set)
    missing_pref = tuple(s for s in req.preferred_skills if s.lower() not in matched_pref_set)

    earned = 2 * len(matched_req) + len(matched_pref)
    possible = 2 * len(req.required_skills) + len(req.preferred_skills)
    score = round(100 * earned / possible) if possible else None
    return score, matched_req, matched_pref, missing_req, missing_pref


def _score_experience(req: JobRequirements, profile: CandidateProfile) -> tuple[
        int | None, float | None, str]:
    """Experience + seniority, renormalized over the part that is knowable.

    Returns ``(score, gap_years, verdict)``. ``score`` is ``None`` when neither
    side states anything; the verdict is ``UNKNOWN`` whenever the candidate has
    not declared years or seniority, because those facts are never invented.
    """
    parts: list[tuple[float, float]] = []      # (weight, score) within component
    gap: float | None = None
    verdict = UNKNOWN

    # --- years ---
    if profile.total_years_experience is not None and req.min_years_experience is not None:
        gap = max(0.0, req.min_years_experience - profile.total_years_experience)
        year_score = 100 if gap == 0 else max(0, 100 - 20 * gap)
        parts.append((0.5, year_score))
        verdict = NO_GAP if gap == 0 else BELOW
    else:
        if profile.total_years_experience is None:
            verdict = UNKNOWN   # never assumed
        elif req.min_years_experience is None:
            verdict = NO_GAP     # candidate has years, job asks for none

    # --- seniority ---
    seniority_alignment = _score_seniority(req.seniority, profile.seniority)
    if seniority_alignment != UNKNOWN:
        seniority_score = {ALIGNED: 100.0, "ADJACENT": 70.0, BELOW: 40.0}[seniority_alignment]
        parts.append((0.5, seniority_score))

    score = None
    if parts:
        total_weight = sum(weight for weight, _ in parts)
        score = round(sum(weight * value for weight, value in parts) / total_weight)
    return score, gap, verdict


def _score_seniority(job_seniority: str | None, profile_seniority: str | None) -> str:
    """ALIGNED / ADJACENT / BELOW / UNKNOWN.

    The term table is ordered most senior first, so a *smaller* rank is more
    senior. A candidate at or above the role's level is ALIGNED (they can
    cover it) rather than penalised; being one band under is ADJACENT, and two
    or more bands under is BELOW.
    """
    job_rank = _SENIORITY_RANK.get((job_seniority or "").lower())
    profile_rank = _SENIORITY_RANK.get((profile_seniority or "").lower())
    if job_rank is None or profile_rank is None:
        return UNKNOWN
    if profile_rank <= job_rank:          # at or above the required level
        return ALIGNED
    if profile_rank == job_rank + 1:      # one band below
        return "ADJACENT"
    return BELOW


def _score_contract(req: JobRequirements, profile: CandidateProfile) -> tuple[int | None, str]:
    """Contract type / remote / location fit, or ``None`` when unknowable.

    Each sub-check is added only when *both* sides actually state something, so
    an unstated preference or an unstated job location never counts as a pass.
    """
    checks: list[bool] = []

    if profile.preferred_contract_types and req.contract_types:
        wanted = {c.lower() for c in profile.preferred_contract_types}
        found = {c.lower() for c in req.contract_types}
        checks.append(bool(wanted & found))

    if profile.remote_preference and req.remote_requirement:
        wanted = profile.remote_preference.lower()
        got = req.remote_requirement.lower()
        if wanted in ("remote-first", "remote-only"):
            checks.append(got in ("remote", "hybrid"))
        elif wanted == "onsite":
            checks.append(got == "onsite")
        elif wanted == "hybrid":
            checks.append(got in ("hybrid", "remote"))

    if profile.preferred_locations and req.location_restriction:
        checks.append(_location_acceptable(req.location_restriction,
                                           profile.preferred_locations))

    if not checks:
        return None, UNKNOWN
    score = round(100 * sum(1 for ok in checks if ok) / len(checks))
    return score, (FIT if score == 100 else MISMATCH)


def _location_acceptable(job_location: str,
                         preferred: tuple[str, ...]) -> bool:
    """True when the posting's location satisfies the candidate's preferences.

    Deliberately conservative:

    - A worldwide / anywhere posting satisfies any preference explicitly, since
      the employer has stated they hire beyond their own country.
    - Otherwise the posting location must appear among the preferred locations.
      Matching is case-insensitive *substring* on the written text only.
    - No geographic inference is performed: "Europe" does not imply "Germany",
      and "EMEA" is not expanded into country lists. A posting we cannot place
      against the preference is treated as a mismatch rather than a pass.
    """
    location = job_location.strip().lower()
    if not location:
        return False
    if any(word in location for word in ("worldwide", "anywhere", "global")):
        return True
    return any(want.strip().lower() in location for want in preferred if want.strip())


# --- public entry point -----------------------------------------------------

def match_job(job: Job, profile: CandidateProfile) -> MatchResult:
    """Match one job against a candidate profile.

    Pure and deterministic: same job + same profile always yields the same
    result. An empty profile yields a fully UNKNOWN result rather than a
    zero score, and the V1 ``job.relevance_score`` is never touched.
    """
    req = extract_requirements(job, profile)

    technical, matched_req, matched_pref, missing_req, missing_pref = (
        _score_technical(req, profile)
    )
    experience, gap, experience_verdict = _score_experience(req, profile)
    contract, contract_verdict = _score_contract(req, profile)
    eligibility, eligibility_verdict = _score_eligibility(job)

    overall = _weighted_overall((
        (W_TECHNICAL, technical),
        (W_EXPERIENCE, experience),
        (W_CONTRACT, contract),
        (W_ELIGIBILITY, eligibility),
    ))

    return MatchResult(
        technical_score=technical,
        experience_score=experience,
        contract_score=contract,
        eligibility_score=eligibility,
        overall_score=overall,
        seniority_alignment=_score_seniority(req.seniority, profile.seniority),
        experience_match=experience_verdict,
        contract_fit=contract_verdict,
        eligibility_verdict=eligibility_verdict,
        matched_required=matched_req,
        matched_preferred=matched_pref,
        missing_required=missing_req,
        missing_preferred=missing_pref,
        experience_gap_years=gap,
        explanation=build_explanation(
            req, profile, technical, experience, contract, eligibility,
            matched_req, matched_pref, missing_req, missing_pref, gap,
            experience_verdict, contract_verdict, eligibility_verdict,
        ),
    )


def build_explanation(
    req: JobRequirements,
    profile: CandidateProfile,
    technical: int | None,
    experience: int | None,
    contract: int | None,
    eligibility: int | None,
    matched_req: tuple[str, ...],
    matched_pref: tuple[str, ...],
    missing_req: tuple[str, ...],
    missing_pref: tuple[str, ...],
    gap: float | None,
    experience_verdict: str,
    contract_verdict: str,
    eligibility_verdict: str,
) -> tuple[str, ...]:
    """Evidence-only sentences.

    Every line is derived from text that was actually found, or explicitly
    states that something is unknown. Nothing is assumed about the candidate.
    """
    lines: list[str] = []

    if technical is None:
        lines.append(f"Technical: UNKNOWN "
                     + ("(job lists no skills to match against)"
                        if req.has_skill_signal or profile.skills
                        else "(no skills declared in profile or job)"))
    else:
        parts = []
        if matched_req:
            parts.append(f"matched required {', '.join(matched_req)}")
        if matched_pref:
            parts.append(f"matched preferred {', '.join(matched_pref)}")
        lines.append(f"Technical {technical}" +
                     (f": {'; '.join(parts)}" if parts else ": no skills matched"))

    if missing_req:
        lines.append(f"Missing required skills: {', '.join(missing_req)}")
    if missing_pref:
        lines.append(f"Missing preferred skills: {', '.join(missing_pref)}")

    if experience_verdict == UNKNOWN:
        if profile.total_years_experience is None:
            lines.append("Experience: UNKNOWN (profile total_years_experience not specified)")
        elif req.min_years_experience is None:
            lines.append("Experience: UNKNOWN (job states no years requirement)")
        else:
            lines.append(f"Experience: UNKNOWN (score {experience})")
    elif experience_verdict == NO_GAP:
        lines.append(
            f"Experience: meets requirement"
            f" (profile {profile.total_years_experience:g} yrs"
            f" vs {req.min_years_experience:g}+ required)"
            if profile.total_years_experience is not None
            and req.min_years_experience is not None
            else f"Experience: {experience_verdict}")
    else:
        lines.append(f"Experience: gap of {gap:g} year(s)")

    alignment = _score_seniority(req.seniority, profile.seniority)
    if alignment == UNKNOWN:
        side = ("profile seniority not specified"
                if not profile.seniority else "job states no seniority")
        lines.append(f"Seniority: UNKNOWN ({side})")
    else:
        lines.append(f"Seniority: {alignment.lower()} "
                     f"(profile: {profile.seniority or 'unset'} | job: {req.seniority})")

    if contract is None:
        lines.append("Contract/remote/location: UNKNOWN "
                     "(no preference declared or job states nothing)")
    else:
        lines.append(f"Contract/remote/location: {contract_verdict.lower()} ({contract})")

    if eligibility is None:
        lines.append("Eligibility: UNKNOWN (job states no restriction)")
    else:
        lines.append(f"Eligibility: {eligibility_verdict.lower()} ({eligibility})")

    return tuple(lines)


__all__ = [
    "JobRequirements",
    "MatchResult",
    "extract_requirements",
    "extract_seniority",
    "match_job",
    "build_explanation",
    "UNKNOWN",
]


def _score_eligibility(job: Job) -> tuple[int | None, str]:
    """100 no restriction, 0 explicit restriction, else UNKNOWN."""
    text = _searchable(job)
    if _ELIGIBILITY_RESTRICTION.search(text):
        return 0, MISMATCH
    # Absence of a restriction is not proof of eligibility, so a job that
    # simply says nothing stays UNKNOWN rather than being granted 100.
    return None, UNKNOWN


def _weighted_overall(components: tuple[tuple[float, int | None], ...]) -> int | None:
    """Weighted mean over the knowable components only."""
    known = [(weight, value) for weight, value in components if value is not None]
    if not known:
        return None
    total = sum(weight for weight, _ in known)
    return round(sum(weight * value for weight, value in known) / total)