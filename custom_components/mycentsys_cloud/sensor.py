"""Sensors: the app's status tiles for SMART operators, ULTRA readings and inputs, share limits.

Everything an operator shows sits with the sensors; nothing of it is filed under
diagnostics. An ULTRA device keeps two diagnostics (supply voltage and connection);
its firmware, number, antenna and connection type are on the device page instead.
Its balance is two token counts on a token SIM and the network's own answer on
any other SIM, never both.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .cloud.models import UltraChannel
from .coordinator import AccountHub, CloudConfigEntry
from .entity import ChannelEntity, GrantEntity, ModuleEntity, OperatorEntity, Roster
from .fleet import Fleet, ModuleRecord, OperatorRecord
from .telemetry import (
    HEALTH_OPTIONS,
    POSITION_OPTIONS,
    POWER_OPTIONS,
    TAMPER_OPTIONS,
    battery_icon,
    cloud_tamper_label,
    connection_icon,
    health_label,
    position_icon,
    power_icon,
    power_label,
    problems,
    tamper_icon,
    tamper_label,
    wifi_icon,
)

_DIAG = EntityCategory.DIAGNOSTIC
_MEASURED = SensorStateClass.MEASUREMENT


@dataclass(frozen=True, kw_only=True)
class OperatorReading(SensorEntityDescription):
    """One value about a SMART operator, read from its fleet record (and the hub for positions)."""

    read: Callable[[OperatorRecord, AccountHub], Any]
    # An icon that follows the operator's state and this reading's value, in place of a fixed one.
    icon_of: Callable[[OperatorRecord, Any], str | None] | None = None


@dataclass(frozen=True, kw_only=True)
class ModuleReading(SensorEntityDescription):
    """One value from an ULTRA module's own status report."""

    read: Callable[[ModuleRecord], Any]
    # An icon that follows the value, in place of a fixed one.
    icon_of: Callable[[Any], str] | None = None
    # A balance, which also says when the module last reported it.
    dated: bool = False


def _operator(key: str, read: Callable[[OperatorRecord, AccountHub], Any], **traits: Any) -> OperatorReading:
    return OperatorReading(key=key, translation_key=traits.pop("translation_key", key), read=read, **traits)


def _module(key: str, member: str, **traits: Any) -> ModuleReading:
    return ModuleReading(key=key, translation_key=key, read=lambda record: getattr(record.diagnostics, member, None), **traits)


def _mains(record: OperatorRecord, hub: AccountHub) -> Any:
    label = power_label(record.broadcast)
    if label is None and record.snapshot is not None:
        label = record.snapshot.supply.label
    return label


def _tamper(record: OperatorRecord, hub: AccountHub) -> Any:
    label = tamper_label(record.broadcast)
    return label if label is not None else cloud_tamper_label(record.snapshot)


def _last_contact(record: OperatorRecord, hub: AccountHub) -> Any:
    stamp = record.operator.link.last_contact
    return dt_util.parse_datetime(stamp) if stamp else None


def _enum(options: tuple[str, ...], **traits: Any) -> dict[str, Any]:
    return dict(device_class=SensorDeviceClass.ENUM, options=list(options), **traits)


def _measured(device_class: SensorDeviceClass, unit: str, **traits: Any) -> dict[str, Any]:
    return dict(device_class=device_class, native_unit_of_measurement=unit, state_class=_MEASURED, **traits)


# Bars for a signal level of 0 to 5; the top two levels both show full bars.
_SIGNAL_BARS = ("mdi:network-strength-outline", "mdi:network-strength-1", "mdi:network-strength-2", "mdi:network-strength-3", "mdi:network-strength-4")


def signal_bars(level: Any) -> str:
    """The bars icon for a module's signal level."""
    if not isinstance(level, int) or level <= 0:
        return _SIGNAL_BARS[0]
    return _SIGNAL_BARS[min(level, len(_SIGNAL_BARS) - 1)]


