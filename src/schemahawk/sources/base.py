"""Source adapter base class: HTTP with timeouts, retries and error isolation.

Security rules every adapter must follow:

- Only request adapter-owned, hard-coded URLs. Query parameters come from our
  own configuration, never from job content.
- Treat every response as untrusted data: parse, normalize, store. Never
  execute, ``eval``, or shell out with any part of a response.
- Never log credentials; error messages use URLs with query strings stripped.
"""
from __future__ import annotations

import abc
import logging
import time
from datetime import datetime, timezone

import requests

from ..models import Job

log = logging.getLogger(__name__)

USER_AGENT = "schemahawk/0.1 (+https://github.com/arbazabdulhakimkhan/schemahawk)"
_ACCEPT = "application/json, application/xml, text/xml, text/html, */*"


class SourceError(Exception):
    """A source could not be fetched. Never fatal for the run."""


class BaseSource(abc.ABC):
    """Base class for all job sources."""

    name: str = "base"
    poll_every_hours: int = 1

    def __init__(self, settings):
        self.settings = settings
        self.warnings: list[str] = []

    # --- configuration ----------------------------------------------------
    @classmethod
    def is_configured(cls, settings) -> bool:
        """True when the source has everything it needs to run."""
        return True

    @classmethod
    def skip_reason(cls, settings) -> str | None:
        """Reason shown in the report when ``is_configured`` is False."""
        return None

    # --- fetching ---------------------------------------------------------
    @abc.abstractmethod
    def fetch(self) -> list[Job]:
        """Fetch and return normalized jobs. May raise :class:`SourceError`."""

    def due(self, now: datetime, *, force: bool = False) -> bool:
        """True when this source should be polled now (respects poll caps)."""
        if force or self.poll_every_hours <= 1:
            return True
        return now.astimezone(timezone.utc).hour % self.poll_every_hours == 0

    def run(self) -> tuple[list[Job], str | None]:
        """Fetch without ever raising: returns ``(jobs, error_message)``."""
        try:
            return self.fetch(), None
        except SourceError as exc:
            return [], str(exc)
        except Exception as exc:  # noqa: BLE001 - one source must not kill the run
            log.exception("source %s failed unexpectedly", self.name)
            return [], f"{type(exc).__name__}: {exc}"

    # --- HTTP helpers -----------------------------------------------------
    def _headers(self, extra: dict | None = None) -> dict:
        return {"User-Agent": USER_AGENT, "Accept": _ACCEPT, **(extra or {})}

    @staticmethod
    def _safe_url(url: str) -> str:
        """Strip query strings so credentials never reach logs or reports."""
        parts = requests.utils.urlparse(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"

    def http_get(self, url: str, *, params: dict | None = None,
                 headers: dict | None = None) -> requests.Response:
        """GET with bounded retries, backoff and 429 handling."""
        retries = max(0, self.settings.http_retries)
        last_error = "unknown error"
        for attempt in range(retries + 1):
            delay: float | None = None
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self._headers(headers),
                    timeout=self.settings.http_timeout,
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: connection/timeout error"
                delay = min(2 ** attempt * 2, 30)
                log.warning("%s: attempt %s/%s failed: %s",
                            self.name, attempt + 1, retries + 1, last_error)
            else:
                if response.status_code < 400:
                    return response
                if response.status_code == 429:
                    last_error = "HTTP 429 rate limited"
                    retry_after = response.headers.get("Retry-After", "").strip()
                    delay = (min(int(retry_after), 60) if retry_after.isdigit()
                             else min(2 ** attempt * 2, 30))
                elif response.status_code < 500:
                    raise SourceError(
                        f"HTTP {response.status_code} for {self._safe_url(url)}")
                else:
                    last_error = f"HTTP {response.status_code} server error"
                    delay = min(2 ** attempt * 2, 30)
                log.warning("%s: attempt %s/%s failed: %s",
                            self.name, attempt + 1, retries + 1, last_error)
            if delay is not None and attempt < retries:
                time.sleep(delay)
        raise SourceError(f"{last_error} after {retries + 1} attempt(s)")

    def get_json(self, url: str, *, params: dict | None = None,
                 headers: dict | None = None) -> object:
        response = self.http_get(url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(f"invalid JSON from {self._safe_url(url)}") from exc

    def get_text(self, url: str, *, params: dict | None = None,
                 headers: dict | None = None) -> str:
        return self.http_get(url, params=params, headers=headers).text
