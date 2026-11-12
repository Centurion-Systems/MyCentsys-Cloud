"""Covers: the gate or garage door itself.

A SMART gate is a single-button operator: open and close both send the
trigger, the operator picks the direction, and the same trigger while it is
moving stops it. A SMART garage door (SDO5) has distinct open-only and
close-only activations, and its run action stops a moving door. An ULTRA
module pulses its trigger output for open, close and stop alike; its position,
when a status input is wired, comes from that input. A shared gate is
triggered through the sharing service and has no position.

A gate's icon follows its position: the closed gate, the open gate, and the
gate with an arrow while it opens or closes. A garage door shows its open
door while it travels.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud import actions, products
from .cloud.errors import CloudError
from .coordinator import AccountHub, CloudConfigEntry
from .entity import GrantEntity, ModuleEntity, OperatorEntity, Roster
from .fleet import Fleet

_LOGGER = logging.getLogger(__name__)

_OPEN_CLOSE = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
_OPEN_CLOSE_STOP = _OPEN_CLOSE | CoverEntityFeature.STOP
_MOVING = ("opening", "closing")
# An ULTRA position input can also report travel without a direction.
_MODULE_MOVING = (*_MOVING, "running")
_GATE_ICON_OPEN = "mdi:gate-open"
_GATE_ICONS = {"closed": "mdi:gate", "opening": "mdi:gate-arrow-left", "closing": "mdi:gate-arrow-right"}
_GARAGE_ICON_MOVING = "mdi:garage-open"
# The cover is the primary entity of its device; its unique id is the fleet key plus this.
_COVER_SUFFIX = "_cover"


async def async_setup_entry(hass: HomeAssistant, entry: CloudConfigEntry, add_entities: AddEntitiesCallback) -> None:
    hub: AccountHub = entry.runtime_data

    def build(fleet: Fleet) -> list[CoverEntity]:
        covers: list[CoverEntity] = [OperatorCover(hub, serial) for serial in fleet.operators]
        covers += [ModuleCover(hub, key) for key, record in fleet.modules.items() if record.module.drives_gate]
        covers += [GrantCover(hub, key) for key, record in fleet.grants.items() if record.grant.is_ultra and record.grant.gate_action]
        return covers

    Roster(hub, add_entities, build).keep_level(entry)


class _PositionedCover(CoverEntity):
    """Open / closed / moving from a position label, shared by the covers that have one."""

    _attr_name = None  # the primary entity carries the device name

    @property
    def position_label(self) -> str | None:
        raise NotImplementedError

    @property
    def is_closed(self) -> bool | None:
        label = self.position_label
        return None if label is None else label == "closed"

    @property
    def is_opening(self) -> bool:
        return self.position_label == "opening"

    @property
    def is_closing(self) -> bool:
        return self.position_label == "closing"

    @property
    def is_moving(self) -> bool:
        return self.position_label in _MOVING

    @property
    def icon(self) -> str | None:
        """A gate: closed, open, or the gate with an arrow while it opens or closes. A garage door:
        its open door while it travels. Anything else is left to Home Assistant's own icon."""
        if self.device_class == CoverDeviceClass.GARAGE:
            return _GARAGE_ICON_MOVING if self.is_moving else None
        label = self.position_label
        if label is None:
            return None
        return _GATE_ICONS.get(label, _GATE_ICON_OPEN)


