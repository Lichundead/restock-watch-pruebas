# Adding a source

A source adapter is one function that takes a `[[watch]]` table and returns
`{target_label: status}`. That is the whole contract.

## The shape

```python
# restock_watch/sources/mystore.py
"""Read availability from MyStore product pages."""

from __future__ import annotations

import re
from typing import Dict

from .. import status as st
from ..http import FetchError, fetch

_BUTTON = re.compile(r'class="buy-button"[^>]*>([^<]+)<', re.IGNORECASE)


def check(watch: dict) -> Dict[str, str]:
    label = watch.get("label") or "mystore"
    try:
        html = fetch(watch["url"], timeout=int(watch.get("timeout", 25)))
    except FetchError:
        # Could not reach the page. Say so — do not guess.
        return {label: st.BLOCKED}

    match = _BUTTON.search(html)
    if not match:
        return {label: st.UNKNOWN}

    return {label: st.normalise(match.group(1))}
```

Register it:

```python
# restock_watch/sources/__init__.py
from . import jsonld, mystore, nowinstock

SOURCES = {
    "jsonld": jsonld.check,
    "nowinstock": nowinstock.check,
    "mystore": mystore.check,
}
```

Use it:

```toml
[[watch]]
source = "mystore"
label = "MyStore"
url = "https://mystore.example/products/thing"
```

## The three rules

**Never guess `OUT_OF_STOCK`.** If you could not read the page, return
`BLOCKED`. If you read it and could not tell, return `UNKNOWN`. Both are
ignored by the transition logic and neither overwrites a known status. A
source that reports `OUT_OF_STOCK` on a timeout will fake a restock the
moment the site comes back.

**Return a stable label.** The label is the key in the state file. If it
changes between runs — because you included a timestamp, or the retailer
renamed itself mid-sentence — the watcher sees a brand new target,
re-baselines it, and stays silent through the restock you were waiting for.

**Let `status.normalise()` do the mapping.** It already handles schema.org
URLs and the usual buy-box wording. Returning a status constant directly is
fine when you genuinely know; inventing a new status string is not, because
nothing downstream will recognise it.

## Returning several targets

One request can cover several retailers — that is what `nowinstock` does.
Prefix the labels so two sources cannot collide:

```python
return {
    f"{prefix}:Amazon": st.OUT_OF_STOCK,
    f"{prefix}:Best Buy": st.PREORDER,
}
```

## Testing it

Swap `fetch` for a function returning saved HTML and assert on the parse —
see `TestNowInStockParser` in `tests/test_watcher.py`. Save a real page to a
fixture while it is out of stock, and again if you ever catch it in stock;
those two files are worth more than any amount of careful reading, because
retailer markup changes without warning.

Then run it for real before you rely on it:

```bash
python3 -m restock_watch --config config.toml --dry-run -v
```
