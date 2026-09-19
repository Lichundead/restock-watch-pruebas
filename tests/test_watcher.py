"""Tests for the parts that decide whether you get woken up at 3am.

Run with: python3 -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from restock_watch import http  # noqa: E402
from restock_watch import schedule  # noqa: E402
from restock_watch import status as st  # noqa: E402
from restock_watch import watcher  # noqa: E402
from restock_watch import sources  # noqa: E402
from restock_watch.schedule import Scheduler  # noqa: E402
from restock_watch.sources import jsonld, nowinstock  # noqa: E402
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

    def test_schema_org_orderable_variants_are_in_stock(self):
        self.assertEqual(st.normalise("LimitedAvailability"), st.IN_STOCK)
        self.assertEqual(st.normalise("MadeToOrder"), st.IN_STOCK)

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

    def test_a_preorder_opening_is_an_actionable_alert(self):
        # Deliberate product decision, pinned so a refactor cannot quietly
        # drop PREORDER from ACTIONABLE: for pre-release hardware the
        # pre-order window is the event people install this to catch.
        watcher.detect_changes({"A": st.OUT_OF_STOCK}, self.state)
        changes = watcher.detect_changes({"A": st.PREORDER}, self.state)

        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0]["actionable"])
        self.assertIn(st.PREORDER, st.ACTIONABLE)

    def test_a_preorder_alert_says_preorder_not_in_stock(self):
        changes = [
            {"target": "Target", "from": "OUT_OF_STOCK", "to": st.PREORDER, "actionable": True}
        ]
        alert = watcher.build_alert(changes, {"general": {"product_name": "Widget"}})
        self.assertTrue(alert.actionable)
        self.assertIn("PREORDER: Widget", alert.title)

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
    """Parsed against markup captured from the live tracker.

    The fixture pins down the contract that matters: every Zelda row on the
    real page is class="offRow", and the actual status lives in a
    <td class="stockStatus*"> cell. Target was showing Preorder at capture
    time while sharing a row class with the out-of-stock rows.
    """

    FIXTURES = Path(__file__).resolve().parent / "fixtures"
    PRODUCT = "Legend of Zelda 40th Anniversary Edition"

    def setUp(self):
        self._real_fetch = nowinstock.fetch
        self.sample = (
            self.FIXTURES / "nowinstock_switch2_zelda_2026-09-17.html"
        ).read_text()
        nowinstock.fetch = lambda *a, **k: self.sample

    def tearDown(self):
        nowinstock.fetch = self._real_fetch

    def test_reads_the_status_cell_not_the_row_class(self):
        result = nowinstock.check({"url": "x", "match": self.PRODUCT, "label": "nis"})
        self.assertEqual(
            result,
            {
                "nis:Amazon": st.OUT_OF_STOCK,
                "nis:Best Buy": st.OUT_OF_STOCK,
                "nis:Nintendo Store": st.OUT_OF_STOCK,
                "nis:Target": st.PREORDER,
                "nis:Walmart": st.OUT_OF_STOCK,
            },
        )

    def test_a_preorder_is_not_reported_as_out_of_stock(self):
        # The regression this fixture exists for: reading the row class alone
        # reported OUT_OF_STOCK here, so the pre-order never alerted.
        result = nowinstock.check(
            {"url": "x", "match": self.PRODUCT, "label": "nis", "retailers": ["Target"]}
        )
        self.assertEqual(result, {"nis:Target": st.PREORDER})
        self.assertIn(result["nis:Target"], st.ACTIONABLE)

    def test_fixture_preserves_the_misleading_row_class(self):
        # Guards against a future "tidy-up" reintroducing class-based parsing
        # because a hand-written fixture happened to agree with it.
        self.assertIn('class="offRow"', self.sample)
        self.assertNotIn('class="preorder"', self.sample)
        self.assertIn("stockStatusPre", self.sample)

    def test_retailer_filter(self):
        result = nowinstock.check(
            {"url": "x", "match": self.PRODUCT, "label": "nis", "retailers": ["Best Buy"]}
        )
        self.assertEqual(result, {"nis:Best Buy": st.OUT_OF_STOCK})

    def test_unrelated_products_on_the_same_page_are_excluded(self):
        result = nowinstock.check({"url": "x", "match": self.PRODUCT, "label": "nis"})
        self.assertEqual(len(result), 5)

    def test_fetch_failure_returns_nothing_rather_than_out_of_stock(self):
        from restock_watch.http import FetchError

        def boom(*a, **k):
            raise FetchError("down")

        nowinstock.fetch = boom
        self.assertEqual(nowinstock.check({"url": "x", "match": self.PRODUCT}), {})


class TestJsonLdParser(unittest.TestCase):
    FIXTURES = Path(__file__).resolve().parent / "fixtures"

    def setUp(self):
        self._real_fetch = jsonld.fetch

    def tearDown(self):
        jsonld.fetch = self._real_fetch

    def test_captured_nintendo_page_parses_by_sku(self):
        # Real markup from the live store page, matched on the store's own SKU.
        html = (self.FIXTURES / "nintendo_switch2_zelda_2026-09-17.html").read_text()
        jsonld.fetch = lambda *a, **k: html
        self.assertEqual(
            jsonld.check({"url": "x", "label": "Nintendo", "match": "121642"}),
            {"Nintendo": st.OUT_OF_STOCK},
        )

    def test_captured_page_fails_closed_on_a_wrong_sku(self):
        # A match that names a different product must never borrow this one's
        # availability — better to say nothing than to alert about the wrong item.
        html = (self.FIXTURES / "nintendo_switch2_zelda_2026-09-17.html").read_text()
        jsonld.fetch = lambda *a, **k: html
        self.assertEqual(
            jsonld.check({"url": "x", "label": "Nintendo", "match": "999999"}),
            {"Nintendo": st.UNKNOWN},
        )

    def test_product_without_any_availability_is_unknown(self):
        html = (self.FIXTURES / "synthetic_product_without_availability.html").read_text()
        jsonld.fetch = lambda *a, **k: html
        self.assertEqual(
            jsonld.check({"url": "x", "label": "Nintendo", "match": "Zelda"}),
            {"Nintendo": st.UNKNOWN},
        )

    def test_prefers_matching_product_jsonld(self):
        html = """
        <script type="application/ld+json">
        {
          "@graph": [
            {"@type":"Product","name":"Other Widget","offers":{"availability":"https://schema.org/InStock"}},
            {"@type":"Product","name":"Target Widget","offers":{"availability":"https://schema.org/OutOfStock"}}
          ]
        }
        </script>
        """
        jsonld.fetch = lambda *a, **k: html
        result = jsonld.check({"url": "x", "label": "store", "match": "Target Widget"})
        self.assertEqual(result, {"store": st.OUT_OF_STOCK})

    def test_requested_product_does_not_fall_back_to_another_product(self):
        html = """
        <script type="application/ld+json">
        {"@type":"Product","name":"Other Widget","offers":{"availability":"https://schema.org/InStock"}}
        </script>
        """
        jsonld.fetch = lambda *a, **k: html
        self.assertEqual(
            jsonld.check({"url": "x", "label": "store", "match": "Target Widget"}),
            {"store": st.UNKNOWN},
        )

    def test_malformed_jsonld_falls_back_without_crashing(self):
        html = """
        <script type="application/ld+json">{ definitely not json }</script>
        <div data-state='{"availability":"https://schema.org/PreOrder"}'></div>
        """
        jsonld.fetch = lambda *a, **k: html
        self.assertEqual(
            jsonld.check({"url": "x", "label": "store"}),
            {"store": st.PREORDER},
        )


class TestWatcherReliability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self._tmp.name) / "state.json")
        self._real_collect = watcher.collect
        self._real_dispatch = watcher.dispatch

    def tearDown(self):
        watcher.collect = self._real_collect
        watcher.dispatch = self._real_dispatch
        self._tmp.cleanup()

    def test_collect_rejects_invalid_source_status(self):
        real_get_source = watcher.get_source
        try:
            watcher.get_source = lambda name: (lambda watch: {"A": "NOT_A_STATUS"})
            self.assertEqual(
                watcher.collect([{"source": "fake", "label": "fake"}]),
                {"A": st.UNKNOWN},
            )
        finally:
            watcher.get_source = real_get_source

    def test_dry_run_does_not_mutate_in_memory_state(self):
        self.state.set("A", st.OUT_OF_STOCK)
        watcher.collect = lambda watches: {"A": st.IN_STOCK}
        config = {
            "watch": [{"source": "fake"}],
            "notify": {"console": {"enabled": True}},
            "general": {"product_name": "Widget"},
        }
        watcher.run_once(config, self.state, dry_run=True)
        self.assertEqual(self.state.get("A"), st.OUT_OF_STOCK)

    def test_all_notification_failures_restore_state_for_retry(self):
        self.state.set("A", st.OUT_OF_STOCK)
        watcher.collect = lambda watches: {"A": st.IN_STOCK}
        watcher.dispatch = lambda channel_config, alert: {"webhook": False}
        config = {
            "watch": [{"source": "fake"}],
            "notify": {"webhook": {"enabled": True}},
            "general": {"product_name": "Widget"},
        }

        with self.assertRaises(watcher.NotificationDeliveryError):
            watcher.run_once(config, self.state)

        self.assertEqual(self.state.get("A"), st.OUT_OF_STOCK)


class TestAlertBody(unittest.TestCase):
    def test_actionable_alert_names_the_product_and_links(self):
        changes = [{"target": "Best Buy", "from": "OUT_OF_STOCK", "to": "IN_STOCK", "actionable": True}]
        config = {"general": {"product_name": "Widget", "links": ["https://example.com/buy"]}}
        alert = watcher.build_alert(changes, config)
        self.assertIn("IN STOCK: Widget", alert.title)
        self.assertIn("https://example.com/buy", alert.body)
        self.assertTrue(alert.actionable)
        self.assertEqual(json.loads(json.dumps(alert.as_dict()))["changes"], changes)


class TestCollectMergesSources(unittest.TestCase):
    """Two sources reporting the same target.

    The rule that matters: UNKNOWN and BLOCKED mean "this source learned
    nothing". They must never cancel out a source that did learn something,
    or a bot-walled second check silently swallows the restock alert.
    """

    def setUp(self):
        self._real_get_source = watcher.get_source

    def tearDown(self):
        watcher.get_source = self._real_get_source

    def _collect(self, first, second):
        results = iter(({"A": first}, {"A": second}))
        watcher.get_source = lambda name: (lambda watch: next(results))
        return watcher.collect(
            [{"source": "one", "label": "one"}, {"source": "two", "label": "two"}]
        )["A"]

    def test_blocked_does_not_veto_a_real_reading(self):
        self.assertEqual(self._collect(st.IN_STOCK, st.BLOCKED), st.IN_STOCK)

    def test_real_reading_replaces_an_earlier_blocked(self):
        self.assertEqual(self._collect(st.BLOCKED, st.IN_STOCK), st.IN_STOCK)

    def test_unknown_does_not_veto_a_real_reading(self):
        self.assertEqual(self._collect(st.PREORDER, st.UNKNOWN), st.PREORDER)

    def test_two_sources_that_both_claim_to_know_and_disagree(self):
        self.assertEqual(self._collect(st.IN_STOCK, st.OUT_OF_STOCK), st.UNKNOWN)

    def test_agreement_is_passed_through(self):
        self.assertEqual(self._collect(st.OUT_OF_STOCK, st.OUT_OF_STOCK), st.OUT_OF_STOCK)

    def test_two_uninformative_readings_stay_uninformative(self):
        self.assertIn(self._collect(st.BLOCKED, st.UNKNOWN), st.UNINFORMATIVE)


class TestNowInStockLinks(unittest.TestCase):
    """Each row's own href, not just the generic [general] links."""

    FIXTURES = Path(__file__).resolve().parent / "fixtures"
    PRODUCT = "Legend of Zelda 40th Anniversary Edition"

    def setUp(self):
        self._real_fetch = nowinstock.fetch
        self.sample = (
            self.FIXTURES / "nowinstock_switch2_zelda_2026-09-17.html"
        ).read_text()
        nowinstock.fetch = lambda *a, **k: self.sample

    def tearDown(self):
        nowinstock.fetch = self._real_fetch

    def test_check_records_a_link_per_target(self):
        nowinstock.check({"url": "x", "match": self.PRODUCT, "label": "nis"})
        self.assertEqual(nowinstock.link_for("nis:Amazon"), "https://example.invalid/product")

    def test_unknown_target_has_no_link(self):
        nowinstock.check({"url": "x", "match": self.PRODUCT, "label": "nis"})
        self.assertIsNone(nowinstock.link_for("nis:Nowhere"))

    def test_a_later_check_drops_links_for_targets_no_longer_seen(self):
        nowinstock.check(
            {"url": "x", "match": self.PRODUCT, "label": "nis", "retailers": ["Amazon"]}
        )
        nowinstock.check(
            {"url": "x", "match": self.PRODUCT, "label": "nis", "retailers": ["Target"]}
        )
        self.assertIsNone(nowinstock.link_for("nis:Amazon"))
        self.assertEqual(nowinstock.link_for("nis:Target"), "https://example.invalid/product")

    def test_amazon_dp_link_drops_the_all_offers_flag(self):
        # Real NowInStock href, captured 2026-09-18: aod=1 forces Amazon's
        # multi-seller popup instead of the product page's own "Reserve
        # now" button.
        url = (
            "https://www.amazon.com/dp/B0HJ6F8L6V"
            "?tag=nisamain-20&linkCode=ogi&th=1&psc=1&m=ATVPDKIKX0DER&aod=1"
        )
        self.assertEqual(nowinstock._clean_link(url), "https://www.amazon.com/dp/B0HJ6F8L6V")

    def test_amazon_gp_product_link_is_also_normalised(self):
        url = "https://www.amazon.com/gp/product/B0FC5FJZ9Z?tag=nisws2-20"
        self.assertEqual(nowinstock._clean_link(url), "https://www.amazon.com/dp/B0FC5FJZ9Z")

    def test_amazon_link_with_title_slug_is_also_normalised(self):
        url = (
            "https://www.amazon.com/Nintendo-Switch-2-System/dp/B0F3GWXLTS/"
            "?tag=nisws2-20&linkCode=ogi&th=1&psc=1&m=ATVPDKIKX0DER&aod=1"
        )
        self.assertEqual(nowinstock._clean_link(url), "https://www.amazon.com/dp/B0F3GWXLTS")

    def test_non_amazon_links_pass_through_unchanged(self):
        # Best Buy and Target/Walmart go through an affiliate redirector
        # (7tiv.net, howl.link) that needs its own query string to work.
        url = "https://howl.link/h3msxu6tmg269"
        self.assertEqual(nowinstock._clean_link(url), url)


