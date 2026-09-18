"""Normalised availability statuses.

Every source adapter must return one of these strings. Keeping the
vocabulary tiny is what makes transition detection reliable: the state
file only ever compares these values.
"""

IN_STOCK = "IN_STOCK"
PREORDER = "PREORDER"
BACKORDER = "BACKORDER"
OUT_OF_STOCK = "OUT_OF_STOCK"

#: The page loaded but we could not tell. Never alerts, never overwrites
#: a known status.
UNKNOWN = "UNKNOWN"
#: Bot wall, CAPTCHA, timeout or network failure. Same rules as UNKNOWN.
BLOCKED = "BLOCKED"

#: Statuses that mean "you can act on this right now".
ACTIONABLE = frozenset({IN_STOCK, PREORDER})
#: Statuses that carry no information — a transition into one of these is
#: never an alert, and they must not clobber the last known good value.
UNINFORMATIVE = frozenset({UNKNOWN, BLOCKED})

ALL = (IN_STOCK, PREORDER, BACKORDER, OUT_OF_STOCK, UNKNOWN, BLOCKED)


def normalise(raw: str) -> str:
    """Map a free-form availability string onto the vocabulary above.

    Handles schema.org URLs ("https://schema.org/InStock"), bare schema.org
    tokens, and the loose wording retailers put in their buy boxes.
    """
    if not raw:
        return UNKNOWN
    text = raw.strip().lower().rsplit("/", 1)[-1].replace("-", "").replace("_", "").replace(" ", "")
    if "preorder" in text:
        return PREORDER
    if "backorder" in text:
        return BACKORDER
    if "outofstock" in text or "soldout" in text or "unavailable" in text:
        return OUT_OF_STOCK
    if "limitedavailability" in text:
        return OUT_OF_STOCK
    if "instock" in text or "instore" in text or "onlineonly" in text:
        return IN_STOCK
    return UNKNOWN