def link_icon(status: Any) -> str:
    """The connection icon of a module: the globe while its connection is active, struck through otherwise."""
    return connection_icon(isinstance(status, str) and status.strip().lower() == "active")


OPERATOR_READINGS: tuple[OperatorReading, ...] = (
    _operator(
        "position",
        lambda record, hub: hub.position_of(record.key) or "unknown",
        icon_of=lambda record, value: position_icon(value, gate=record.is_gate, garage=record.is_garage),
        **_enum(POSITION_OPTIONS),
    ),
    _operator("mains", _mains, icon_of=lambda record, value: power_icon(value), **_enum(POWER_OPTIONS)),
    _operator("health", lambda record, hub: health_label(record.broadcast), icon="mdi:heart-pulse", **_enum(HEALTH_OPTIONS)),
    _operator("tamper", _tamper, icon_of=lambda record, value: tamper_icon(value), **_enum(TAMPER_OPTIONS)),
    _operator(
        "battery",
        lambda record, hub: record.broadcast.battery_volts if record.broadcast else None,
        icon_of=lambda record, value: battery_icon(record.broadcast),
        **_measured(SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, suggested_display_precision=1),
    ),
    _operator("last_contact", _last_contact, device_class=SensorDeviceClass.TIMESTAMP),
    _operator(
        "wifi_signal",
        lambda record, hub: record.operator.link.rssi,
        translation_key=None,
        icon_of=lambda record, value: wifi_icon(value),
        **_measured(SensorDeviceClass.SIGNAL_STRENGTH, SIGNAL_STRENGTH_DECIBELS_MILLIWATT),
    ),
)

MODULE_READINGS: tuple[ModuleReading, ...] = (
    _module("ultra_signal", "signal", state_class=_MEASURED, icon_of=signal_bars),
    _module("ultra_supply", "supply_volts", **_measured(SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, entity_category=_DIAG, suggested_display_precision=2)),
    _module("ultra_link", "link_status", entity_category=_DIAG, icon_of=link_icon),
)
# A module's balance, by its SIM: tokens for calls and SMSs, or what the network answered.
TOKEN_READINGS: tuple[ModuleReading, ...] = (
    _module("ultra_call_credits", "call_credits", icon="mdi:phone-outline", state_class=_MEASURED, dated=True),
    _module("ultra_sms_credits", "sms_credits", icon="mdi:message-outline", state_class=_MEASURED, dated=True),
)
NETWORK_BALANCE_READINGS: tuple[ModuleReading, ...] = (_module("ultra_balance", "network_balance", icon="mdi:cash", dated=True),)


def balance_readings(record: ModuleRecord) -> tuple[tuple[ModuleReading, ...], tuple[ModuleReading, ...]]:
    """(the balance readings a module shows, those it must not show); both empty while its SIM is not known."""
    if record.token_sim is None:
        return (), ()
    return (TOKEN_READINGS, NETWORK_BALANCE_READINGS) if record.token_sim else (NETWORK_BALANCE_READINGS, TOKEN_READINGS)


async def async_setup_entry(hass: HomeAssistant, entry: CloudConfigEntry, add_entities: AddEntitiesCallback) -> None:
    hub: AccountHub = entry.runtime_data

    def build(fleet: Fleet) -> list[SensorEntity]:
        found: list[SensorEntity] = []
        for serial in fleet.operators:
            found += [OperatorSensor(hub, serial, reading) for reading in OPERATOR_READINGS]
        for key, record in fleet.modules.items():
            found += [ModuleDiagnosticSensor(hub, key, reading) for reading in (*MODULE_READINGS, *balance_readings(record)[0])]
            # Every status input (the position input is the cover) shows its configured state names.
            found += [InputStateSensor(hub, key, channel) for channel in record.module.status_inputs]
        for key, record in fleet.grants.items():
            if record.grant.trigger_limit:
                found.append(TriggersLeftSensor(hub, key))
            if record.grant.ends_utc:
                found.append(GrantExpirySensor(hub, key))
        return found

    def retired(fleet: Fleet) -> list[str]:
        return [f"{key}_{reading.key}" for key, record in fleet.modules.items() for reading in balance_readings(record)[1]]

    Roster(hub, add_entities, build, domain="sensor", retired=retired).keep_level(entry)


