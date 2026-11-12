"""Activation identifiers understood by each operator family.

An activation id is the 16-bit action number carried in an activation request.
Sliding and swing gates use one table of ids; garage door
operators use their own; the wireless siren another. Ids are listed here exactly
as the operators define them, with user-facing labels and a little metadata so
callers can build controls without knowing the operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "Activation",
    "GATE_ACTIVATIONS",
    "SWING_ACTIVATIONS",
    "GARAGE_ACTIVATIONS",
    "SIREN_ACTIVATIONS",
    "ACTIVATIONS_BY_FAMILY",
    "activations_for",
    "activation",
    "label_for",
    "tamper_arm_id",
    "tamper_disarm_id",
    "tamper_clear_id",
    "trigger_id",
    "GATE_TRIGGER",
    "GATE_PEDESTRIAN",
    "GATE_HOLIDAY_LOCK",
    "GATE_KEEP_OPEN",
    "GATE_COURTESY_LIGHT",
    "GATE_COURTESY_LIGHT_TIMER",
    "GATE_TAMPER_ARM",
    "GATE_TAMPER_DISARM",
    "GATE_CLEAR_ALARMS",
    "GATE_CANCEL_AUTOCLOSE",
    "GATE_CANCEL_PIRAC",
    "GATE_CANCEL_AUTOCLOSE_AND_PIRAC",
    "GATE_CUSTOM_1",
    "GATE_CUSTOM_2",
    "GATE_CUSTOM_3",
    "GATE_AUX",
    "GARAGE_RUN",
    "GARAGE_LIGHT",
    "GARAGE_HOLIDAY_LOCK",
    "GARAGE_KEEP_OPEN",
    "GARAGE_OPEN_ONLY",
    "GARAGE_CLOSE_ONLY",
    "GARAGE_TAMPER_ARM",
    "GARAGE_TAMPER_DISARM",
    "GARAGE_CLEAR_ALARMS",
    "GARAGE_AUTOCLOSE_OVERRIDE",
]


@dataclass(frozen=True, slots=True)
class Activation:
    """One action an operator can perform."""

    id: int
    key: str
    label: str
    # True when the action toggles a standing mode (holiday lock, keep open) rather
    # than performing a one-off movement.
    toggles_mode: bool = False
    # Position of this action in the operator's per-user permission bitmask, when
    # the family has one (gates). None for families without per-action permissions.
    bitmask_position: int | None = None


# --- gates (sliders and swings) --------------------------------------------------------------

GATE_AUX: Final = 70
GATE_HOLIDAY_LOCK: Final = 1
GATE_KEEP_OPEN: Final = 4
GATE_COURTESY_LIGHT: Final = 16
GATE_COURTESY_LIGHT_TIMER: Final = 17
GATE_TAMPER_ARM: Final = 25
GATE_TAMPER_DISARM: Final = 26
GATE_CLEAR_ALARMS: Final = 27
GATE_CANCEL_AUTOCLOSE: Final = 31
GATE_CANCEL_PIRAC: Final = 32
GATE_CANCEL_AUTOCLOSE_AND_PIRAC: Final = 33
GATE_TRIGGER: Final = 34
GATE_PEDESTRIAN: Final = 35
GATE_CUSTOM_1: Final = 37
GATE_CUSTOM_2: Final = 38
GATE_CUSTOM_3: Final = 39

GATE_ACTIVATIONS: Final[tuple[Activation, ...]] = (
    Activation(GATE_TRIGGER, "trigger", "Open / close", bitmask_position=11),
    Activation(GATE_PEDESTRIAN, "pedestrian", "Pedestrian", bitmask_position=12),
    Activation(GATE_CUSTOM_1, "custom_1", "Custom 1", bitmask_position=13),
    Activation(GATE_CUSTOM_2, "custom_2", "Custom 2", bitmask_position=14),
    Activation(GATE_CUSTOM_3, "custom_3", "Custom 3", bitmask_position=15),
    Activation(GATE_HOLIDAY_LOCK, "holiday_lock", "Holiday lock", toggles_mode=True, bitmask_position=1),
    Activation(GATE_KEEP_OPEN, "keep_open", "Keep open", toggles_mode=True, bitmask_position=2),
    Activation(GATE_COURTESY_LIGHT, "courtesy_light", "Courtesy light", bitmask_position=3),
    Activation(GATE_COURTESY_LIGHT_TIMER, "courtesy_light_timer", "Courtesy light timer", bitmask_position=4),
    Activation(GATE_AUX, "aux", "Auxiliary output", bitmask_position=0),
    Activation(GATE_TAMPER_ARM, "tamper_arm", "Arm tamper alarm", bitmask_position=5),
    Activation(GATE_TAMPER_DISARM, "tamper_disarm", "Disarm tamper alarm", bitmask_position=6),
    Activation(GATE_CLEAR_ALARMS, "clear_alarms", "Clear alarms", bitmask_position=7),
    Activation(GATE_CANCEL_AUTOCLOSE, "cancel_autoclose", "Cancel auto-close", bitmask_position=8),
    Activation(GATE_CANCEL_PIRAC, "cancel_pirac", "Cancel PIRAC", bitmask_position=9),
    Activation(
        GATE_CANCEL_AUTOCLOSE_AND_PIRAC,
        "cancel_autoclose_and_pirac",
        "Cancel auto-close and PIRAC",
        bitmask_position=10,
    ),
)

# Swings have the same ids but no tamper (XMR) input, so no tamper actions, and the
# permission bitmask positions shift down accordingly.
SWING_ACTIVATIONS: Final[tuple[Activation, ...]] = (
    Activation(GATE_TRIGGER, "trigger", "Open / close", bitmask_position=8),
    Activation(GATE_PEDESTRIAN, "pedestrian", "Pedestrian", bitmask_position=9),
    Activation(GATE_CUSTOM_1, "custom_1", "Custom 1", bitmask_position=10),
    Activation(GATE_CUSTOM_2, "custom_2", "Custom 2", bitmask_position=11),
    Activation(GATE_CUSTOM_3, "custom_3", "Custom 3", bitmask_position=12),
    Activation(GATE_HOLIDAY_LOCK, "holiday_lock", "Holiday lock", toggles_mode=True, bitmask_position=1),
    Activation(GATE_KEEP_OPEN, "keep_open", "Keep open", toggles_mode=True, bitmask_position=2),
    Activation(GATE_COURTESY_LIGHT, "courtesy_light", "Courtesy light", bitmask_position=3),
    Activation(GATE_COURTESY_LIGHT_TIMER, "courtesy_light_timer", "Courtesy light timer", bitmask_position=4),
    Activation(GATE_AUX, "aux", "Auxiliary output", bitmask_position=0),
    Activation(GATE_CANCEL_AUTOCLOSE, "cancel_autoclose", "Cancel auto-close", bitmask_position=5),
    Activation(GATE_CANCEL_PIRAC, "cancel_pirac", "Cancel PIRAC", bitmask_position=6),
    Activation(
        GATE_CANCEL_AUTOCLOSE_AND_PIRAC,
        "cancel_autoclose_and_pirac",
        "Cancel auto-close and PIRAC",
        bitmask_position=7,
    ),
)

# --- garage door operators ---------------------------------------------------------------

GARAGE_RUN: Final = 1
GARAGE_LIGHT: Final = 2
GARAGE_HOLIDAY_LOCK: Final = 3
GARAGE_KEEP_OPEN: Final = 4
GARAGE_OPEN_ONLY: Final = 5
GARAGE_CLOSE_ONLY: Final = 6
GARAGE_TAMPER_ARM: Final = 7
GARAGE_TAMPER_DISARM: Final = 8
GARAGE_CLEAR_ALARMS: Final = 9
GARAGE_AUTOCLOSE_OVERRIDE: Final = 10

GARAGE_ACTIVATIONS: Final[tuple[Activation, ...]] = (
    Activation(GARAGE_RUN, "trigger", "Open / close"),
    Activation(GARAGE_OPEN_ONLY, "open_only", "Open only"),
    Activation(GARAGE_CLOSE_ONLY, "close_only", "Close only"),
    Activation(GARAGE_HOLIDAY_LOCK, "holiday_lock", "Holiday lock", toggles_mode=True),
    Activation(GARAGE_KEEP_OPEN, "keep_open", "Keep open", toggles_mode=True),
    Activation(GARAGE_LIGHT, "courtesy_light", "Courtesy light"),
    Activation(GARAGE_TAMPER_ARM, "tamper_arm", "Arm tamper alarm"),
    Activation(GARAGE_TAMPER_DISARM, "tamper_disarm", "Disarm tamper alarm"),
    Activation(GARAGE_CLEAR_ALARMS, "clear_alarms", "Clear alarms"),
    Activation(GARAGE_AUTOCLOSE_OVERRIDE, "autoclose_override", "Override auto-close"),
)

# --- wireless siren -----------------------------------------------------------------------

SIREN_ACTIVATIONS: Final[tuple[Activation, ...]] = (
    Activation(1, "trigger_alarm", "Trigger alarm"),
    Activation(2, "clear_alarm", "Clear alarm"),
    Activation(3, "mute_alarm", "Mute alarm"),
    Activation(4, "unmute_alarm", "Unmute alarm"),
    Activation(5, "tamper_arm", "Arm tamper alarm"),
    Activation(6, "tamper_disarm", "Disarm tamper alarm"),
)

ACTIVATIONS_BY_FAMILY: Final[dict[str, tuple[Activation, ...]]] = {
    "slider": GATE_ACTIVATIONS,
    "swing": SWING_ACTIVATIONS,
    "garage": GARAGE_ACTIVATIONS,
    "siren": SIREN_ACTIVATIONS,
}


def activations_for(family: str) -> tuple[Activation, ...]:
    """All activations of a family; empty for an unknown family."""
    return ACTIVATIONS_BY_FAMILY.get(family, ())


def activation(family: str, key: str) -> Activation | None:
    """Look an activation up by its stable key, e.g. ``"keep_open"``."""
    for item in activations_for(family):
        if item.key == key:
            return item
    return None


def label_for(family: str, activation_id: int) -> str:
    for item in activations_for(family):
        if item.id == activation_id:
            return item.label
    return f"Action {activation_id}"


def _id(family: str, key: str) -> int | None:
    item = activation(family, key)
    return item.id if item else None


def trigger_id(family: str) -> int | None:
    """The main open/close action of a family."""
    return _id(family, "trigger")


def tamper_arm_id(family: str) -> int | None:
    return _id(family, "tamper_arm")


def tamper_disarm_id(family: str) -> int | None:
    return _id(family, "tamper_disarm")


def tamper_clear_id(family: str) -> int | None:
    return _id(family, "clear_alarms") or _id(family, "clear_alarm")
