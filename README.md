# schemahawk 🦅

> An hourly, approval-gated **freelance Data Engineering opportunity agent** — it hunts fresh
> opportunities, validates their freshness, scores them against your real skills, and prepares
> job-specific applications. *You* stay in control of what gets sent.

**Status:** V0 skeleton — V1 (Discovery) under construction. See [Roadmap](#-roadmap).

## What it does

Runs every hour (GitHub Actions), discovers worldwide freelance/contract Data Engineering
opportunities, eliminates the stale/scammy/duplicate ones, and surfaces only high-quality
matches with a prepared, personalized application queued for approval.

## Pipeline

```
Hourly scheduler (GitHub Actions / Railway)
  → Job discovery (LinkedIn · job boards · freelance sites · company careers · search-indexed)
  → Freshness validation (0–60 min = priority · >3 h normally skipped · uncertain = flagged)
  → Deduplication + normalize (URL / hash / company / title / JD similarity)
  → Quality + eligibility filter (scam · location · work rights · contract · seniority)
  → AI match engine (0–100 score + reasons)          ── <70 → skip
  → Contact / apply route (official apply first)     ── ≥70 ↓
  → Approval queue → send & track → feedback / learning
```

## Matching model

| Factor                 | Weight | Checks                                                    |
|------------------------|--------|-----------------------------------------------------------|
| Technical skills       | 40%    | SQL, Python, ADF, Databricks, Snowflake, BigQuery, ETL/ELT, Power BI, Tableau, Alteryx |
| Experience / seniority | 20%    | Years, responsibilities, seniority alignment              |
| Freelance/contract fit | 15%    | Contract, freelance, project, part-time preference        |
| Location / eligibility | 10%    | Remote + country/work-authorization requirements          |
| Freshness              | 10%    | Recency and timestamp confidence                          |
| Application quality    | 5%     | Legitimate recruiter/contact/application route            |

## 🗺️ Roadmap

| Version | Goal                                                                       |
|---------|----------------------------------------------------------------------------|
| V1      | Discovery — fresh opportunities from a small source set; validated timestamps; stored results |
| V2      | Matching — normalization, dedup, eligibility checks, 0–100 scoring         |
| V3      | Resume — job-specific resume versions from the master resume               |
| V4      | Outreach — legitimate contact routes + personalized drafts                 |
| V5      | Approval — review queue and approval workflow                              |
| V6      | Automation — complete discovery pipeline every hour                        |
| V7      | Optimization — response-rate tracking, better ranking and messaging        |

## Project structure

```
schemahawk/
├── .github/workflows/hourly.yml   # hourly scheduler (GitHub Actions)
├── src/schemahawk/
│   ├── __main__.py                # CLI entry point
│   ├── config.py                  # settings from .env
│   └── pipeline.py                # V1+ pipeline stage skeletons
├── notebooks/                     # exploratory work
├── requirements.txt
└── .env.example                   # template — real .env is git-ignored
```

## Getting started

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
pip install -r requirements.txt
Copy-Item .env.example .env          # then fill in your keys
$env:PYTHONPATH = 'src'
python -m schemahawk --dry-run
```

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
