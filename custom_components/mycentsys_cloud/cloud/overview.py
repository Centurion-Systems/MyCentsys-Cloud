"""Decoding of the status overview a SMART operator publishes.

Every Plus operator periodically publishes a fixed-layout, little-endian status
block ("overview"). Its layout depends on the operator family and, within a
family, on the firmware generation. This module turns those bytes into one
family-independent :class:`Overview` with named fields, derived states
(battery, mains, tamper, standing modes) and readable notification labels.

The firmware's own enumerations are the reference for every value here. Where
the three families disagree on where a fact lives (a flag bit, a condition bit
or a status byte), the per-family tables below say where to look.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import IntEnum
import struct
from typing import Final

from .products import FAMILY_GARAGE, FAMILY_SLIDER, FAMILY_SWING, to_internal_code

__all__ = [
    "OverviewError",
    "OperatorState",
    "MainsStatus",
    "BatteryStatus",
    "TamperState",
    "Overview",
    "DeviceAnnouncement",
    "OVERVIEW_HEADER_LEN",
    "parse_firmware_version",
    "decode_overview",
    "decode_overview_message",
    "beam_condition",
    "notification_label",
]

# Overview payloads carry a 4-byte transfer header in front of the status block.
OVERVIEW_HEADER_LEN: Final = 4


class OverviewError(ValueError):
    """The overview bytes could not be decoded."""


class OperatorState(IntEnum):
    """Where the gate or door is, normalised across families."""

    UNKNOWN = 0
    OPEN = 1
    CLOSED = 2
    PARTLY_OPEN = 3
    PARTLY_CLOSED = 4
    OPENING = 5
    CLOSING = 6
    LEARNING = 7
    LOST = 8

    @property
    def label(self) -> str:
        return self.name.lower()

    @property
    def is_moving(self) -> bool:
        return self in (OperatorState.OPENING, OperatorState.CLOSING)


class MainsStatus(IntEnum):
    UNKNOWN = 0
    NORMAL = 1
    LOW = 2
    OFF = 3
    COMMS_OFF = 4  # the power supply is not talking to the controller

    @property
    def label(self) -> str:
        return self.name.lower()


class BatteryStatus(IntEnum):
    UNKNOWN = 0
    GOOD = 1
    LOW = 2
    REPLACE = 3
    NONE = 4  # no battery fitted
    DAMAGED = 5

    @property
    def label(self) -> str:
        return self.name.lower()


class TamperState(IntEnum):
    UNKNOWN = 0
    ARMED = 1
    DISARMED = 2
    DISABLED = 3

    @property
    def label(self) -> str:
        return self.name.lower()


# --- gate / door status bytes ---------------------------------------------------------

# Sliders and swings: APPGATE_STATUS.
_GATE_STATES: Final[dict[int, OperatorState]] = {
    0: OperatorState.OPEN,
    1: OperatorState.CLOSED,
    2: OperatorState.PARTLY_OPEN,
    3: OperatorState.PARTLY_CLOSED,
    4: OperatorState.OPENING,
    5: OperatorState.CLOSING,
}

# Garage door operators: APPMOBILE_DOOR_STATUS.
_DOOR_STATES: Final[dict[int, OperatorState]] = {
    1: OperatorState.OPEN,
    2: OperatorState.OPENING,
    3: OperatorState.CLOSED,
    4: OperatorState.CLOSING,
    5: OperatorState.PARTLY_OPEN,
    6: OperatorState.LEARNING,
    7: OperatorState.LOST,
}

# Sliders and swings: APPPOWER_DISPLAY_STATUS. Garage: APPMOBILE_POWER_STATUS.
_GATE_POWER: Final[dict[int, MainsStatus]] = {
    0: MainsStatus.NORMAL,
    1: MainsStatus.LOW,
    2: MainsStatus.UNKNOWN,
    3: MainsStatus.COMMS_OFF,
}
_DOOR_POWER: Final[dict[int, MainsStatus]] = {
    0: MainsStatus.NORMAL,
    1: MainsStatus.LOW,
    2: MainsStatus.UNKNOWN,
    3: MainsStatus.OFF,
}

# IO state byte of the tamper (XMR) input, shared by sliders and garage operators.
_XMR_DISABLED: Final = 2
_XMR_ARMED: Final = 17
_XMR_DISARMED: Final = 18

# --- beam status byte -------------------------------------------------------------------

_BEAM_CONDITIONS: Final[dict[int, str]] = {0: "disabled", 1: "idle"}
for _code in range(2, 7):
    _BEAM_CONDITIONS[_code] = "clear"
for _code in range(7, 12):
    _BEAM_CONDITIONS[_code] = "obstructed"
for _code in range(12, 16):
    _BEAM_CONDITIONS[_code] = "not_connected"
for _code in range(16, 21):
    _BEAM_CONDITIONS[_code] = "wiring_error"
for _code in range(21, 25):
    _BEAM_CONDITIONS[_code] = "sleep"


# Beam-byte conditions that are faults in their own right (the rest are states).
_BEAM_FAULT_CONDITIONS: Final[dict[str, str]] = {
    "not_connected": "not connected",
    "wiring_error": "wiring error",
}


def beam_condition(raw: int | None) -> str | None:
    """Reduce the beam status byte (25 values) to what the beam is physically doing."""
    if raw is None:
        return None
    return _BEAM_CONDITIONS.get(raw)


# --- notification flags -----------------------------------------------------------------
#
# Bit number -> (key, label, informational). Informational flags describe a mode the
# user chose, a transient state, or a power/battery state the power and battery
# readings already report; none of those is a fault, so they stay out of ``problems``.

_SIREN_FLAGS: Final[tuple[tuple[str, str, bool], ...]] = (
    ("siren_notifications", "Siren notifications enabled", True),
    ("siren_low_battery", "Siren battery low", False),
    ("siren_batteries_removed", "Siren batteries removed", False),
    ("siren_mains_disconnected", "Siren mains disconnected", False),
    ("siren_tamper_armed", "Siren tamper alarm armed", True),
    ("siren_tamper_triggered", "Siren tamper alarm triggered", False),
    ("siren_muted", "Siren muted", True),
    ("siren_mains_driven", "Siren on mains", True),
    ("siren_triggered", "Siren triggered", False),
    ("siren_maintenance", "Siren in maintenance mode", True),
    ("siren_battery_driven", "Siren on battery", True),
    ("siren_operator_1_disconnected", "Siren lost operator 1", False),
    ("siren_operator_2_disconnected", "Siren lost operator 2", False),
    ("siren_operator_3_disconnected", "Siren lost operator 3", False),
    ("siren_operator_4_disconnected", "Siren lost operator 4", False),
    ("siren_io_overcurrent", "Siren IO over-current", False),
    ("siren_battery_critical", "Siren battery critical", False),
)


def _with_siren(base: dict[int, tuple[str, str, bool]], first_bit: int) -> dict[int, tuple[str, str, bool]]:
    table = dict(base)
    for offset, entry in enumerate(_SIREN_FLAGS):
        table[first_bit + offset] = entry
    return table


_SLIDER_FLAGS: Final[dict[int, tuple[str, str, bool]]] = _with_siren(
    {
        0: ("battery_low", "Battery low", True),
        1: ("mains_failed", "Mains supply failed", True),
        2: ("manual_override", "Manual override engaged", True),
        3: ("config_busy", "Configuration busy", True),
        4: ("motor_control_startup", "Motor control starting", True),
        5: ("mains_low", "Mains supply low", True),
        6: ("invalid_files", "Configuration files invalid", False),
        7: ("collision", "Collision", False),
        8: ("io1_overcurrent", "IO 1 over-current", False),
        9: ("io2_overcurrent", "IO 2 over-current", False),
        10: ("io3_overcurrent", "IO 3 over-current", False),
        11: ("io4_overcurrent", "IO 4 over-current", False),
        12: ("io5_overcurrent", "IO 5 over-current", False),
        13: ("io6_overcurrent", "IO 6 over-current", False),
        14: ("aux_12v_overcurrent", "12 V auxiliary over-current", False),
        15: ("aux_24v_overcurrent", "24 V auxiliary over-current", False),
        16: ("bridge_temperature", "Motor bridge temperature", False),
        17: ("override_fault", "Override sensor fault", False),
        18: ("cpu_temperature", "Controller temperature", False),
        19: ("opening_beam_test_failed", "Opening beam test failed", False),
        20: ("closing_beam_test_failed", "Closing beam test failed", False),
        21: ("gate_stalled", "Gate stalled", False),
        22: ("motor_disconnected", "Motor disconnected", False),
        23: ("max_collisions", "Maximum collisions reached", False),
        24: ("position_lost", "Gate position lost", False),
        25: ("emergency_stop", "Emergency stop", False),
        26: ("limits_not_set", "Limits not set", False),
        27: ("override_sensor", "Override sensor", False),
        28: ("replace_battery", "Replace battery", False),
        29: ("origin_sensor", "Origin sensor", False),
        30: ("origin_config_fault", "Origin configuration fault", False),
        31: ("motor_control_comms", "Motor control communication unhealthy", False),
        32: ("open_slippage", "Possible slippage while opening", False),
        33: ("encoder_while_running", "Encoder fault while running", False),
        34: ("motor_control_supply", "Motor control supply failure", False),
        35: ("temperature_warning", "Temperature warning", False),
        36: ("opening_beam_alarm", "Opening beam alarm", False),
        37: ("closing_beam_alarm", "Closing beam alarm", False),
        38: ("tamper_alarm", "Tamper alarm triggered", False),
        39: ("holiday_lock", "Holiday lock on", True),
        40: ("beams_open", "Beams open", False),
        41: ("origin_fault", "Origin fault", False),
        42: ("tamper_fault", "Tamper input fault", False),
        43: ("controller_unclipped", "Controller unclipped", False),
        44: ("psu_comms", "Power supply communication unhealthy", False),
        45: ("no_batteries", "No batteries", False),
        46: ("motor_wires", "Motor wiring fault", False),
        47: ("close_slippage", "Possible slippage while closing", False),
        48: ("battery_usage", "Running on battery", True),
        49: ("psu_hardware_fault", "Power supply hardware fault", False),
        50: ("magnet_missing", "Magnet missing", False),
        51: ("photons_disconnected", "Wireless beams disconnected", False),
        52: ("rail_12v_failure", "12 V rail failure", False),
        53: ("bridge_or_fuse", "Motor bridge or fuse fault", False),
        54: ("keep_open", "Keep open on", True),
        55: ("gate_out_of_spec", "Gate out of specification", False),
        56: ("multiple_stalls", "Multiple stall events", False),
        58: ("photons_battery_low", "Wireless beam battery low", False),
        59: ("magnet_error", "Magnet error", False),
        60: ("tamper_armed", "Tamper alarm armed", True),
    },
    61,
)

_SWING_FLAGS: Final[dict[int, tuple[str, str, bool]]] = _with_siren(
    {
        0: ("mains_failed", "Mains supply failed", True),
        1: ("mains_low", "Mains supply low", True),
        2: ("psu_hardware_fault", "Power supply hardware fault", False),
        3: ("battery_low", "Battery low", True),
        4: ("replace_battery", "Replace battery", False),
        5: ("no_batteries", "No batteries", False),
        6: ("battery_usage", "Running on battery", True),
        7: ("config_busy", "Configuration busy", True),
        8: ("invalid_files", "Configuration files invalid", False),
        9: ("encoder_while_running", "Encoder fault while running", False),
        10: ("psu_comms", "Power supply communication unhealthy", False),
        11: ("io_config_error", "IO configuration error", False),
        12: ("io4_overcurrent", "IO 4 over-current", False),
        13: ("io5_overcurrent", "IO 5 over-current", False),
        14: ("io6_overcurrent", "IO 6 over-current", False),
        15: ("master_collision", "Master leaf collision", False),
        16: ("slave_collision", "Slave leaf collision", False),
        17: ("max_collisions", "Maximum collisions reached", False),
        18: ("master_stalled", "Master leaf stalled", False),
        19: ("slave_stalled", "Slave leaf stalled", False),
        20: ("position_lost", "Gate position lost", False),
        21: ("limits_not_set", "Limits not set", False),
        22: ("aux_12v_failure", "12 V auxiliary failure", False),
        23: ("bridge_temperature", "Motor bridge temperature", False),
        24: ("master_bridge_or_fuse", "Master bridge or fuse fault", False),
        25: ("slave_bridge_or_fuse", "Slave bridge or fuse fault", False),
        26: ("cpu_temperature", "Controller temperature", False),
        27: ("temperature_warning", "Temperature warning", False),
        28: ("master_motor_disconnected", "Master motor disconnected", False),
        29: ("slave_motor_disconnected", "Slave motor disconnected", False),
        30: ("emergency_stop", "Emergency stop", False),
        31: ("holiday_lock", "Holiday lock on", True),
        32: ("opening_beam_test_failed", "Opening beam test failed", False),
        33: ("closing_beam_test_failed", "Closing beam test failed", False),
        34: ("opening_beam_alarm", "Opening beam alarm", False),
        35: ("closing_beam_alarm", "Closing beam alarm", False),
        36: ("beams_open", "Beams open", False),
        37: ("photons_disconnected", "Wireless beams disconnected", False),
        38: ("photons_battery_low", "Wireless beam battery low", False),
        39: ("keep_open", "Keep open on", True),
        40: ("free_exit", "Free exit active", True),
    },
    41,
)

_GARAGE_FLAGS: Final[dict[int, tuple[str, str, bool]]] = _with_siren(
    {
        0: ("collision", "Collision", False),
        1: ("safety_strip_advice", "Safety strip advice", False),
        2: ("door_imbalanced", "Door imbalanced", False),
        3: ("run_time_exceeded", "Run time exceeded", False),
        4: ("current_sensor", "Current sensor fault", False),
        5: ("max_collisions", "Maximum collisions reached", False),
        6: ("power_low", "Power low", True),
        7: ("drive_fault", "Drive fault", False),
        8: ("doss_fault", "Door position sensor fault", False),
        9: ("battery_low", "Battery low", True),
        10: ("position_lost", "Door position lost", False),
        11: ("limits_not_set", "Limits not set", False),
        12: ("photons_disconnected", "Wireless beams disconnected", False),
        13: ("user_stop", "Stopped by user", True),
        14: ("tamper_alarm", "Tamper alarm triggered", False),
        15: ("photons_battery_low", "Wireless beam battery low", False),
        16: ("mains_failed", "Mains disconnected", True),
        17: ("beams_error", "Beams error", False),
        18: ("beams_open", "Beams open", False),
        19: ("holiday_lock", "Holiday lock on", True),
        20: ("keep_open", "Keep open on", True),
        21: ("battery_preventing_motion", "Battery too low to move", False),
        22: ("batteries_damaged", "Batteries damaged", False),
        23: ("no_batteries", "No batteries", False),
        24: ("mains_low", "Mains supply low", True),
        25: ("io1_overcurrent", "IO 1 over-current", False),
        26: ("io2_overcurrent", "IO 2 over-current", False),
        27: ("tamper_armed", "Tamper alarm armed", True),
    },
    28,
)

_FLAG_TABLES: Final[dict[str, dict[int, tuple[str, str, bool]]]] = {
    FAMILY_SLIDER: _SLIDER_FLAGS,
    FAMILY_SWING: _SWING_FLAGS,
    FAMILY_GARAGE: _GARAGE_FLAGS,
}

# The bits that count as a fault for the health roll-up: the conditions the app's
# health view and the first releases of this integration reported, per family.
# Power and battery states, user-chosen modes, transient states, the wireless
# siren's relayed flags and every beam condition (made, broken, sleeping, disabled,
# self-test) are left out; every bit still shows under ``active_flags`` and the
# beam self-test flags keep their own diagnostic sensor.
_FAULT_BITS: Final[dict[str, frozenset[int]]] = {
    FAMILY_SLIDER: frozenset({7, 21, 22, 23, 24, 25, 26, 28, 30, 35, 38, 41, 42, 43, 45, 49, 51, 56, 58}),
    FAMILY_SWING: frozenset({2, 4, 5, 15, 16, 17, 18, 19, 20, 21, 27, 28, 29, 30, 37, 38}),
    FAMILY_GARAGE: frozenset({0, 5, 7, 10, 11, 14, 21, 22, 23}),
}

# Condition flag bits that mean "standing open" timer is active, per family.
_STANDING_OPEN_BIT: Final[dict[str, int]] = {
    FAMILY_SLIDER: 14,
    FAMILY_SWING: 14,
    FAMILY_GARAGE: 2,
}
_GARAGE_CONDITION_HOLIDAY_LOCK_BIT: Final = 0
_GARAGE_CONDITION_AUTOCLOSE_OVERRIDE_BIT: Final = 1


def notification_label(family: str, bit: int) -> str | None:
    entry = _FLAG_TABLES.get(family, {}).get(bit)
    return entry[1] if entry else None


# --- the decoded overview -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Overview:
    """One decoded status overview."""

    family: str
    layout: str
    state: OperatorState
    raw_state: int
    battery_raw: int
    battery_volts: float
    input_voltage_raw: int | None
    temperature_raw: int
    mains: MainsStatus
    raw_power: int
    opening_beam_raw: int | None
    closing_beam_raw: int
    xmr_raw: int | None
    notification_flags: int
    condition_flags: int
    seconds_remaining: int
    timer_flags: int
    gate_lock_raw: int | None = None
    is_double_gate: bool | None = None
    feedback_handle: int = 0
    trigger_handle: int = 0
    _flags: dict[int, tuple[str, str, bool]] = field(default_factory=dict, repr=False, compare=False)

    # -- flags ----------------------------------------------------------------------------

    def flag(self, bit: int) -> bool:
        return bool((self.notification_flags >> bit) & 1)

    def condition(self, bit: int) -> bool:
        return bool((self.condition_flags >> bit) & 1)

    def _flag_by_key(self, key: str) -> bool:
        for bit, (flag_key, _label, _info) in self._flags.items():
            if flag_key == key:
                return self.flag(bit)
        return False

    def has_flag(self, key: str) -> bool:
        """Whether the named notification flag is set (False for a key this family lacks)."""
        return self._flag_by_key(key)

    @property
    def known_flag_keys(self) -> frozenset[str]:
        """Every flag key this operator family can report."""
        return frozenset(entry[0] for entry in self._flags.values())

    def any_flag(self, keys: Iterable[str]) -> bool | None:
        """Whether any of ``keys`` is set; None when this family reports none of them."""
        wanted = set(keys) & self.known_flag_keys
        if not wanted:
            return None
        return any(self._flag_by_key(key) for key in wanted)

    @property
    def active_flags(self) -> list[str]:
        """Keys of every set notification flag, in bit order."""
        return [entry[0] for bit, entry in sorted(self._flags.items()) if self.flag(bit)]

    @property
    def problems(self) -> list[str]:
        """Labels of the faults the operator reports: the family's fault flags (see
        ``_FAULT_BITS``) plus a wired beam that is not connected or has a wiring error.
        A beam that is made, broken, sleeping or disabled is a state, not a fault."""
        faults = _FAULT_BITS.get(self.family, frozenset())
        found = [entry[1] for bit, entry in sorted(self._flags.items()) if bit in faults and self.flag(bit)]
        for name, condition in (("Opening beam", self.opening_beam), ("Closing beam", self.closing_beam)):
            if condition in _BEAM_FAULT_CONDITIONS:
                found.append(f"{name} {_BEAM_FAULT_CONDITIONS[condition]}")
        return found

    @property
    def notices(self) -> list[str]:
        """Labels of every other set flag: states, modes and relayed siren flags."""
        faults = _FAULT_BITS.get(self.family, frozenset())
        return [entry[1] for bit, entry in sorted(self._flags.items()) if bit not in faults and self.flag(bit)]

    @property
    def unknown_flags(self) -> list[int]:
        """Set bits this module has no name for (new firmware), for diagnostics."""
        return [bit for bit in range(96) if self.flag(bit) and bit not in self._flags]

    # -- derived states -------------------------------------------------------------------

    @property
    def is_garage(self) -> bool:
        return self.family == FAMILY_GARAGE

    @property
    def healthy(self) -> bool:
        return not self.problems

    @property
    def battery(self) -> BatteryStatus:
        if self._flag_by_key("no_batteries"):
            return BatteryStatus.NONE
        if self._flag_by_key("batteries_damaged"):
            return BatteryStatus.DAMAGED
        if self._flag_by_key("battery_low"):
            return BatteryStatus.LOW
        if self._flag_by_key("replace_battery"):
            return BatteryStatus.REPLACE
        return BatteryStatus.GOOD

    @property
    def mains_failed(self) -> bool:
        """True when the operator reports no mains supply."""
        return self._flag_by_key("mains_failed") or self.mains is MainsStatus.OFF

    @property
    def on_battery(self) -> bool:
        return self.mains_failed or self._flag_by_key("battery_usage")

    @property
    def tamper(self) -> TamperState:
        """Whether the tamper (XMR) alarm input is armed."""
        if self.xmr_raw is None:
            return TamperState.UNKNOWN
        if self.xmr_raw == _XMR_ARMED or self._flag_by_key("tamper_armed"):
            return TamperState.ARMED
        if self.xmr_raw == _XMR_DISARMED:
            return TamperState.DISARMED
        if self.xmr_raw == _XMR_DISABLED:
            return TamperState.DISABLED
        return TamperState.UNKNOWN

    @property
    def tamper_triggered(self) -> bool:
        return self._flag_by_key("tamper_alarm")

    @property
    def holiday_lock(self) -> bool:
        if self.family == FAMILY_GARAGE and self.condition(_GARAGE_CONDITION_HOLIDAY_LOCK_BIT):
            return True
        return self._flag_by_key("holiday_lock")

    @property
    def keep_open(self) -> bool:
        return self._flag_by_key("keep_open")

    @property
    def autoclose_override(self) -> bool:
        return self.family == FAMILY_GARAGE and self.condition(_GARAGE_CONDITION_AUTOCLOSE_OVERRIDE_BIT)

    @property
    def standing_open(self) -> bool:
        """The gate has been standing open past its configured time."""
        return self.condition(_STANDING_OPEN_BIT[self.family])

    @property
    def opening_beam(self) -> str | None:
        return beam_condition(self.opening_beam_raw)

    @property
    def closing_beam(self) -> str | None:
        return beam_condition(self.closing_beam_raw)

    @property
    def input_volts(self) -> float | None:
        if not self.input_voltage_raw:
            return None
        return self.input_voltage_raw / 1000


@dataclass(frozen=True, slots=True)
class DeviceAnnouncement:
    """Who published an overview, from the message's user properties."""

    serial_number: str
    product_code: int | None
    firmware_version: str | None
    name: str | None
    mac_address: str | None
    sys_config: int | None = None


