"""Tiny HTTP helper.

Deliberately stdlib-only so the core watcher has zero install steps.

Two things here exist to let you poll *often* without being rude:

``ETag``/``Last-Modified`` caching means a repeat check usually costs the
server a 304 with no body instead of a full page render. That is the single
politest way to raise your polling rate, and most CDNs count it far more
cheaply than a real request.

``RateLimited`` carries the server's own ``Retry-After``, so when a site does
push back the scheduler can obey the number it asked for rather than guessing.
"""

from __future__ import annotations

import email.utils
import gzip
import time
import urllib.error
import urllib.request
import zlib
from typing import Dict, Tuple

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FetchError(Exception):
    """The page could not be retrieved. Treated as BLOCKED, never as a status."""


class NotModified(Exception):
    """The server said 304: the page is unchanged since our last fetch.

    Deliberately *not* a FetchError. A 304 is a successful, informative
    answer — "nothing changed" — whereas a FetchError means "we learned
    nothing". Conflating them would turn a cheap confirmation into a
    BLOCKED reading.
    """


class RateLimited(FetchError):
    """429 or 503. ``retry_after`` is the server's own number, in seconds."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


#: url -> (etag, last_modified) from the most recent successful fetch.
_VALIDATORS: Dict[str, Tuple[str | None, str | None]] = {}


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse Retry-After, which is either a delta in seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - time.time())


def forget(url: str) -> None:
    """Drop cached validators for ``url``, forcing a full fetch next time."""
    _VALIDATORS.pop(url, None)


def fetch(
    url: str,
    timeout: int = 25,
    user_agent: str = DEFAULT_USER_AGENT,
    conditional: bool = True,
) -> str:
    """GET a URL and return the decoded body.

    Raises ``NotModified`` when the server answers 304, ``RateLimited`` on
    429/503, and ``FetchError`` on anything else that went wrong.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }

    if conditional:
        etag, last_modified = _VALIDATORS.get(url, (None, None))
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            charset = response.headers.get_content_charset() or "utf-8"

            if conditional:
                etag = response.headers.get("ETag")
                last_modified = response.headers.get("Last-Modified")
                if etag or last_modified:
                    _VALIDATORS[url] = (etag, last_modified)

            return raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            raise NotModified(url) from None
        if exc.code in (429, 503):
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
            raise RateLimited(f"{url}: HTTP {exc.code}", retry_after) from exc
        raise FetchError(f"{url}: {exc}") from exc
    except (urllib.error.URLError, OSError, ValueError, zlib.error) as exc:
        raise FetchError(f"{url}: {exc}") from exc
