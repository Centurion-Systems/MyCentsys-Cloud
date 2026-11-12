"""Entity bases, one per kind of fleet record, and the roster that places entities.

An entity knows a fleet key and nothing else. Whenever Home Assistant asks for
its state it looks its record up in the fleet the hub last published, so the
hub is free to swap records on every poll. A unique id is the fleet key
followed by what the entity shows; a shared gate's ids also carry the
account's number.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import re
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud.models import AccessGrant, ModuleProfile, UltraChannel
from .cloud.numbers import digits
from .const import DOMAIN, MANUFACTURER
from .coordinator import AccountHub, CloudConfigEntry
from .fleet import Fleet, GrantRecord, ModuleRecord, OperatorRecord

EntityBuilder = Callable[[Fleet], Iterable[Entity]]
# The unique ids a platform must no longer have for the fleet as it is.
Retired = Callable[[Fleet], Iterable[str]]


class Roster:
    """What one platform has placed in Home Assistant, kept level with the fleet.

    A platform states its entities as a function of the fleet. The roster
    calls that function each time the hub publishes a fleet and passes on only
    the entities it has not placed before, matched on unique id. A device
    linked after setup therefore arrives without a reload, and a platform may
    leave an entity out for as long as the fleet cannot yet tell whether the
    device has that feature.

    A platform whose entities depend on something that can change (an ULTRA
    shows tokens or a network balance, by its SIM) also names the unique ids
    that no longer apply. Those are taken out of Home Assistant's registry,
    whether this run placed them or an earlier version did, and can be placed
    again later.
    """

    def __init__(
        self,
        hub: AccountHub,
        add_entities: AddEntitiesCallback,
        build: EntityBuilder,
        *,
        domain: str | None = None,
        retired: Retired | None = None,
    ) -> None:
        self._hub = hub
        self._add = add_entities
        self._build = build
        self._domain = domain
        self._retired = retired
        self._placed: set[str] = set()

    @callback
    def level(self) -> None:
        """Place whatever the current fleet calls for and is not placed yet."""
        fleet = self._hub.data
        if fleet is None:
            return
        if self._retired is not None:
            self._retire(set(self._retired(fleet)))
        missing = {entity.unique_id: entity for entity in self._build(fleet) if entity.unique_id}
        for unique_id in self._placed:
            missing.pop(unique_id, None)
        if missing:
            self._placed.update(missing)
            self._add(list(missing.values()))

    def _retire(self, unique_ids: set[str]) -> None:
        """Take entities that no longer apply out of the registry, which also removes them from the page."""
        if not unique_ids:
            return
        self._placed -= unique_ids
        hass = getattr(self._hub, "hass", None)
        registry = er.async_get(hass) if hass is not None and self._domain else None
        if registry is None:
            return
        for unique_id in unique_ids:
            entity_id = registry.async_get_entity_id(self._domain, DOMAIN, unique_id)
            if entity_id:
                registry.async_remove(entity_id)

    def keep_level(self, entry: CloudConfigEntry) -> None:
        """Level now, and after every hub update until the entry unloads."""
        self.level()
        entry.async_on_unload(self._hub.async_add_listener(self.level))


class _FleetEntity(CoordinatorEntity[AccountHub]):
    """Common ground: bound to one fleet key, named after the device."""

    _attr_has_entity_name = True

    def __init__(self, hub: AccountHub, key: str) -> None:
        super().__init__(hub)
        self._key = key

    def _registry_device(self, identifier: str, name: str, **details: Any) -> DeviceInfo:
        """The registry device every entity of this record hangs off."""
        return DeviceInfo(identifiers={(DOMAIN, identifier)}, name=name, manufacturer=MANUFACTURER, **details)

    @property
    def hub(self) -> AccountHub:
        return self.coordinator

    @property
    def fleet(self) -> Fleet:
        return self.coordinator.data or Fleet()


# --- SMART operators -----------------------------------------------------------------------------


class OperatorEntity(_FleetEntity):
    """An entity of one SMART operator; its fleet key is the serial number."""

    @property
    def record(self) -> OperatorRecord | None:
        return self.fleet.operators.get(self._key)

    @property
    def serial(self) -> str:
        return self._key

    @property
    def available(self) -> bool:
        return bool(super().available and self.record is not None)

    @property
    def device_info(self) -> DeviceInfo:
        record = self.record
        operator = record.operator if record else None
        return self._registry_device(
            self._key,
            record.label if record else self._key,
            model=operator.label if operator else None,
            serial_number=self._key,
            sw_version=record.firmware if record else None,
        )


# --- ULTRA modules -------------------------------------------------------------------------------

# An icon for an ULTRA IO, from words in the installer's name. The entity's
# name is always the configured name; only the icon is a guess.
_CHANNEL_ICONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mdi:walk", ("PED", "PEDEST")),
    ("mdi:garage", ("GRGS", "GRG", "GDO", "GARAGE", "DOOR")),
    ("mdi:lightbulb", ("LGHT", "LIGHT", "LGT", "LAMP")),
    ("mdi:lock", ("LCK", "LOCK")),
    ("mdi:fence-electric", ("FENCE", "ENERGI")),
    ("mdi:alarm-light", ("SIREN", "ALARM", "PANIC")),
    ("mdi:shield-home", ("ARM",)),
    ("mdi:water-pump", ("PUMP",)),
    ("mdi:stop", ("STOP",)),
    ("mdi:gate-open", ("HLD", "HOLD")),
    ("mdi:gate-open", ("TRG", "TRIGGER", "GATE")),
)
_ICON_OUTPUT = "mdi:gesture-tap-button"
_ICON_ARMED = "mdi:shield-home"
_ICON_DISARMED = "mdi:shield-off"
# A state or an IO that an installer named after arming: "Armed", "Disarmed", "Arm", "Arm status".
# "Alarm" is not one of them.
_NAMED_DISARMED = re.compile(r"\b(?:dis|un)arm(?:ed|ing)?\b|\bnot\s+armed\b", re.IGNORECASE)
_NAMED_ARMED = re.compile(r"\barm(?:ed|ing)?\b", re.IGNORECASE)


def armed_by_name(state_name: str | None, io_name: str | None = None, on: bool | None = None) -> bool | None:
    """Whether an IO of an alarm says the alarm is armed: True, False, or None when it says neither.

    The name of the state it is in decides ("Armed" / "Disarmed"). An IO whose
    states are named otherwise but which is itself named after arming ("Arm",
    "Arm status") is armed while it is on.
    """
    state = state_name or ""
    if _NAMED_DISARMED.search(state):
        return False
    if _NAMED_ARMED.search(state):
        return True
    name = io_name or ""
    if on is not None and _NAMED_ARMED.search(name) and not _NAMED_DISARMED.search(name):
        return on
    return None
_ICON_INPUT = "mdi:electric-switch"


def channel_icon(channel: UltraChannel) -> str:
    """The icon for a configured IO, from the words in its name."""
    for icon, words in _CHANNEL_ICONS:
        if channel.mentions(words):
            return icon
    return _ICON_INPUT if channel.is_input else _ICON_OUTPUT


def channel_name(channel: UltraChannel) -> str:
    """The entity name of a configured IO: the installer's name, or a numbered fallback."""
    return channel.label or f"{'Input' if channel.is_input else 'Output'} {channel.number}"


