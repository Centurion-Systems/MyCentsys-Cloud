"""Buttons: Open and Close for a gate, one-shot operator actions, ULTRA momentary outputs and
module tools, shared actions."""

from __future__ import annotations

import logging
from typing import NamedTuple

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud import actions
from .cloud.errors import CloudError
from .cloud.models import ChannelRole, GrantedAction, UltraChannel
from .coordinator import AccountHub, CloudConfigEntry
from .entity import ChannelEntity, GrantEntity, ModuleEntity, OperatorEntity, Roster, grant_action_style
from .fleet import Fleet, ModuleRecord, OperatorRecord

_LOGGER = logging.getLogger(__name__)

_CONFIG = EntityCategory.CONFIG


class GateMove(NamedTuple):
    """Open or Close as a button: translation key and unique-id suffix, icon, and the positions
    in which the gate is already there or on its way."""

    key: str
    icon: str
    already: tuple[str, ...]


# A gate has one trigger for both directions, so a press is only sent when it
# moves the gate the way the button says.
GATE_MOVES = (
    GateMove("open", "mdi:gate-open", ("open", "opening")),
    GateMove("close", "mdi:gate", ("closed", "closing")),
)


class OperatorAction(NamedTuple):
    """A one-shot activation shown as a button: translation key, activation id, icon."""

    key: str
    activation_id: int
    icon: str


class ModuleTool(NamedTuple):
    """A maintenance action on an ULTRA module: translation key, unique-id suffix, icon."""

    key: str
    suffix: str
    icon: str


REFRESH_TOKENS = ModuleTool("ultra_tokens", "refresh_tokens", "mdi:phone-refresh")
REFRESH_NETWORK_BALANCE = ModuleTool("ultra_balance", "refresh_balance", "mdi:phone-refresh")
REFRESH_CONFIGURATION = ModuleTool("ultra_configuration", "refresh_configuration", "mdi:cog-refresh")


# Gate actions exist only once the operator is positively a gate: the same ids
# mean something else on a garage door.
GATE_ACTIONS = (
    OperatorAction("pedestrian", actions.GATE_PEDESTRIAN, "mdi:walk"),
    OperatorAction("cancel_auto_close", actions.GATE_CANCEL_AUTOCLOSE, "mdi:timer-off-outline"),
    OperatorAction("cancel_pirac", actions.GATE_CANCEL_PIRAC, "mdi:motion-sensor-off"),
    OperatorAction("cancel_auto_close_pirac", actions.GATE_CANCEL_AUTOCLOSE_AND_PIRAC, "mdi:timer-cancel-outline"),
)
GARAGE_ACTIONS = (OperatorAction("autoclose_override", actions.GARAGE_AUTOCLOSE_OVERRIDE, "mdi:timer-off-outline"),)


def _operator_buttons(hub: AccountHub, record: OperatorRecord) -> list[ButtonEntity]:
    serial = record.key
    buttons: list[ButtonEntity] = []
    if record.is_gate:
        buttons += [OperatorGateButton(hub, serial, move) for move in GATE_MOVES]
        buttons += [OperatorActionButton(hub, serial, action) for action in GATE_ACTIONS]
    elif record.is_garage:
        buttons += [OperatorActionButton(hub, serial, action) for action in GARAGE_ACTIONS]
    # Clear alarms exists on sliders and garage doors, not on swings.
    if actions.tamper_clear_id(record.family or "") is not None:
        buttons.append(ClearAlarmsButton(hub, serial))
    return buttons


def balance_tools(record: ModuleRecord) -> tuple[ModuleTool | None, ModuleTool | None]:
    """(the balance button a module has, the one it must not have); both None while its SIM is not known."""
    if record.token_sim is None:
        return None, None
    return (REFRESH_TOKENS, REFRESH_NETWORK_BALANCE) if record.token_sim else (REFRESH_NETWORK_BALANCE, REFRESH_TOKENS)


async def async_setup_entry(hass: HomeAssistant, entry: CloudConfigEntry, add_entities: AddEntitiesCallback) -> None:
    hub: AccountHub = entry.runtime_data

    def build(fleet: Fleet) -> list[ButtonEntity]:
        buttons: list[ButtonEntity] = []
        for record in fleet.operators.values():
            buttons += _operator_buttons(hub, record)
        for key, module_record in fleet.modules.items():
            module = module_record.module
            buttons.append(RefreshConfigurationButton(hub, key, REFRESH_CONFIGURATION))
            balance_tool = balance_tools(module_record)[0]
            if balance_tool is not None:
                buttons.append(RefreshBalanceButton(hub, key, balance_tool))
            # The gate trigger is the cover; every other momentary output is a button.
            trigger = module.trigger
            if trigger is not None and not module.is_garage_door:
                buttons += [ModuleGateButton(hub, key, move) for move in GATE_MOVES]
            buttons += [
                OutputPulseButton(hub, key, channel)
                for channel in module.outputs
                if channel.role == ChannelRole.MOMENTARY and channel is not trigger
            ]
        for key, grant_record in fleet.grants.items():
            grant = grant_record.grant
            if grant.is_ultra:
                buttons += [GrantActionButton(hub, key, action) for action in grant.extra_actions]
        return buttons

    def retired(fleet: Fleet) -> list[str]:
        gone = ((key, balance_tools(record)[1]) for key, record in fleet.modules.items())
        return [f"{key}_{tool.suffix}" for key, tool in gone if tool is not None]

    Roster(hub, add_entities, build, domain="button", retired=retired).keep_level(entry)


