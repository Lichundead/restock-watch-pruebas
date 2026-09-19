"""Optional source: drive a real headless browser.

Some large retailers render the buy box in JavaScript and serve a bot wall
to plain HTTP clients, so the structured-data source sees nothing useful
there. This adapter loads the page in headless Chromium and inspects it the
way a person would.

It is optional on purpose. Playwright plus a Chromium download is a few
hundred megabytes, and the plain-HTTP sources cover most stores:

    pip install playwright
    python3 -m playwright install chromium

A bot wall is reported as BLOCKED, never as OUT_OF_STOCK — a blocked check
must not look like a status change.
"""

from __future__ import annotations

from typing import Dict

from .. import status as st
from ..http import DEFAULT_USER_AGENT

_BOT_WALL_MARKERS = (
    "enter the characters you see",
    "verify you're human",
    "we need to make sure",
    "unusual traffic",
    "captcha",
    "click the button below to continue shopping",
)

# Reads the page the way a shopper does: is there a button that would take
# my money, and what does it say?
#
# Three rules learned the hard way, each of which had this returning
# IN_STOCK for a page that was plainly sold out:
#
#   1. Scope to the buy box. A product page is full of other products'
#      "Add to Cart" buttons — recommendations, "bought together",
#      carousels. Scanning the whole document finds one every time.
#   2. Only trust a *visible, enabled* button. Amazon leaves the
#      add-to-cart element in the DOM and hides it when the item is gone,
#      so "the element exists" means nothing on its own.
#   3. Match Spanish as well as English. Amazon serves localised pages by
#      IP regardless of Accept-Language, and a pre-order button reading
#      "Reserva ahora" matches none of the English needles — which used to
#      fall through to the InStock default.
#
# And when the evidence runs out it returns '' (UNKNOWN), never a guess.
# UNKNOWN never alerts and never overwrites a known status; a wrong
# IN_STOCK is a false alarm, or worse, a silent wrong baseline.
_BUY_BOX_PROBE = """() => {
    const BUYBOX = ['#desktop_buybox', '#buybox', '#qualifiedBuybox',
                    '#addToCart_feature_div', '#rightCol', '#centerCol'];
    // Order matters below: "disponible" is a substring of "no disponible",
    // and a pre-order page says "Disponible para reserva". So: pre-order
    // first, then out-of-stock, then in-stock.
    const PRE = ['pre-order', 'preorder', 'pre order', 'reserva', 'reservar',
                 'precompra', 'reservalo'];
    const OUT = ['currently unavailable', 'unavailable', 'out of stock',
                 'sold out', 'no disponible', 'no esta disponible',
                 'agotado', 'sin stock', 'no dispon'];
    const IN  = ['in stock', 'en stock', 'add to cart', 'add to bag',
                 'anadir al carrito', 'agregar al carrito', 'comprar ahora',
                 'buy now', 'disponible'];

    const norm = (s) => (s || '')
        .toLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const text = (el) => norm(el && (el.value || el.innerText || el.textContent));
    const hit = (s, list) => list.some((n) => s.includes(n));

    const visible = (el) => {
        if (!el || el.disabled) return false;
        if (el.offsetParent === null) return false;
        const st = window.getComputedStyle(el);
        return st.visibility !== 'hidden' && st.display !== 'none';
    };

    const classify = (s) => {
        if (!s) return '';
        if (hit(s, PRE)) return 'PreOrder';
        if (hit(s, OUT)) return 'OutOfStock';
        if (hit(s, IN)) return 'InStock';
        return '';
    };

    // 1. Structured data, when the page bothers to publish it.
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
            const parsed = JSON.parse(script.textContent);
            for (const item of (Array.isArray(parsed) ? parsed : [parsed])) {
                const offers = item && item.offers;
                if (!offers) continue;
                const offer = Array.isArray(offers) ? offers[0] : offers;
                const availability = typeof offer === 'string' ? offer : (offer.availability || '');
                if (availability) return availability;
            }
        } catch (_) { /* malformed block, try the next one */ }
    }

    // 2. The buy box itself, if we can find it.
    let box = null;
    for (const selector of BUYBOX) {
        const found = document.querySelector(selector);
        if (found) { box = found; break; }
    }

    // 3. The availability line — the most explicit statement on the page.
    const availability = (box || document).querySelector('#availability')
        || document.querySelector('#availability');
    const fromAvailability = classify(text(availability));
    if (fromAvailability) return fromAvailability;

    // 4. A real, clickable buy button inside the buy box.
    if (box) {
        const cart = box.querySelector('#add-to-cart-button');
        if (visible(cart)) {
            const fromCart = classify(text(cart));
            if (fromCart) return fromCart;
        }
        for (const button of box.querySelectorAll('input[type="submit"], button, a[role="button"]')) {
            if (!visible(button)) continue;
            const fromButton = classify(text(button));
            if (fromButton) return fromButton;
        }
    }

    // Nothing conclusive. Say so rather than guessing.
    return '';
}"""


#: label -> the URL that actually answered, from the most recent check().
#: A browser watch points straight at one product page, so that URL is the
#: best possible buy link — better than the tracker's affiliate redirect.
_LAST_LINKS: Dict[str, str] = {}

#: How long to keep polling for the buy box before giving up on a page.
DEFAULT_RENDER_WAIT_MS = 2500
#: Gap between probes while waiting. Short enough that a page which renders
#: in 600ms is not billed for a full fixed sleep.
_PROBE_STEP_MS = 250


def link_for(target: str) -> str | None:
    """The product URL for ``target`` as of the most recent check(), if any."""
    return _LAST_LINKS.get(target)


def _probe_when_ready(page, probe_js: str, budget_ms: int):
    """Poll the buy box until it answers, or the budget runs out.

    The original fixed 2s sleep paid the worst case on every single check.
    Amazon usually renders the buy box well before that, and this source is
    the slow one in the config — a second saved here is a second off how
    long a pre-order window can open and close unseen.
    """
    waited = 0
    raw = ""
    while True:
        raw = page.evaluate(probe_js)
        if raw:
            return raw
        if waited >= budget_ms:
            return raw
        page.wait_for_timeout(_PROBE_STEP_MS)
        waited += _PROBE_STEP_MS


def check(watch: dict) -> Dict[str, str]:
    label = watch.get("label") or "browser"
    urls = [watch["url"]] + list(watch.get("fallback_urls", []))
    timeout_ms = int(watch.get("timeout", 30)) * 1000
    render_wait_ms = int(watch.get("render_wait_ms", DEFAULT_RENDER_WAIT_MS))

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "the 'browser' source needs Playwright: "
            "pip install playwright && python3 -m playwright install chromium"
        ) from None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            # Headless Chromium advertises itself; this is the one flag worth
            # setting. Containers may also need --no-sandbox — add it here if
            # Chromium refuses to start, and understand why before you do.
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                user_agent=watch.get("user_agent", DEFAULT_USER_AGENT),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            page = context.new_page()

            for url in urls:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                except PlaywrightTimeout:
                    continue

                body = page.evaluate("() => document.body.innerText || ''")
                if len(body) < 500:
                    continue
                if any(marker in body[:2000].lower() for marker in _BOT_WALL_MARKERS):
                    continue

                raw = _probe_when_ready(page, _BUY_BOX_PROBE, render_wait_ms)
                _LAST_LINKS[label] = url
                return {label: st.normalise(raw)}

            # Every URL was a bot wall or a timeout. Drop any stale link:
            # this cycle learned nothing about where to buy.
            _LAST_LINKS.pop(label, None)
            return {label: st.BLOCKED}
        finally:
            browser.close()
