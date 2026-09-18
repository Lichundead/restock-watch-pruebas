"""Telegram bot message.

Setup, about two minutes:

  1. Message @BotFather, send /newbot, keep the token.
  2. Message your new bot once (bots cannot open a chat with you).
  3. Open https://api.telegram.org/bot<TOKEN>/getUpdates and read
     result[0].message.chat.id — that is your chat id.
  4. Put both in the environment:
       RESTOCK_TELEGRAM_TOKEN=...
       RESTOCK_TELEGRAM_CHAT_ID=...
"""

from __future__ import annotations

import json
import os
import urllib.request


def send(config: dict, alert) -> None:
    token = os.environ.get("RESTOCK_TELEGRAM_TOKEN")
    chat_id = os.environ.get("RESTOCK_TELEGRAM_CHAT_ID") or config.get("chat_id")
    if not token or not chat_id:
        raise RuntimeError(
            "telegram channel needs $RESTOCK_TELEGRAM_TOKEN and $RESTOCK_TELEGRAM_CHAT_ID"
        )

    payload = json.dumps(
        {
            "chat_id": str(chat_id),
            "text": f"{alert.title}\n\n{alert.body}",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=int(config.get("timeout", 10))) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"telegram rejected the message: {body.get('description')}")
