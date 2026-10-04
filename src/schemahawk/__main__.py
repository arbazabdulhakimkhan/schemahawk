"""schemahawk CLI entry point.

Examples::

    python -m schemahawk --dry-run
    python -m schemahawk --source remoteok --limit 20 --verbose
    python -m schemahawk --min-score 70 --since 60 --report-dir reports

Flags are intentionally explicit (no prompts, no interactive input) so the
same command works locally, in CI and from a scheduler.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

from .config import Settings
from .pipeline import run_discovery
from .store import Store


def parse_since(value: str) -> int:
    """Convert ``45``, ``45m``, ``3h`` or ``2d`` into minutes."""
    text = value.strip().lower()
    try:
        if text.endswith("m"):
            return int(float(text[:-1]))
        if text.endswith("h"):
            return int(float(text[:-1]) * 60)
        if text.endswith("d"):
            return int(float(text[:-1]) * 1440)
        return int(float(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid duration {value!r} (use e.g. 60, 90m, 3h)"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schemahawk",
        description="Hourly, approval-gated freelance Data Engineering opportunity agent.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="discover and report only; never writes to the database")
    parser.add_argument("--source", metavar="NAME",
                        help="poll only this source (e.g. remoteok, remotive)")
    parser.add_argument("--since", type=parse_since, metavar="DURATION",
                        help="only treat jobs at most this fresh as candidates (60, 90m, 3h)")
    parser.add_argument("--min-score", type=int, metavar="N",
                        help="override MIN_RELEVANCE_SCORE for this run")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="max jobs fetched per source (0 = unlimited)")
    parser.add_argument("--db", metavar="PATH", help="override the SQLite path")
    parser.add_argument("--report-dir", metavar="DIR",
                        help="write a timestamped report file into this directory")
    parser.add_argument("--force", action="store_true",
                        help="ignore per-source poll intervals (e.g. Remotive's 6h cap)")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    return parser


def _print_settings(settings: Settings) -> None:
    print("schemahawk dry-run")
    print(f"  python            : {sys.version.split()[0]}")
    for label, value in settings.summary().items():
        print(f"  {label:<17} : {value}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    settings = Settings.from_env()
    if args.db:
        settings = replace(settings, db_path=args.db)
    if args.limit is not None:
        settings = replace(settings, per_source_limit=args.limit)

    if args.dry_run:
        _print_settings(settings)
        print("")

    known_keys = None
    store = None
    if args.dry_run:
        store = Store(None)  # in-memory: nothing is persisted
        if settings.db_path and os.path.exists(settings.db_path):
            with Store(settings.db_path, read_only=True) as read_store:
                known_keys = read_store.existing_keys()

    try:
        report = run_discovery(
            settings,
            only_source=args.source,
            force=args.force or args.dry_run,
            since_minutes=args.since,
            min_score=args.min_score,
            store=store,
            known_keys=known_keys,
        )
    except ValueError as exc:  # bad --source etc.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must always exit cleanly
        logging.getLogger("schemahawk").exception("run failed")
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    text = report.render()
    print(text)

    if args.report_dir:
        os.makedirs(args.report_dir, exist_ok=True)
        stamp = report.started_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(args.report_dir, f"discovery-{stamp}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"report written: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