class OperatorSensor(OperatorEntity, SensorEntity):
    """One of the app's status tiles, or a diagnostic, of a SMART operator."""

    entity_description: OperatorReading

    def __init__(self, hub: AccountHub, serial: str, reading: OperatorReading) -> None:
        super().__init__(hub, serial)
        self.entity_description = reading
        self._attr_unique_id = f"{serial}_{reading.key}"

    @property
    def native_value(self) -> Any:
        record = self.record
        return self.entity_description.read(record, self.hub) if record else None

    @property
    def icon(self) -> str | None:
        record, icon_of = self.record, self.entity_description.icon_of
        if icon_of is None or record is None:
            return self.entity_description.icon
        return icon_of(record, self.native_value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        record = self.record
        if self.entity_description.key != "health" or record is None or record.broadcast is None:
            return None
        return {"conditions": problems(record.broadcast), "notices": list(record.broadcast.notices)}


class ModuleDiagnosticSensor(ModuleEntity, SensorEntity):
    """A value from an ULTRA module's status report, which is only read while one of these is enabled."""

    entity_description: ModuleReading

    def __init__(self, hub: AccountHub, key: str, reading: ModuleReading) -> None:
        super().__init__(hub, key)
        self.entity_description = reading
        self._attr_unique_id = f"{key}_{reading.key}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.hub.want_diagnostics(self.module_key)

    async def async_will_remove_from_hass(self) -> None:
        self.hub.drop_diagnostics(self.module_key)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> Any:
        record = self.record
        return self.entity_description.read(record) if record else None

    @property
    def icon(self) -> str | None:
        icon_of = self.entity_description.icon_of
        return icon_of(self.native_value) if icon_of else self.entity_description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        record = self.record
        if not self.entity_description.dated or record is None or record.diagnostics is None:
            return None
        reported = record.diagnostics.airtime_at
        return {"reported": reported} if reported else None


class InputStateSensor(ChannelEntity, SensorEntity):
    """A status input of an ULTRA module, shown with the installer's rising and falling names."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, hub: AccountHub, key: str, channel: UltraChannel) -> None:
        super().__init__(hub, key, channel)
        self._attr_unique_id = f"{key}_input_state_{channel.number}"
        self._attr_options = sorted({channel.on_label, channel.off_label})

    @property
    def options(self) -> list[str] | None:
        channel = self.channel
        return sorted({channel.on_label, channel.off_label}) if channel else self._attr_options

    @property
    def native_value(self) -> str | None:
        channel = self.channel
        return channel.state_label(self.state_id) if channel else None


class _GrantLimitSensor(GrantEntity, SensorEntity):
    """A share limit; stays available after the share ran out, so the reason is visible."""

    limit: SensorEntityDescription

    def __init__(self, hub: AccountHub, key: str) -> None:
        super().__init__(hub, key)
        self.entity_description = self.limit
        self._attr_unique_id = self.scoped_id(self.limit.key)

    @property
    def available(self) -> bool:
        return bool(self.hub.last_update_success and self.grant is not None)


class TriggersLeftSensor(_GrantLimitSensor):
    limit = SensorEntityDescription(key="uses_left", icon="mdi:counter", state_class=_MEASURED)
    _attr_name = "Uses left"

    @property
    def native_value(self) -> Any:
        grant = self.grant
        return grant.triggers_left(self.number) if grant else None


class GrantExpirySensor(_GrantLimitSensor):
    limit = SensorEntityDescription(key="valid_until", icon="mdi:calendar-clock", device_class=SensorDeviceClass.TIMESTAMP)
    _attr_name = "Access ends"

    @property
    def native_value(self) -> Any:
        grant = self.grant
        return grant.expires_at if grant else None
