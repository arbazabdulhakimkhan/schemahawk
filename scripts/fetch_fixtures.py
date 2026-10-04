"""Download tiny, trimmed samples from the V1 source APIs for parser tests.

Run manually (network required) - never in CI::

    python scripts/fetch_fixtures.py

Writes ``tests/fixtures/*.json``. Descriptions are truncated so the repository
stays small while field shapes and timestamp formats stay exactly as the live
APIs return them.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

import requests

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TIMEOUT = (5, 20)
HEADERS = {"User-Agent": "schemahawk-fixtures/0.1 (+https://github.com/arbazabdulhakimkhan/schemahawk)"}
DESC_LIMIT = 500
KEEP = 3
_TAG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")


def _trim(value, limit: int = DESC_LIMIT):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def _trim_job(job: dict) -> dict:
    out = {}
    for key, value in job.items():
        if key in ("description", "jobDescription", "content", "descriptionPlain"):
            out[key] = _trim(value)
        else:
            out[key] = value
    return out


def fetch_json(url: str, params: dict | None = None):
    response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def remoteok() -> list:
    payload = fetch_json("https://remoteok.com/api")
    # Keep element 0 (the legal notice) so the "skip it" behaviour is tested.
    kept = [payload[0]]
    jobs = [e for e in payload if isinstance(e, dict) and "position" in e][:KEEP]
    kept.extend(_trim_job(job) for job in jobs)
    return kept


def remotive() -> dict:
    payload = fetch_json("https://remotive.com/api/remote-jobs",
                         {"search": "data engineer", "limit": str(KEEP)})
    return {"job-count": payload.get("job-count"),
            "jobs": [_trim_job(job) for job in (payload.get("jobs") or [])[:KEEP]]}


def jobicy() -> dict:
    payload = fetch_json("https://jobicy.com/api/v2/remote-jobs", {"count": str(KEEP)})
    return {"jobs": [_trim_job(job) for job in (payload.get("jobs") or [])[:KEEP]]}


def _child_tag(child):
    """Return a writable XML tag name, dropping namespaces (e.g. media:content)."""
    tag = child.tag.split("}")[-1]
    return tag if _TAG_RE.match(tag) else None


def wwr_items() -> list[dict]:
    url = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    items = []
    for item in list(root.iter("item"))[:KEEP]:
        entry = {}
        for child in item:
            tag = _child_tag(child)
            if tag:
                entry[tag] = _trim(child.text or "")
        items.append(entry)
    return items


def wwr() -> dict:
    return {"items": wwr_items()}


def wwr_xml() -> str:
    """Rebuild a small, valid RSS document from the live feed (for the parser test)."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>']
    for item in wwr_items():
        parts.append("  <item>")
        for tag, value in item.items():
            parts.append(f"    <{tag}>{escape(str(value))}</{tag}>")
        parts.append("  </item>")
    parts.append("</channel></rss>")
    return "\n".join(parts) + "\n"


TARGETS = {
    "remoteok.json": remoteok,
    "remotive.json": remotive,
    "jobicy.json": jobicy,
    "weworkremotely.json": wwr,
    "weworkremotely.xml": wwr_xml,
}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    failures = 0
    for filename, loader in TARGETS.items():
        try:
            data = loader()
        except Exception as exc:  # noqa: BLE001 - manual helper
            print(f"[FAIL] {filename}: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        path = FIXTURES / filename
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(f"[ OK ] {filename} ({path.stat().st_size} bytes)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
