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


#: Modules that can name the exact URL behind a target, each exposing
#: link_for(target). Optional: a source with nothing better than the
#: watch's own url simply has no entry here. Looked up by attribute at
#: call time (not bound here) so tests can monkeypatch e.g.
#: nowinstock.link_for and have it take effect.
#:
#: Order matters: browser watches point straight at a product page, which
#: beats the tracker's affiliate redirect. Targets are named per watch, so
#: in practice only one of these ever answers for a given target anyway.
LINK_SOURCES = {
    "browser": browser,
    "nowinstock": nowinstock,
}


def link_for(target: str) -> "str | None":
    """The best known product URL for ``target``, or None if nothing beats
    the generic links already in ``[general] links``."""
    for module in LINK_SOURCES.values():
        link = module.link_for(target)
        if link:
            return link
    return None