class _GateButton(ButtonEntity):
    """Open or Close with the gate's own icon; does nothing when the gate is already there."""

    _move: GateMove

    def _place(self, key: str, move: GateMove) -> None:
        self._move = move
        self._attr_translation_key = move.key
        self._attr_icon = move.icon
        self._attr_unique_id = f"{key}_{move.key}"

    def _already_there(self, position: str | None) -> bool:
        if position in self._move.already:
            _LOGGER.debug("%s ignored: the gate is already %s", self._move.key, position)
            return True
        return False


class OperatorGateButton(OperatorEntity, _GateButton):
    """Open or Close for a SMART gate: the gate's trigger, sent only when it moves the gate that way."""

    def __init__(self, hub: AccountHub, serial: str, move: GateMove) -> None:
        super().__init__(hub, serial)
        self._place(serial, move)

    async def async_press(self) -> None:
        if self._already_there(self.hub.position_of(self.serial)):
            return
        record = self.record
        activation_id = actions.trigger_id(record.family or "") if record else None
        if not activation_id:
            raise HomeAssistantError("Open and close are not available on this operator.")
        await self.hub.async_send_operator(self.serial, activation_id, what="Open/close")


class ModuleGateButton(ModuleEntity, _GateButton):
    """Open or Close for a gate behind an ULTRA module: a pulse of its trigger output."""

    def __init__(self, hub: AccountHub, key: str, move: GateMove) -> None:
        super().__init__(hub, key)
        self._place(key, move)

    async def async_press(self) -> None:
        if self._already_there(self.position_label):
            return
        record = self.record
        trigger = record.module.trigger if record else None
        if trigger is None:
            raise HomeAssistantError("No trigger output is configured for this gate.")
        await self.hub.async_pulse_module(self.module_key, trigger.number, what="Open/close")


class OperatorActionButton(OperatorEntity, ButtonEntity):
    """A one-shot action of a SMART operator."""

    def __init__(self, hub: AccountHub, serial: str, action: OperatorAction) -> None:
        super().__init__(hub, serial)
        self._activation_id = action.activation_id
        self._attr_translation_key = action.key
        self._attr_icon = action.icon
        self._attr_unique_id = f"{serial}_{action.key}"

    async def async_press(self) -> None:
        await self.hub.async_send_operator(self.serial, self._activation_id, what=str(self.name))


class ClearAlarmsButton(OperatorEntity, ButtonEntity):
    """Clear the operator's alarms (the app's tamper-alarm "Clear")."""

    _attr_translation_key = "clear_alarms"
    _attr_icon = "mdi:alarm-light-off"

    def __init__(self, hub: AccountHub, serial: str) -> None:
        super().__init__(hub, serial)
        self._attr_unique_id = f"{serial}_clear_alarms"

    async def async_press(self) -> None:
        record = self.record
        activation_id = actions.tamper_clear_id(record.family or "") if record else None
        if not activation_id:
            raise HomeAssistantError("This operator has no alarm to clear.")
        await self.hub.async_send_operator(self.serial, activation_id, what="Clear alarms")


class _ModuleToolButton(ModuleEntity, ButtonEntity):
    """A maintenance action on an ULTRA module, shown in the device page's Configuration section."""

    _attr_entity_category = _CONFIG

    def __init__(self, hub: AccountHub, key: str, tool: ModuleTool) -> None:
        super().__init__(hub, key)
        self._tool = tool
        self._attr_translation_key = tool.key
        self._attr_icon = tool.icon
        self._attr_unique_id = f"{key}_{tool.suffix}"


class RefreshConfigurationButton(_ModuleToolButton):
    """Re-read this module's configuration (otherwise read only when it changes)."""

    async def async_press(self) -> None:
        self.hub.request_module_reload()


class RefreshBalanceButton(_ModuleToolButton):
    """Ask the module for its balance: tokens on a token SIM, else the network's (billable, so on demand only)."""

    async def async_press(self) -> None:
        record = self.record
        if record is None:
            raise HomeAssistantError("This device is no longer available.")
        try:
            # Tokens have a request of their own; a network balance comes with the general refresh.
            if self._tool is REFRESH_TOKENS:
                await self.hub.client.ultra_tokens_refresh(record.module.device_id)
            else:
                await self.hub.client.ultra_airtime_refresh(record.module.device_id)
        except CloudError as err:
            self.hub.note_module_error(err)
            raise HomeAssistantError(f"The balance request was refused: {err}") from err
        self.hub.follow_balance(self.module_key)


class OutputPulseButton(ChannelEntity, ButtonEntity):
    """A momentary output of an ULTRA module, named as the installer configured it."""

    def __init__(self, hub: AccountHub, key: str, channel: UltraChannel) -> None:
        super().__init__(hub, key, channel)
        self._attr_unique_id = f"{key}_output_{channel.number}"

    async def async_press(self) -> None:
        await self.hub.async_pulse_module(self.module_key, self._number, what=f"Activate {self._attr_name}")


class GrantActionButton(GrantEntity, ButtonEntity):
    """One of the extra actions a share grants besides opening the gate."""

    def __init__(self, hub: AccountHub, key: str, action: GrantedAction) -> None:
        super().__init__(hub, key)
        self._granted_id = action.id
        self._attr_name, self._attr_icon = grant_action_style(action.name)
        self._attr_unique_id = self.scoped_id(f"granted_{action.id}")

    async def async_press(self) -> None:
        grant = self.grant
        action = grant.action(self._granted_id) if grant else None
        if grant is None or action is None:
            raise HomeAssistantError("The shared gate no longer offers this action.")
        try:
            await self.hub.client.use_grant(grant, action)
        except CloudError as err:
            raise HomeAssistantError(f"{self._attr_name} was not carried out: {err}") from err
        self.hub.share_was_used()
