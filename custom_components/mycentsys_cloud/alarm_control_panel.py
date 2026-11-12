"""Alarm panel: an ULTRA module wired to an alarm system.

A module whose IO names describe an alarm, with an "Arm" two-state output
(armed / disarmed state names) and optionally an alarm input, is also shown
as one alarm control panel:

* armed / disarmed follows the arm output's reported state;
* triggered follows the alarm input while it is on;
* arm and disarm pulse the arm output (the module toggles it), and skip the
  pulse when the reported state already matches.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AccountHub, CloudConfigEntry
from .entity import ModuleEntity, Roster
from .fleet import Fleet


async def async_setup_entry(hass: HomeAssistant, entry: CloudConfigEntry, add_entities: AddEntitiesCallback) -> None:
    hub: AccountHub = entry.runtime_data

    def build(fleet: Fleet) -> list[AlarmControlPanelEntity]:
        return [ModuleAlarmPanel(hub, key) for key, record in fleet.modules.items() if record.module.alarm_arm is not None]

    Roster(hub, add_entities, build).keep_level(entry)


class ModuleAlarmPanel(ModuleEntity, AlarmControlPanelEntity):
    """An alarm system behind an ULTRA module, driven by its arm output."""

    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_code_arm_required = False
    _attr_name = None

    def __init__(self, hub: AccountHub, key: str) -> None:
        super().__init__(hub, key)
        self._attr_unique_id = f"{key}_alarm"

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        record = self.record
        if record is None:
            return None
        if self.channel_is_on(record.module.alarm_event) is True:
            return AlarmControlPanelState.TRIGGERED
        armed = self.channel_is_on(record.module.alarm_arm)
        if armed is None:
            return None
        return AlarmControlPanelState.ARMED_AWAY if armed else AlarmControlPanelState.DISARMED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        record = self.record
        arm = record.module.alarm_arm if record else None
        event = record.module.alarm_event if record else None
        return {
            "arm_output": arm.label if arm else None,
            "armed_state_name": arm.on_label if arm else None,
            "disarmed_state_name": arm.off_label if arm else None,
            "alarm_input": event.label if event else None,
        }

    async def _set_armed(self, armed: bool) -> None:
        record = self.record
        arm = record.module.alarm_arm if record else None
        if arm is None:
            raise HomeAssistantError("This alarm device is no longer available.")
        if self.channel_is_on(arm) is armed:
            return
        await self.hub.async_pulse_module(self.module_key, arm.number, what="Arm" if armed else "Disarm")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._set_armed(True)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._set_armed(False)
