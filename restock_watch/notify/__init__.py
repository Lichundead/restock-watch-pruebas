"""Notification channels.

Each channel exposes ``send(config, alert)`` and raises on failure. The
dispatcher calls every enabled channel independently: one broken channel
must never stop the others, because the whole point is that the alert
arrives.

Secrets come from the environment, never from the config file. See
``.env.example``.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict

from . import console, email_smtp, telegram, webhook

LOG = logging.getLogger("restock-watch.notify")

CHANNELS: Dict[str, Callable[[dict, "Alert"], None]] = {
    "console": console.send,
    "telegram": telegram.send,
    "email": email_smtp.send,
    "webhook": webhook.send,
}


class Alert:
    """One actionable change, rendered for every channel."""

    def __init__(self, title: str, body: str, changes: list, actionable: bool):
        self.title = title
        self.body = body
        self.changes = changes
        self.actionable = actionable

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "body": self.body,
            "changes": self.changes,
            "actionable": self.actionable,
        }


def dispatch(channel_configs: dict, alert: Alert) -> Dict[str, bool]:
    """Send ``alert`` on every enabled channel. Returns {channel: succeeded}."""
    results: Dict[str, bool] = {}
    for name, channel_config in channel_configs.items():
        if not channel_config.get("enabled", False):
            continue
        send = CHANNELS.get(name)
        if send is None:
            LOG.warning("unknown notification channel %r — skipping", name)
            continue
        try:
            send(channel_config, alert)
            results[name] = True
            LOG.info("notified via %s", name)
        except Exception as exc:  # one bad channel must not sink the rest
            results[name] = False
            LOG.error("channel %s failed: %s", name, exc)
    return results
