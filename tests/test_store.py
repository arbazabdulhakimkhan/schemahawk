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

def test_compensation_round_trips_through_storage():
    from schemahawk.models import Compensation
    job = make_job(url="https://e.com/pay", source_job_id="pay")
    job.compensation = Compensation(min_value=30.0, max_value=45.0,
                                    currency="USD", period="hour",
                                    raw="$30 - $45 per hour")
    with Store(None) as store:
        assert store.upsert_jobs([job]) == (1, 0)
        row = store.conn.execute(
            "SELECT compensation_min, compensation_max, compensation_currency,"
            " compensation_period, compensation_raw FROM jobs"
        ).fetchone()
    assert row["compensation_min"] == 30.0
    assert row["compensation_max"] == 45.0
    assert row["compensation_currency"] == "USD"
    assert row["compensation_period"] == "hour"
    assert row["compensation_raw"] == "$30 - $45 per hour"


def test_missing_compensation_stores_null_not_zero():
    """A job with no published pay must not look like it pays nothing."""
    from schemahawk.normalize import attach_keys
    job = make_job(url="https://e.com/nopay", source_job_id="nopay")
    attach_keys(job)
    assert job.compensation is None
    with Store(None) as store:
        store.upsert_jobs([job])
        row = store.conn.execute(
            "SELECT compensation_min, compensation_currency FROM jobs").fetchone()
    assert row["compensation_min"] is None
    assert row["compensation_currency"] is None


def test_legacy_database_gains_compensation_columns(tmp_path):
    """An existing database created before these columns must keep working."""
    import sqlite3

    from schemahawk.store import Store

    path = tmp_path / "legacy.db"
    with Store(str(path)):
        pass  # create the current schema
    # Rebuild the table without the compensation columns, as an older release.
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    legacy = [c for c in cols if not c.startswith("compensation_")]
    int_cols = {"times_seen", "timestamp_confidence", "freshness_minutes",
                "relevance_score", "remote"}
    decls = ", ".join(f"{c} {'INTEGER' if c in int_cols else 'TEXT'}"
                      for c in legacy)
    conn.execute("ALTER TABLE jobs RENAME TO jobs_old")
    conn.execute(f"CREATE TABLE jobs ({decls})")
    conn.execute(f"INSERT INTO jobs SELECT {', '.join(legacy)} FROM jobs_old")
    conn.execute("DROP TABLE jobs_old")
    conn.commit()
    conn.close()

    with Store(str(path)) as store:
        after = [r["name"] for r in store.conn.execute("PRAGMA table_info(jobs)")]
        assert "compensation_min" in after
        # Re-opening must be a no-op rather than a duplicate-column error.
    with Store(str(path)) as store:
        again = [r["name"] for r in store.conn.execute("PRAGMA table_info(jobs)")]
        assert len(again) == len(after)

    assert iso_utc(NOW) == "2026-01-15T12:00:00+00:00"