class TestAlertUsesTheSpecificLink(unittest.TestCase):
    def test_build_alert_shows_the_per_target_link_when_present(self):
        changes = [
            {
                "target": "nis:Amazon",
                "from": "OUT_OF_STOCK",
                "to": "PREORDER",
                "actionable": True,
                "link": "https://amazon.example/dp/B0TEST",
            }
        ]
        alert = watcher.build_alert(changes, {"general": {"product_name": "Widget"}})
        self.assertIn("nis:Amazon: OUT_OF_STOCK -> PREORDER — https://amazon.example/dp/B0TEST", alert.body)

    def test_build_alert_omits_the_dash_when_no_link_is_known(self):
        changes = [
            {"target": "Nintendo", "from": "OUT_OF_STOCK", "to": "IN_STOCK", "actionable": True}
        ]
        alert = watcher.build_alert(changes, {"general": {"product_name": "Widget"}})
        self.assertIn("Nintendo: OUT_OF_STOCK -> IN_STOCK", alert.body)
        self.assertNotIn("—", alert.body)

    def test_run_once_attaches_the_nowinstock_link_before_alerting(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = State(Path(tmp.name) / "state.json")
        state.set("nis:Amazon", st.OUT_OF_STOCK)

        real_link_for = nowinstock.link_for
        nowinstock.link_for = lambda target: (
            "https://amazon.example/dp/B0TEST" if target == "nis:Amazon" else None
        )
        real_collect = watcher.collect
        watcher.collect = lambda watches: {"nis:Amazon": st.PREORDER}
        sent = {}
        real_dispatch = watcher.dispatch
        watcher.dispatch = lambda channel_config, alert: (
            sent.update(body=alert.body) or {"console": True}
        )
        try:
            config = {
                "watch": [{"source": "nowinstock"}],
                "notify": {"console": {"enabled": True}},
                "general": {"product_name": "Widget"},
            }
            watcher.run_once(config, state)
        finally:
            nowinstock.link_for = real_link_for
            watcher.collect = real_collect
            watcher.dispatch = real_dispatch

        self.assertIn("https://amazon.example/dp/B0TEST", sent["body"])


class TestConditionalRequests(unittest.TestCase):
    """A 304 means 'unchanged', which is not the same as 'we learned nothing'."""

    def setUp(self):
        self._real_fetch = nowinstock.fetch
        self._real_jsonld_fetch = jsonld.fetch

    def tearDown(self):
        nowinstock.fetch = self._real_fetch
        jsonld.fetch = self._real_jsonld_fetch

    def test_not_modified_is_not_a_fetch_error(self):
        # Subclassing FetchError would make every source treat a cheap 304
        # as a bot wall.
        self.assertFalse(issubclass(http.NotModified, http.FetchError))

    def test_nowinstock_304_reports_nothing_rather_than_blocked(self):
        def not_modified(*a, **k):
            raise http.NotModified("x")

        nowinstock.fetch = not_modified
        self.assertEqual(nowinstock.check({"url": "x", "label": "nis"}), {})

    def test_jsonld_304_reports_nothing_rather_than_blocked(self):
        def not_modified(*a, **k):
            raise http.NotModified("x")

        jsonld.fetch = not_modified
        # BLOCKED here would discard a perfectly good known status.
        self.assertEqual(jsonld.check({"url": "x", "label": "store"}), {})

    def test_304_leaves_cached_links_intact(self):
        fixtures = Path(__file__).resolve().parent / "fixtures"
        sample = (fixtures / "nowinstock_switch2_zelda_2026-09-17.html").read_text()
        nowinstock.fetch = lambda *a, **k: sample
        nowinstock.check(
            {"url": "x", "match": "Legend of Zelda 40th Anniversary Edition", "label": "nis"}
        )

        def not_modified(*a, **k):
            raise http.NotModified("x")

        nowinstock.fetch = not_modified
        nowinstock.check({"url": "x", "label": "nis"})
        self.assertEqual(nowinstock.link_for("nis:Amazon"), "https://example.invalid/product")

    def test_retry_after_parses_plain_seconds(self):
        self.assertEqual(http._retry_after_seconds("120"), 120.0)

    def test_retry_after_ignores_junk(self):
        self.assertIsNone(http._retry_after_seconds("soon"))
        self.assertIsNone(http._retry_after_seconds(None))

    def test_an_absent_target_is_not_a_change(self):
        # This is what makes a 304 safe: the target simply is not in the
        # observed map, and detect_changes only iterates what it was given.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = State(Path(tmp.name) / "state.json")
        state.set("A", st.IN_STOCK)
        self.assertEqual(watcher.detect_changes({}, state), [])
        self.assertEqual(state.get("A"), st.IN_STOCK)


class TestScheduler(unittest.TestCase):
    """Each watch on its own clock, and backoff that obeys the server."""

    def setUp(self):
        self.now = 1000.0

    def _clock(self):
        return self.now

    def test_a_watch_uses_its_own_interval_over_the_global_one(self):
        watches = [{"label": "fast", "interval_seconds": 60}, {"label": "slow"}]
        sched = Scheduler(watches, default_interval=300, clock=self._clock)

        self.assertEqual([w["label"] for _, w in sched.due()], ["fast", "slow"])
        for index, _ in list(sched.due()):
            sched.record_success(index)

        self.now += 60
        self.assertEqual([w["label"] for _, w in sched.due()], ["fast"])

        self.now += 240
        self.assertEqual({w["label"] for _, w in sched.due()}, {"fast", "slow"})

    def test_offset_staggers_the_first_run(self):
        watches = [{"label": "a"}, {"label": "b", "offset_seconds": 30}]
        sched = Scheduler(watches, default_interval=60, clock=self._clock)

        self.assertEqual([w["label"] for _, w in sched.due()], ["a"])
        self.now += 30
        self.assertEqual({w["label"] for _, w in sched.due()}, {"a", "b"})

    def test_rate_limit_backs_that_watch_off_and_doubles_on_repeats(self):
        sched = Scheduler([{"label": "a", "interval_seconds": 60}], 60, clock=self._clock)
        sched.record_rate_limited(0)

        self.now += 60
        self.assertEqual(sched.due(), [], "should still be backing off at 1x interval")
        self.now += 61
        self.assertEqual(len(sched.due()), 1)

        # A second strike backs off further than the first.
        sched.record_rate_limited(0)
        self.now += 121
        self.assertEqual(sched.due(), [])

    def test_server_retry_after_wins_when_it_is_longer(self):
        sched = Scheduler([{"label": "a", "interval_seconds": 60}], 60, clock=self._clock)
        sched.record_rate_limited(0, retry_after=600)
        self.now += 599
        self.assertEqual(sched.due(), [])
        self.now += 2
        self.assertEqual(len(sched.due()), 1)

    def test_backoff_is_capped(self):
        sched = Scheduler([{"label": "a", "interval_seconds": 60}], 60, clock=self._clock)
        sched.record_rate_limited(0, retry_after=10**9)
        self.now += schedule.MAX_BACKOFF_SECONDS + 1
        self.assertEqual(len(sched.due()), 1)

    def test_success_clears_the_backoff(self):
        sched = Scheduler([{"label": "a", "interval_seconds": 60}], 60, clock=self._clock)
        sched.record_rate_limited(0)
        sched.record_rate_limited(0)
        sched.record_success(0)

        self.now += 61
        self.assertEqual(len(sched.due()), 1, "backoff should not survive a clean run")

    def test_sleep_is_capped_so_ctrl_c_stays_responsive(self):
        sched = Scheduler([{"label": "a"}], 3600, clock=self._clock)
        sched.record_success(0)
        self.assertLessEqual(sched.sleep_seconds(), 60.0)

    def test_one_blocked_watch_does_not_delay_the_others(self):
        watches = [{"label": "walled"}, {"label": "fine"}]
        sched = Scheduler(watches, default_interval=60, clock=self._clock)
        sched.record_rate_limited(0, retry_after=1800)
        sched.record_success(1)

        self.now += 61
        self.assertEqual([w["label"] for _, w in sched.due()], ["fine"])


class TestPerWatchIntervalValidation(unittest.TestCase):
    def test_a_per_watch_interval_below_the_floor_is_rejected(self):
        from restock_watch.config import ConfigError, validate

        config = {
            "watch": [
                {"source": "jsonld", "url": "https://x.invalid", "interval_seconds": 5}
            ]
        }
        with self.assertRaises(ConfigError):
            validate(config)

    def test_a_per_watch_interval_at_the_floor_is_accepted(self):
        from restock_watch.config import validate

        config = {
            "watch": [
                {"source": "jsonld", "url": "https://x.invalid", "interval_seconds": 60}
            ],
            "general": {"interval_seconds": 300},
        }
        validate(config)

    def test_negative_offset_is_rejected(self):
        from restock_watch.config import ConfigError, validate

        config = {
            "watch": [
                {"source": "jsonld", "url": "https://x.invalid", "offset_seconds": -5}
            ]
        }
        with self.assertRaises(ConfigError):
            validate(config)


class TestCollectReportsErrors(unittest.TestCase):
    def setUp(self):
        self._real_get_source = watcher.get_source

    def tearDown(self):
        watcher.get_source = self._real_get_source

    def test_on_error_receives_the_rate_limit_exception(self):
        boom = http.RateLimited("nope", retry_after=42)

        def explode(watch):
            raise boom

        watcher.get_source = lambda name: explode
        seen = []
        result = watcher.collect(
            [{"source": "x", "label": "a"}], on_error=lambda pos, exc: seen.append((pos, exc))
        )

        self.assertEqual(seen, [(0, boom)])
        self.assertEqual(seen[0][1].retry_after, 42)
        # And it is still BLOCKED, so it cannot look like a status change.
        self.assertEqual(result, {"a": st.BLOCKED})

    def test_collect_still_works_without_a_callback(self):
        watcher.get_source = lambda name: (lambda watch: {"a": st.IN_STOCK})
        self.assertEqual(watcher.collect([{"source": "x", "label": "a"}]), {"a": st.IN_STOCK})


class TestCliExitCodes(unittest.TestCase):
    """A delivery failure is an operational event, not a crash."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_collect = watcher.collect
        self._real_dispatch = watcher.dispatch

    def tearDown(self):
        watcher.collect = self._real_collect
        watcher.dispatch = self._real_dispatch
        self._tmp.cleanup()

    def _config_file(self) -> Path:
        path = Path(self._tmp.name) / "config.toml"
        path.write_text(
            "[general]\n"
            'product_name = "Widget"\n'
            "interval_seconds = 300\n"
            f'state_file = "{Path(self._tmp.name) / "state.json"}"\n'
            "[[watch]]\n"
            'source = "jsonld"\n'
            'url = "https://example.invalid/thing"\n'
            'label = "A"\n'
            "[notify.webhook]\n"
            "enabled = true\n"
            'url = "http://127.0.0.1:9/nope"\n'
        )
        return path

    def test_total_delivery_failure_exits_cleanly(self):
        from restock_watch.__main__ import EXIT_DELIVERY_FAILED, main

        state = State(Path(self._tmp.name) / "state.json")
        state.set("A", st.OUT_OF_STOCK)
        state.save()

        watcher.collect = lambda watches: {"A": st.IN_STOCK}
        watcher.dispatch = lambda channel_config, alert: {"webhook": False}

        # No exception escapes to the user, and the code is one a cron or
        # systemd wrapper can act on.
        self.assertEqual(main(["-c", str(self._config_file())]), EXIT_DELIVERY_FAILED)

        # State was not advanced, so the next cycle retries the alert.
        self.assertEqual(State(Path(self._tmp.name) / "state.json").get("A"), st.OUT_OF_STOCK)


if __name__ == "__main__":
    unittest.main()