# --- struct layouts -----------------------------------------------------------------------------

# D-series (sliders), first generation. 36 bytes.
_SLIDER_V2 = struct.Struct("<HBBIIIIHBBBBBBBBHHH")
# D-series (sliders), 2.1+ / D10 4.1+. 64 bytes: adds gate lock, a third flag word, spare bytes.
_SLIDER_V3 = struct.Struct("<HBBIIIIHBBBBBBBBHHHI24x")
# V-series (swings), first generation. 52 bytes, with two motor info blocks.
_SWING_V1 = struct.Struct("<BBHIIIIBBhiBBhiBBBBHBBBBHHH")
# V-series (swings), 2.1+. 64 bytes: adds a third flag word and spare bytes.
_SWING_V2 = struct.Struct("<BBHIIIIBBhiBBhiBBBBHBBBBHHHI8x")
# Garage (SDO5), first generation. 24 bytes.
_GARAGE_V3 = struct.Struct("<IHBBHBBBBBBBBBBHH")
# Garage (SDO5), 1.3+. 44 bytes: adds a second flag word and spare bytes.
_GARAGE_V4 = struct.Struct("<IHBBHBBBBBBBBBBHHI16x")

_LAYOUTS_BY_SIZE: Final[dict[str, dict[int, str]]] = {
    FAMILY_SLIDER: {_SLIDER_V2.size: "slider_v2", _SLIDER_V3.size: "slider_v3"},
    FAMILY_SWING: {_SWING_V1.size: "swing_v1", _SWING_V2.size: "swing_v2"},
    FAMILY_GARAGE: {_GARAGE_V3.size: "garage_v3", _GARAGE_V4.size: "garage_v4"},
}

