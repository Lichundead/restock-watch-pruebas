"""Tiny HTTP helper.

Deliberately stdlib-only so the core watcher has zero install steps.
"""

from __future__ import annotations

import gzip
import urllib.error
import urllib.request
import zlib

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FetchError(Exception):
    """The page could not be retrieved. Treated as BLOCKED, never as a status."""


def fetch(url: str, timeout: int = 25, user_agent: str = DEFAULT_USER_AGENT) -> str:
    """GET a URL and return the decoded body, or raise FetchError."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except (urllib.error.URLError, OSError, ValueError, zlib.error) as exc:
        raise FetchError(f"{url}: {exc}") from exc
