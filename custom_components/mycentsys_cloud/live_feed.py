"""The live feed: one long-lived subscription to the operators' status broadcasts.

``cloud.mqtt.OverviewListener`` does the subscribing, on this integration's own
feed identity, and never publishes, so the MyCentsys Remote app is never
disturbed. :class:`LiveFeed` owns the policy around it:

* it waits until the fleet has an operator to follow;
* it reopens the feed after a pause when the broker refuses it, fetching a
  fresh client bundle first; the pause grows while connections do not last and
  is spread at random, so many installs do not return to the broker at once;
* it watches the connection and rebuilds it once it has been down for too
  long, again with a fresh bundle;
* it hands every broadcast to the hub on the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import random
from typing import TYPE_CHECKING, Any

from .cloud.mqtt import SessionError
from .cloud.overview import DeviceAnnouncement, Overview
from .const import (
    FEED_BACKOFF_CEILING,
    FEED_BACKOFF_FLOOR,
    FEED_BACKOFF_GROWTH,
    FEED_BACKOFF_SPREAD,
    FEED_CHECK_SECONDS,
    FEED_HEALTHY_SECONDS,
    FEED_STALE_SECONDS,
)

if TYPE_CHECKING:
    from .coordinator import AccountHub

_LOGGER = logging.getLogger(__name__)


class _Backoff:
    """The pause before the feed is opened again.

    Its length grows after every connection that did not last and starts over
    after one that did. The pause actually taken is drawn at random around
    that length (``between`` picks it; a test passes its own).
    """

    def __init__(self, between: Callable[[float, float], float] = random.uniform) -> None:
        self.length = float(FEED_BACKOFF_FLOOR)
        self._between = between

    def after(self, uptime: float) -> float:
        if uptime >= FEED_HEALTHY_SECONDS:
            self.length = float(FEED_BACKOFF_FLOOR)
        length = self.length
        self.length = min(length * FEED_BACKOFF_GROWTH, float(FEED_BACKOFF_CEILING))
        return self._between(length * (1 - FEED_BACKOFF_SPREAD), length * (1 + FEED_BACKOFF_SPREAD))


class LiveFeed:
    """Keeps the broadcast feed open for the account's SMART operators."""

    def __init__(self, hub: "AccountHub") -> None:
        self._hub = hub
        self._closing = False
        self._task_started = False
        self._feed: Any = None
        self._following: set[str] = set()
        self._active = False

    @property
    def active(self) -> bool:
        """Whether broadcasts are flowing right now."""
        return self._active

    def start(self) -> None:
        if self._task_started:
            return
        self._task_started = True
        self._closing = False
        self._hub.spawn(self._keep_open(), name="live_feed")

    async def async_stop(self) -> None:
        self._closing = True
        self._task_started = False
        self._mark(False)
        feed, self._feed = self._feed, None
        if feed is not None:
            await self._hub.broker.async_close_feed(feed)

    def follow(self, serials: list[str]) -> None:
        """Add operators that appeared after the feed was opened."""
        fresh = [serial for serial in serials if serial not in self._following]
        self._following.update(fresh)
        if self._feed is not None:
            for serial in fresh:
                self._feed.watch(serial)

    def _operators_to_follow(self) -> tuple[list[str], dict[str, str]]:
        fleet = self._hub.data
        if fleet is None:
            return [], {}
        families = {serial: record.family for serial, record in fleet.operators.items() if record.family}
        return list(fleet.operators), families

    def _on_broadcast(self, announcement: DeviceAnnouncement, overview: Overview | None) -> None:
        # Network thread.
        self._hub.ingest_from_thread(announcement, overview)

    def _mark(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._hub.async_update_listeners()

    async def _open(self, serials: list[str], families: dict[str, str]) -> Any:
        """One attempt to open the feed; None when it could not be opened."""
        try:
            return await self._hub.broker.async_open_feed(serials, families, self._on_broadcast)
        except SessionError as err:
            _LOGGER.debug("Live feed refused: %s", err)
            self._hub.broker.forget_bundle()
        except Exception as err:  # noqa: BLE001 - the feed is best-effort
            _LOGGER.debug("Live feed could not be opened: %s", err.__class__.__name__)
        return None

    async def _keep_open(self) -> None:
        backoff = _Backoff()
        while not self._closing:
            serials, families = self._operators_to_follow()
            if not serials:
                await asyncio.sleep(FEED_BACKOFF_FLOOR)
                continue
            opened_at = self._hub.hass.loop.time()
            feed = await self._open(serials, families)
            if feed is not None:
                self._feed = feed
                self._following = set(serials)
                self._mark(True)
                await self._watch(feed)
                self._feed = None
                self._mark(False)
                await self._hub.broker.async_close_feed(feed)
            if self._closing:
                return
            await asyncio.sleep(backoff.after(self._hub.hass.loop.time() - opened_at))

    async def _watch(self, feed: Any) -> None:
        """Return once the connection has been down for too long, or the feed is being closed."""
        down_since: float | None = None
        while not self._closing:
            await asyncio.sleep(FEED_CHECK_SECONDS)
            if feed.connected:
                down_since = None
                self._mark(True)
                continue
            now = self._hub.hass.loop.time()
            down_since = now if down_since is None else down_since
            self._mark(False)
            if now - down_since >= FEED_STALE_SECONDS:
                _LOGGER.debug("Live feed stayed disconnected; rebuilding it")
                self._hub.broker.forget_bundle()
                return
