"""Deduplication tests: each signal, plus the "do not over-merge" guarantee."""
from __future__ import annotations

from schemahawk.dedupe import dedupe
from schemahawk.normalize import attach_keys

from conftest import make_job


def _prepare(jobs):
    for job in jobs:
        attach_keys(job)
    return jobs


def test_same_canonical_url_is_duplicate():
    a = make_job(url="https://example.com/jobs/1?utm_source=x", source_job_id="1")
    b = make_job(url="https://www.example.com/jobs/1/", source_job_id="2",
                 company="Other Co", title="Different Role Entirely")
    unique, duplicates = dedupe(_prepare([a, b]))
    assert len(unique) == 1
    assert duplicates[0][1] == "same canonical URL"


def test_same_source_job_id_is_duplicate():
    a = make_job(url="https://example.com/a", source_job_id="42")
    b = make_job(url="https://example.com/b", source_job_id="42")
    unique, duplicates = dedupe(_prepare([a, b]))
    assert len(unique) == 1
    assert duplicates[0][1] == "same source job id"


def test_same_content_hash_is_duplicate_across_sources():
    a = make_job(url="https://a.example/jobs/1", source_job_id="1", source="remoteok")
    b = make_job(url="https://b.example/jobs/9", source_job_id="9", source="jobicy")
    unique, duplicates = dedupe(_prepare([a, b]))
    assert len(unique) == 1
    assert duplicates[0][1] == "same content hash"


def test_same_company_and_title_is_duplicate():
    a = make_job(url="https://a.example/1", source_job_id="1",
                 description="Totally different text one")
    b = make_job(url="https://b.example/2", source_job_id="2",
                 description="Totally different text two")
    unique, duplicates = dedupe(_prepare([a, b]))
    assert len(unique) == 1
    assert duplicates[0][1] == "same company+title"


def test_near_duplicate_via_similarity():
    a = make_job(url="https://a.example/1", source_job_id="1",
                 title="Senior Data Engineer Azure Databricks Snowflake Airflow dbt Kafka Spark",
                 description="Build ETL pipelines with Python and SQL every day")
    b = make_job(url="https://b.example/2", source_job_id="2",
                 title="Senior Data Engineer Azure Databricks Snowflake Airflow dbt BigQuery Spark",
                 description="Build ETL pipelines with Python and SQL every day")
    c = make_job(company="Other Co", title="Data Analyst",
                 description="Dashboards and reporting", url="https://b.example/3",
                 source_job_id="3")
    unique, duplicates = dedupe(_prepare([a, b, c]))
    assert len(unique) == 2
    assert duplicates and "near-duplicate" in duplicates[0][1]
    assert any(job.title == "Data Analyst" for job in unique)


def test_no_company_still_dedupes_by_similarity():
    a = make_job(company=None, title="Freelance Data Engineer",
                 description="Long identical description text here", url="https://a.example/1",
                 source_job_id="1")
    b = make_job(company=None, title="Freelance Data Engineer",
                 description="Long identical description text here", url="https://b.example/2",
                 source_job_id="2")
    unique, _ = dedupe(_prepare([a, b]))
    assert len(unique) == 1


def test_cross_run_dedup_uses_known_keys():
    a = make_job(url="https://example.com/jobs/1", source_job_id="1")
    b = make_job(url="https://example.com/jobs/1", source_job_id="1")
    _prepare([a, b])
    unique, duplicates = dedupe([b], known_urls={a.url_canonical})
    assert unique == []
    assert duplicates[0][1] == "same canonical URL"


def test_distinct_jobs_same_company_are_kept():
    a = make_job(title="Data Engineer", url="https://a/1", source_job_id="1",
                 description="Spark and Kafka experience required")
    b = make_job(title="Analytics Engineer", url="https://a/2", source_job_id="2",
                 description="dbt and Snowflake experience required")
    unique, duplicates = dedupe(_prepare([a, b]))
    assert len(unique) == 2
    assert duplicates == []


def test_jobs_without_any_key_are_kept():
    a = make_job(company=None, description=None, url=None, source_job_id=None)
    b = make_job(company=None, description=None, url=None, source_job_id=None)
    unique, _ = dedupe(_prepare([a, b]))
    assert len(unique) == 2