class ModuleEntity(_FleetEntity):
    """An entity of one ULTRA module; its fleet key is ``ultra-<id>``."""

    @property
    def record(self) -> ModuleRecord | None:
        return self.fleet.modules.get(self._key)

    @property
    def module_key(self) -> str:
        return self._key

    @property
    def available(self) -> bool:
        return bool(super().available and self.record is not None)

    def channel_is_on(self, channel: UltraChannel | None) -> bool | None:
        record = self.record
        if channel is None or record is None or record.readings is None:
            return None
        return record.readings.is_on(channel.number)

    @property
    def position_label(self) -> str | None:
        """The gate or door position: seen live, else the last reading; None without a position input."""
        live = self.hub.live_position(self.module_key)
        if live is not None:
            return live
        record = self.record
        return record.readings.position if record and record.readings else None

    @property
    def device_info(self) -> DeviceInfo:
        record = self.record
        module = record.module if record else None
        return self._registry_device(
            self._key,
            record.label if record else self._key,
            model=f"{module.model}, {module.profile_label}" if module else "G-ULTRA device",
            serial_number=module.imei if module else None,
            sw_version=record.firmware if record else None,
            hw_version=record.hardware if record else None,
        )


class ChannelEntity(ModuleEntity):
    """An entity that shows or drives a single IO, as the installer set it up."""

    def __init__(self, hub: AccountHub, key: str, channel: UltraChannel) -> None:
        super().__init__(hub, key)
        self._number = channel.number
        self._attr_name = channel_name(channel)
        self._attr_icon = channel_icon(channel)

    @property
    def channel(self) -> UltraChannel | None:
        record = self.record
        if record is None:
            return None
        return next((channel for channel in record.module.channels if channel.number == self._number), None)

    @property
    def state_id(self) -> int | None:
        record = self.record
        return record.readings.state_of(self._number) if record and record.readings else None

    @property
    def is_on_now(self) -> bool | None:
        return self.channel_is_on(self.channel)

    @property
    def icon(self) -> str | None:
        """On a module wired to an alarm, an IO that says armed or disarmed shows the shield, struck
        through while disarmed. Every other IO keeps the icon of its name."""
        record, channel = self.record, self.channel
        if record is not None and channel is not None and record.module.profile == ModuleProfile.ALARM:
            armed = armed_by_name(channel.state_label(self.state_id), channel.label, self.is_on_now)
            if armed is not None:
                return _ICON_ARMED if armed else _ICON_DISARMED
        return self._attr_icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        channel = self.channel
        if channel is None:
            return {"io_number": self._number}
        return {
            "io_number": self._number,
            "io_name": channel.label,
            "direction": "input" if channel.is_input else "output",
            "on_state_name": channel.on_label,
            "off_state_name": channel.off_label,
        }


