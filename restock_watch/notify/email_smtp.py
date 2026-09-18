"""Email over SMTP.

With Gmail you need an app password, not your account password — regular
passwords are refused. Set:

    RESTOCK_SMTP_HOST=smtp.gmail.com
    RESTOCK_SMTP_PORT=587
    RESTOCK_SMTP_USER=you@example.com
    RESTOCK_SMTP_PASSWORD=<app password>
    RESTOCK_EMAIL_TO=you@example.com
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send(config: dict, alert) -> None:
    host = os.environ.get("RESTOCK_SMTP_HOST") or config.get("host")
    port = int(os.environ.get("RESTOCK_SMTP_PORT") or config.get("port", 587))
    user = os.environ.get("RESTOCK_SMTP_USER")
    password = os.environ.get("RESTOCK_SMTP_PASSWORD")
    recipient = os.environ.get("RESTOCK_EMAIL_TO") or config.get("to")
    sender = os.environ.get("RESTOCK_EMAIL_FROM") or user

    if not host or not recipient or not sender:
        raise RuntimeError(
            "email channel needs $RESTOCK_SMTP_HOST, $RESTOCK_SMTP_USER and $RESTOCK_EMAIL_TO"
        )

    message = EmailMessage()
    message["Subject"] = alert.title
    message["From"] = sender
    message["To"] = recipient
    message.set_content(alert.body)

    timeout = int(config.get("timeout", 20))
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
            if user and password:
                server.login(user, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(message)
