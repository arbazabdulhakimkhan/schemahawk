"""Run report model and ASCII renderer.

The rendered report is the operator-facing artifact of every run: it is
printed to stdout, written to a file by the CLI, uploaded as a CI artifact and
stored verbatim in the ``pipeline_runs`` table.

Everything here is intentionally plain ASCII so it renders identically in
Windows consoles, CI logs and GitHub artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import FreshnessStatus, Job
from .matching import UNKNOWN, MatchResult

_STAMP = "%Y-%m-%d %H:%M UTC"

_UNKNOWN = UNKNOWN


def _score(value: int | None) -> str:
    """Render an unknown component as UNKNOWN rather than a misleading number."""
    return _UNKNOWN if value is None else str(value)


def _render_match(job: Job, match: MatchResult) -> list[str]:
    """One candidate-match block.

    The two scores are reported side by side and never merged: the V1 relevance
    score says the listing is a relevant Data Engineering job, while the
    candidate match score says how well it fits this profile. Unknown
    components print as UNKNOWN instead of a number.
    """
    relevance = job.relevance_score if job.relevance_score is not None else "-"
    lines = [
        f"- {job.title or '(untitled)'} @ {job.company or 'unknown company'}",
        f"    Technical: {_score(match.technical_score)} | "
        f"Experience: {_score(match.experience_score)} | "
        f"Contract/location: {_score(match.contract_score)} | "
        f"Eligibility: {_score(match.eligibility_score)}",
        f"    Candidate Match Score: {_score(match.overall_score)} | "
        f"V1 Relevance Score: {relevance}",
    ]
    lines += [f"    {line}" for line in match.explanation]
    return lines


def _fmt(moment: datetime | None) -> str:
    if moment is None:
        return "(running)"
    return moment.strftime(_STAMP)


@dataclass
class RunReport:
    """Aggregate counters plus per-source notes for a single discovery run."""

    started_at: datetime
    completed_at: datetime | None = None
    status: str = "OK"

    sources_checked: int = 0
    source_failures: list[tuple[str, str]] = field(default_factory=list)
    source_notes: list[tuple[str, str]] = field(default_factory=list)

    discovered: int = 0
    unique_jobs: int = 0
    fresh: int = 0
    recent: int = 0
    old: int = 0
    unknown_ts: int = 0

    duplicates_removed: int = 0
    rejected_non_de: int = 0
    rejected_ineligible: int = 0
    rejected_quality: int = 0
    suspicious: int = 0

    strong_candidates: int = 0
    strong: list[Job] = field(default_factory=list)
    matches: list[tuple[Job, MatchResult]] = field(default_factory=list)

    stored_inserted: int = 0
    stored_updated: int = 0
    db_path: str | None = None
    run_id: int | None = None

    # --- mutation helpers -------------------------------------------------
    def fail(self, source: str, reason: str) -> None:
        self.source_failures.append((source, reason))

    def note(self, source: str, reason: str) -> None:
        self.source_notes.append((source, reason))

    # --- rendering --------------------------------------------------------
    def render(self) -> str:
        """Render the operator summary in the blueprint's exact shape."""
        lines: list[str] = [
            "SchemaHawk - Discovery Run",
            "",
            f"Started:    {_fmt(self.started_at)}",
            f"Completed:  {_fmt(self.completed_at)}",
            "",
            f"Sources checked: {self.sources_checked}",
        ]

        if self.source_failures:
            lines.append("Source failures:")
            for name, reason in self.source_failures:
                lines.append(f"- {name} - {reason}")
        else:
            lines.append("Source failures: none")

        if self.source_notes:
            lines.append("Notes:")
            for name, reason in self.source_notes:
                lines.append(f"- {name} - {reason}")

        lines += [
            "",
            f"Discovered: {self.discovered}",
            f"Fresh <=60m: {self.fresh}",
            f"Recent 1-3h: {self.recent}",
            f"Old: {self.old}",
            f"Unknown timestamp: {self.unknown_ts}",
            "",
            f"Duplicates removed: {self.duplicates_removed}",
            "",
            "Rejected:",
            f"- Non-data-engineering: {self.rejected_non_de}",
            f"- Ineligible: {self.rejected_ineligible}",
            f"- Scam/quality: {self.rejected_quality}",
            "",
            f"Suspicious (kept, excluded from strong): {self.suspicious}",
            f"Strong candidates: {self.strong_candidates}",
        ]

        if self.db_path:
            lines.append(f"Database: {self.db_path}")
        if self.run_id:
            lines.append(f"Run id: {self.run_id}")

        if self.strong:
            lines += ["", "Top candidates:"]
            for job in self.strong:
                freshness = (
                    f"{job.freshness_minutes}m"
                    if job.freshness_minutes is not None
                    else "unknown"
                )
                company = job.company or "unknown company"
                score = job.relevance_score if job.relevance_score is not None else "-"
                lines.append(
                    f"- [{score}] {job.title or '(untitled)'} @ {company}"
                    f" ({job.source}, {freshness}, {job.freshness_status})"
                )
                if job.url:
                    lines.append(f"    {job.url}")

        if self.matches:
            lines += ["", "Overall candidate match:"]
            for job, match in self.matches:
                lines += _render_match(job, match)

        return "\n".join(lines) + "\n"

    # --- convenience ------------------------------------------------------
    def freshness_bucket(self, job: Job) -> None:
        """Increment the freshness counters for ``job`` (UNKNOWN-safe)."""
        if job.freshness_status == FreshnessStatus.FRESH:
            self.fresh += 1
        elif job.freshness_status == FreshnessStatus.RECENT:
            self.recent += 1
        elif job.freshness_status == FreshnessStatus.OLD:
            self.old += 1
        else:
            self.unknown_ts += 1
