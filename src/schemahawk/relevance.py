"""Deterministic, rule-based relevance scoring (V1).

No LLM calls in V1: this keeps scoring cheap, explainable and reproducible in
CI. AI-assisted matching is a V2 concern. Signals are configurable via
``RELEVANCE_EXTRA_KEYWORDS`` in the environment.

Keywords are regex fragments matched on token boundaries - ``(?<!\\w)`` before
and ``(?!\\w)`` after - so substrings never create false positives. This was
added after live data showed "ssis" matching inside "a**ssis**tant" and "sql"
matching inside "postgresql". The matcher itself lives in ``textmatch`` so the
scorer and the V2 match engine share one implementation.

Scoring (0-100):
  +60  the title contains a strong data-engineering role phrase
  +N   title skill keywords (table below)
  +N   description skill keywords, capped at 20 so a long JD cannot dominate
  +10  explicit freelance/contract wording in the title
  cap 5 when the title only matches a non-data role word
"""
from __future__ import annotations

from .models import Job
from .textmatch import plain_fragment, token_matcher

# Strong data-engineering role phrases.
STRONG_ROLES: tuple[str, ...] = (
    r"data\s+engineer\w*", r"etl", r"elt", r"etl\s+developer\w*",
    r"analytics\s+engineer\w*", r"data\s+platform\w*",
    r"databricks\s+engineer\w*", r"snowflake\s+engineer\w*",
    r"bigquery\s+engineer\w*", r"bi\s+data\s+engineer\w*", r"bi\s+developer\w*",
    r"business\s+intelligence", r"data\s+pipelines?", r"data\s+warehous\w*",
    r"big\s+data\s+engineer\w*", r"data\s+integration", r"informatica", r"ssis",
    r"data\s+modeler\w*",
)
STRONG_ROLE_SCORE = 60

# Skill keywords found in the title.
TITLE_SKILLS: tuple[tuple[str, int], ...] = (
    (r"azure\s+data\s+factory", 10), (r"data\s+factory", 8), (r"adf", 8),
    (r"sql", 7), (r"python", 7), (r"databricks", 9), (r"snowflake", 9),
    (r"bigquery", 9), (r"pyspark", 7), (r"spark", 5), (r"power\s+bi", 7),
    (r"tableau", 6), (r"alteryx", 7), (r"dbt", 7), (r"airflow", 7),
    (r"kafka", 5), (r"ssis", 7), (r"informatica", 7), (r"talend", 6),
    (r"data\s+warehous\w*", 10), (r"data\s+lake\w*", 9), (r"data\s+pipelines?", 10),
    (r"etl", 10), (r"elt", 10), (r"azure", 4), (r"aws", 3), (r"gcp", 3),
)

# Skill keywords found in the description (capped so long JDs cannot dominate).
DESC_SKILLS: tuple[tuple[str, int], ...] = (
    (r"sql", 3), (r"python", 3), (r"databricks", 4), (r"snowflake", 4),
    (r"bigquery", 4), (r"azure\s+data\s+factory", 4), (r"adf", 3), (r"etl", 3),
    (r"elt", 3), (r"spark", 2), (r"pyspark", 3), (r"power\s+bi", 3),
    (r"tableau", 2), (r"alteryx", 3), (r"dbt", 3), (r"airflow", 3), (r"kafka", 2),
    (r"data\s+warehous\w*", 4), (r"data\s+lake\w*", 3), (r"data\s+pipelines?", 4),
)
DESC_CAP = 20

# Freelance/contract context bonus (applied to the title only).
CONTRACT_WORDS: tuple[str, ...] = (
    r"freelance", r"contract\w*", r"part[-\s]?time", r"project[-\s]based",
    r"temporary",
)
CONTRACT_BONUS = 10

# A non-data title gets capped low.
NEGATIVE_STRONG: tuple[str, ...] = (
    r"front[-\s]?end", r"graphic\s+designer\w*", r"ui\s+designer\w*",
    r"ux\s+designer\w*", r"social\s+media", r"content\s+writer\w*",
    r"copywriter\w*", r"sales\s+representative\w*", r"sales\s+executive\w*",
    r"recruiter\w*", r"telecaller\w*", r"business\s+development",
    r"video\s+editor\w*", r"customer\s+support",
)
NEGATIVE_FLOOR = 5


_STRONG_MATCHERS = tuple(token_matcher(fragment) for fragment in STRONG_ROLES)
_TITLE_MATCHERS = tuple((token_matcher(f), p) for f, p in TITLE_SKILLS)
_DESC_MATCHERS = tuple((token_matcher(f), p) for f, p in DESC_SKILLS)
_CONTRACT_MATCHERS = tuple(token_matcher(f) for f in CONTRACT_WORDS)
_NEGATIVE_MATCHERS = tuple(token_matcher(f) for f in NEGATIVE_STRONG)


def score_relevance(job: Job, extra_strong_roles: tuple[str, ...] = ()) -> int:
    """Compute ``job.relevance_score`` (0-100) and return it."""
    title = job.title or ""
    description = job.description or ""

    extra = tuple(token_matcher(plain_fragment(role))
                  for role in extra_strong_roles if role and role.strip())
    has_strong_role = (
        any(pattern.search(title) for pattern in _STRONG_MATCHERS)
        or any(pattern.search(title) for pattern in extra)
    )

    score = 0
    if has_strong_role:
        score += STRONG_ROLE_SCORE
    elif any(pattern.search(title) for pattern in _NEGATIVE_MATCHERS):
        score = min(score, NEGATIVE_FLOOR)

    for pattern, points in _TITLE_MATCHERS:
        if pattern.search(title):
            score += points

    desc_points = 0
    for pattern, points in _DESC_MATCHERS:
        if pattern.search(description):
            desc_points += points
    score += min(desc_points, DESC_CAP)

    if any(pattern.search(title) for pattern in _CONTRACT_MATCHERS):
        score += CONTRACT_BONUS

    job.relevance_score = max(0, min(100, score))
    return job.relevance_score
