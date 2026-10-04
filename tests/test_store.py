"""SQLite store tests: schema, upsert semantics, run records, read-only mode."""
from __future__ import annotations

import pytest

from schemahawk.report import RunReport
from schemahawk.store import Store, iso_utc

from conftest import NOW, make_job


def test_schema_and_empty_reads():
    with Store(None) as store:
        assert store.count_jobs() == 0
        assert store.counts_by_status() == {}
        keys = store.existing_keys()
        assert keys == {"urls": set(), "source_ids": set(),
                        "hashes": set(), "company_titles": set()}


def test_insert_then_update_increments_times_seen():
    with Store(None) as store:
        job = make_job()
        from schemahawk.normalize import attach_keys
        attach_keys(job)
        assert store.upsert_jobs([job]) == (1, 0)

        again = make_job()  # same URL / company / title / description
        attach_keys(again)
        assert store.upsert_jobs([again]) == (0, 1)
        assert store.count_jobs() == 1
        times_seen = store.conn.execute(
            "SELECT times_seen FROM jobs").fetchone()[0]
        assert times_seen == 2


def test_inserted_job_gets_an_id_and_persists_keys():
    with Store(None) as store:
        job = make_job(url="https://Example.com/jobs/9?utm_source=news")
        from schemahawk.normalize import attach_keys
        attach_keys(job)
        store.upsert_jobs([job])
        assert job.id
        keys = store.existing_keys()
        assert "https://example.com/jobs/9" in keys["urls"]
        assert ("fake", "123") in keys["source_ids"]
        assert job.content_hash in keys["hashes"]


def test_url_less_job_uses_content_hash_fallback():
    with Store(None) as store:
        from schemahawk.normalize import attach_keys
        job = make_job(url=None, source_job_id=None)
        attach_keys(job)
        store.upsert_jobs([job])
        again = make_job(url=None, source_job_id=None)
        attach_keys(again)
        assert store.upsert_jobs([again]) == (0, 1)
        assert store.count_jobs() == 1


def test_record_run_stores_report_and_returns_id():
    with Store(None) as store:
        report = RunReport(started_at=NOW, completed_at=NOW)
        report.discovered = 12
        report.fresh = 3
        report.fail("adzuna", "HTTP 401")
        run_id = store.record_run(report)
        assert run_id > 0 and report.run_id == run_id

        row = store.latest_runs(1)[0]
        assert row["discovered"] == 12
        assert row["fresh_count"] == 3
        assert "adzuna: HTTP 401" in row["sources_failed"]
        assert "SchemaHawk - Discovery Run" in row["report_text"]
        assert "Source failures:" in row["report_text"]


def test_file_backed_store_and_read_only_reader(tmp_path):
    path = str(tmp_path / "data" / "schemahawk.db")
    job = make_job()
    from schemahawk.normalize import attach_keys
    attach_keys(job)
    with Store(path) as writer:
        writer.upsert_jobs([job])

    with Store(path, read_only=True) as reader:
        assert reader.count_jobs() == 1
        assert "https://example.com/jobs/123" in reader.existing_keys()["urls"]
        with pytest.raises(RuntimeError):
            reader.upsert_jobs([make_job()])
        with pytest.raises(RuntimeError):
            reader.record_run(RunReport(started_at=NOW))


def test_iso_utc_serialization():
    assert iso_utc(None) is None
    assert iso_utc(NOW) == "2026-01-15T12:00:00+00:00"
