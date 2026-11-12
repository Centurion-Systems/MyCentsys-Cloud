"""How an operator's own status broadcast maps onto the entities' vocabularies.

Pure functions over :class:`cloud.overview.Overview` (and the cloud snapshot),
kept apart from the entities so the mapping can be tested without Home
Assistant. The state words chosen here are what automations match on, which
makes them part of the integration's interface.
"""

from __future__ import annotations

from typing import Any, Final

from .cloud.models import CloudSnapshot, TamperReport
from .cloud.overview import BatteryStatus, MainsStatus, OperatorState, Overview, TamperState
from .cloud.products import FAMILY_SWING

__all__ = [
    "POSITION_OPTIONS",
    "POWER_OPTIONS",
    "HEALTH_OPTIONS",
    "TAMPER_OPTIONS",
    "position_label",
    "cloud_position_label",
    "power_label",
    "health_label",
    "tamper_label",
    "cloud_tamper_label",
    "tamper_armed",
    "problems",
    "connection_icon",
    "battery_icon",
    "power_icon",
    "position_icon",
    "wifi_icon",
    "tamper_icon",
]

POSITION_OPTIONS: Final = (
    "unknown",
    "open",
    "closed",
    "partly_open",
    "partly_closed",
    "opening",
    "closing",
    "learning",
    "position_lost",
)
POWER_OPTIONS: Final = ("normal", "low", "off", "unknown")
HEALTH_OPTIONS: Final = ("good", "attention", "unknown")
TAMPER_OPTIONS: Final = ("armed", "disarmed", "triggered", "disabled", "unknown")

# The operator's own state names, in the entities' vocabulary.
_POSITION_LABELS: Final[dict[OperatorState, str]] = {
    OperatorState.UNKNOWN: "unknown",
    OperatorState.OPEN: "open",
    OperatorState.CLOSED: "closed",
    OperatorState.PARTLY_OPEN: "partly_open",
    OperatorState.PARTLY_CLOSED: "partly_closed",
    OperatorState.OPENING: "opening",
    OperatorState.CLOSING: "closing",
    OperatorState.LEARNING: "learning",
    OperatorState.LOST: "position_lost",
}

# The cloud snapshot's theft-alarm states in the tamper sensor's vocabulary.
_CLOUD_TAMPER: Final[dict[TamperReport, str]] = {
    TamperReport.ACTIVATED: "triggered",
    TamperReport.CLEARED: "disarmed",
    TamperReport.DISABLED: "disabled",
}


def position_label(overview: Overview | None) -> str | None:
    """The gate or door position from the operator's broadcast, or None."""
    if overview is None:
        return None
    label = _POSITION_LABELS.get(overview.state, "unknown")
    return None if label == "unknown" else label


def cloud_position_label(snapshot: CloudSnapshot | None) -> str | None:
    """The position the cloud last recorded, or None when it does not know."""
    if snapshot is None:
        return None
    label = _POSITION_LABELS.get(snapshot.position, "unknown")
    return None if label == "unknown" else label


def power_label(overview: Overview | None) -> str | None:
    """``normal``, ``low``, ``off`` or ``unknown``: the app's Mains tile."""
    if overview is None:
        return None
    if overview.mains_failed:
        return "off"
    if overview.mains is MainsStatus.NORMAL:
        return "normal"
    if overview.mains is MainsStatus.LOW:
        return "low"
    return "unknown"


def health_label(overview: Overview | None) -> str | None:
    if overview is None:
        return None
    return "attention" if overview.problems else "good"


def tamper_label(overview: Overview | None) -> str | None:
    """``triggered``, ``armed``, ``disarmed``, ``disabled`` or ``unknown``.

    Swing operators have no tamper input and report it disabled.
    """
    if overview is None:
        return None
    if overview.tamper_triggered:
        return "triggered"
    if overview.family == FAMILY_SWING:
        return "disabled"
    return {
        TamperState.ARMED: "armed",
        TamperState.DISARMED: "disarmed",
        TamperState.DISABLED: "disabled",
    }.get(overview.tamper, "unknown")