class OperatorCover(OperatorEntity, _PositionedCover):
    """A SMART operator as a cover: gate, garage door, or not yet classified."""

    def __init__(self, hub: AccountHub, serial: str) -> None:
        super().__init__(hub, serial)
        self._attr_unique_id = serial + _COVER_SUFFIX

    @property
    def _family(self) -> str | None:
        record = self.record
        return record.family if record else None

    @property
    def device_class(self) -> CoverDeviceClass:
        return CoverDeviceClass.GARAGE if self._family == products.FAMILY_GARAGE else CoverDeviceClass.GATE

    @property
    def supported_features(self) -> CoverEntityFeature:
        # Stop is only offered once the operator is known: it is the trigger
        # or run action, which would move an idle operator of unknown kind.
        return _OPEN_CLOSE_STOP if self._family else _OPEN_CLOSE

    @property
    def position_label(self) -> str | None:
        return self.hub.position_of(self.serial)

    async def _send(self, activation_id: int | None, what: str) -> None:
        if not activation_id:
            raise HomeAssistantError(f"{what} is not available on this operator.")
        await self.hub.async_send_operator(self.serial, activation_id, what=what)

    async def async_open_cover(self, **kwargs: Any) -> None:
        if self._family == products.FAMILY_GARAGE:
            await self._send(actions.GARAGE_OPEN_ONLY, "Open")
            return
        # An operator neither the listing nor its own broadcast has classified
        # gets the gate trigger, which is harmless on every gate.
        await self._send(actions.trigger_id(self._family or products.FAMILY_SLIDER), "Open/close")

    async def async_close_cover(self, **kwargs: Any) -> None:
        if self._family == products.FAMILY_GARAGE:
            await self._send(actions.GARAGE_CLOSE_ONLY, "Close")
            return
        await self._send(actions.trigger_id(self._family or products.FAMILY_SLIDER), "Open/close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """The trigger (gate) or run (garage door) action halts a moving operator; idle, it would start one."""
        if not self.is_moving:
            _LOGGER.debug("Stop ignored: the operator is not moving")
            return
        family = self._family
        if family is None:
            raise HomeAssistantError("Stop is not available until the operator has reported its type.")
        await self._send(actions.trigger_id(family), "Stop")


class ModuleCover(ModuleEntity, _PositionedCover):
    """A gate or garage door behind an ULTRA module.

    With a position input wired the cover reports and follows the real
    position; without one it is assumed-state. Either way open, close and stop
    pulse the module's trigger output, as the button on a remote does.
    """

    _attr_supported_features = _OPEN_CLOSE_STOP

    def __init__(self, hub: AccountHub, key: str) -> None:
        super().__init__(hub, key)
        self._attr_unique_id = key + _COVER_SUFFIX

    @property
    def device_class(self) -> CoverDeviceClass:
        record = self.record
        return CoverDeviceClass.GARAGE if record and record.module.is_garage_door else CoverDeviceClass.GATE

    @property
    def is_moving(self) -> bool:
        return self.position_label in _MODULE_MOVING

    @property
    def assumed_state(self) -> bool:
        return self.position_label is None

    async def _pulse_trigger(self, what: str = "Open/close") -> None:
        record = self.record
        trigger = record.module.trigger if record else None
        if trigger is None:
            raise HomeAssistantError("No trigger output is configured for this gate.")
        await self.hub.async_pulse_module(self.module_key, trigger.number, what=what)

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._pulse_trigger()

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._pulse_trigger()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """A pulse halts a moving gate or door. When the position is known and it is not moving,
        the pulse would start it instead, so nothing is sent."""
        if self.position_label is not None and not self.is_moving:
            _LOGGER.debug("Stop ignored: the gate or door is not moving")
            return
        await self._pulse_trigger("Stop")


class GrantCover(GrantEntity, CoverEntity):
    """A gate shared with this account, triggered through the sharing service; no position."""

    _attr_name = None
    _attr_supported_features = _OPEN_CLOSE

    def __init__(self, hub: AccountHub, key: str) -> None:
        super().__init__(hub, key)
        self._attr_unique_id = self.scoped_id("cover")
        # Nothing reports a shared gate's position, so the state is what was last asked for.
        self._attr_assumed_state, self._attr_device_class = True, CoverDeviceClass.GATE

    @property
    def is_closed(self) -> bool | None:
        return None

    async def _use_gate_action(self) -> None:
        grant = self.grant
        action = grant.gate_action if grant else None
        if grant is None or action is None:
            raise HomeAssistantError("The shared gate can no longer be triggered.")
        try:
            await self.hub.client.use_grant(grant, action)
        except CloudError as err:
            raise HomeAssistantError(f"Failed to trigger the gate: {err}") from err
        self.hub.share_was_used()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._use_gate_action()

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._use_gate_action()
