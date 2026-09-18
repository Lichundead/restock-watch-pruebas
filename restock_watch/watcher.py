"""The polling loop: check every watch, detect transitions, alert once."""

from __future__ import annotations

import logging
from typing import Dict, List

from . import status as st
from .notify import Alert, dispatch
from .sources import get as get_source
from .state import State

LOG = logging.getLogger("restock-watch")


def collect(watches: List[dict]) -> Dict[str, str]:
    """Run every watch and merge the results into one {target: status} map."""
    observed: Dict[str, str] = {}
    for watch in watches:
        label = watch.get("label") or watch.get("source")
        try:
            result = get_source(watch["source"])(watch)
        except Exception as exc:
            # A source that throws is a source that told us nothing. Record
            # BLOCKED so the run is visible in the log, and move on.
            LOG.error("watch %s failed: %s", label, exc)
            observed[str(label)] = st.BLOCKED
            continue
        if not result:
            LOG.warning("watch %s returned nothing", label)
        observed.update(result)
    return observed


def detect_changes(observed: Dict[str, str], state: State) -> List[dict]:
    """Compare against last known status and update state in place.

    An uninformative reading (UNKNOWN/BLOCKED) never counts as a change and
    never overwrites a known status — otherwise a CAPTCHA today plus a normal
    page tomorrow would look like a restock.
    """
    changes: List[dict] = []
    for target in sorted(observed):
        current = observed[target]
        previous = state.get(target)

        if current in st.UNINFORMATIVE:
            LOG.info("%s: %s (no signal, keeping %s)", target, current, previous or "nothing")
            continue

        if previous is None:
            # First sighting: record it as the baseline. Alerting here would
            # mean a fresh install pages you about a pre-order you already
            # knew about.
            LOG.info("%s: baseline %s", target, current)
            state.set(target, current)
            continue

        if previous == current:
            LOG.info("%s: %s (unchanged)", target, current)
            continue

        LOG.info("%s: %s -> %s", target, previous, current)
        changes.append(
            {
                "target": target,
                "from": previous,
                "to": current,
                "actionable": current in st.ACTIONABLE,
            }
        )
        state.set(target, current)

    return changes


def build_alert(changes: List[dict], config: dict) -> Alert:
    product = config.get("general", {}).get("product_name", "Tracked item")
    links = config.get("general", {}).get("links", [])
    actionable = [c for c in changes if c["actionable"]]

    if actionable:
        title = f"IN STOCK: {product}"
        lines = [f"{product} is available:", ""]
        lines += [f"  {c['target']}: {c['from']} -> {c['to']}" for c in actionable]
        other = [c for c in changes if not c["actionable"]]
        if other:
            lines += ["", "Also changed:"]
            lines += [f"  {c['target']}: {c['from']} -> {c['to']}" for c in other]
        if links:
            lines += ["", "Buy links:"] + [f"  {link}" for link in links]
    else:
        title = f"Status changed: {product}"
        lines = [f"{product} changed, but is not purchasable yet:", ""]
        lines += [f"  {c['target']}: {c['from']} -> {c['to']}" for c in changes]

    return Alert(
        title=title,
        body="\n".join(lines),
        changes=changes,
        actionable=bool(actionable),
    )


def run_once(config: dict, state: State, dry_run: bool = False) -> int:
    """One polling cycle. Returns the number of changes that alerted."""
    observed = collect(config["watch"])
    changes = detect_changes(observed, state)

    general = config.get("general", {})
    alert_on_any_change = bool(general.get("alert_on_any_change", False))
    worth_alerting = [c for c in changes if c["actionable"] or alert_on_any_change]

    if not worth_alerting:
        if not dry_run:
            state.save()
        return 0

    alert = build_alert(worth_alerting, config)

    if dry_run:
        LOG.info("dry run — not sending, not saving state")
        print(f"\n--- would send ---\n{alert.title}\n\n{alert.body}\n")
        return len(worth_alerting)

    results = dispatch(config.get("notify", {}), alert)
    # State is saved regardless of delivery outcome: re-alerting on every
    # cycle because one channel is down is worse than missing one message,
    # and the successful channels already told you.
    state.save()

    if results and not any(results.values()):
        LOG.error("every notification channel failed")

    return len(worth_alerting)
