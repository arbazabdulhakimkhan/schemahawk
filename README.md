# schemahawk 🦅

> An hourly, approval-gated **freelance Data Engineering opportunity agent** — it hunts fresh
> opportunities, validates their freshness, scores them against your real skills, and prepares
> job-specific applications. *You* stay in control of what gets sent.

**Status:** **V1 (Discovery) is shipped** — hourly discovery from public job APIs with freshness
validation, dedup, quality/eligibility filtering, relevance scoring, SQLite storage and run reports.
V2+ (AI matching, resume, outreach, approval) is next. See [Roadmap](#-roadmap).

## What it does

Runs every hour (GitHub Actions), discovers fresh worldwide freelance/contract Data Engineering
opportunities, eliminates the stale/scammy/duplicate ones, and stores the survivors with a 0–100
relevance score plus a written run report. **V1 only discovers and reports — it never applies,
messages, or scrapes behind a login.**

## Pipeline (V1 — implemented)

```
Hourly scheduler (GitHub Actions, concurrency-guarded)
  → Discover       (RemoteOK · Remotive · Jobicy · We Work Remotely · [Adzuna] · [company boards])
  → Normalize      (HTML strip · entity/whitespace cleanup · canonical URL · SHA-256 content hash)
  → Freshness      (FRESH ≤60m · RECENT ≤180m · OLD >180m · UNKNOWN — never guessed)
  → Dedupe         (URL · source job id · content hash · company+title · title/JD similarity)
  → Quality        (fee/crypto/gift-card scam patterns → SUSPICIOUS or REJECTED)
  → Eligibility    (citizenship/clearance/visa/country restrictions → RESTRICTED)
  → Relevance      (deterministic 0–100 rule score; below MIN_RELEVANCE_SCORE → rejected)
  → Store + Report (SQLite jobs + pipeline_runs · timestamped text report · CI artifact)
```

Planned stages, still to come: AI match engine → contact route → approval queue → send & track.

## Planned matching model (V2+)

| Factor                 | Weight | Checks                                                    |
|------------------------|--------|-----------------------------------------------------------|
| Technical skills       | 40%    | SQL, Python, ADF, Databricks, Snowflake, BigQuery, ETL/ELT, Power BI, Tableau, Alteryx |
| Experience / seniority | 20%    | Years, responsibilities, seniority alignment              |
| Freelance/contract fit | 15%    | Contract, freelance, project, part-time preference        |
| Location / eligibility | 10%    | Remote + country/work-authorization requirements          |
| Freshness              | 10%    | Recency and timestamp confidence                          |
| Application quality    | 5%     | Legitimate recruiter/contact/application route            |

## Freshness rules

Freshness is always `now_utc − posted_at`: it never depends on when the scheduler happened to fire,
and a timestamp is never fabricated, guessed, or rounded into the fresh window.

| Age                  | Status  | Meaning                                   |
|----------------------|---------|-------------------------------------------|
| ≤ 60 min             | FRESH   | priority candidate                        |
| 61–180 min           | RECENT  | still a candidate                         |
| > 180 min            | OLD     | stored, but not a candidate               |
| missing / unreliable | UNKNOWN | **never** treated as fresh                |

Timestamp confidence: `100` exact tz-aware · `95` exact tz-naive (assumed UTC) · `90` relative
("2h ago") · `70` "today" · `40` date-only · `0` unparseable. Only ≥90 can produce FRESH/RECENT —
coarse and date-only values stay UNKNOWN.

## Relevance scoring (V1)

No LLM calls in V1: scoring is deterministic, explainable and reproducible in CI.

| Signal | Points |
|---|---|
| Strong data-engineering role phrase in the title | +60 |
| Title skill keywords (SQL, Python, ADF/Data Factory, Databricks, Snowflake, BigQuery, ETL/ELT, Power BI, Tableau, Alteryx, dbt, Airflow, Kafka, SSIS, Informatica, Talend, Azure/AWS/GCP) | +5 … +10 each |
| Description skill keywords | up to +20 (capped so a long JD cannot dominate) |
| Freelance/contract wording in the title | +10 |
| Non-data role word and no strong role phrase | capped at 5 |

Keywords match on **token boundaries**, so `ssis` cannot match inside "a**ssis**tant" and `sql`
cannot match inside "postgre**sql**". Extend the rules with `RELEVANCE_EXTRA_KEYWORDS`.

## Data sources & attribution

| Source | Endpoint | Notes |
|---|---|---|
| RemoteOK | `remoteok.com/api` | Public API. **Attribution required**: job data © [RemoteOK](https://remoteok.com). The first array element is a legal notice and is skipped. |
| Remotive | `remotive.com/api/remote-jobs` | Public API; their terms ask for light polling, so it is polled at most every 6 h with ≤2 queries per poll. |
| Jobicy | `jobicy.com/api/v2/remote-jobs` | Public API (`count` ≤ 50). |
| We Work Remotely | category RSS feeds | Public RSS, parsed with the standard library only. |
| Adzuna | `api.adzuna.com` | **Optional**: set `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` (free tier). One query per configured country, ≤8 per poll. |
| Company boards | Greenhouse / Lever public JSON | **Optional**: set `GREENHOUSE_BOARDS` / `LEVER_BOARDS`. |
| LinkedIn | — | **Not supported in V1** and never enabled implicitly: there is no public guest API and scraping would breach its terms of service. |

## Storage & reports

* SQLite at `data/schemahawk.db` (git-ignored, WAL mode): `jobs` holds every discovered listing
  together with its verdicts; `pipeline_runs` holds one row per run, including the rendered report.
* Every run prints its report; `--report-dir reports` also writes `discovery-<UTC>.txt`, which CI
  uploads as an artifact.
* Duplicates are never stored twice: canonical URL, `source`+`source_job_id`, content hash,
  company+title and — for near-matches — title-Jaccard ≥ 0.8 with description-Jaccard ≥ 0.5.
* Rejected listings are kept with a reason so every verdict stays auditable.

## CLI

```powershell
$env:PYTHONPATH = 'src'
python -m schemahawk --dry-run                       # discover + report, writes nothing
python -m schemahawk --source remoteok --limit 20 --verbose
python -m schemahawk --since 90m --min-score 70 --report-dir reports
python scripts/inspect_db.py                         # read-only database summary
python scripts/fetch_fixtures.py                     # refresh test fixtures (needs network)
```

| Flag | Meaning |
|---|---|
| `--dry-run` | Report only; never writes to the database (reads it read-only for cross-run dedup) |
| `--source NAME` | Poll a single source (`remoteok`, `remotive`, `jobicy`, `weworkremotely`, `adzuna`, `company_boards`) |
| `--since DURATION` | Only listings at most this fresh count as candidates (`60`, `90m`, `3h`, `2d`) |
| `--min-score N` | Override `MIN_RELEVANCE_SCORE` for this run |
| `--limit N` | Maximum listings fetched per source (0 = unlimited) |
| `--db PATH` | Override the SQLite path |
| `--report-dir DIR` | Write a timestamped report file into this directory |
| `--force` | Ignore per-source poll intervals (e.g. Remotive's 6 h cap) |
| `--verbose` | Debug logging |

Exit codes: `0` success · `1` unexpected failure · `2` bad arguments (e.g. unknown `--source`).

## 🗺️ Roadmap

| Version | Goal                                                                       |
|---------|----------------------------------------------------------------------------|
| V1 ✅   | Discovery — **shipped**: 4 live sources, freshness validation, dedup, quality/eligibility, relevance scoring, SQLite storage, run reports, CI |
| V2      | Matching — AI-assisted match engine with reasons; profile-weighted scoring  |
| V3      | Resume — job-specific resume versions from the master resume               |
| V4      | Outreach — legitimate contact routes + personalized drafts                 |
| V5      | Approval — review queue and approval workflow                              |
| V6      | Automation — complete discovery pipeline every hour                        |
| V7      | Optimization — response-rate tracking, better ranking and messaging        |

## Project structure

```
schemahawk/
├── .github/workflows/hourly.yml   # hourly scheduler: test → discover → artifact
├── src/schemahawk/
│   ├── __main__.py                # CLI entry point
│   ├── config.py                  # settings from .env (secrets never in code)
│   ├── models.py                  # Job dataclass + verdict constants
│   ├── normalize.py               # HTML/text/URL normalization, timestamps, hashes
│   ├── freshness.py               # FRESH/RECENT/OLD/UNKNOWN evaluation
│   ├── dedupe.py                  # URL/id/hash/company+title/similarity dedup
│   ├── quality.py                 # scam + eligibility classification
│   ├── relevance.py               # deterministic 0–100 scoring
│   ├── store.py                   # SQLite (jobs, pipeline_runs)
│   ├── report.py                  # run report model + ASCII renderer
│   ├── pipeline.py                # V1 orchestration
│   └── sources/                   # adapters: base + remoteok, remotive, jobicy,
│                                  # weworkremotely, adzuna, company_boards, linkedin
├── scripts/
│   ├── fetch_fixtures.py          # refresh trimmed real-API test fixtures
│   └── inspect_db.py              # read-only database summary
├── tests/                         # pytest suite (offline; fixtures in tests/fixtures)
├── requirements.txt               # runtime
├── requirements-dev.txt           # + pytest
└── .env.example                   # template — real .env is git-ignored
```

## Getting started

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
pip install -r requirements-dev.txt  # runtime + pytest
Copy-Item .env.example .env          # then fill in your keys
$env:PYTHONPATH = 'src'
python -m pytest                     # offline test suite (no network needed)
python -m schemahawk --dry-run        # live discovery, writes nothing
```

### Tests

The suite is fully offline: source adapters are tested against trimmed, real API payloads in
`tests/fixtures/`, and the pipeline runs with an injected fake source and an in-memory database.
Refresh the fixtures (needs network) with `python scripts/fetch_fixtures.py`.

### Scheduling

`.github/workflows/hourly.yml` triggers hourly (UTC minute 0). Add repo secrets
(`OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) under
**Settings → Secrets and variables → Actions**. You can also trigger manually via
**Actions → hourly-discovery → Run workflow**.

## Safety principles

- **No bulk sending** — approval gate first, controlled sending always.
- Prefer **official application pages** and legitimate professional contacts.
- Tailor emphasis to each job — **never invent experience**.
- Track every recipient, company, job URL and status to **prevent duplicates**.
- If a site presents CAPTCHA/MFA: **pause, never bypass**.
- **No credentials in code, prompts or spreadsheets** — secrets live only in `.env` / repo secrets.

## V1 exclusions (deliberate)

V1 discovers and reports only. It never auto-applies, never messages anyone, never generates a
resume, and never calls an LLM. It also does not scrape behind login walls, does not bypass
CAPTCHA/MFA, and does not touch LinkedIn (see [Data sources](#data-sources--attribution)).

Job descriptions are treated as **untrusted input**: they are cleaned, pattern-matched, scored and
stored as data — never executed, `eval`'d, or used to build shell commands or URLs.
