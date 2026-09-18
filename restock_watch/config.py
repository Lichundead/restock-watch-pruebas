"""Config loading and validation.

TOML, parsed with the standard library on Python 3.11+, so installing this
project is 'clone it'. Nothing secret belongs in here — the config names
channels, the environment holds their credentials.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

#: Anything faster than this hammers the retailer, gets your IP blocked, and
#: makes the check less reliable rather than more. Raise it if you can.
MIN_INTERVAL_SECONDS = 60


class ConfigError(Exception):
    pass


def load(path: str | Path) -> dict:
    path = Path(path)
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(
            f"no config at {path}. Copy config.example.toml to config.toml and edit it."
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None

    validate(config)
    return config


def validate(config: dict) -> None:
    watches = config.get("watch")
    if not watches:
        raise ConfigError("config has no [[watch]] entries — nothing to check")
    if not isinstance(watches, list):
        raise ConfigError("[[watch]] must be a list of tables (note the double brackets)")

    from .sources import SOURCES

    for index, watch in enumerate(watches):
        where = watch.get("label") or f"watch #{index + 1}"
        if "source" not in watch:
            raise ConfigError(f"{where}: missing 'source'")
        if watch["source"] not in SOURCES:
            raise ConfigError(
                f"{where}: unknown source {watch['source']!r}; "
                f"available: {', '.join(sorted(SOURCES))}"
            )
        if "url" not in watch:
            raise ConfigError(f"{where}: missing 'url'")

    interval = int(config.get("general", {}).get("interval_seconds", 300))
    if interval < MIN_INTERVAL_SECONDS:
        raise ConfigError(
            f"interval_seconds is {interval}; the floor is {MIN_INTERVAL_SECONDS}. "
            "Polling a storefront faster than once a minute gets you rate-limited "
            "or blocked, and is rude. See the README."
        )

    if sys.version_info < (3, 11):
        raise ConfigError("restock-watch needs Python 3.11 or newer (for tomllib)")
