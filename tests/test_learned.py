"""What is learned about an account's operators survives a restart, in Home Assistant's storage."""

from __future__ import annotations

import asyncio
import types
from typing import Any

from ha_stub import load_integration

load_integration()

from homeassistant.core import HomeAssistant  # noqa: E402

from custom_components.mycentsys_cloud import async_remove_entry, coordinator as hub_module  # noqa: E402
from custom_components.mycentsys_cloud.const import DOMAIN, GRANTS_POLL_SECONDS  # noqa: E402
from custom_components.mycentsys_cloud.learned import OperatorCodes  # noqa: E402

ENTRY = "entry-1"
KEY = f"{DOMAIN}.{ENTRY}"


def _documents(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault("storage", {})


def test_codes_are_written_to_storage_and_read_back() -> None:
    hass = HomeAssistant()
    codes = OperatorCodes(hass, ENTRY)
    asyncio.run(codes.async_load())
    assert codes.get("S1") is None and KEY not in _documents(hass)  # nothing to write yet
    assert codes.note("S1", 41) is True
    assert codes.note("S1", 41) is False and codes.note("S2", None) is False  # no news
    assert codes.note("S1", 39) is True  # the operator was replaced by another product
    assert _documents(hass)[KEY] == {"operator_codes": {"S1": 39}}

    again = OperatorCodes(hass, ENTRY)  # after a restart
    asyncio.run(again.async_load())
    assert again.get("S1") == 39 and again.get("S2") is None
    # Another entry keeps its own.
    other = OperatorCodes(hass, "entry-2")
    asyncio.run(other.async_load())
    assert other.get("S1") is None


def test_codes_an_earlier_build_left_in_the_entry_are_taken_over() -> None:
    hass = HomeAssistant()
    codes = OperatorCodes(hass, ENTRY)
    asyncio.run(codes.async_load({"S1": 41, "S2": 25}))
    assert (codes.get("S1"), codes.get("S2")) == (41, 25)
    assert _documents(hass)[KEY] == {"operator_codes": {"S1": 41, "S2": 25}}
    # What the storage already knows wins over the older copy in the entry.
    codes.note("S1", 39)
    later = OperatorCodes(hass, ENTRY)
    asyncio.run(later.async_load({"S1": 41}))
    assert later.get("S1") == 39 and later.get("S2") == 25


def test_unreadable_contents_are_ignored() -> None:
    hass = HomeAssistant()
    _documents(hass)[KEY] = {"operator_codes": {"S1": "41", "S2": True, "S3": 25, 7: 41}}
    codes = OperatorCodes(hass, ENTRY)
    asyncio.run(codes.async_load(["not", "a", "mapping"]))
    assert (codes.get("S1"), codes.get("S2"), codes.get("S3"), codes.get("7")) == (None, None, 25, 41)
    for broken in ("text", {"operator_codes": 5}, None):
        _documents(hass)[KEY] = broken
        asyncio.run(codes.async_load())
        assert codes.get("S3") is None


def test_removing_the_entry_removes_its_stored_codes() -> None:
    hass = HomeAssistant()
    codes = OperatorCodes(hass, ENTRY)
    asyncio.run(codes.async_load())
    codes.note("S1", 41)
    assert KEY in _documents(hass)
    asyncio.run(async_remove_entry(hass, types.SimpleNamespace(entry_id=ENTRY)))
    assert KEY not in _documents(hass)


class _Entries:
    """The config entries registry, as far as the hub writes to it."""

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def async_update_entry(self, entry: Any, *, data: dict[str, Any]) -> None:
        entry.data = data
        self.writes.append(data)


def _hub(entry_data: dict[str, Any]) -> tuple[Any, Any, _Entries]:
    hass = HomeAssistant()
    hass.config_entries = _Entries()
    entry = types.SimpleNamespace(entry_id=ENTRY, data=entry_data)
    hub = types.SimpleNamespace(hass=hass, entry=entry, codes=OperatorCodes(hass, ENTRY), data=None)
    return hub, hass, hass.config_entries


def test_the_hub_moves_the_codes_out_of_the_entry_once() -> None:
    hub, hass, entries = _hub({"account_number": "+27820000000", "operator_codes": {"S1": 41}})
    asyncio.run(hub_module.AccountHub.async_restore(hub))
    assert hub.codes.get("S1") == 41
    assert hub.entry.data == {"account_number": "+27820000000"} and len(entries.writes) == 1
    assert _documents(hass)[KEY] == {"operator_codes": {"S1": 41}}
    # The next start finds nothing to move and leaves the entry alone.
    asyncio.run(hub_module.AccountHub.async_restore(hub))
    assert len(entries.writes) == 1 and hub.codes.get("S1") == 41


def test_a_newly_announced_code_never_touches_the_entry() -> None:
    hub, hass, entries = _hub({"account_number": "+27820000000"})
    asyncio.run(hub_module.AccountHub.async_restore(hub))
    hub_module.AccountHub._note_announced_code(hub, "S1", 41)
    hub_module.AccountHub._note_announced_code(hub, "S1", 41)
    hub_module.AccountHub._note_announced_code(hub, "S2", None)
    assert hub_module.AccountHub.reported_code(hub, "S1") == 41
    assert entries.writes == []  # writing to the entry would reload the integration
    assert _documents(hass)[KEY] == {"operator_codes": {"S1": 41}}
    assert hass.data["storage_writes"] == [KEY]


def test_using_a_share_reads_the_shares_again_at_once() -> None:
    asked: list[str] = []

    async def refresh() -> None:
        asked.append("refresh")

    hass = types.SimpleNamespace(async_create_task=lambda job: asyncio.run(job))
    hub = types.SimpleNamespace(hass=hass, _grants=hub_module._Stamped(["share"], hub_module.time.monotonic()), async_request_refresh=refresh)
    assert GRANTS_POLL_SECONDS == 250 and not hub._grants.older_than(GRANTS_POLL_SECONDS)
    hub_module.AccountHub.share_was_used(hub)
    assert hub._grants.older_than(GRANTS_POLL_SECONDS) and asked == ["refresh"]
    assert hub._grants.value == ["share"]  # still shown until the new list is in


def test_a_used_share_counts_as_old_on_a_machine_that_has_only_just_started(monkeypatch) -> None:
    # The monotonic clock starts near zero at boot; "old" must not depend on how long the machine has run.
    monkeypatch.setattr(hub_module.time, "monotonic", lambda: 12.0)
    shares = hub_module._Stamped(["share"], 11.0)
    assert not shares.older_than(GRANTS_POLL_SECONDS)
    shares.expire()
    assert shares.older_than(GRANTS_POLL_SECONDS) and shares.older_than(10**9)
    # The same goes for something that was never read at all.
    assert hub_module._Stamped().older_than(10**9)
