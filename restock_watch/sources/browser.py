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
_BUY_BOX_PROBE = """() => {
    const text = (el) => ((el && (el.value || el.innerText || el.textContent)) || '').toLowerCase();

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

    const cart = document.getElementById('add-to-cart-button');
    if (cart) return text(cart).includes('pre-order') ? 'PreOrder' : 'InStock';

    for (const button of document.querySelectorAll('input[type="submit"], button, a[role="button"]')) {
        const label = text(button);
        if (label.includes('pre-order') || label.includes('preorder')) return 'PreOrder';
        if (label.includes('add to cart') || label.includes('add to bag')) return 'InStock';
    }

    const availability = document.getElementById('availability');
    if (availability) return text(availability);

    return '';
}"""


def check(watch: dict) -> Dict[str, str]:
    label = watch.get("label") or "browser"
    urls = [watch["url"]] + list(watch.get("fallback_urls", []))
    timeout_ms = int(watch.get("timeout", 30)) * 1000

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
                    page.wait_for_timeout(2000)
                except PlaywrightTimeout:
                    continue

                body = page.evaluate("() => document.body.innerText || ''")
                if len(body) < 500:
                    continue
                if any(marker in body[:2000].lower() for marker in _BOT_WALL_MARKERS):
                    continue

                raw = page.evaluate(_BUY_BOX_PROBE)
                return {label: st.normalise(raw)}

            return {label: st.BLOCKED}
        finally:
            browser.close()