# Firmware (major, minor) from which the newer layout is used.
_NEW_LAYOUT_FROM: Final[dict[str, tuple[int, int]]] = {
    FAMILY_SLIDER: (2, 1),
    FAMILY_SWING: (2, 1),
    FAMILY_GARAGE: (1, 3),
}
_D10_INTERNAL_CODE: Final = 0x2D
_D10_NEW_LAYOUT_FROM: Final = (4, 1)


def parse_firmware_version(text: str | None) -> tuple[int, int] | None:
    """``"4.1.0.0"`` -> ``(4, 1)``; None when the text is not a version."""
    if not text:
        return None
    parts = text.strip().split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    return major, minor


def _wants_new_layout(family: str, firmware: tuple[int, int] | None, external_code: int | str | None) -> bool | None:
    if firmware is None:
        return None
    threshold = _NEW_LAYOUT_FROM[family]
    if family == FAMILY_SLIDER and to_internal_code(external_code) == _D10_INTERNAL_CODE:
        threshold = _D10_NEW_LAYOUT_FROM
    return firmware >= threshold


def _pick_layout(
    family: str, payload: bytes, firmware: tuple[int, int] | None, external_code: int | str | None
) -> str:
    sizes = _LAYOUTS_BY_SIZE.get(family)
    if sizes is None:
        raise OverviewError(f"No overview layout for family {family!r}")
    old_size, new_size = sorted(sizes)
    new_layout = _wants_new_layout(family, firmware, external_code)
    if new_layout is None:
        # No firmware version known: let the payload length decide.
        if len(payload) >= new_size:
            return sizes[new_size]
        if len(payload) >= old_size:
            return sizes[old_size]
        raise OverviewError(f"Overview of {len(payload)} bytes is too short for {family}")
    wanted = new_size if new_layout else old_size
    if len(payload) < wanted:
        # Firmware says new layout but the bytes are short (or vice versa): fall back
        # to whatever fits rather than reading past the end.
        if len(payload) >= old_size:
            return sizes[old_size]
        raise OverviewError(f"Overview of {len(payload)} bytes is too short for {family}")
    return sizes[wanted]


