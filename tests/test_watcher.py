"""Tests for the parts that decide whether you get woken up at 3am.

Run with: python3 -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from restock_watch import status as st  # noqa: E402
from restock_watch import watcher  # noqa: E402
from restock_watch.sources import nowinstock  # noqa: E402
from restock_watch.state import State  # noqa: E402


class TestNormalise(unittest.TestCase):
    def test_schema_org_urls(self):
        self.assertEqual(st.normalise("https://schema.org/InStock"), st.IN_STOCK)
        self.assertEqual(st.normalise("https://schema.org/OutOfStock"), st.OUT_OF_STOCK)
        self.assertEqual(st.normalise("https://schema.org/PreOrder"), st.PREORDER)
        self.assertEqual(st.normalise("https://schema.org/BackOrder"), st.BACKORDER)

    def test_buy_box_wording(self):
        self.assertEqual(st.normalise("Pre-order now"), st.PREORDER)
        self.assertEqual(st.normalise("Currently unavailable"), st.OUT_OF_STOCK)
        self.assertEqual(st.normalise("In Stock"), st.IN_STOCK)

    def test_limited_availability_is_not_in_stock(self):
        self.assertEqual(st.normalise("LimitedAvailability"), st.OUT_OF_STOCK)

    def test_nothing_useful(self):
        self.assertEqual(st.normalise(""), st.UNKNOWN)
        self.assertEqual(st.normalise("banana"), st.UNKNOWN)


class TestDetectChanges(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self._tmp.name) / "state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_sighting_is_a_baseline_not_an_alert(self):
        changes = watcher.detect_changes({"A": st.PREORDER}, self.state)
        self.assertEqual(changes, [])
        self.assertEqual(self.state.get("A"), st.PREORDER)

    def test_restock_is_reported_once(self):
        watcher.detect_changes({"A": st.OUT_OF_STOCK}, self.state)
        first = watcher.detect_changes({"A": st.IN_STOCK}, self.state)
        second = watcher.detect_changes({"A": st.IN_STOCK}, self.state)
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]["actionable"])
        self.assertEqual(second, [])

    def test_blocked_never_clobbers_a_known_status(self):
        watcher.detect_changes({"A": st.IN_STOCK}, self.state)
        changes = watcher.detect_changes({"A": st.BLOCKED}, self.state)
        self.assertEqual(changes, [])
        self.assertEqual(self.state.get("A"), st.IN_STOCK)

    def test_captcha_then_normal_page_is_not_a_restock(self):
        watcher.detect_changes({"A": st.OUT_OF_STOCK}, self.state)
        watcher.detect_changes({"A": st.BLOCKED}, self.state)
        changes = watcher.detect_changes({"A": st.OUT_OF_STOCK}, self.state)
        self.assertEqual(changes, [])

    def test_going_out_of_stock_is_a_change_but_not_actionable(self):
        watcher.detect_changes({"A": st.IN_STOCK}, self.state)
        changes = watcher.detect_changes({"A": st.OUT_OF_STOCK}, self.state)
        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0]["actionable"])


class TestStatePersistence(unittest.TestCase):
    def test_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            first = State(path)
            first.set("A", st.PREORDER)
            first.save()
            self.assertEqual(State(path).get("A"), st.PREORDER)

    def test_corrupt_state_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{ not json")
            self.assertEqual(State(path).statuses, {})


class TestNowInStockParser(unittest.TestCase):
    SAMPLE = """
    <tr id="tr12" class="offrow"><td><a href="/a">Widget Deluxe Edition: Amazon</a></td></tr>
    <tr id="tr13" class="preorder"><td><a href="/b">Widget Deluxe Edition: Best Buy</a></td></tr>
    <tr id="tr14" class="onrow"><td><a href="/c">Unrelated Product: Target</a></td></tr>
    """

    def setUp(self):
        self._real_fetch = nowinstock.fetch
        nowinstock.fetch = lambda *a, **k: self.SAMPLE

    def tearDown(self):
        nowinstock.fetch = self._real_fetch

    def test_matches_only_the_requested_product(self):
        result = nowinstock.check({"url": "x", "match": "Widget", "label": "nis"})
        self.assertEqual(
            result, {"nis:Amazon": st.OUT_OF_STOCK, "nis:Best Buy": st.PREORDER}
        )

    def test_retailer_filter(self):
        result = nowinstock.check(
            {"url": "x", "match": "Widget", "label": "nis", "retailers": ["Best Buy"]}
        )
        self.assertEqual(result, {"nis:Best Buy": st.PREORDER})

    def test_fetch_failure_returns_nothing_rather_than_out_of_stock(self):
        from restock_watch.http import FetchError

        def boom(*a, **k):
            raise FetchError("down")

        nowinstock.fetch = boom
        self.assertEqual(nowinstock.check({"url": "x", "match": "Widget"}), {})


class TestAlertBody(unittest.TestCase):
    def test_actionable_alert_names_the_product_and_links(self):
        changes = [{"target": "Best Buy", "from": "OUT_OF_STOCK", "to": "IN_STOCK", "actionable": True}]
        config = {"general": {"product_name": "Widget", "links": ["https://example.com/buy"]}}
        alert = watcher.build_alert(changes, config)
        self.assertIn("IN STOCK: Widget", alert.title)
        self.assertIn("https://example.com/buy", alert.body)
        self.assertTrue(alert.actionable)
        self.assertEqual(json.loads(json.dumps(alert.as_dict()))["changes"], changes)


if __name__ == "__main__":
    unittest.main()
