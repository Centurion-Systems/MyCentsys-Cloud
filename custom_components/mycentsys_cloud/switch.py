"""Switches: standing modes and the tamper alarm of a SMART gate, latching outputs of an ULTRA module."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud import actions
from .cloud.models import ChannelRole, UltraChannel
from .cloud.overview import Overview
from .const import BROADCAST_CONFIRM_SECONDS
from .coordinator import AccountHub, CloudConfigEntry
from .entity import ChannelEntity, OperatorEntity, Roster
from .fleet import Fleet
from .telemetry import tamper_armed


class StandingMode(NamedTuple):
    """A mode the operator reports in its broadcast and toggles on one activation."""

    key: str
    icon: str
    activation_id: int
    is_set: Callable[[Overview], bool]


# Offered to gates only. On a garage door the same activation numbers do something else entirely.
GATE_MODES = (
    StandingMode("holiday_lock", "mdi:lock-clock", actions.GATE_HOLIDAY_LOCK, lambda broadcast: broadcast.holiday_lock),
    StandingMode("keep_open", "mdi:gate-open", actions.GATE_KEEP_OPEN, lambda broadcast: broadcast.keep_open),
)


async def async_setup_entry(hass: HomeAssistant, entry: CloudConfigEntry, add_entities: AddEntitiesCallback) -> None:
    hub: AccountHub = entry.runtime_data

    def build(fleet: Fleet) -> list[SwitchEntity]:
        switches: list[SwitchEntity] = []
        for serial, record in fleet.operators.items():
            if record.is_gate:
                switches += [StandingModeSwitch(hub, serial, mode) for mode in GATE_MODES]
            # The tamper alarm exists on sliders and garage doors, not on swings.
            if actions.tamper_arm_id(record.family or "") is not None:
                switches.append(TamperAlarmSwitch(hub, serial))
        for key, module_record in fleet.modules.items():
            module = module_record.module
            trigger = module.trigger
            switches += [
                OutputToggleSwitch(hub, key, channel)
                for channel in module.outputs
                if channel.role == ChannelRole.LATCHING and channel is not trigger
            ]
        return switches

    Roster(hub, add_entities, build).keep_level(entry)


class _StateSwitch(SwitchEntity):
    """A switch that only sends when the requested state differs from the reported one."""

    async def _apply(self, on: bool) -> None:
        raise NotImplementedError

    async def _want(self, on: bool) -> None:
        if self.is_on is not on:
            await self._apply(on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._want(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._want(False)


class _ToggleOnce(_StateSwitch):
    """On and off send the same command (the device toggles), so either direction is one send."""

    async def _send_toggle(self) -> None:
        raise NotImplementedError

    async def _apply(self, on: bool) -> None:
        await self._send_toggle()


_NOT_APPLIED = (
    "The gate accepted the command but did not apply it. This can happen when the "
    "operator needs attention (for example if it has lost its origin and needs a re-learn)."
)


class StandingModeSwitch(OperatorEntity, _ToggleOnce):
    """Holiday Lock or Keep Open on a SMART gate, read from the operator's own broadcast."""

    def __init__(self, hub: AccountHub, serial: str, mode: StandingMode) -> None:
        super().__init__(hub, serial)
        self._mode = mode
        self._attr_translation_key = mode.key
        self._attr_icon = mode.icon
        self._attr_unique_id = f"{serial}_{mode.key}"

    @property
    def is_on(self) -> bool | None:
        record = self.record
        return self._mode.is_set(record.broadcast) if record and record.broadcast else None

    async def _send_toggle(self) -> None:
        record = self.record
        if record is None or not record.is_gate:
            raise HomeAssistantError("This control is only available on gate operators, once the gate has reported its type.")
        was_on = self.is_on
        await self.hub.async_send_operator(self.serial, self._mode.activation_id, what=str(self.name))
        # An accepted activation is no proof the mode changed (an operator without
        # its limits takes the command and ignores it), so wait for its own report.
        if not self.hub.feed_connected:
            return
        confirmation = await self.hub.wait_for_broadcast(self.serial, BROADCAST_CONFIRM_SECONDS)
        if confirmation is not None and was_on is not None and self._mode.is_set(confirmation) == was_on:
            raise HomeAssistantError(_NOT_APPLIED)


class TamperAlarmSwitch(OperatorEntity, _StateSwitch):
    """Arm or disarm the operator's tamper alarm (the app's Arm / Off). On means armed."""

    _attr_translation_key = "tamper_alarm"
    _attr_icon = "mdi:shield-home"

    def __init__(self, hub: AccountHub, serial: str) -> None:
        super().__init__(hub, serial)
        self._attr_unique_id = f"{serial}_tamper_alarm"

    @property
    def is_on(self) -> bool | None:
        record = self.record
        return tamper_armed(record.broadcast) if record else None

    async def _apply(self, on: bool) -> None:
        record = self.record
        family = record.family if record else None
        choose = actions.tamper_arm_id if on else actions.tamper_disarm_id
        activation_id = choose(family or "")
        if not activation_id:
            raise HomeAssistantError("This operator has no tamper alarm control.")
        await self.hub.async_send_operator(self.serial, activation_id, what="Tamper alarm")


class OutputToggleSwitch(ChannelEntity, _ToggleOnce):
    """A two-state output of an ULTRA module, named as configured; ``state_name`` carries the configured state."""

    def __init__(self, hub: AccountHub, key: str, channel: UltraChannel) -> None:
        super().__init__(hub, key, channel)
        self._attr_unique_id = f"{key}_latch_{channel.number}"

    @property
    def is_on(self) -> bool | None:
        return self.is_on_now

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        channel = self.channel
        return {**super().extra_state_attributes, "state_name": channel.state_label(self.state_id) if channel else None}

    async def _send_toggle(self) -> None:
        await self.hub.async_pulse_module(self.module_key, self._number, what=f"Switch {self._attr_name}")