def _state(table: dict[int, OperatorState], raw: int) -> OperatorState:
    return table.get(raw, OperatorState.UNKNOWN)


def decode_overview(
    payload: bytes,
    *,
    family: str,
    firmware: str | tuple[int, int] | None = None,
    external_code: int | str | None = None,
) -> Overview:
    """Decode a status block (without its 4-byte transfer header)."""
    payload = bytes(payload)
    version = parse_firmware_version(firmware) if isinstance(firmware, str) else firmware
    layout = _pick_layout(family, payload, version, external_code)

    if layout in ("slider_v2", "slider_v3"):
        fmt = _SLIDER_V2 if layout == "slider_v2" else _SLIDER_V3
        fields = fmt.unpack_from(payload)
        (
            battery, _pos_or_pad, temperature, flags1, flags2, condition, seconds, timer_flags,
            gate_status, irbo, irbc, xmr, power, feedback_handle, trigger_handle, lock_or_spare,
            _feedback_ind, _trigger_ind, input_voltage,
        ) = fields[:19]
        flags3 = fields[19] if layout == "slider_v3" else 0
        return Overview(
            family=FAMILY_SLIDER,
            layout=layout,
            state=_state(_GATE_STATES, gate_status),
            raw_state=gate_status,
            battery_raw=battery,
            battery_volts=battery / 100,
            input_voltage_raw=input_voltage,
            temperature_raw=temperature,
            mains=_GATE_POWER.get(power, MainsStatus.UNKNOWN),
            raw_power=power,
            opening_beam_raw=irbo,
            closing_beam_raw=irbc,
            xmr_raw=xmr,
            notification_flags=flags1 | (flags2 << 32) | (flags3 << 64),
            condition_flags=condition,
            seconds_remaining=seconds,
            timer_flags=timer_flags,
            gate_lock_raw=lock_or_spare if layout == "slider_v3" else None,
            feedback_handle=feedback_handle,
            trigger_handle=trigger_handle,
            _flags=_SLIDER_FLAGS,
        )

    if layout in ("swing_v1", "swing_v2"):
        fmt = _SWING_V1 if layout == "swing_v1" else _SWING_V2
        fields = fmt.unpack_from(payload)
        (
            _version, temperature, battery, flags1, flags2, condition, seconds,
            _m_status, _m_pad, _m_mm, _m_counts,
            _s_status, _s_pad, _s_mm, _s_counts,
            gate_status, irbo, irbc, gate_lock, timer_flags, power, feedback_handle, trigger_handle,
            double_gate, _feedback_ind, _trigger_ind, input_voltage,
        ) = fields[:27]
        flags3 = fields[27] if layout == "swing_v2" else 0
        return Overview(
            family=FAMILY_SWING,
            layout=layout,
            state=_state(_GATE_STATES, gate_status),
            raw_state=gate_status,
            battery_raw=battery,
            battery_volts=battery / 100,
            input_voltage_raw=input_voltage,
            temperature_raw=temperature,
            mains=_GATE_POWER.get(power, MainsStatus.UNKNOWN),
            raw_power=power,
            opening_beam_raw=irbo,
            closing_beam_raw=irbc,
            xmr_raw=None,
            notification_flags=flags1 | (flags2 << 32) | (flags3 << 64),
            condition_flags=condition,
            seconds_remaining=seconds,
            timer_flags=timer_flags,
            gate_lock_raw=gate_lock,
            is_double_gate=double_gate == 0xFF,
            feedback_handle=feedback_handle,
            trigger_handle=trigger_handle,
            _flags=_SWING_FLAGS,
        )

    fmt = _GARAGE_V3 if layout == "garage_v3" else _GARAGE_V4
    fields = fmt.unpack_from(payload)
    (
        flags1, battery, condition, _pad1, seconds, timer_flags, _pad2,
        door_status, irbc, xmr, power, feedback_handle, trigger_handle, _pad3, _pad4,
        _feedback_ind, _trigger_ind,
    ) = fields[:17]
    flags2 = fields[17] if layout == "garage_v4" else 0
    return Overview(
        family=FAMILY_GARAGE,
        layout=layout,
        state=_state(_DOOR_STATES, door_status),
        raw_state=door_status,
        battery_raw=battery,
        battery_volts=battery / 10,
        input_voltage_raw=None,
        temperature_raw=0,
        mains=_DOOR_POWER.get(power, MainsStatus.UNKNOWN),
        raw_power=power,
        opening_beam_raw=None,
        closing_beam_raw=irbc,
        xmr_raw=xmr,
        notification_flags=flags1 | (flags2 << 32),
        condition_flags=condition,
        seconds_remaining=seconds,
        timer_flags=timer_flags,
        feedback_handle=feedback_handle,
        trigger_handle=trigger_handle,
        _flags=_GARAGE_FLAGS,
    )


