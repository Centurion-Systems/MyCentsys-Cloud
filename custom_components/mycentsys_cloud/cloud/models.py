"""Typed views of the documents the Centurion backends return.

Each type here is the integration's reading of one backend contract:

* GatewayApi ``DeviceInterfaceModel`` (the two operator listings)      -> :class:`Operator`
* GatewayApi ``OperatorOverviewResponseInterfaceModel``                -> :class:`CloudSnapshot`
* GatewayApi ``CertificateResponseInterfaceModel``                     -> :class:`ClientBundle`
* G-Web ``MCRActivationConfV2`` (a module and its IO configuration)   -> :class:`UltraModule`, :class:`UltraChannel`
* G-Web ``IOStatusN`` (the live IO state list)                        -> :class:`UltraReadings`
* G-Web ``MCRDeviceStatus`` (the synchronised diagnostics)            -> :class:`ModuleDiagnostics`
* AccessSharingService ``SharedAccessInterfaceModel``                  -> :class:`AccessGrant`, :class:`GrantedAction`, :class:`GrantHolder`

Reading is strict where the contract is (an identifier that is missing or of
the wrong type raises :class:`ModelError`) and forgiving where the backends
are (optional members become ``None``, PascalCase and camelCase are both
accepted, because GatewayApi serialises in camelCase while G-Web keeps the C#
member names). Nothing in this module does I/O or logs.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import json
import re
from typing import Any, Final

from .errors import ModelError
from .numbers import same_number
from .overview import OperatorState
from .products import FAMILY_GARAGE, family_for_code, is_gate_family

__all__ = [
    "Fields",
    "parse_utc",
    "WifiLink",
    "Operator",
    "ROLE_REMOTE",
    "ROLE_ADMIN",
    "combine_listings",
    "SupplyState",
    "TamperReport",
    "CloudSnapshot",
    "FEEDBACK_CHANNEL",
    "ChannelRole",
    "ModuleProfile",
    "PROFILE_LABELS",
    "MODULE_TYPES",
    "POSITION_BY_STATE",
    "channel_is_on",
    "UltraChannel",
    "UltraModule",
    "UltraReadings",
    "ModuleDiagnostics",
    "ShareKind",
    "GrantedAction",
    "GrantHolder",
    "GrantState",
    "AccessGrant",
    "ClientBundle",
]


# ---------------------------------------------------------------------------------------------
# reading JSON members
# ---------------------------------------------------------------------------------------------

_INTEGER_TEXT: Final = re.compile(r"-?\d+")
_DECIMAL_TEXT: Final = re.compile(r"-?\d+(?:[.,]\d+)?")
_TRUE_WORDS: Final = frozenset({"true", "1", "yes"})
_FALSE_WORDS: Final = frozenset({"false", "0", "no", ""})


def _as_text(value: Any) -> str | None:
    match value:
        case None:
            return None
        case bool():
            raise ModelError("Expected text")
        case int() | float():
            return str(value)
        case str():
            return value
    raise ModelError("Expected text")


def _as_integer(value: Any) -> int | None:
    match value:
        case None | "":
            return None
        case bool():
            return int(value)
        case int():
            return value
        case float() if value.is_integer():
            return int(value)
        case str() if _INTEGER_TEXT.fullmatch(value.strip()):
            return int(value.strip())
    raise ModelError("Expected an integer")


def _as_flag(value: Any) -> bool | None:
    match value:
        case None:
            return None
        case bool():
            return value
        case int():
            return value != 0
        case str() if value.strip().lower() in _TRUE_WORDS:
            return True
        case str() if value.strip().lower() in _FALSE_WORDS:
            return False
    raise ModelError("Expected a boolean")


def _as_decimal(value: Any) -> float | None:
    """A number, also when the backend wrote it as text with a unit ("12.6 V")."""
    match value:
        case bool() | None:
            return None
        case int() | float():
            return float(value)
        case str():
            found = _DECIMAL_TEXT.search(value)
            return float(found.group().replace(",", ".")) if found else None
    return None


class Fields:
    """One JSON object from a backend, read by member name regardless of case."""

    __slots__ = ("_members", "_what")

    def __init__(self, raw: Any, what: str) -> None:
        if not isinstance(raw, dict):
            raise ModelError(f"{what} is not an object")
        self._members: dict[str, Any] = raw
        self._what = what

    @classmethod
    def empty(cls, what: str = "") -> "Fields":
        return cls({}, what)

    def raw(self, *names: str) -> Any:
        """The first member present under any of ``names`` (exact first, then case-folded)."""
        for name in names:
            if name in self._members:
                return self._members[name]
        folded = {str(key).casefold(): value for key, value in self._members.items()}
        for name in names:
            if name.casefold() in folded:
                return folded[name.casefold()]
        return None

    def text(self, *names: str) -> str:
        """Trimmed text; empty when absent."""
        return (_as_text(self.raw(*names)) or "").strip()

    def maybe_text(self, *names: str) -> str | None:
        return self.text(*names) or None

    def integer(self, *names: str) -> int | None:
        return _as_integer(self.raw(*names))

    def loose_integer(self, *names: str) -> int | None:
        """An integer, or None for anything that is not one (never raises)."""
        try:
            return self.integer(*names)
        except ModelError:
            return None

    def flag(self, *names: str) -> bool | None:
        return _as_flag(self.raw(*names))

    def decimal(self, *names: str) -> float | None:
        return _as_decimal(self.raw(*names))

    def nested(self, *names: str) -> "Fields":
        """A child object; an empty view when the member is absent or not an object."""
        child = self.raw(*names)
        return Fields(child, self._what) if isinstance(child, dict) else Fields.empty(self._what)

    def sequence(self, *names: str) -> list[Any]:
        """A child array; empty when the member is absent or not an array."""
        child = self.raw(*names)
        return list(child) if isinstance(child, list) else []

    def records(self, *names: str) -> Iterator["Fields"]:
        """Every object element of a child array (other elements are skipped)."""
        for item in self.sequence(*names):
            if isinstance(item, dict):
                yield Fields(item, self._what)


def parse_utc(value: Any) -> datetime | None:
    """A backend timestamp as an aware datetime; naive values are UTC. None when unreadable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------
