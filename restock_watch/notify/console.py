"""Print the alert to stdout. Always available, useful under cron mail."""

from __future__ import annotations

import sys


def send(config: dict, alert) -> None:
    stream = sys.stderr if config.get("stderr") else sys.stdout
    print(f"\n*** {alert.title} ***\n{alert.body}\n", file=stream, flush=True)
