"""Text, URL and timestamp normalization shared across pipeline stages.

Everything here is pure, dependency-free and therefore trivially testable.

Security note: text from job boards is untrusted *data*. It is only ever
cleaned, hashed and stored. It is never executed, evaluated, or used to build
shell commands or URLs.
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

from .models import Compensation, RATE_PERIODS

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN = re.compile(r"[a-z0-9#+]+")
_WWW = re.compile(r"^www\.")
_RELATIVE = re.compile(
    r"^(?:about|approx(?:imately)?|~)?\s*(\d+)\s*"
    r"(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s*(?:ago)?$",
    re.IGNORECASE,
)
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNIT_MINUTES = {
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "d": 1440, "day": 1440, "days": 1440,
}

# --- timestamp confidence scale -------------------------------------------
# 100  exact timestamp, timezone-aware
#  95  exact timestamp, timezone-naive (assumed UTC, as most job APIs document)
#  90  relative ("43m", "2 hours ago", "just now")
#  70  coarse ("today")      -> minute-level freshness NOT derivable
#  40  date-only             -> minute-level freshness NOT derivable
#   0  unrecognized/unparseable
CONFIDENCE_EXACT = 100
CONFIDENCE_EXACT_NAIVE = 95
CONFIDENCE_RELATIVE = 90
CONFIDENCE_COARSE = 70
CONFIDENCE_DATE_ONLY = 40
CONFIDENCE_NONE = 0


def clean_text(text: object, max_len: int | None = None) -> str | None:
    """Collapse whitespace, strip control characters and unescape entities."""
    if text is None:
        return None
    value = html.unescape(_CONTROL.sub(" ", str(text)))
    value = _WS.sub(" ", value).strip()
    if max_len is not None and len(value) > max_len:
        value = value[:max_len].rstrip() + "..."
    return value or None


def strip_html(raw: object, max_len: int | None = None) -> str | None:
    """Convert an HTML fragment to plain text without external dependencies.

    Handles double-escaped markup (e.g. Greenhouse board content) by
    unescaping, removing tags, then unescaping again.
    """
    if raw is None:
        return None
    text = html.unescape(str(raw))
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return clean_text(text, max_len)
# --- compensation parsing ---------------------------------------------------
#
# Live API shapes observed, all of which were previously discarded:
#   Jobicy    salaryMin / salaryMax / salaryCurrency / salaryPeriod ("hourly")
#   RemoteOK  salary_min / salary_max          (no period published at all)
#   Remotive  salary: "$90k - $105k"           (a formatted string)

_CURRENCY_SYMBOL = {"$": "USD", "\u00a3": "GBP", "\u20ac": "EUR", "\u20b9": "INR"}
_CURRENCY_CODES = frozenset(
    {"USD", "GBP", "EUR", "INR", "CAD", "AUD", "CHF", "SEK", "PLN", "NZD"})
# The alternation must be grouped: without the non-capturing wrapper it binds
# to the *whole* pattern, so the range tail silently stopped matching.
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_NUM_RANGE = re.compile(
    rf"({_NUM}\s*[kK]?)\s*(?:-|\u2013|to|\u2014)\s*"
    r"(?:[$\u00a3\u20ac\u20b9]\s*)?"
    rf"({_NUM}\s*[kK]?)", re.I)
_COMPENSATION = re.compile(
    r"([$\u00a3\u20ac\u20b9]|USD|GBP|EUR|INR|CAD|AUD)\s*"
    rf"({_NUM}\s*[kK]?(?:\s*(?:-|\u2013|to|\u2014)\s*"
    r"(?:[$\u00a3\u20ac\u20b9]\s*)?"
    rf"{_NUM}\s*[kK]?)?)"
    r"(?:\s*(?:/|per)\s*([A-Za-z]{2,9}))?",
    re.I,
)
_PERIOD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hour", ("per hour", "/hour", "/hr", "hourly", "an hour", "hr")),
    ("day", ("per day", "/day", "daily", "day rate")),
    ("month", ("per month", "/month", "monthly")),
    ("year", ("per year", "/year", "annually", "yearly", "per annum", "/yr")),
    ("project", ("per project", "fixed price", "fixed-price", "project fee")),
)


def _clean_number(text: str) -> float:
    """Parse "1,500", "90k" or "80.5". A trailing ``k`` means thousand."""
    stripped = text.strip()
    scale = 1000.0 if stripped.lower().endswith("k") else 1.0
    value = float(stripped[:-1].replace(",", "")) if scale > 1.0 \
        else float(stripped.replace(",", ""))
    return value * scale


def _to_float(value: object) -> float | None:
    """Coerce a board-supplied number. Bools are rejected (bool subclasses int)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return _clean_number(value)
        except ValueError:
            return None
    return None


def _period_from(value: object) -> str | None:
    """Map a published period to our vocabulary, or ``None`` if unstated.

    A period is never defaulted: RemoteOK's ``salary_min`` of 170000 is plainly
    annual, but nothing in the payload says so, so ``period`` stays ``None``
    instead of being guessed.
    """
    text = str(value).strip().lower() if value is not None else ""
    if not text:
        return None
    for period, hints in _PERIOD_HINTS:
        if any(hint in text for hint in hints):
            return period
    return text if text in RATE_PERIODS else None