# --- the published message ---------------------------------------------------------------------


def _properties_dict(user_properties: list[tuple[str, str]] | None) -> dict[str, str]:
    """Last value wins, keys as published (``ClientId``, ``PC``, ``FW``, ``Name``, ``MAC``, ``SysConfig``)."""
    result: dict[str, str] = {}
    for item in user_properties or ():
        try:
            key, value = item
        except (TypeError, ValueError):
            continue
        result[str(key)] = str(value)
    return result


def _int_or_none(text: str | None) -> int | None:
    try:
        return int(str(text).strip(), 10)
    except (TypeError, ValueError):
        return None


def decode_overview_message(
    user_properties: list[tuple[str, str]] | None,
    payload: bytes,
    *,
    family: str | None = None,
) -> tuple[DeviceAnnouncement, Overview | None]:
    """Decode one ``<serial>/deviceOverview`` message.

    The sender identifies itself in MQTT user properties and prefixes the status
    block with a 4-byte transfer header. The family is taken from the product
    code in the message unless the caller already knows it. When the status
    block cannot be decoded the announcement is still returned, with ``None``.
    """
    from .products import family_for_code  # local import keeps module import order simple

    props = _properties_dict(user_properties)
    serial = props.get("ClientId", "").strip()
    if not serial:
        raise OverviewError("Overview message carries no device serial")
    announcement = DeviceAnnouncement(
        serial_number=serial,
        product_code=_int_or_none(props.get("PC")),
        firmware_version=props.get("FW"),
        name=props.get("Name"),
        mac_address=props.get("MAC"),
        sys_config=_int_or_none(props.get("SysConfig")),
    )
    resolved_family = family or family_for_code(announcement.product_code)
    if resolved_family is None or len(payload) <= OVERVIEW_HEADER_LEN:
        return announcement, None
    try:
        overview = decode_overview(
            bytes(payload)[OVERVIEW_HEADER_LEN:],
            family=resolved_family,
            firmware=announcement.firmware_version,
            external_code=announcement.product_code,
        )
    except (OverviewError, struct.error):
        return announcement, None
    return announcement, overview
