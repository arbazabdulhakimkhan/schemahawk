"""LinkedIn source stub.

V1 deliberately does not touch LinkedIn. There is no public guest search API,
and scraping search results or driving a logged-in session would violate
LinkedIn's terms of service (login walls, CAPTCHA, rate limits). The blueprint
also forbids CAPTCHA/MFA bypass outright.

The stub exists so the report can state that clearly, and so a future
ToS-compliant integration (an official partner feed or the company's own
posting pages) can be slotted in without any pipeline changes.
"""
from __future__ import annotations

from ..models import Job
from .base import BaseSource, SourceError

MESSAGE = (
    "not supported in V1: LinkedIn has no public guest API and scraping "
    "would violate its terms of service"
)


class LinkedInSource(BaseSource):
    name = "linkedin"
    poll_every_hours = 1

    @classmethod
    def is_configured(cls, settings) -> bool:
        return False

    @classmethod
    def skip_reason(cls, settings) -> str | None:
        return MESSAGE

    def fetch(self) -> list[Job]:
        raise SourceError(MESSAGE)