def parse_compensation(text: object = None, *, min_value: object = None,
                       max_value: object = None, currency: object = None,
                       period: object = None) -> Compensation | None:
    """Normalize whatever pay information a board published.

    Structured fields take precedence (Jobicy). Otherwise the free-text form is
    parsed (Remotive's "$90k - $105k"). Returns ``None`` when nothing was
    published - never a zero-valued object, which would read as "unpaid".
    """
    raw = text.strip() if isinstance(text, str) and text.strip() else None

    low = _to_float(min_value)
    high = _to_float(max_value)
    if low is not None or high is not None:
        code = currency.strip().upper() if isinstance(currency, str) else None
        return Compensation(
            min_value=low,
            max_value=high,
            currency=code if code in _CURRENCY_CODES else None,
            period=_period_from(period),
            raw=raw,
        )

    if raw is None:
        return None

    match = _COMPENSATION.search(raw)
    if match is None:
        return Compensation(raw=raw)      # published something, unparseable

    symbol, amount, suffix = match.group(1), match.group(2), match.group(3)
    # ``search`` rather than ``match``: the captured amount can carry a
    # trailing currency symbol ("90k - $105k"), so anchoring to the start is
    # fine but the separator/symbol must be tolerated mid-string.
    span = _NUM_RANGE.search(amount)
    if span:
        low, high = _clean_number(span.group(1)), _clean_number(span.group(2))
    else:
        low, high = _clean_number(amount), None

    symbol_upper = symbol.upper()
    return Compensation(
        min_value=low,
        max_value=high,
        currency=(symbol_upper if symbol_upper in _CURRENCY_CODES
                  else _CURRENCY_SYMBOL.get(symbol)),
        period=_period_from(suffix),
        raw=raw,
    )


def canonical_url(url: object) -> str | None:
    """Lowercased host (no ``www.``), query/fragment stripped, trailing ``/`` removed."""
    if not url:
        return None
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return None
    if not parts.netloc:
        return None
    host = _WWW.sub("", parts.netloc.lower())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", host, path, "", ""))


def token_set(text: object) -> frozenset[str]:
    """Lowercase alphanumeric token set used for similarity checks."""
    if not text:
        return frozenset()
    return frozenset(_TOKEN.findall(str(text).lower()))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard similarity of two token sets (0.0 when either is empty)."""
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def _norm_words(text: object) -> str:
    if not text:
        return ""
    return " ".join(_TOKEN.findall(str(text).lower()))


def content_hash(title: object, company: object, description: object) -> str | None:
    """Stable SHA-256 over normalized title + company + description head.

    Used as a cross-source duplicate signal. Returns ``None`` when there is
    nothing meaningful to hash.
    """
    parts = [
        _norm_words(title)[:120],
        _norm_words(company)[:80],
        _norm_words(description)[:400],
    ]
    # Require at least two real components: a bare title must never make two
    # genuinely different postings look like the same job.
    if sum(1 for part in parts if part) < 2:
        return None
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def company_title_key(company: object, title: object) -> str | None:
    """Normalized ``company|title`` key (secondary duplicate signal)."""
    company_norm = _norm_words(company)
    title_norm = _norm_words(title)
    if not company_norm or not title_norm:
        return None
    return f"{company_norm}|{title_norm}"


def attach_keys(job: object) -> None:
    """Compute the dedup keys on a ``models.Job`` in place."""
    job.url_canonical = canonical_url(job.url)
    job.content_hash = content_hash(job.title, job.company, job.description)
    job.company_title = company_title_key(job.company, job.title)


def _from_epoch(value: float) -> datetime | None:
    if value > 10**12:  # milliseconds
        value /= 1000.0
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_timestamp(raw: object, now: datetime | None = None) -> tuple[datetime | None, int | None]:
    """Parse many timestamp shapes into ``(aware UTC datetime, confidence)``.

    Coarse values ("today", date-only) intentionally return ``(None, conf)``:
    they carry signal, but minute-level freshness is not derivable from them,
    and the blueprint forbids guessing.
    """
    if raw is None:
        return None, None
    now = now or datetime.now(timezone.utc)

    if isinstance(raw, bool):  # bool is an int subclass; never a timestamp
        return None, CONFIDENCE_NONE
    if isinstance(raw, (int, float)):
        return _from_epoch(float(raw)), CONFIDENCE_EXACT

    text = str(raw).strip()
    if not text:
        return None, None
    if text.isdigit():
        return _from_epoch(float(int(text))), CONFIDENCE_EXACT

    lowered = text.lower()
    if lowered in ("just now", "now", "moments ago"):
        return now, CONFIDENCE_RELATIVE
    match = _RELATIVE.match(lowered)
    if match:
        amount = int(match.group(1))
        minutes = amount * _UNIT_MINUTES[match.group(2).lower()]
        return now - timedelta(minutes=minutes), CONFIDENCE_RELATIVE
    if lowered == "yesterday":
        return now - timedelta(days=1), CONFIDENCE_RELATIVE
    if lowered == "today":
        return None, CONFIDENCE_COARSE
    if _DATE_ONLY.match(text):
        return None, CONFIDENCE_DATE_ONLY

    # RFC 2822 (RSS pubDate) before ISO: ISO strings never parse as RFC 2822.
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc), CONFIDENCE_EXACT_NAIVE
        return parsed.astimezone(timezone.utc), CONFIDENCE_EXACT

    iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None, CONFIDENCE_NONE
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc), CONFIDENCE_EXACT_NAIVE
    return parsed.astimezone(timezone.utc), CONFIDENCE_EXACT

