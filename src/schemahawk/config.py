"""Configuration loaded from environment variables / .env.

Secrets never live in code — they come from the environment (or a git-ignored
`.env` file locally, or GitHub Actions secrets in CI).

Every tunable has a safe default so a bare `python -m schemahawk` works with
no configuration at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env if present; .env is git-ignored

# Sources enabled by default. Keyed sources (adzuna) and board sources
# (company_boards) declare themselves unconfigured when no keys/boards are set
# and are then reported as "skipped" instead of "failed".
DEFAULT_ENABLED_SOURCES: tuple[str, ...] = (
    "remoteok",
    "remotive",
    "jobicy",
    "weworkremotely",
    "adzuna",
    "company_boards",
)
DEFAULT_ADZUNA_COUNTRIES: tuple[str, ...] = ("gb", "us", "de", "ca", "au", "nl", "fr", "in")


def _csv(value: str | None) -> tuple[str, ...]:
    """Split a comma separated env value into a stripped tuple."""
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _timeout_env(name: str, default: tuple[int, int]) -> tuple[int, int]:
    parts = _csv(os.getenv(name))
    if len(parts) != 2:
        return default
    try:
        connect, read = int(parts[0]), int(parts[1])
    except ValueError:
        return default
    return (max(1, connect), max(1, read))


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the schemahawk agent (V1 discovery)."""

    # --- secrets (used by V2+ features; V1 discovery never uses them) ---
    openai_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- profile ---
    master_resume_path: str = "data/master_resume.docx"
    target_titles: tuple[str, ...] = ("Data Engineer",)

    # --- freshness thresholds (minutes) ---
    fresh_max_minutes: int = 60
    recent_max_minutes: int = 180

    # --- relevance ---
    min_relevance_score: int = 60
    relevance_extra_keywords: tuple[str, ...] = ()

    # --- sources / HTTP behaviour ---
    enabled_sources: tuple[str, ...] = DEFAULT_ENABLED_SOURCES
    http_timeout: tuple[int, int] = (5, 20)  # (connect, read) seconds
    http_retries: int = 2
    per_source_limit: int = 60
    max_description_chars: int = 5000

    # --- optional keyed sources ---
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_countries: tuple[str, ...] = DEFAULT_ADZUNA_COUNTRIES
    greenhouse_boards: tuple[str, ...] = ()
    lever_boards: tuple[str, ...] = ()

    # --- storage / filtering policy ---
    db_path: str = "data/schemahawk.db"
    reject_restricted: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            master_resume_path=os.getenv("RESUME_MASTER_PATH", "data/master_resume.docx"),
            target_titles=_csv(os.getenv("PROFILE_TITLES")) or ("Data Engineer",),
            fresh_max_minutes=_int_env("FRESH_MAX_MINUTES", 60),
            recent_max_minutes=_int_env("RECENT_MAX_MINUTES", 180),
            min_relevance_score=_int_env("MIN_RELEVANCE_SCORE", 60),
            relevance_extra_keywords=_csv(os.getenv("RELEVANCE_EXTRA_KEYWORDS")),
            enabled_sources=_csv(os.getenv("ENABLED_SOURCES")) or DEFAULT_ENABLED_SOURCES,
            http_timeout=_timeout_env("HTTP_TIMEOUT_SECONDS", (5, 20)),
            http_retries=_int_env("HTTP_RETRIES", 2),
            per_source_limit=_int_env("PER_SOURCE_LIMIT", 60),
            max_description_chars=_int_env("MAX_DESCRIPTION_CHARS", 5000),
            adzuna_app_id=os.getenv("ADZUNA_APP_ID", ""),
            adzuna_app_key=os.getenv("ADZUNA_APP_KEY", ""),
            adzuna_countries=_csv(os.getenv("ADZUNA_COUNTRIES")) or DEFAULT_ADZUNA_COUNTRIES,
            greenhouse_boards=_csv(os.getenv("GREENHOUSE_BOARDS")),
            lever_boards=_csv(os.getenv("LEVER_BOARDS")),
            db_path=os.getenv("SCHEMAHAWK_DB", "data/schemahawk.db"),
            reject_restricted=os.getenv("REJECT_RESTRICTED", "true").strip().lower()
            not in ("0", "false", "no"),
        )

    def summary(self) -> dict[str, str]:
        """Printable settings summary with secrets masked (used by --dry-run)."""

        def mask(value: str) -> str:
            return "set" if value else "MISSING"

        return {
            "openai key": mask(self.openai_api_key),
            "telegram token": mask(self.telegram_bot_token),
            "telegram chat id": mask(self.telegram_chat_id),
            "adzuna app id": mask(self.adzuna_app_id),
            "adzuna app key": mask(self.adzuna_app_key),
            "target titles": ", ".join(self.target_titles),
            "fresh <= minutes": str(self.fresh_max_minutes),
            "recent <= minutes": str(self.recent_max_minutes),
            "min relevance score": str(self.min_relevance_score),
            "enabled sources": ", ".join(self.enabled_sources),
            "per-source limit": str(self.per_source_limit),
            "greenhouse boards": ", ".join(self.greenhouse_boards) or "(none)",
            "lever boards": ", ".join(self.lever_boards) or "(none)",
            "db path": self.db_path,
        }