# --- shared gates --------------------------------------------------------------------------------

# How a granted action is shown, by the stem of the app's label key
# ("PedestrianDescription" -> "Pedestrian"). Unknown stems are spaced out.
_GRANT_ACTION_STYLE: dict[str, tuple[str, str]] = {
    "OpenClose": ("Gate", "mdi:gate-open"),
    "Pedestrian": ("Pedestrian", "mdi:walk"),
    "HolidayLock": ("Holiday lock", "mdi:lock"),
    "KeepOpen": ("Keep open", "mdi:gate-open"),
    "ArmTamperAlarm": ("Arm tamper alarm", "mdi:shield"),
    "DisarmTamperAlarm": ("Disarm tamper alarm", "mdi:shield-off"),
    "ClearAlarms": ("Clear alarms", "mdi:alarm-light-off"),
    "CancelAutoClose": ("Cancel auto-close", "mdi:timer-off-outline"),
}
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def grant_action_style(label_key: str) -> tuple[str, str]:
    """(entity name, icon) for a granted action's label key."""
    stem = (label_key or "").removesuffix("Description")
    known = _GRANT_ACTION_STYLE.get(stem)
    if known is not None:
        return known
    spaced = _CAMEL_BOUNDARY.sub(" ", stem).strip()
    return (spaced or label_key or "Action"), _ICON_OUTPUT


class GrantEntity(_FleetEntity):
    """An entity of a gate shared with this account; its fleet key is ``access-<guid>``.

    Two accounts signed in to the same Home Assistant can both hold a share of
    one gate, so every id of a shared gate ends in the account's number.
    """

    @property
    def record(self) -> GrantRecord | None:
        return self.fleet.grants.get(self._key)

    @property
    def grant(self) -> AccessGrant | None:
        record = self.record
        return record.grant if record else None

    @property
    def number(self) -> str:
        return self.hub.number

    def scoped_id(self, suffix: str) -> str:
        return f"{self._key}-{digits(self.number)}_{suffix}"

    @property
    def available(self) -> bool:
        grant = self.grant
        return bool(super().available and grant is not None and grant.usable_by(self.number))

    @property
    def device_info(self) -> DeviceInfo:
        record = self.record
        return self._registry_device(f"{self._key}-{digits(self.number)}", record.label if record else self._key, model="Shared gate")