# GatewayApi: SMART operators
# ---------------------------------------------------------------------------------------------

ROLE_REMOTE: Final = "remote"
ROLE_ADMIN: Final = "admin"


@dataclass(frozen=True, slots=True)
class WifiLink:
    """What the gateway last heard from an operator's Wi-Fi module (``deviceWiFiStatus``)."""

    online: bool | None = None
    last_contact: str | None = None
    rssi: int | None = None

    @classmethod
    def read(cls, fields: Fields) -> "WifiLink":
        return cls(
            online=fields.flag("isOnline"),
            last_contact=fields.maybe_text("lastBackendConnectionDate"),
            rssi=fields.integer("wifiRssi"),
        )


@dataclass(frozen=True, slots=True)
class Operator:
    """A SMART operator the account may use (``DeviceInterfaceModel``).

    ``roles`` records which listings returned it: the account is a remote user
    of the operator, its administrator, or both.
    """

    serial: str
    label: str = ""
    product_type: int | None = None
    product_code: int | None = None
    wifi_capable: bool = False
    mac: str | None = None
    link: WifiLink = field(default_factory=WifiLink)
    faulty: bool | None = None
    warranty_void: bool | None = None
    firmware: str | None = None
    ble_firmware: str | None = None
    latitude: str | None = None
    longitude: str | None = None
    roles: frozenset[str] = frozenset()

    @classmethod
    def from_listing(cls, raw: Any, *, role: str = ROLE_REMOTE) -> "Operator":
        fields = Fields(raw, "Operator")
        serial = fields.text("serialNumber")
        if not serial:
            raise ModelError("Operator has no serial number")
        code = fields.integer("productCode")
        return cls(
            serial=serial,
            label=fields.text("deviceName"),
            product_type=fields.integer("productType"),
            product_code=code if code and code > 0 else None,
            wifi_capable=bool(fields.flag("isWifiDevice")),
            mac=fields.maybe_text("macAddress"),
            link=WifiLink.read(fields.nested("deviceWiFiStatus")),
            faulty=fields.flag("faultyDevice"),
            warranty_void=fields.flag("warrantyVoid"),
            latitude=fields.maybe_text("lattitude", "latitude"),
            longitude=fields.maybe_text("longitude"),
            firmware=fields.nested("deviceHardware").maybe_text("coreFirmwareVersion"),
            ble_firmware=fields.nested("deviceHardware").maybe_text("bluetoothFirmwareVersion"),
            roles=frozenset({role}),
        )

    def with_role(self, role: str) -> "Operator":
        return Operator(**{**_members_of(self), "roles": self.roles | {role}})

    @property
    def family(self) -> str | None:
        """The listed product code's family (``slider``, ``swing``, ``garage``), if a Plus operator."""
        return family_for_code(self.product_code)

    @property
    def drives(self) -> str | None:
        """``gate`` or ``garage`` from the listed product code; None while unclassified."""
        family = self.family
        if is_gate_family(family):
            return "gate"
        return "garage" if family == FAMILY_GARAGE else None

    @property
    def administered(self) -> bool:
        return ROLE_ADMIN in self.roles

    @property
    def remote_linked(self) -> bool:
        return ROLE_REMOTE in self.roles