def cloud_tamper_label(snapshot: CloudSnapshot | None) -> str | None:
    if snapshot is None or snapshot.tamper is None:
        return None
    return _CLOUD_TAMPER.get(snapshot.tamper, "unknown")


def tamper_armed(overview: Overview | None) -> bool | None:
    """Whether the tamper alarm is armed (a triggered alarm counts as armed)."""
    if overview is None or overview.family == FAMILY_SWING:
        return None
    if overview.tamper_triggered or overview.tamper is TamperState.ARMED:
        return True
    if overview.tamper is TamperState.DISARMED:
        return False
    return None


def problems(overview: Overview | None) -> list[str]:
    """The labels of every fault the operator reports."""
    return list(overview.problems) if overview is not None else []


# ---------------------------------------------------------------------------------------------
# Icons that follow the state
# ---------------------------------------------------------------------------------------------

_BATTERY_ICONS: Final[dict[BatteryStatus, str]] = {
    BatteryStatus.LOW: "mdi:battery-low",
    BatteryStatus.REPLACE: "mdi:battery-alert",
    BatteryStatus.DAMAGED: "mdi:battery-alert",
    BatteryStatus.NONE: "mdi:battery-off",
}
# What a gate or a garage door shows for each position; a position that is not here shows nothing of its own.
_GATE_ICONS: Final[dict[str, str]] = {
    "open": "mdi:gate-open",
    "closed": "mdi:gate",
    "partly_open": "mdi:gate-arrow-left",
    "partly_closed": "mdi:gate-arrow-left",
    "opening": "mdi:gate-arrow-left",
    "closing": "mdi:gate-arrow-right",
    "learning": "mdi:gate-alert",
    "position_lost": "mdi:gate-alert",
}
_GARAGE_ICONS: Final[dict[str, str]] = {
    "open": "mdi:garage-open",
    "closed": "mdi:garage",
    "partly_open": "mdi:garage-open",
    "partly_closed": "mdi:garage-open",
    "opening": "mdi:garage-open",
    "closing": "mdi:garage-open",
    "learning": "mdi:garage-alert",
    "position_lost": "mdi:garage-alert",
}
# The weakest signal, in dBm, that still shows this many Wi-Fi bars.
_WIFI_BARS: Final = ((-50, 4), (-60, 3), (-70, 2), (-80, 1))


def connection_icon(connected: bool | None) -> str:
    """A globe while connected to the cloud, struck through while not, or while that is not known."""
    return "mdi:web" if connected else "mdi:web-off"


def battery_icon(overview: Overview | None) -> str:
    """The full battery unless the operator reports it low, due for replacement, damaged or missing."""
    if overview is None:
        return "mdi:battery"
    return _BATTERY_ICONS.get(overview.battery, "mdi:battery")


def power_icon(label: Any) -> str:
    """The plug, struck through while the mains supply is off."""
    return "mdi:power-plug-off" if label == "off" else "mdi:power-plug"


def position_icon(label: Any, *, gate: bool, garage: bool) -> str | None:
    """A gate's or a garage door's own picture of where it stands; None leaves Home Assistant's icon."""
    if garage:
        return _GARAGE_ICONS.get(label)
    return _GATE_ICONS.get(label) if gate else None


def wifi_icon(rssi: Any) -> str:
    """Wi-Fi bars for a signal strength in dBm."""
    if isinstance(rssi, bool) or not isinstance(rssi, (int, float)):
        return "mdi:wifi-strength-off-outline"
    bars = next((count for floor, count in _WIFI_BARS if rssi >= floor), 0)
    return f"mdi:wifi-strength-{bars}" if bars else "mdi:wifi-strength-outline"


def tamper_icon(label: Any) -> str:
    """The shield, struck through while the tamper alarm is disarmed or disabled."""
    return "mdi:shield-off" if label in ("disarmed", "disabled") else "mdi:shield-home"
