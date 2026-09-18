"""Persistent last-seen status, kept in one JSON file.

The state file is what makes alerts fire exactly once per event instead of
every polling cycle, and it is why the watcher survives a reboot without
re-alerting on things you already know about.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.statuses: Dict[str, str] = {}
        self.meta: Dict[str, object] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
            return
        if isinstance(data, dict):
            self.statuses = dict(data.get("statuses") or {})
            self.meta = dict(data.get("meta") or {})

    def save(self) -> None:
        """Write atomically — a cron job killed mid-write must not corrupt state."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"statuses": self.statuses, "meta": self.meta}, indent=2, sort_keys=True
        )
        handle, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w") as tmp:
                tmp.write(payload)
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def get(self, target: str) -> str | None:
        return self.statuses.get(target)

    def set(self, target: str, value: str) -> None:
        self.statuses[target] = value
