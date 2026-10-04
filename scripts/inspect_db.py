"""Print a quick summary of the local SchemaHawk database (read-only).

Usage::

    python scripts/inspect_db.py [db-path]

Never writes to the database: it opens the file with SQLite's ``mode=ro`` URI,
so it is safe to run against the production DB at any time.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "data/schemahawk.db"


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else DEFAULT_DB
    if not Path(path).exists():
        print(f"no database at {path} (run a discovery first)")
        return 1

    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"database: {path}")
    print(f"jobs: {conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]}")

    print("by status:")
    for row in conn.execute(
            "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status ORDER BY c DESC"):
        print(f"  {row['status']:<12} {row['c']}")

    print("by source:")
    for row in conn.execute(
            "SELECT source, COUNT(*) AS c FROM jobs GROUP BY source ORDER BY c DESC"):
        print(f"  {row['source']:<16} {row['c']}")

    print("top scoring jobs:")
    query = ("SELECT relevance_score AS s, freshness_status AS f, source, title "
             "FROM jobs ORDER BY (relevance_score IS NULL), relevance_score DESC LIMIT 12")
    for row in conn.execute(query):
        print(f"  [{row['s']}] {(row['f'] or '-'):<7} {row['source']:<14} {row['title']}")

    print("recent runs:")
    for row in conn.execute(
            "SELECT id, started_at, status, discovered, fresh_count, recent_count,"
            " strong_candidates FROM pipeline_runs ORDER BY id DESC LIMIT 3"):
        print(f"  #{row['id']} {row['started_at']} {row['status']}"
              f" discovered={row['discovered']} fresh={row['fresh_count']}"
              f" recent={row['recent_count']} strong={row['strong_candidates']}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
