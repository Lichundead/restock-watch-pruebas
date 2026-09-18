"""Plain-HTTP source: read schema.org product availability out of the page.

Works on any store that ships structured data in the served HTML — which
covers most first-party console/GPU/console-bundle product pages. No
browser, no JavaScript, no dependencies.
"""

from __future__ import annotations

import re
from typing import Dict

from .. import status as st
from ..http import FetchError, fetch

# Matches both `"availability":"https://schema.org/InStock"` and the
# array-wrapped form some Next.js storefronts emit.
_AVAILABILITY = re.compile(r'"availability"\s*:\s*"([^"]+)"')
_AVAILABILITY_LIST = re.compile(r'"availability"\s*:\s*\[\s*"([^"]+)"')


def check(watch: dict) -> Dict[str, str]:
    label = watch.get("label") or "store"
    url = watch["url"]
    try:
        html = fetch(url, timeout=int(watch.get("timeout", 25)))
    except FetchError:
        return {label: st.BLOCKED}

    match = _AVAILABILITY.search(html) or _AVAILABILITY_LIST.search(html)
    if not match:
        return {label: st.UNKNOWN}

    result = st.normalise(match.group(1))

    # Some storefronts keep availability at OutOfStock while a separate tag
    # flips to a pre-order state first. Honour the more actionable of the two.
    for extra in _AVAILABILITY_LIST.findall(html):
        candidate = st.normalise(extra)
        if candidate in st.ACTIONABLE:
            result = candidate
            break

    return {label: result}
