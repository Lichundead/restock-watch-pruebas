"""Multi-retailer source: scrape a NowInStock tracker page.

One fetch gives you Amazon, Best Buy, Target and Walmart at once, which is
why it is worth having alongside a first-party check. It lags the retailer
by a little, so treat it as breadth rather than as your fastest signal.
"""

from __future__ import annotations

import re
from typing import Dict

from .. import status as st
from ..http import FetchError, fetch

_ROW = re.compile(r'<tr id="tr\d+"[^>]*class="([\w\s-]+)"[^>]*>(.*?)</tr>', re.DOTALL)
_RETAILER = re.compile(r">[^<]*:\s*([\w][\w\s./&\'-]+)</a>")
_TAG = re.compile(r"<[^>]+>")


def _status_from_row_class(row_class: str) -> str:
    css = row_class.lower()
    if "preorder" in css:
        return st.PREORDER
    if "onrow" in css:
        return st.IN_STOCK
    if "offrow" in css or "off" in css:
        return st.OUT_OF_STOCK
    return st.UNKNOWN


def check(watch: dict) -> Dict[str, str]:
    url = watch["url"]
    # Only rows containing this string are tracked, so one tracker page can
    # host several products without them bleeding into each other.
    needle = (watch.get("match") or "").lower()
    prefix = watch.get("label") or "nowinstock"
    only = {r.lower() for r in watch.get("retailers", [])}

    try:
        html = fetch(url, timeout=int(watch.get("timeout", 25)))
    except FetchError:
        return {}

    results: Dict[str, str] = {}
    for row_class, row_html in _ROW.findall(html):
        text = _TAG.sub(" ", row_html)
        if needle and needle not in text.lower():
            continue
        retailer_match = _RETAILER.search(row_html)
        if not retailer_match:
            continue
        retailer = retailer_match.group(1).strip()
        if only and retailer.lower() not in only:
            continue
        results[f"{prefix}:{retailer}"] = _status_from_row_class(row_class)

    return results
