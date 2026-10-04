"""Configuration loaded from environment variables / .env.

Secrets never live in code — they come from the environment (or a git-ignored
`.env` file locally, or GitHub Actions secrets in CI).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env if present; .env is git-ignored


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the schemahawk agent."""

    openai_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    master_resume_path: str = "data/master_resume.docx"
    target_titles: tuple[str, ...] = ("Data Engineer",)
    max_age_minutes: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        titles = tuple(
            t.strip()
            for t in os.getenv("PROFILE_TITLES", "Data Engineer").split(",")
            if t.strip()
        )
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            master_resume_path=os.getenv("RESUME_MASTER_PATH", "data/master_resume.docx"),
            target_titles=titles or ("Data Engineer",),
            max_age_minutes=int(os.getenv("MAX_OPPORTUNITY_AGE_MINUTES", "60")),
        )
