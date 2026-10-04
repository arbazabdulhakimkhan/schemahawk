"""Skill vocabulary tests (Phase 2A).

These pin the three properties the expansion depends on: a skill is recognised
by canonical name *and* alias, never matched inside a longer word, and never
inflated by an ambiguous bare term.
"""
from __future__ import annotations

import pytest

from schemahawk.matching import JOB_SKILL_VOCABULARY, extract_requirements
from schemahawk.models import Job
from schemahawk.profile import parse_profile
from schemahawk.skills import (
    DEFAULT_VOCABULARY,
    alias_map,
    canonical_names,
    vocabulary_for,
)

# The 37 skills that shipped in Phase 1. Their canonical names must not change,
# because operator profiles and stored jobs reference them.
ORIGINAL_37 = (
    "SQL", "Python", "Java", "Scala", "Spark", "PySpark", "Databricks",
    "Snowflake", "Google BigQuery", "Redshift", "Synapse",
    "Azure Data Factory (ADF)", "Airflow", "dbt", "Kafka", "ETL / ELT",
    "Informatica", "SSIS", "Talend", "Alteryx Designer", "Power BI",
    "Power Query", "Tableau Desktop", "Tableau Server", "DAX", "Azure SQL",
    "REST APIs", "Jira REST API", "GitHub", "Data quality / QA",
    "Dashboard development", "Data Modeling", "AWS", "Azure", "GCP", "Hadoop",
    "Hive",
)


def extracted(text: str, profile_skills=("Python",)) -> tuple:
    job = Job(source="t", title="Data Engineer", company="C",
              description=text, location=None)
    reqs = extract_requirements(
        job, parse_profile({"version": 1, "skills": list(profile_skills)}))
    return reqs.required_skills + reqs.preferred_skills


def extracted_with(text: str, profile) -> tuple:
    """Extract using a caller-supplied profile (for operator extensions)."""
    job = Job(source="t", title="Data Engineer", company="C",
              description=text, location=None)
    reqs = extract_requirements(job, profile)
    return reqs.required_skills + reqs.preferred_skills


# --- structure --------------------------------------------------------------

def test_vocabulary_grew_and_has_no_duplicates():
    names = [s.canonical.lower() for s in DEFAULT_VOCABULARY]
    assert len(names) == len(set(names)), "duplicate canonical skill names"


def test_vocabulary_is_substantially_larger_than_the_original():
    assert len(DEFAULT_VOCABULARY) > len(ORIGINAL_37)
    # A floor, so an accidental deletion fails rather than silently shrinking it.
    assert len(DEFAULT_VOCABULARY) >= 80


def test_all_original_37_skills_are_preserved_exactly():
    names = set(canonical_names())
    missing = [s for s in ORIGINAL_37 if s not in names]
    assert not missing, f"original skills renamed or dropped: {missing}"


def test_every_skill_has_a_non_empty_category():
    assert all(s.category.strip() for s in DEFAULT_VOCABULARY)



# --- new canonical skills detected -----------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("We use PostgreSQL and MySQL in production.", "PostgreSQL"),
    ("Runs on Kubernetes with Docker.", "Kubernetes"),
    ("Managed through Terraform.", "Terraform"),
    ("Integrate via GraphQL and gRPC.", "GraphQL"),
    ("Reporting in Looker for executives.", "Looker"),
    ("Data lives in SQL Server.", "SQL Server"),
    ("Orchestrated with Dagster and Prefect.", "Dagster"),
    ("Streaming ingest via Apache Flink.", "Apache Flink"),
    ("Built pipelines with Trino.", "Trino"),
    ("Dashboard in Domo and Qlik.", "Domo"),
    ("Salesforce is the system of record.", "Salesforce"),
    ("Shopify plus Stripe events.", "Shopify"),
    ("Deep expertise in Excel required.", "Excel"),
    ("Data governance and data lineage are key.", "Data governance"),
    ("We maintain a data catalog.", "Data catalog"),
    ("Validating with Great Expectations.", "Great Expectations"),
    ("CI runs on GitHub Actions.", "GitHub Actions"),
    ("Exposed through webhooks.", "Webhooks"),
])
def test_new_skills_are_detected(text, expected):
    assert expected in extracted(text)


