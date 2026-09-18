"""POST the alert as JSON to any URL.

This is the escape hatch: point it at n8n, Home Assistant, Discord (use a
``content`` transform), ntfy, or your own service.
"""

from __future__ import annotations

import json
import os
import urllib.request


def send(config: dict, alert) -> None:
    url = config.get("url") or os.environ.get("RESTOCK_WEBHOOK_URL")
    if not url:
        raise RuntimeError("webhook channel needs 'url' or $RESTOCK_WEBHOOK_URL")

    payload = json.dumps(alert.as_dict()).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    token = os.environ.get("RESTOCK_WEBHOOK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=int(config.get("timeout", 10))) as response:
        if response.status >= 300:
            raise RuntimeError(f"webhook returned HTTP {response.status}")
