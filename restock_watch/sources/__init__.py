"""Source adapters.

A source adapter is a callable:

    check(watch_config) -> dict[str, str]

It maps a human-readable target label (usually a retailer name) to one of
the statuses in ``restock_watch.status``. Returning an empty dict means
"nothing observed this cycle" and is treated as a soft failure, not as
"everything went out of stock".

To add your own retailer, drop a module in this package that exposes
``check(watch)`` and register it in ``SOURCES`` below. See
``docs/ADDING-A-SOURCE.md``.
"""

from __future__ import annotations

from typing import Callable, Dict

from . import browser, jsonld, nowinstock

SOURCES: Dict[str, Callable[[dict], Dict[str, str]]] = {
    "jsonld": jsonld.check,
    "nowinstock": nowinstock.check,
    "browser": browser.check,
}


def get(name: str) -> Callable[[dict], Dict[str, str]]:
    try:
        return SOURCES[name]
    except KeyError:
        raise KeyError(
            f"unknown source {name!r}; available: {', '.join(sorted(SOURCES))}"
        ) from None