def _members_of(instance: Any) -> dict[str, Any]:
    return {name: getattr(instance, name) for name in instance.__slots__}


def combine_listings(remote: Iterable[Operator], admin: Iterable[Operator]) -> tuple[Operator, ...]:
    """One operator per serial from both listings, remote-user entries first.

    An operator in both listings keeps the remote-user entry (the fuller one)
    and gains the administrator role.
    """
    roster: dict[str, Operator] = {}
    for operator in remote:
        roster.setdefault(operator.serial, operator.with_role(ROLE_REMOTE))
    for operator in admin:
        known = roster.get(operator.serial)
        roster[operator.serial] = known.with_role(ROLE_ADMIN) if known else operator.with_role(ROLE_ADMIN)
    return tuple(roster.values())


class SupplyState(IntEnum):
    """``PowerSupplyStatusEnum`` of the operator overview."""

    NORMAL = 0
    LOW = 1
    OFF = 2
    UNKNOWN = 3

    @property
    def label(self) -> str:
        return self.name.lower()


class TamperReport(IntEnum):
    """``TheftAlarmStateEnum`` of the operator overview."""

    ACTIVATED = 0
    CLEARED = 1
    DISABLED = 2

    @property
    def label(self) -> str:
        return self.name.lower()


def _member_of(enum: type[IntEnum], value: int | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    return enum(value) if value in enum._value2member_map_ else fallback


@dataclass(frozen=True, slots=True)
class CloudSnapshot:
    """The gateway's last recorded state of one operator (``OperatorOverviewResponseInterfaceModel``)."""

    serial: str
    position: OperatorState = OperatorState.UNKNOWN
    supply: SupplyState = SupplyState.UNKNOWN
    closing_beam: int | None = None
    opening_beam: int | None = None
    tamper: TamperReport | None = None
    flags: int = 0

    @classmethod
    def from_overview(cls, raw: Any) -> "CloudSnapshot":
        fields = Fields(raw, "Operator overview")
        serial = fields.text("operatorSerialNumber")
        if not serial:
            raise ModelError("Operator overview has no serial number")
        return cls(
            serial=serial,
            position=_member_of(OperatorState, fields.integer("operatorStatus"), OperatorState.UNKNOWN),
            supply=_member_of(SupplyState, fields.integer("powerSupplyStatus"), SupplyState.UNKNOWN),
            closing_beam=fields.integer("closingBeamStatus"),
            opening_beam=fields.integer("openingBeamStatus"),
            tamper=_member_of(TamperReport, fields.integer("theftAlarmState"), None),
            flags=fields.loose_integer("notificationFlags") or 0,
        )

    @property
    def position_label(self) -> str:
        return self.position.label

    @property
    def position_known(self) -> bool:
        return self.position is not OperatorState.UNKNOWN


# ---------------------------------------------------------------------------------------------
# G-Web: ULTRA modules
# ---------------------------------------------------------------------------------------------

# The IO number the app reads gate or door position from, when the installer wired one.
FEEDBACK_CHANNEL: Final = 4

_DIRECTION_INPUT: Final = 1


class ChannelRole:
    """What an IO does, read the way the app reads the installer's configuration."""

    FEEDBACK: Final = "feedback"  # IO 4 named Gate Status / Door Status / Garage Status: the position
    STATUS: Final = "status"  # any other input: a named two-state status
    LATCHING: Final = "latching"  # an output with named on/off states: a toggle
    MOMENTARY: Final = "momentary"  # any other output: a pulse


class ModuleProfile:
    """What a module is wired to, inferred from the installer's IO names."""

    GATE: Final = "gate"
    GARAGE: Final = "garage"
    ALARM: Final = "alarm"
    FENCE: Final = "fence"
    GENERIC: Final = "generic"


PROFILE_LABELS: Final[dict[str, str]] = {
    ModuleProfile.GATE: "Gate",
    ModuleProfile.GARAGE: "Garage door",
    ModuleProfile.ALARM: "Alarm",
    ModuleProfile.FENCE: "Electric fence",
    ModuleProfile.GENERIC: "I/O device",
}

# ``DeviceType`` as G-Web numbers the module generations.
MODULE_TYPES: Final[dict[int, str]] = {
    10: "G-ULTRA 2G",
    11: "G-SPEAK ULTRA 2G",
    12: "G-ULTRA 3G",
    13: "G-SPEAK ULTRA 3G",
    14: "G-ULTRA Penta",
    15: "G-SPEAK ULTRA Penta",
    16: "G-ULTRA 4G",
    17: "G-SPEAK ULTRA 4G",
    18: "G-ULTRA 4GS",
    19: "G-SPEAK ULTRA 4GS",
}

# ``IOStateID`` values of the position feedback input.
POSITION_BY_STATE: Final[dict[int, str]] = {
    48: "opening",
    49: "open",
    50: "closing",
    51: "closed",
    52: "running",
}

# ``IOStateID`` values of a named two-state IO: even is the rising (on) state,
# odd the falling (off) state.
_TWO_STATE_IDS: Final = range(88, 100)

# Words in an installer's IO name that say what the IO drives or reports.
_WORDS_GATE: Final = ("TRG", "TRIGGER", "GATE")
_WORDS_DOOR: Final = ("GARAGE", "GRGS", "GRG", "GDO", "DOOR")
_WORDS_FENCE: Final = ("FENCE", "ENERGI", "ELECTRIC")
_WORDS_ALARM: Final = ("ALARM", "ARM", "PANIC", "SIREN")
_WORDS_ALARM_EVENT: Final = ("ALARM", "TRIGGER", "SIREN", "PANIC")


def channel_is_on(state_id: int | None) -> bool | None:
    """True or False when the state id belongs to a two-state IO, otherwise None."""
    if state_id in _TWO_STATE_IDS:
        return state_id % 2 == 0
    return None


@dataclass(frozen=True, slots=True)
class UltraChannel:
    """One configured IO of a module (an ``IOConfig`` entry)."""

    number: int
    label: str = ""
    is_input: bool = False
    on_name: str = ""
    off_name: str = ""
    named_states: bool = False
    image_id: int | None = None

    @classmethod
    def read(cls, fields: Fields) -> "UltraChannel":
        number = fields.integer("IONumber")
        if number is None or number < 0:
            raise ModelError("IO configuration has no IO number")
        return cls(
            number=number,
            label=fields.text("IOName"),
            is_input=fields.integer("IODirection") == _DIRECTION_INPUT,
            on_name=fields.text("OnStateName"),
            off_name=fields.text("OffStateName"),
            named_states=bool(fields.flag("StateNameVisible")),
            image_id=fields.integer("ButtonImageId"),
        )

    def mentions(self, words: tuple[str, ...]) -> bool:
        """Whether the installer's name contains any of ``words`` (case-insensitive)."""
        upper = self.label.upper()
        return any(word in upper for word in words)

    @property
    def is_output(self) -> bool:
        return not self.is_input

    @property
    def feedback_for(self) -> str | None:
        """``gate`` or ``door`` when this IO is the position feedback, else None."""
        if self.number != FEEDBACK_CHANNEL:
            return None
        lowered = self.label.lower()
        # "Garage Status" and "Garage Door Status" are a door, however the rest is worded.
        if "door status" in lowered or ("garage" in lowered and "status" in lowered):
            return "door"
        if "gate status" in lowered:
            return "gate"
        return None

    @property
    def role(self) -> str:
        if self.feedback_for is not None:
            return ChannelRole.FEEDBACK
        if self.is_input:
            return ChannelRole.STATUS
        return ChannelRole.LATCHING if self.named_states else ChannelRole.MOMENTARY

    @property
    def opens_gate(self) -> bool:
        return self.is_output and self.mentions(_WORDS_GATE)

    @property
    def opens_door(self) -> bool:
        return self.is_output and self.mentions(_WORDS_DOOR)

    @property
    def on_label(self) -> str:
        return self.on_name or "On"

    @property
    def off_label(self) -> str:
        return self.off_name or "Off"

    def state_label(self, state_id: int | None) -> str | None:
        """The configured rising or falling name for a reported state id."""
        on = channel_is_on(state_id)
        if on is None:
            return None
        return self.on_label if on else self.off_label


# The SIM type that holds call and SMS tokens; every other type has a network balance.
_TOKEN_SIM_TYPE: Final = 0


@dataclass(frozen=True, slots=True)
class UltraModule:
    """A G-ULTRA / G-SPEAK ULTRA module and its IO configuration (``MCRActivationConfV2``)."""

    device_id: int
    label: str = ""
    imei: str | None = None
    type_id: int | None = None
    config_version: int | None = None
    online: bool | None = None
    is_shared: bool | None = None
    is_admin: bool | None = None
    channels: tuple[UltraChannel, ...] = ()
    # The SIM set up on the module: True for a token SIM (a balance of call and SMS tokens),
    # False for any other (a network balance), None where the configuration does not say.
    token_sim: bool | None = None

    @classmethod
    def from_configuration(cls, raw: Any) -> "UltraModule":
        fields = Fields(raw, "Device configuration")
        device_id = fields.integer("DeviceId")
        if not device_id or device_id <= 0:
            raise ModelError("Device configuration has no device id")
        # A module without IOs is padded with one nameless placeholder entry.
        channels = [UltraChannel.read(entry) for entry in fields.records("IOConfigs") if entry.text("IOName")]
        online, admin = fields.integer("DeviceOnline"), fields.integer("DeviceAdmin")
        sim_type = fields.loose_integer("SimType")
        return cls(
            device_id=device_id,
            label=fields.text("DeviceName"),
            imei=fields.maybe_text("DeviceImei"),
            type_id=fields.integer("DeviceType"),
            online=None if online is None else bool(online),
            is_admin=None if admin is None else bool(admin),
            is_shared=fields.flag("IsSharedAccess"),
            config_version=fields.integer("RefreshPermissionVersion"),
            channels=tuple(sorted(channels, key=lambda channel: channel.number)),
            token_sim=None if sim_type is None or sim_type < 0 else sim_type == _TOKEN_SIM_TYPE,
        )

    @property
    def key(self) -> str:
        """The integration-wide key of this module."""
        return f"ultra-{self.device_id}"

    @property
    def model(self) -> str:
        return MODULE_TYPES.get(self.type_id or 0, "G-ULTRA device")

    @property
    def outputs(self) -> tuple[UltraChannel, ...]:
        return tuple(channel for channel in self.channels if channel.is_output)

    @property
    def status_inputs(self) -> tuple[UltraChannel, ...]:
        """Inputs other than the position feedback."""
        return tuple(channel for channel in self.channels if channel.role == ChannelRole.STATUS)

    @property
    def feedback(self) -> UltraChannel | None:
        return next((channel for channel in self.channels if channel.role == ChannelRole.FEEDBACK), None)

    @property
    def drives_gate(self) -> bool:
        """Whether anything about the configuration says this module moves a gate or door."""
        if self.feedback is not None:
            return True
        return any(channel.opens_gate or channel.opens_door for channel in self.outputs)

    @property
    def trigger(self) -> UltraChannel | None:
        """The output pulsed to open or close, chosen among outputs only; None when not a gate."""
        if not self.drives_gate:
            return None
        outputs = self.outputs
        by_preference = (
            next((channel for channel in outputs if channel.opens_gate), None),
            next((channel for channel in outputs if channel.opens_door), None),
            outputs[0] if outputs else None,
        )
        return next((channel for channel in by_preference if channel is not None), None)

    @property
    def is_garage_door(self) -> bool:
        feedback = self.feedback
        if feedback is not None:
            return feedback.feedback_for == "door"
        trigger = self.trigger
        return trigger is not None and trigger.opens_door and not trigger.opens_gate

    def _any_channel_mentions(self, words: tuple[str, ...]) -> bool:
        return any(channel.mentions(words) for channel in self.channels)

    @property
    def profile(self) -> str:
        """Gate or door evidence first; then fence before alarm, as energisers are often named "Fence alarm"."""
        if self.drives_gate:
            return ModuleProfile.GARAGE if self.is_garage_door else ModuleProfile.GATE
        if self._any_channel_mentions(_WORDS_FENCE):
            return ModuleProfile.FENCE
        if self._any_channel_mentions(_WORDS_ALARM):
            return ModuleProfile.ALARM
        return ModuleProfile.GENERIC

    @property
    def profile_label(self) -> str:
        return PROFILE_LABELS[self.profile]

    @property
    def alarm_arm(self) -> UltraChannel | None:
        """The latching output that arms an alarm panel: "Arm" preferred over anything else containing ARM."""
        if self.profile != ModuleProfile.ALARM:
            return None
        latching = [channel for channel in self.outputs if channel.role == ChannelRole.LATCHING]
        plain_arm = [channel for channel in latching if "ARM" in channel.label.upper() and "ALARM" not in channel.label.upper()]
        any_arm = [channel for channel in latching if "ARM" in channel.label.upper()]
        return (plain_arm or any_arm or [None])[0]

    @property
    def alarm_event(self) -> UltraChannel | None:
        """The input that reports the alarm sounding, if the installer wired one."""
        if self.profile != ModuleProfile.ALARM:
            return None
        return next((channel for channel in self.status_inputs if channel.mentions(_WORDS_ALARM_EVENT)), None)


@dataclass(frozen=True, slots=True)
class UltraReadings:
    """The live IO states of one module (``IOStatusN``).

    ``states`` lists the state ids in IO order, starting at IO 1.
    The position is read from the configured feedback IO alone.
    """

    device_id: int
    online: bool = True
    states: tuple[int | None, ...] = ()
    config_version: int | None = None
    feedback_number: int | None = None

    @classmethod
    def offline(cls, device_id: int, *, feedback_number: int | None = None) -> "UltraReadings":
        return cls(device_id=device_id, online=False, feedback_number=feedback_number)

    @classmethod
    def from_states(cls, device_id: int, raw: Any, *, feedback_number: int | None = None) -> "UltraReadings":
        fields = Fields(raw, "IO states")
        states: list[int | None] = []
        for entry in fields.sequence("IOList"):
            state: int | None = None
            if isinstance(entry, dict):
                state = Fields(entry, "IO state").loose_integer("IOStateID")
            states.append(state)
        return cls(
            device_id=device_id,
            states=tuple(states),
            config_version=fields.integer("RefreshPermissionVersion"),
            feedback_number=feedback_number,
        )

    def state_of(self, number: int) -> int | None:
        """The reported state id of IO ``number``; None offline or when the IO was not reported."""
        if not self.online or not 1 <= number <= len(self.states):
            return None
        return self.states[number - 1]

    def is_on(self, number: int) -> bool | None:
        return channel_is_on(self.state_of(number))

    @property
    def position(self) -> str | None:
        """``opening``, ``open``, ``closing``, ``closed`` or ``running``, from the feedback IO only."""
        if self.feedback_number is None:
            return None
        state = self.state_of(self.feedback_number)
        return POSITION_BY_STATE.get(state) if state is not None else None


_AIRTIME_TEXT: Final = re.compile(r"Call:\s*(\d+)\s*SMS:\s*(\d+)", re.IGNORECASE)
# A network's balance answer ends in a numbered menu ("1) Buy bundles"), which is not part of the balance.
_MENU_ENTRY: Final = re.compile(r"\d+\)[^\n]*")
_BALANCE_PENDING: Final = "loading"
_STATE_LENGTH: Final = 255  # the longest state Home Assistant keeps


def network_reply(text: str) -> str | None:
    """What a network answered to a balance request, on one line and without its menu; None when empty."""
    lines = text.replace("\\n", "\n").replace("\r", "\n")
    reply = " ".join(_MENU_ENTRY.sub("", lines).split())
    return reply[:_STATE_LENGTH] or None
_ANTENNA_NAMES: Final = ("internal", "external")


@dataclass(frozen=True, slots=True)
class ModuleDiagnostics:
    """What a module last synchronised about itself (``MCRDeviceStatus``)."""

    device_id: int
    number: str | None = None
    firmware: str | None = None
    supply_volts: float | None = None
    antenna: str | None = None
    signal: int | None = None
    network: str | None = None
    link_status: str | None = None
    synced_at: str | None = None
    airtime_at: str | None = None
    call_credits: int | None = None
    sms_credits: int | None = None
    # On a SIM without tokens: the network's own answer to the last balance request.
    network_balance: str | None = None

    @classmethod
    def from_status(cls, device_id: int, raw: Any) -> "ModuleDiagnostics":
        fields = Fields(raw, "Device status")
        antenna = fields.raw("Antenna")
        match antenna:
            case bool() | int() if int(antenna) in (0, 1):
                antenna_name: str | None = _ANTENNA_NAMES[int(antenna)]
            case str():
                antenna_name = antenna.strip().lower() or None
            case _:
                antenna_name = None
        airtime = fields.text("Airtime")
        credits = _AIRTIME_TEXT.search(airtime)
        return cls(
            device_id=device_id,
            supply_volts=fields.decimal("Voltage"),
            signal=fields.loose_integer("Signal"),
            antenna=antenna_name,
            firmware=fields.maybe_text("Firmware"),
            link_status=fields.maybe_text("OnlineStatus"),
            network=fields.maybe_text("ConnectionType"),
            number=fields.maybe_text("Number"),
            synced_at=fields.maybe_text("LastSynced"),
            airtime_at=fields.maybe_text("AirtimeLastUpdated"),
            call_credits=int(credits.group(1)) if credits else None,
            sms_credits=int(credits.group(2)) if credits else None,
            network_balance=None if credits else network_reply(airtime),
        )

    @property
    def balance_pending(self) -> bool:
        """A balance was asked for and the module has not answered yet."""
        return (self.network_balance or "").strip(" .").lower() == _BALANCE_PENDING

    @property
    def uses_tokens(self) -> bool | None:
        """What the report suggests about the SIM, for a module whose configuration does not say:
        True for tokens, False for a network's own answer, None while it shows neither."""
        if self.call_credits is not None or self.sms_credits is not None:
            return True
        if self.network_balance and not self.balance_pending:
            return False
        return None

    @property
    def balance_mark(self) -> tuple[Any, ...]:
        """Changes whenever a new balance, of either kind, has arrived."""
        return (self.call_credits, self.sms_credits, self.network_balance, self.airtime_at)


# ---------------------------------------------------------------------------------------------
# AccessSharingService: gates shared with the account
# ---------------------------------------------------------------------------------------------


class ShareKind(IntEnum):
    """``ShareDeviceTypeEnum``."""

    NONE = 0
    ULTRA = 1
    SMART = 2


# The open/close action of a share is the gate trigger (activation 34 on every
# gate family); the app's label key identifies it on payloads without an id.
_GATE_TRIGGER_ACTIVATION: Final = 34
_GATE_ACTION_LABELS: Final = frozenset({"OpenCloseDescription", "Open/Close"})


@dataclass(frozen=True, slots=True)
class GrantedAction:
    """One activation the owner granted within a share (a payload ``Activation``)."""

    id: int
    name: str = ""
    trigger_id: int = 0
    io_number: int = 0

    @classmethod
    def read(cls, fields: Fields) -> "GrantedAction":
        action_id = fields.integer("Id")
        if action_id is None:
            raise ModelError("Granted action has no id")
        return cls(
            id=action_id,
            name=fields.text("Name"),
            trigger_id=fields.integer("TriggerId") or 0,
            io_number=fields.integer("IoNumber", "IONumber") or 0,
        )

    @property
    def opens_gate(self) -> bool:
        return self.trigger_id == _GATE_TRIGGER_ACTIVATION or self.name in _GATE_ACTION_LABELS


@dataclass(frozen=True, slots=True)
class GrantHolder:
    """One recipient of a share and how much of it they have used (a ``users`` entry)."""

    number: str | None = None
    modified_utc: str | None = None
    used_triggers: int = 0

    @classmethod
    def read(cls, fields: Fields) -> "GrantHolder":
        user = fields.nested("user")
        return cls(
            number=user.maybe_text("number"),
            modified_utc=user.maybe_text("lastModifiedDateUtc"),
            used_triggers=fields.integer("currentTriggerCount") or 0,
        )


class GrantState:
    """Why a share can or cannot be used right now (mirrors the service's refusal reasons)."""

    ACTIVE: Final = "active"
    REVOKED: Final = "revoked"
    NOT_STARTED: Final = "not_started"
    EXPIRED: Final = "expired"
    EXHAUSTED: Final = "exhausted"


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """A gate another account shared with this number (``SharedAccessInterfaceModel``)."""

    guid: str
    device_label: str = ""
    kind: int | None = None
    revoked: bool = False
    revoked_by_admin: bool = False
    starts_utc: str | None = None
    ends_utc: str | None = None
    trigger_limit: int | None = None
    ultra_device_id: int | None = None
    smart_serial: str | None = None
    modified_utc: str | None = None
    owner_number: str | None = None
    actions: tuple[GrantedAction, ...] = ()
    holders: tuple[GrantHolder, ...] = ()

    @classmethod
    def from_share(cls, raw: Any) -> "AccessGrant":
        fields = Fields(raw, "Shared access")
        guid = fields.text("guid")
        if not guid:
            raise ModelError("Shared access has no guid")
        return cls(
            guid=guid,
            device_label=fields.text("deviceName"),
            kind=fields.integer("shareDeviceType"),
            revoked=bool(fields.flag("accessRevoked")),
            revoked_by_admin=bool(fields.flag("accessRevokedByAdministrator")),
            starts_utc=fields.maybe_text("startTimeUtc"),
            ends_utc=fields.maybe_text("endTimeUtc"),
            trigger_limit=fields.integer("maximumTriggerCount"),
            ultra_device_id=fields.integer("ultraDeviceId"),
            smart_serial=fields.maybe_text("smartSerialNumber"),
            modified_utc=fields.maybe_text("lastModifiedDateUtc"),
            owner_number=fields.nested("owner").nested("user").maybe_text("number"),
            actions=tuple(GrantedAction.read(entry) for entry in _payload_of(fields).records("Activations")),
            holders=tuple(GrantHolder.read(entry) for entry in fields.records("users")),
        )

    @property
    def key(self) -> str:
        """The integration-wide key of this share (one Home Assistant may hold it under several accounts)."""
        return f"access-{self.guid}"

    @property
    def is_ultra(self) -> bool:
        return self.kind == ShareKind.ULTRA or self.ultra_device_id is not None

    @property
    def gate_action(self) -> GrantedAction | None:
        return next((action for action in self.actions if action.opens_gate), None)

    @property
    def extra_actions(self) -> tuple[GrantedAction, ...]:
        return tuple(action for action in self.actions if not action.opens_gate)

    def action(self, action_id: int) -> GrantedAction | None:
        return next((action for action in self.actions if action.id == action_id), None)

    def granted_by(self, number: str | None) -> bool:
        """Whether ``number`` is the share's owner (who already has the gate directly)."""
        return same_number(number, self.owner_number)

    def holder(self, number: str | None) -> GrantHolder | None:
        return next((holder for holder in self.holders if same_number(holder.number, number)), None)

    def holder_modified_utc(self, number: str | None) -> str | None:
        holder = self.holder(number)
        return holder.modified_utc if holder else None

    def triggers_left(self, number: str | None) -> int | None:
        """How many triggers ``number`` still has; None on an unlimited share."""
        if not self.trigger_limit:
            return None
        holder = self.holder(number)
        return max(0, self.trigger_limit - (holder.used_triggers if holder else 0))

    @property
    def expires_at(self) -> datetime | None:
        return parse_utc(self.ends_utc)

    def state_for(self, number: str | None, *, at: datetime | None = None) -> str:
        """The share's standing for ``number``: the first reason it cannot be used, else active."""
        if self.revoked or self.revoked_by_admin:
            return GrantState.REVOKED
        moment = at or datetime.now(timezone.utc)
        starts, ends = parse_utc(self.starts_utc), self.expires_at
        if starts is not None and moment < starts:
            return GrantState.NOT_STARTED
        if ends is not None and moment > ends:
            return GrantState.EXPIRED
        if self.triggers_left(number) == 0:
            return GrantState.EXHAUSTED
        return GrantState.ACTIVE

    def usable_by(self, number: str | None, *, at: datetime | None = None) -> bool:
        return self.state_for(number, at=at) == GrantState.ACTIVE


def _payload_of(fields: Fields) -> Fields:
    """The share's serialised payload (JSON text, or already an object), or an empty view."""
    payload = fields.raw("serializedSharedAccessPayload")
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    return Fields(payload, "Shared access payload") if isinstance(payload, dict) else Fields.empty()


# ---------------------------------------------------------------------------------------------
# GatewayApi: broker client certificate
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClientBundle:
    """The PKCS#12 bundle the broker accepts from this account, with its password."""

    pfx: bytes
    password: str

    def __repr__(self) -> str:  # the material never reaches a log
        return f"ClientBundle({len(self.pfx)} bytes)"