# --- aliases ----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Store raw files in S3.", "Amazon S3"),
    ("Cataloguing runs in Glue.", "AWS Glue"),
    ("Query cost control in BigQuery.", "Google BigQuery"),
    ("Dashboards built in Tableau.", "Tableau Desktop"),
    ("ETL pipelines in ADF.", "Azure Data Factory (ADF)"),
    ("postgres is our primary store.", "PostgreSQL"),
    ("Runners on k8s.", "Kubernetes"),
])
def test_aliases_resolve_to_the_canonical_skill(text, expected):
    assert expected in extracted(text)


def test_one_technology_is_never_counted_as_two_skills():
    hits = extracted("We use BigQuery daily.")
    assert "Google BigQuery" in hits
    assert "BigQuery" not in hits      # the alias is not a separate entry


# --- token boundaries and false positives ----------------------------------

def test_new_skills_do_not_match_inside_longer_words():
    assert extracted("We need as3 in the name.") == ()
    assert extracted("An onsite role in Bengaluru.") == ()


@pytest.mark.parametrize("text", [
    "R&D budget approved for the team.",
    "We are a leading company in the UAE hiring staff.",
    "Quality time with the family is required.",
    "We attend a major API conference each year.",
    "Analyze market segmentation and run segment campaigns.",
    "Fast-paced retail environment with great benefits.",
    "Integration tests run nightly in CI.",
    "Our pipeline is robust. You will work on strategy.",
])
def test_ambiguous_bare_terms_are_not_skills(text):
    """Terms that would produce false positives are deliberately absent.

    ``R`` matches "R&D", ``segment`` matches "segmentation", ``quality`` matches
    "quality time", ``api`` matches "retail". Requiring a qualified phrase is
    the trade this makes: less recall, far fewer wrong matches.
    """
    hits = extracted(text)
    for bogus in ("R", "segment", "Quality", "quality", "API", "api",
                  "integration", "pipeline"):
        assert bogus not in hits, f"{bogus!r} should not be a skill"


def test_excluded_ambiguous_terms_are_not_in_the_vocabulary():
    names = {s.canonical.lower() for s in DEFAULT_VOCABULARY}
    for banned in ("r", "api", "quality", "segment", "pipeline", "integration"):
        assert banned not in names


# --- operator extension -----------------------------------------------------

def test_vocabulary_for_returns_the_default_without_a_profile():
    assert vocabulary_for(None) == DEFAULT_VOCABULARY


def test_operator_can_add_extra_skills():
    profile = parse_profile({"version": 1, "preferences": {
        "extra_skills": [{"name": "Dremio", "category": "database"}]}})
    names = [s.canonical for s in vocabulary_for(profile)]
    assert "Dremio" in names
    assert "Dremio" in extracted_with("We run queries on Dremio.", profile)


def test_extra_skills_may_carry_aliases():
    profile = parse_profile({"version": 1, "preferences": {
        "extra_skills": [{"name": "Dremio", "aliases": ["dremio engine"]}]}})
    assert "Dremio" in extracted_with("Built on the dremio engine.", profile)


def test_extra_skill_duplicating_a_builtin_is_ignored():
    profile = parse_profile({"version": 1, "preferences": {
        "extra_skills": ["Snowflake", "Python"]}})
    names = [s.canonical for s in vocabulary_for(profile)]
    assert names.count("Snowflake") == 1
    assert len(names) == len(DEFAULT_VOCABULARY)


def test_malformed_extra_skills_are_ignored_not_fatal():
    profile = parse_profile({"version": 1, "preferences": {
        "extra_skills": [None, 42, {"name": ""}, "   ", {"name": "Dremio"}]}})
    names = [s.canonical for s in vocabulary_for(profile)]
    assert names.count("Dremio") == 1
    assert len(names) == len(DEFAULT_VOCABULARY) + 1


def test_compatibility_alias_matches_the_vocabulary():
    assert JOB_SKILL_VOCABULARY == canonical_names(DEFAULT_VOCABULARY)


def test_aliases_never_collide_with_a_canonical_name():
    canonicals = {s.canonical.lower() for s in DEFAULT_VOCABULARY}
    assert not (set(alias_map()) & canonicals)

