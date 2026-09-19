"""Per-watch scheduling, staggering and backoff.

The reason this module exists: one global ``interval_seconds`` forces the
cheapest check and the most expensive one onto the same cadence. A
first-party JSON-LD page is one small GET that a 304 usually makes free; a
tracker page aggregating four retailers is neither cheap nor as fresh. Making
the first wait for the second is what people "fix" by lowering the global
interval, which is exactly how the expensive check gets rate-limited.

So each watch keeps its own clock:

* ``interval_seconds`` per watch, falling back to the global one.
* ``offset_seconds`` to stagger watches that would otherwise fire together.
  Staggering two *different* endpoints halves how long you wait to hear the
  news while leaving each endpoint's own rate untouched. Staggering two
  watches pointed at the *same* URL does not — that is simply polling it
  twice as often, and the remote server experiences it that way.
* Automatic backoff when a server pushes back, obeying its ``Retry-After``
  when it sends one. Getting rate-limited and carrying on at full speed is
  how a soft throttle becomes a hard ban.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List

from .http import RateLimited

LOG = logging.getLogger("restock-watch.schedule")

#: Ceiling on automatic backoff. Past this the watch is effectively parked,
#: and a human should look at why.
MAX_BACKOFF_SECONDS = 3600


class Scheduler:
    """Decides which watches are due, and when to wake up next."""

    def __init__(
        self,
        watches: List[dict],
        default_interval: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._clock = clock
        self._watches = watches
        self._interval: Dict[int, float] = {}
        self._next_due: Dict[int, float] = {}
        self._strikes: Dict[int, int] = {}

        now = clock()
        for index, watch in enumerate(watches):
            interval = float(watch.get("interval_seconds") or default_interval)
            offset = float(watch.get("offset_seconds") or 0)
            self._interval[index] = interval
            self._next_due[index] = now + offset
            self._strikes[index] = 0

    def due(self) -> List[tuple[int, dict]]:
        """The watches whose turn it is, as (index, watch) pairs."""
        now = self._clock()
        return [
            (index, self._watches[index])
            for index in range(len(self._watches))
            if self._next_due[index] <= now
        ]

    def record_success(self, index: int) -> None:
        """A clean run: clear any backoff and schedule the next check."""
        if self._strikes[index]:
            LOG.info(
                "watch %s recovered; back to its normal %.0fs interval",
                self._label(index),
                self._interval[index],
            )
        self._strikes[index] = 0
        self._next_due[index] = self._clock() + self._interval[index]

    def record_rate_limited(self, index: int, retry_after: float | None = None) -> None:
        """The server pushed back. Wait longer — its number wins if it gave one."""
        self._strikes[index] += 1
        backoff = min(
            self._interval[index] * (2 ** self._strikes[index]), MAX_BACKOFF_SECONDS
        )
        if retry_after is not None:
            backoff = min(max(backoff, retry_after), MAX_BACKOFF_SECONDS)
        self._next_due[index] = self._clock() + backoff
        LOG.warning(
            "watch %s was rate-limited (strike %d); backing off %.0fs",
            self._label(index),
            self._strikes[index],
            backoff,
        )

    def sleep_seconds(self, cap: float = 60.0) -> float:
        """How long to sleep before anything is due again.

        Capped so Ctrl-C stays responsive and a long backoff does not make
        the process look hung.
        """
        if not self._next_due:
            return cap
        now = self._clock()
        wait = min(self._next_due.values()) - now
        return max(0.0, min(wait, cap))

    def _label(self, index: int) -> str:
        watch = self._watches[index]
        return str(watch.get("label") or watch.get("source") or f"#{index + 1}")


def rate_limit_from(exc: BaseException) -> float | None:
    """``Retry-After`` if ``exc`` is a RateLimited, else None."""
    if isinstance(exc, RateLimited):
        return exc.retry_after
    return None
