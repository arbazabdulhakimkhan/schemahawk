"""CLI tests: flag parsing, dry-run, report files and exit codes.

``run_discovery`` is stubbed so these tests never touch the network or a real
database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pytest

from schemahawk import __main__ as cli
from schemahawk.models import FreshnessStatus, Job
from schemahawk.report import RunReport
from schemahawk.store import Store

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def captured(monkeypatch):
    """Stub the pipeline and record the arguments the CLI passed to it."""
    seen: dict = {}

    def fake_run(settings, **kwargs):
        seen["settings"] = settings
        seen.update(kwargs)
        report = RunReport(started_at=NOW, completed_at=NOW)
        report.discovered = 2
        report.strong_candidates = 1
        report.strong = [Job(source="fake", title="Data Engineer", company="Acme",
                             url="https://e.com/1", freshness_minutes=10,
                             freshness_status=FreshnessStatus.FRESH,
                             relevance_score=85)]
        return report

    monkeypatch.setattr(cli, "run_discovery", fake_run)
    return seen


def test_parse_since_accepts_units():
    assert cli.parse_since("45") == 45
    assert cli.parse_since("90m") == 90
    assert cli.parse_since("3h") == 180
    assert cli.parse_since("2d") == 2880


def test_parse_since_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_since("soon")


def test_dry_run_prints_settings_and_report(captured, capsys, tmp_path):
    exit_code = cli.main(["--dry-run", "--source", "remoteok",
                          "--db", str(tmp_path / "x.db")])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "schemahawk dry-run" in out
    assert "enabled sources" in out
    assert "SchemaHawk - Discovery Run" in out
    assert "Top candidates:" in out
    assert captured["only_source"] == "remoteok"
    # A dry run must never create or write the database file.
    assert not (tmp_path / "x.db").exists()
    assert captured["store"].read_only is False


def test_dry_run_uses_existing_db_for_cross_run_dedup(captured, tmp_path):
    path = tmp_path / "existing.db"
    with Store(str(path)) as store:
        assert store.count_jobs() == 0
    cli.main(["--dry-run", "--db", str(path)])
    assert captured["known_keys"] is not None
    assert captured["known_keys"]["urls"] == set()


def test_missing_db_means_no_known_keys(captured, tmp_path):
    cli.main(["--dry-run", "--db", str(tmp_path / "absent.db")])
    assert captured["known_keys"] is None


def test_unknown_source_returns_exit_code_2(capsys):
    exit_code = cli.main(["--source", "nope"])
    assert exit_code == 2
    assert "unknown source" in capsys.readouterr().err


def test_report_dir_writes_timestamped_file(captured, tmp_path):
    exit_code = cli.main(["--report-dir", str(tmp_path)])
    assert exit_code == 0
    files = list(tmp_path.glob("discovery-*.txt"))
    assert len(files) == 1
    assert "SchemaHawk - Discovery Run" in files[0].read_text(encoding="utf-8")


def test_limit_and_min_score_are_passed_through(captured):
    cli.main(["--limit", "7", "--min-score", "80", "--since", "30m", "--force"])
    assert captured["settings"].per_source_limit == 7
    assert captured["min_score"] == 80
    assert captured["since_minutes"] == 30
    assert captured["force"] is True


def test_unexpected_failure_returns_exit_code_1(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(cli, "run_discovery", boom)
    assert cli.main([]) == 1
    assert "RuntimeError" in capsys.readouterr().err
