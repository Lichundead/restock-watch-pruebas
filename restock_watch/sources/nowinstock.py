"""Multi-retailer source: scrape a NowInStock tracker page.

One fetch gives you Amazon, Best Buy, Target and Walmart at once, which is
why it is worth having alongside a first-party check. It lags the retailer
by a little, so treat it as breadth rather than as your fastest signal.
"""

from __future__ import annotations

import re
from typing import Dict

from .. import status as st
from ..http import FetchError, NotModified, fetch

_ROW = re.compile(r'<tr id="tr\d+"[^>]*class="([\w\s-]+)"[^>]*>(.*?)</tr>', re.DOTALL)
# Captures the row's own link alongside the retailer name — same anchor,
# so the URL always points at the exact retailer the name says.
_RETAILER = re.compile(r'<a href="([^"]+)"[^>]*>[^<]*:\s*([\w][\w\s./&\'-]+)</a>')
_TAG = re.compile(r"<[^>]+>")

#: target -> product URL at that retailer, from the most recent check().
#: Populated as a side effect so watcher.py can attach the right buy link
#: to an alert without a second fetch of the tracker page.
_LAST_LINKS: Dict[str, str] = {}

# NowInStock's Amazon links carry &m=<seller-id>&aod=1 ("all offers
# display"). That flag forces the multi-seller offer popup instead of the
# product page's own buy box, which for a pre-order item is exactly the
# single "Reserve now" / "Pre-order now" button — so aod=1 trades one click
# for several. Rewriting to the bare /dp/<ASIN> page restores that button.
# Matches every shape NowInStock uses: /dp/ASIN, /gp/product/ASIN, and
# /Product-Title-Slug/dp/ASIN.
_AMAZON_ASIN = re.compile(
    r"amazon\.[a-z.]+/(?:[^/]*/)?(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE
)

# The real status lives in a dedicated cell. The row's own class is only a
# coarse highlight: a row showing "Preorder" is still class="offRow", so
# reading the row class alone reports OUT_OF_STOCK and misses the pre-order
# entirely — which on a pre-release console is the whole point.
_STATUS_CELL = re.compile(
    r'<td[^>]*class="[^"]*(stockStatus\w*)[^"]*"[^>]*>(.*?)</td>',
    re.DOTALL | re.IGNORECASE,
)

_CELL_CLASS_STATUS = {
    "stockstatusin": st.IN_STOCK,
    "stockstatusavailable": st.IN_STOCK,
    "stockstatuspre": st.PREORDER,
    "stockstatusorder": st.PREORDER,
    "stockstatusback": st.BACKORDER,
    "stockstatusout": st.OUT_OF_STOCK,
}


def _status_from_row_class(row_class: str) -> str:
    """Fallback only, for markup with no status cell."""
    css = row_class.lower()
    if "preorder" in css:
        return st.PREORDER
    if "onrow" in css:
        return st.IN_STOCK
    if "offrow" in css or "off" in css:
        return st.OUT_OF_STOCK
    return st.UNKNOWN


def _status_from_row(row_html: str, row_class: str) -> str:
    """Prefer the status cell; fall back to the row class."""
    cell = _STATUS_CELL.search(row_html)
    if cell:
        mapped = _CELL_CLASS_STATUS.get(cell.group(1).lower())
        if mapped:
            return mapped
        # Unrecognised class: the visible label is the next best evidence.
        text = _TAG.sub(" ", cell.group(2)).strip()
        from_text = st.normalise(text)
        if from_text != st.UNKNOWN:
            return from_text

    return _status_from_row_class(row_class)


def _clean_link(url: str) -> str:
    """Strip tracking params that change *which page* a link lands on.

    Amazon's aod=1 is the only case seen so far: it swaps the single-CTA
    product page for the multi-seller offer list. Other retailers' links
    are redirects through an affiliate domain (7tiv.net, howl.link) whose
    query string the redirector itself needs, so those pass through as-is.
    """
    match = _AMAZON_ASIN.search(url)
    if match:
        return f"https://www.amazon.com/dp/{match.group(1)}"
    return url


def check(watch: dict) -> Dict[str, str]:
    url = watch["url"]
    # Only rows containing this string are tracked, so one tracker page can
    # host several products without them bleeding into each other.
    needle = (watch.get("match") or "").lower()
    prefix = watch.get("label") or "nowinstock"
    only = {r.lower() for r in watch.get("retailers", [])}

    try:
        html = fetch(url, timeout=int(watch.get("timeout", 25)))
    except NotModified:
        # The tracker page is byte-identical to last time, so every status
        # on it is too. Report nothing: an absent target is "no change",
        # and the cached links stay valid.
        return {}
    except FetchError:
        return {}

    results: Dict[str, str] = {}
    links: Dict[str, str] = {}
    for row_class, row_html in _ROW.findall(html):
        text = _TAG.sub(" ", row_html)
        if needle and needle not in text.lower():
            continue
        retailer_match = _RETAILER.search(row_html)
        if not retailer_match:
            continue
        link, retailer = retailer_match.group(1), retailer_match.group(2).strip()
        if only and retailer.lower() not in only:
            continue
        target = f"{prefix}:{retailer}"
        results[target] = _status_from_row(row_html, row_class)
        links[target] = _clean_link(link)

    # Replace, don't merge: a target missing from this cycle (delisted,
    # filtered out) should not keep pointing at a stale link forever.
    _LAST_LINKS.clear()
    _LAST_LINKS.update(links)

    return results


def link_for(target: str) -> str | None:
    """The product URL for ``target`` as of the most recent check(), if any."""
    return _LAST_LINKS.get(target)
