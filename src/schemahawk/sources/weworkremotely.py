"""We Work Remotely public RSS adapter (stdlib XML parsing only).

Feed item titles are formatted as ``Company: Role``. ``pubDate`` is RFC 2822
with an explicit timezone (confidence 100); ``region``/``category`` elements
are folded into the description as ``[source tags: ...]``.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..models import Job
from ..normalize import clean_text, parse_timestamp, strip_html
from .base import BaseSource, SourceError

FEEDS: tuple[str, ...] = (
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
)


class WeWorkRemotelySource(BaseSource):
    name = "weworkremotely"
    poll_every_hours = 1

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()
        for feed_url in FEEDS:
            text = self.get_text(feed_url)
            try:
                root = ET.fromstring(text)
            except ET.ParseError as exc:
                raise SourceError(f"invalid RSS from {feed_url}: {exc}") from exc
            for item in root.iter("item"):
                job = self._to_job(item)
                if job is None:
                    continue
                key = job.source_job_id or job.url
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                jobs.append(job)
        return jobs

    def _to_job(self, item: ET.Element) -> Job | None:
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not raw_title or not link:
            return None

        company, role = _split_title(raw_title)
        region = (item.findtext("region") or "").strip() or None
        category = (item.findtext("category") or "").strip() or None

        raw_date = item.findtext("pubDate")
        posted_at, confidence = parse_timestamp(raw_date) if raw_date else (None, None)

        description = strip_html(item.findtext("description"),
                                max_len=self.settings.max_description_chars)
        extras = [value for value in (region, category) if value]
        if extras:
            label = ", ".join(extras)
            description = (f"{description} [source tags: {label}]"
                           if description else f"[source tags: {label}]")

        remote = None
        if region and any(word in region.lower()
                          for word in ("anywhere", "worldwide")):
            remote = True

        return Job(
            source=self.name,
            source_job_id=(item.findtext("guid") or link).strip() or None,
            url=clean_text(link, 500),
            title=role,
            company=company,
            description=description,
            location=region,
            remote=remote,
            contract_type=None,
            posted_at=posted_at,
            posted_at_raw=str(raw_date) if raw_date else None,
            timestamp_confidence=confidence,
        )


def _split_title(raw_title: str) -> tuple[str | None, str | None]:
    if ":" in raw_title:
        company, _, role = raw_title.partition(":")
        return clean_text(company, 120), clean_text(role, 200)
    return None, clean_text(raw_title, 200)
