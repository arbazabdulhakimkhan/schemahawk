"""Source registry and factory.

``build_sources`` returns the sources to poll plus a list of ``(name, note)``
pairs for sources that are registered but not runnable (missing credentials,
unsupported integration). Those appear in the report as notes, not failures.
"""
from __future__ import annotations

from .adzuna import AdzunaSource
from .base import BaseSource, SourceError
from .company_boards import CompanyBoardsSource
from .jobicy import JobicySource
from .linkedin import LinkedInSource
from .remoteok import RemoteOKSource
from .remotive import RemotiveSource
from .weworkremotely import WeWorkRemotelySource

REGISTRY: dict[str, type[BaseSource]] = {
    "remoteok": RemoteOKSource,
    "remotive": RemotiveSource,
    "jobicy": JobicySource,
    "weworkremotely": WeWorkRemotelySource,
    "adzuna": AdzunaSource,
    "company_boards": CompanyBoardsSource,
    "linkedin": LinkedInSource,
}

__all__ = ["BaseSource", "SourceError", "REGISTRY", "build_sources", "available_sources"]


def available_sources() -> tuple[str, ...]:
    """Names of all registered sources (stable order)."""
    return tuple(REGISTRY)


def build_sources(
    settings,
    *,
    only: str | None = None,
) -> tuple[list[BaseSource], list[tuple[str, str]]]:
    """Instantiate the configured sources.

    Returns ``(sources, notes)`` where ``notes`` explains any requested source
    that was skipped because it is unconfigured or unsupported.
    """
    if only:
        names: tuple[str, ...] = (only,)
    else:
        names = tuple(settings.enabled_sources) or tuple(REGISTRY)
        # Linkedin is never enabled implicitly: it is unsupported in V1.
        names = tuple(name for name in names if name != "linkedin")

    unknown = [name for name in names if name not in REGISTRY]
    if unknown:
        raise ValueError(
            f"unknown source(s): {', '.join(unknown)}; "
            f"available: {', '.join(available_sources())}"
        )

    sources: list[BaseSource] = []
    notes: list[tuple[str, str]] = []
    for name in names:
        cls = REGISTRY[name]
        if not cls.is_configured(settings):
            notes.append((name, cls.skip_reason(settings) or "not configured"))
            continue
        sources.append(cls(settings))
    return sources, notes
