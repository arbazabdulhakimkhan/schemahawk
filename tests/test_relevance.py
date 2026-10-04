"""Deterministic relevance scoring tests."""
from __future__ import annotations

from schemahawk.relevance import score_relevance

from conftest import make_job


def test_strong_title_scores_high():
    job = make_job(title="Senior Data Engineer",
                   description="Azure Data Factory, SQL, Python, Databricks")
    assert score_relevance(job) >= 70


def test_score_is_capped_at_100():
    job = make_job(
        title="Freelance Data Engineer SQL Python Databricks Snowflake Power BI Azure "
              "Data Factory Airflow dbt Kafka",
        description="SQL Python Databricks Snowflake BigQuery ADF ETL ELT Spark Airflow",
    )
    assert score_relevance(job) == 100


def test_non_data_role_scores_low():
    job = make_job(title="Graphic Designer", description="Photoshop and Illustrator")
    score = score_relevance(job)
    assert score < 60
    assert score <= 5


def test_strong_role_without_skills_still_scores_above_threshold():
    job = make_job(title="Data Engineer", description="Join our team.")
    assert score_relevance(job) >= 60


def test_description_cap_prevents_jd_keyword_stuffing():
    description = "sql python databricks snowflake bigquery etl elt spark airflow" * 20
    job = make_job(title="Operations Manager", description=description)
    # Base title gives nothing; the description contribution is capped at 20.
    assert score_relevance(job) <= 20


def test_contract_bonus_applies_to_title():
    plain = make_job(title="Data Engineer")
    contract = make_job(title="Data Engineer (Freelance Contract)")
    assert score_relevance(contract) > score_relevance(plain)


def test_extra_keywords_are_configurable():
    job = make_job(title="Looker Developer", description="")
    baseline = score_relevance(job)
    boosted = score_relevance(job, ("looker developer",))
    assert baseline == 0
    assert boosted == 60


def test_scoring_is_deterministic():
    job = make_job()
    assert score_relevance(job) == score_relevance(job)


def test_keyword_matching_ignores_substrings():
    """Regression: live data had "ssis" matching inside "assistant"."""
    job = make_job(title="Executive Assistant to the CEO",
                   description="Scheduling, travel and admin support.")
    assert score_relevance(job) < 60

    assert score_relevance(make_job(title="Junior Payroll Assistant", description="")) < 60


def test_keyword_matching_allows_plural_and_gerund_forms():
    assert score_relevance(make_job(title="Data Warehousing Specialist", description="")) >= 60
    assert score_relevance(make_job(title="Analytics Engineer", description="")) >= 60


def test_negative_role_words_only_cap_after_strong_check():
    job = make_job(title="Frontend Data Engineer", description="")
    # "data engineer" wins: a strong role phrase is never capped by a negative.
    assert score_relevance(job) >= 60
