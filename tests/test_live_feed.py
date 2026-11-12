"""The live feed supervisor: open, follow, deliver, rebuild, close."""

from __future__ import annotations

import asyncio
from typing import Any

from ha_stub import load_integration

load_integration()

from custom_components.mycentsys_cloud import live_feed as feed_module  # noqa: E402
from custom_components.mycentsys_cloud.cloud.models import Operator  # noqa: E402
from custom_components.mycentsys_cloud.cloud.mqtt import SessionError  # noqa: E402
from custom_components.mycentsys_cloud.cloud.overview import DeviceAnnouncement  # noqa: E402
from custom_components.mycentsys_cloud.fleet import Fleet, OperatorRecord  # noqa: E402


class StubFeed:
    """What the broker hands back: a connected subscription that can be told to watch more."""

    def __init__(self) -> None:
        self.connected = True
        self.watching: list[str] = []
        self.closed = False

    def watch(self, serial: str) -> None:
        self.watching.append(serial)

    def stop(self) -> None:
        self.closed = True
        self.connected = False


class StubBroker:
    def __init__(self) -> None:
        self.opened: list[tuple[list[str], dict[str, str]]] = []
        self.feeds: list[StubFeed] = []
        self.on_broadcast: Any = None
        self.bundles_forgotten = 0
        self.refuse_next = False

    async def async_open_feed(self, serials, families, on_broadcast):
        self.opened.append((list(serials), dict(families)))
        if self.refuse_next:
            self.refuse_next = False
            raise SessionError("refused")
        self.on_broadcast = on_broadcast
        feed = StubFeed()
        self.feeds.append(feed)
        return feed

    async def async_close_feed(self, feed) -> None:
        feed.stop()

    def forget_bundle(self) -> None:
        self.bundles_forgotten += 1


class StubHub:
    def __init__(self, serials: tuple[str, ...] = ("S1", "S2"), *, classified: dict[str, int] | None = None) -> None:
        self.hass = type("Hass", (), {"loop": asyncio.get_event_loop()})()
        self.broker = StubBroker()
        self.data = Fleet(
            operators={
                serial: OperatorRecord(Operator.from_listing({"serialNumber": serial}), reported_code=(classified or {}).get(serial))
                for serial in serials
            }
        )
        self.delivered: list[tuple[str, Any]] = []
        self.refreshes = 0
        self.jobs: list[asyncio.Task] = []

    def spawn(self, job, *, name: str) -> None:
        self.jobs.append(asyncio.get_event_loop().create_task(job, name=name))

    def ingest_from_thread(self, announcement, overview) -> None:
        self.delivered.append((announcement.serial_number, overview))

    def async_update_listeners(self) -> None:
        self.refreshes += 1

    def cancel_jobs(self) -> None:
        for job in self.jobs:
            job.cancel()


def _hurry(monkeypatch) -> None:
    monkeypatch.setattr(feed_module, "FEED_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(feed_module, "FEED_BACKOFF_FLOOR", 0.01)
    monkeypatch.setattr(feed_module, "FEED_BACKOFF_CEILING", 0.05)
    monkeypatch.setattr(feed_module, "FEED_STALE_SECONDS", 0.05)
    monkeypatch.setattr(feed_module, "FEED_HEALTHY_SECONDS", 1000.0)


async def _eventually(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


def test_feed_follows_the_fleet_operators_and_delivers_broadcasts(monkeypatch) -> None:
    _hurry(monkeypatch)

    async def scenario() -> None:
        hub = StubHub(classified={"S1": 41})
        feed = feed_module.LiveFeed(hub)
        feed.start()
        await _eventually(lambda: feed.active)
        serials, families = hub.broker.opened[0]
        assert serials == ["S1", "S2"]
        assert families == {"S1": "slider"}
        hub.broker.on_broadcast(DeviceAnnouncement("S1", 41, "4.1.0.0", "Gate", "02:1A:7C:3E:90:5B"), None)
        assert hub.delivered == [("S1", None)]
        feed.follow(["S2", "S3"])
        assert hub.broker.feeds[0].watching == ["S3"]
        await feed.async_stop()
        assert not feed.active
        assert hub.broker.feeds[0].closed
        hub.cancel_jobs()

    asyncio.run(scenario())


def test_feed_is_rebuilt_after_a_refusal_and_after_a_stale_drop(monkeypatch) -> None:
    _hurry(monkeypatch)

    async def scenario() -> None:
        hub = StubHub()
        hub.broker.refuse_next = True
        feed = feed_module.LiveFeed(hub)
        feed.start()
        await _eventually(lambda: feed.active)
        assert hub.broker.bundles_forgotten == 1  # the refusal fetched a fresh client bundle
        assert len(hub.broker.opened) == 2
        first = hub.broker.feeds[0]
        first.connected = False
        await _eventually(lambda: not feed.active)
        await _eventually(lambda: len(hub.broker.feeds) == 2)
        assert first.closed
        assert hub.broker.bundles_forgotten == 2
        await _eventually(lambda: feed.active)
        await feed.async_stop()
        hub.cancel_jobs()

    asyncio.run(scenario())


def test_feed_waits_until_there_is_an_operator_to_follow(monkeypatch) -> None:
    _hurry(monkeypatch)

    async def scenario() -> None:
        hub = StubHub(serials=())
        feed = feed_module.LiveFeed(hub)
        feed.start()
        await asyncio.sleep(0.05)
        assert hub.broker.opened == []
        assert not feed.active
        await feed.async_stop()
        hub.cancel_jobs()

    asyncio.run(scenario())


def test_the_pause_grows_after_short_connections_and_starts_over_after_one_that_lasted() -> None:
    # The lengths as shipped; the middle of the spread is the length itself.
    backoff = feed_module._Backoff(between=lambda low, high: (low + high) / 2)
    assert [backoff.after(1.0) for _ in range(6)] == [4, 12, 36, 108, 240, 240]
    assert backoff.after(44.0) == 240  # not long enough to count as lasting
    assert backoff.after(45.0) == 4
    assert backoff.after(1.0) == 12


def test_each_pause_is_spread_around_its_length() -> None:
    asked: list[tuple[float, float]] = []

    def lowest(low: float, high: float) -> float:
        asked.append((low, high))
        return low

    backoff = feed_module._Backoff(between=lowest)
    assert [backoff.after(1.0) for _ in range(3)] == [3.0, 9.0, 27.0]
    assert asked == [(3.0, 5.0), (9.0, 15.0), (27.0, 45.0)]
    # Left to itself it draws at random, and never outside the spread.
    drawn = [feed_module._Backoff().after(1.0) for _ in range(200)]
    assert all(3.0 <= pause <= 5.0 for pause in drawn) and len(set(drawn)) > 100
