"""schemahawk CLI entry point."""
from __future__ import annotations

import argparse
import sys

from .config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="schemahawk",
        description="Hourly, approval-gated freelance Data Engineering opportunity agent.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print configuration status and pipeline stages, then exit.",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.dry_run:
        print("schemahawk dry-run")
        print(f"  python            : {sys.version.split()[0]}")
        print(f"  openai key        : {'set' if settings.openai_api_key else 'MISSING'}")
        print(f"  telegram token    : {'set' if settings.telegram_bot_token else 'MISSING'}")
        print(f"  telegram chat id  : {'set' if settings.telegram_chat_id else 'MISSING'}")
        print(f"  target titles     : {', '.join(settings.target_titles)}")
        print(f"  freshness window  : <= {settings.max_age_minutes} minutes")
        print(
            "  pipeline stages   : discover -> freshness -> dedupe -> filter"
            " -> match -> outreach -> approval"
        )
        print("OK: dry-run complete (V1 discovery is not wired up yet - see README roadmap).")
        return 0

    print("V1 (discovery) is not implemented yet — see the README roadmap.")
    print("Tip: run `python -m schemahawk --dry-run` to verify configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
