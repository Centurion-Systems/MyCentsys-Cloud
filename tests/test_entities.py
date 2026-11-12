"""The entities every platform creates, and the unique ids they register under.

Home Assistant keys its entity registry, and with it every dashboard and
automation, on the unique id. The full set is spelled out here so that a
change to any of them is a deliberate one.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

from ha_stub import load_integration

integration = load_integration()

from custom_components.mycentsys_cloud import (  # noqa: E402
    alarm_control_panel,
    binary_sensor,
    button,
    cover,
    sensor,
    switch,
)
from custom_components.mycentsys_cloud.cloud.models import (  # noqa: E402
    AccessGrant,
    CloudSnapshot,
    ModuleDiagnostics,
    Operator,
    UltraModule,
    UltraReadings,
)
from custom_components.mycentsys_cloud.const import DOMAIN  # noqa: E402
from custom_components.mycentsys_cloud.fleet import Fleet, GrantRecord, ModuleRecord, OperatorRecord  # noqa: E402

NUMBER = "+27820000000"
SCOPE = "27820000000"
GUID = "3f2c9a10-5b7e-4d21-9c44-0a1b2c3d4e5f"
GUID_SMART = "22222222-2222-2222-2222-222222222222"
SLIDER, GARAGE, SWING, UNKNOWN = "S1", "S2", "S3", "S4"


class FakeCoordinator:
    """The hub surface the platforms use, over a fixed fleet."""

    def __init__(self, fleet: Fleet, codes: dict[str, int]) -> None:
        self.data = fleet
        self.number = NUMBER
        self.last_update_success = True
        self.classify(codes)
        self._listeners: list[Any] = []

    def classify(self, codes: dict[str, int]) -> None:
        """What each operator reported about itself (the broadcast header's product code)."""
        for serial, record in self.data.operators.items():
            record.reported_code = codes.get(serial)

    def async_add_listener(self, callback):
        self._listeners.append(callback)
        return lambda: None

    def reported_code(self, serial: str) -> int | None:
        return self.data.operators[serial].reported_code

    def position_of(self, key: str) -> str | None:
        return None

    def live_position(self, key: str) -> str | None:
        return None


def _io(number, name, *, direction=0, on="", off="", visible=False):
    return {"IONumber": number, "IOName": name, "IODirection": direction, "OnStateName": on, "OffStateName": off, "StateNameVisible": visible}


def _module(device_id, name, ios):
    return UltraModule.from_configuration({"DeviceId": device_id, "DeviceName": name, "DeviceType": 16, "DeviceOnline": 1, "RefreshPermissionVersion": 1, "IOConfigs": ios})


def _share(guid, *, ultra: bool, limited: bool = True):
    return AccessGrant.from_share(
        {
            "guid": guid,
            "deviceName": "Estate gate",
            "shareDeviceType": 1 if ultra else 2,
            "ultraDeviceId": 555 if ultra else None,
            "maximumTriggerCount": 5 if limited else None,
            "endTimeUtc": "2098-12-31T00:00:00" if limited else None,
            "serializedSharedAccessPayload": '{"Activations":[{"Id":77,"Name":"OpenCloseDescription","TriggerId":34,"IoNumber":1},{"Id":78,"Name":"PedestrianDescription","TriggerId":35,"IoNumber":2}]}',
            "users": [{"currentTriggerCount": 1, "user": {"number": NUMBER}}],
            "owner": {"user": {"number": "+27820000001"}},
        }
    )


def _data() -> tuple[Fleet, dict[str, int]]:
    def wifi(serial: str) -> OperatorRecord:
        # The listing carries no product code here: classification comes from the broadcast (see ``classify``).
        raw = {"serialNumber": serial, "deviceName": f"Operator {serial}", "isWifiDevice": True, "macAddress": "A1:B2:C3:D4:E5:F6"}
        raw["deviceHardware"] = {"coreFirmwareVersion": "4.1.0.0", "bluetoothFirmwareVersion": "2.0.3.0"}
        return OperatorRecord(operator=Operator.from_listing(raw), snapshot=CloudSnapshot(serial=serial))

    gate_module = _module(
        1001,
        "Home gate",
        [
            _io(1, "Gate TRG"),
            _io(2, "Pedestrian"),
            _io(3, "Lights", on="On", off="Off", visible=True),
            _io(4, "Gate Status", direction=1, on="Open", off="Closed", visible=True),
            _io(5, "Mains", direction=1, on="Present", off="Failed", visible=True),
        ],
    )
    alarm_module = _module(
        2002,
        "House alarm",
        [
            _io(1, "Arm", on="Armed", off="Disarmed", visible=True),
            _io(2, "Panic"),
            _io(3, "Alarm", direction=1, on="Triggered", off="Quiet", visible=True),
        ],
    )
    fleet = Fleet(
        operators={serial: wifi(serial) for serial in (SLIDER, GARAGE, SWING, UNKNOWN)},
        modules={
            "ultra-1001": ModuleRecord(
                module=gate_module,
                readings=UltraReadings(1001, states=(48, 48, 88, 51, 89, 48), feedback_number=4),
                diagnostics=ModuleDiagnostics(
                    1001, number="27820000000", firmware="1.5.1", antenna="internal", signal=3, network="4G", link_status="Active", call_credits=65, sms_credits=60
                ),
                token_sim=True,
            ),
            # No status read yet: what its SIM holds is not known, so it shows no balance.
            "ultra-2002": ModuleRecord(module=alarm_module, readings=UltraReadings(2002, states=(88, 48, 89, 48, 48, 48))),
        },
        grants={
            f"access-{GUID}": GrantRecord(grant=_share(GUID, ultra=True)),
            f"access-{GUID_SMART}": GrantRecord(grant=_share(GUID_SMART, ultra=False)),
        },
    )
    return fleet, {SLIDER: 41, GARAGE: 39, SWING: 25}


def _entities(platform, coordinator) -> list[Any]:
    from homeassistant.core import HomeAssistant

    hass = HomeAssistant()
    entry = _entry()
    entry.runtime_data = coordinator
    added: list[Any] = []
    asyncio.run(platform.async_setup_entry(hass, entry, lambda entities: added.extend(entities)))
    return added


def _entry():
    from homeassistant.config_entries import ConfigEntry

    return ConfigEntry(entry_id="entry")


@pytest.fixture(scope="module")
def coordinator() -> FakeCoordinator:
    data, codes = _data()
    return FakeCoordinator(data, codes)


def _operator_sensors(serial: str) -> set[str]:
    return {f"{serial}_{key}" for key in ("position", "mains", "health", "tamper", "battery", "last_contact", "wifi_signal")}


def _module_diag(key: str, *balance: str) -> set[str]:
    return {f"{key}_{name}" for name in ("ultra_signal", "ultra_supply", "ultra_link", *balance)}


EXPECTED: dict[str, set[str]] = {
    "cover": {f"{SLIDER}_cover", f"{GARAGE}_cover", f"{SWING}_cover", f"{UNKNOWN}_cover", "ultra-1001_cover", f"access-{GUID}-{SCOPE}_cover"},
    "button": {
        # Open and Close: on gates only, never a garage door, an unclassified operator or an alarm.
        f"{SLIDER}_open",
        f"{SLIDER}_close",
        f"{SWING}_open",
        f"{SWING}_close",
        "ultra-1001_open",
        "ultra-1001_close",
        f"{SLIDER}_pedestrian",
        f"{SLIDER}_cancel_auto_close",
        f"{SLIDER}_cancel_pirac",
        f"{SLIDER}_cancel_auto_close_pirac",
        f"{SLIDER}_clear_alarms",
        f"{GARAGE}_autoclose_override",
        f"{GARAGE}_clear_alarms",
        f"{SWING}_pedestrian",
        f"{SWING}_cancel_auto_close",
        f"{SWING}_cancel_pirac",
        f"{SWING}_cancel_auto_close_pirac",
        "ultra-1001_refresh_tokens",
        "ultra-1001_refresh_configuration",
        "ultra-1001_output_2",
        "ultra-2002_refresh_configuration",
        "ultra-2002_output_2",
        f"access-{GUID}-{SCOPE}_granted_78",
    },
    "switch": {
        f"{SLIDER}_holiday_lock",
        f"{SLIDER}_keep_open",
        f"{SLIDER}_tamper_alarm",
        f"{GARAGE}_tamper_alarm",
        f"{SWING}_holiday_lock",
        f"{SWING}_keep_open",
        "ultra-1001_latch_3",
        "ultra-2002_latch_1",
    },
    "sensor": (
        _operator_sensors(SLIDER)
        | _operator_sensors(GARAGE)
        | _operator_sensors(SWING)
        | _operator_sensors(UNKNOWN)
        | _module_diag("ultra-1001", "ultra_call_credits", "ultra_sms_credits")
        | {"ultra-1001_input_state_5"}
        | _module_diag("ultra-2002")
        | {"ultra-2002_input_state_3"}
        | {f"access-{GUID}-{SCOPE}_uses_left", f"access-{GUID}-{SCOPE}_valid_until"}
        | {f"access-{GUID_SMART}-{SCOPE}_uses_left", f"access-{GUID_SMART}-{SCOPE}_valid_until"}
    ),
    "binary_sensor": {f"{serial}_reachable" for serial in (SLIDER, GARAGE, SWING, UNKNOWN)} | {"ultra-1001_input_on_5", "ultra-2002_input_on_3"},
    "alarm_control_panel": {"ultra-2002_alarm"},
}

PLATFORMS = {
    "cover": cover,
    "button": button,
    "switch": switch,
    "sensor": sensor,
    "binary_sensor": binary_sensor,
    "alarm_control_panel": alarm_control_panel,
}


@pytest.mark.parametrize("platform_name", sorted(PLATFORMS))
def test_unique_ids(platform_name: str, coordinator: FakeCoordinator) -> None:
    entities = _entities(PLATFORMS[platform_name], coordinator)
    unique_ids = [entity.unique_id for entity in entities]
    assert len(unique_ids) == len(set(unique_ids)), "duplicate unique ids"
    assert set(unique_ids) == EXPECTED[platform_name]


def test_entity_names_and_keys(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for name in PLATFORMS for entity in _entities(PLATFORMS[name], coordinator)}
    # Primary entities carry the device name.
    for uid in (f"{SLIDER}_cover", "ultra-1001_cover", f"access-{GUID}-{SCOPE}_cover", "ultra-2002_alarm"):
        assert by_uid[uid].name is None
    # Translated entities keep their translation keys.
    expected_keys = {
        f"{SLIDER}_open": "open",
        f"{SWING}_close": "close",
        "ultra-1001_open": "open",
        f"{SLIDER}_pedestrian": "pedestrian",
        f"{SLIDER}_cancel_auto_close": "cancel_auto_close",
        f"{SLIDER}_cancel_pirac": "cancel_pirac",
        f"{SLIDER}_cancel_auto_close_pirac": "cancel_auto_close_pirac",
        f"{GARAGE}_autoclose_override": "autoclose_override",
        f"{SLIDER}_clear_alarms": "clear_alarms",
        f"{SLIDER}_holiday_lock": "holiday_lock",
        f"{SLIDER}_keep_open": "keep_open",
        f"{SLIDER}_tamper_alarm": "tamper_alarm",
        f"{SLIDER}_position": "position",
        f"{SLIDER}_mains": "mains",
        f"{SLIDER}_health": "health",
        f"{SLIDER}_tamper": "tamper",
        f"{SLIDER}_battery": "battery",
        f"{SLIDER}_reachable": "reachable",
        f"{SLIDER}_last_contact": "last_contact",
        "ultra-1001_refresh_tokens": "ultra_tokens",
        "ultra-1001_refresh_configuration": "ultra_configuration",
        "ultra-1001_ultra_signal": "ultra_signal",
        "ultra-1001_ultra_supply": "ultra_supply",
        "ultra-1001_ultra_sms_credits": "ultra_sms_credits",
    }
    for uid, key in expected_keys.items():
        assert by_uid[uid].translation_key == key, uid
    assert by_uid[f"{SLIDER}_wifi_signal"].translation_key is None
    # ULTRA IO entities are named exactly as the installer configured them.
    assert by_uid["ultra-1001_output_2"].name == "Pedestrian"
    assert by_uid["ultra-1001_latch_3"].name == "Lights"
    assert by_uid["ultra-1001_input_state_5"].name == "Mains"
    assert by_uid["ultra-1001_input_on_5"].name == "Mains on"
    assert by_uid["ultra-2002_latch_1"].name == "Arm"
    # Shared entities.
    assert by_uid[f"access-{GUID}-{SCOPE}_granted_78"].name == "Pedestrian"
    assert by_uid[f"access-{GUID}-{SCOPE}_uses_left"].name == "Uses left"
    assert by_uid[f"access-{GUID}-{SCOPE}_valid_until"].name == "Access ends"


def test_device_identifiers(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for name in PLATFORMS for entity in _entities(PLATFORMS[name], coordinator)}
    assert by_uid[f"{SLIDER}_cover"].device_info["identifiers"] == {(DOMAIN, SLIDER)}
    assert by_uid["ultra-1001_cover"].device_info["identifiers"] == {(DOMAIN, "ultra-1001")}
    assert by_uid[f"access-{GUID}-{SCOPE}_cover"].device_info["identifiers"] == {(DOMAIN, f"access-{GUID}-{SCOPE}")}
    assert by_uid["ultra-1001_cover"].device_info["model"] == "G-ULTRA 4G, Gate"
    assert by_uid["ultra-2002_alarm"].device_info["model"] == "G-ULTRA 4G, Alarm"
    assert by_uid[f"{SLIDER}_cover"].device_info["manufacturer"] == "Centurion Systems"


def test_entity_defaults(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for name in PLATFORMS for entity in _entities(PLATFORMS[name], coordinator)}
    assert by_uid[f"{SLIDER}_wifi_signal"].entity_registry_enabled_default is True
    assert by_uid["ultra-1001_input_on_5"].entity_registry_enabled_default is False
    assert by_uid["ultra-1001_ultra_supply"].entity_registry_enabled_default is True
    assert by_uid[f"{GARAGE}_cover"].device_class.value == "garage"
    assert by_uid[f"{SLIDER}_cover"].device_class.value == "gate"
    assert by_uid["ultra-1001_cover"].device_class.value == "gate"
    assert by_uid[f"{SLIDER}_position"].options == ["unknown", "open", "closed", "partly_open", "partly_closed", "opening", "closing", "learning", "position_lost"]
    assert by_uid["ultra-1001_input_state_5"].options == ["Failed", "Present"]


def test_entities_read_the_coordinator_data(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for name in PLATFORMS for entity in _entities(PLATFORMS[name], coordinator)}
    assert by_uid["ultra-1001_cover"].is_closed is True
    assert by_uid["ultra-1001_cover"].assumed_state is False
    assert by_uid["ultra-1001_latch_3"].is_on is True
    assert by_uid["ultra-1001_latch_3"].extra_state_attributes["state_name"] == "On"
    assert by_uid["ultra-1001_input_state_5"].native_value == "Failed"
    assert by_uid["ultra-1001_input_on_5"].is_on is False
    assert by_uid["ultra-2002_alarm"].alarm_state.value == "armed_away"
    assert by_uid["ultra-2002_alarm"].extra_state_attributes["armed_state_name"] == "Armed"
    assert by_uid[f"{SLIDER}_position"].native_value == "unknown"
    assert by_uid[f"{SLIDER}_health"].native_value is None
    assert by_uid[f"access-{GUID}-{SCOPE}_uses_left"].native_value == 4
    assert by_uid[f"access-{GUID}-{SCOPE}_cover"].available is True
    assert by_uid[f"access-{GUID}-{SCOPE}_cover"].is_closed is None


def test_only_two_module_values_are_diagnostics_and_the_tools_are_configuration(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for name in PLATFORMS for entity in _entities(PLATFORMS[name], coordinator)}
    category = {uid: getattr(entity.entity_category, "value", None) for uid, entity in by_uid.items()}
    diagnostics = {uid for uid, value in category.items() if value == "diagnostic"}
    assert diagnostics == {"ultra-1001_ultra_supply", "ultra-1001_ultra_link", "ultra-2002_ultra_supply", "ultra-2002_ultra_link"}
    configuration = {uid for uid, value in category.items() if value == "config"}
    assert configuration == {"ultra-1001_refresh_tokens", "ultra-1001_refresh_configuration", "ultra-2002_refresh_configuration"}
    # Nothing of a SMART operator is filed under diagnostics.
    assert not any(uid.startswith((SLIDER, GARAGE, SWING, UNKNOWN)) for uid in diagnostics)


def test_module_signal_shows_bars_and_details_sit_on_the_device_page(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for entity in _entities(sensor, coordinator)}
    signal = by_uid["ultra-1001_ultra_signal"]
    assert signal.native_value == 3 and signal.icon == "mdi:network-strength-3"
    assert by_uid["ultra-2002_ultra_signal"].icon == "mdi:network-strength-outline"  # nothing reported yet
    assert [sensor.signal_bars(level) for level in (None, 0, 1, 4, 5, 9)] == [
        "mdi:network-strength-outline",
        "mdi:network-strength-outline",
        "mdi:network-strength-1",
        "mdi:network-strength-4",
        "mdi:network-strength-4",
        "mdi:network-strength-4",
    ]
    device = by_uid["ultra-1001_ultra_signal"].device_info
    assert device["sw_version"] == "1.5.1; Number: +27 82 000 0000"
    assert device["hw_version"] == "Antenna: Internal; Connection: 4G"
    assert device["serial_number"] is None  # this fixture names no IMEI
    bare = by_uid["ultra-2002_ultra_signal"].device_info
    assert bare["sw_version"] is None and bare["hw_version"] is None
    record = coordinator.data.modules["ultra-1001"]
    record.module = dataclasses.replace(record.module, imei="490154205050518")
    assert by_uid["ultra-1001_ultra_signal"].device_info["serial_number"] == "490154205050518"


def test_a_gate_shows_core_and_bluetooth_firmware_and_a_garage_door_one(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for entity in _entities(cover, coordinator)}
    assert by_uid[f"{SLIDER}_cover"].device_info["sw_version"] == "Core: 4.1.0.0; Wireless: 2.0.3.0"
    assert by_uid[f"{SWING}_cover"].device_info["sw_version"] == "Core: 4.1.0.0; Wireless: 2.0.3.0"
    assert by_uid[f"{GARAGE}_cover"].device_info["sw_version"] == "4.1.0.0"


def test_gate_only_controls_appear_once_the_operator_is_classified() -> None:
    fleet, codes = _data()
    coordinator = FakeCoordinator(fleet, {})
    before = {entity.unique_id for entity in _entities(button, coordinator)}
    assert not any(uid.startswith(f"{SLIDER}_") for uid in before)
    coordinator.classify(codes)
    after = {entity.unique_id for entity in _entities(button, coordinator)}
    assert f"{SLIDER}_pedestrian" in after and f"{GARAGE}_autoclose_override" in after


def test_cover_controls_follow_the_operator_family(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for entity in _entities(cover, coordinator)}
    stop = cover.CoverEntityFeature.STOP
    # Gates and garage doors can be stopped mid-travel; an unclassified operator only gets open/close.
    assert by_uid[f"{SLIDER}_cover"].supported_features & stop
    assert by_uid[f"{GARAGE}_cover"].supported_features & stop
    assert not by_uid[f"{UNKNOWN}_cover"].supported_features & stop
    assert by_uid[f"{GARAGE}_cover"].device_class.value == "garage"
    # An ULTRA gate has all three as well.
    assert by_uid["ultra-1001_cover"].supported_features == cover.CoverEntityFeature.OPEN | cover.CoverEntityFeature.CLOSE | stop


class _MovableCoordinator(FakeCoordinator):
    """A hub whose positions a test sets, and which records the pulses it is asked for."""

    def __init__(self) -> None:
        super().__init__(*_data())
        self.positions: dict[str, str] = {}
        self.pulses: list[tuple[str, int, str]] = []
        self.sent: list[tuple[str, int, str]] = []

    def position_of(self, key: str) -> str | None:
        return self.positions.get(key)

    def live_position(self, key: str) -> str | None:
        return self.positions.get(key)

    async def async_pulse_module(self, key: str, io_number: int, *, what: str) -> None:
        self.pulses.append((key, io_number, what))

    async def async_send_operator(self, serial: str, activation_id: int, *, what: str) -> None:
        self.sent.append((serial, activation_id, what))


def test_a_travelling_gate_or_door_shows_its_open_icon() -> None:
    hub = _MovableCoordinator()
    by_uid = {entity.unique_id: entity for entity in _entities(cover, hub)}
    gate, swing, door, ultra = (by_uid[uid] for uid in (f"{SLIDER}_cover", f"{SWING}_cover", f"{GARAGE}_cover", "ultra-1001_cover"))
    gate_icons = {"closed": "mdi:gate", "open": "mdi:gate-open", "opening": "mdi:gate-arrow-left", "closing": "mdi:gate-arrow-right"}
    for position, icon in gate_icons.items():
        hub.positions = {SLIDER: position, SWING: position, GARAGE: position, "ultra-1001": position}
        assert gate.icon == swing.icon == ultra.icon == icon
        # A garage door keeps Home Assistant's own open and closed icons, and shows its open door while it travels.
        assert door.icon == ("mdi:garage-open" if position in ("opening", "closing") else None)
    # Stopped part-way, or travelling without a known direction: not closed, so the open gate.
    hub.positions = {SLIDER: "partly_open", "ultra-1001": "running"}
    assert gate.icon == "mdi:gate-open" and ultra.icon == "mdi:gate-open" and not ultra.is_closed
    # Nothing known about the position: Home Assistant's own icon.
    hub.positions = {}
    assert gate.icon is None and swing.icon is None


def test_stop_on_an_ultra_gate_pulses_the_trigger_only_while_it_can_be_moving() -> None:
    hub = _MovableCoordinator()
    hub.data.modules["ultra-1001"] = dataclasses.replace(hub.data.modules["ultra-1001"], readings=None)
    ultra = {entity.unique_id: entity for entity in _entities(cover, hub)}["ultra-1001_cover"]
    # No position known: stop is the same pulse as the remote's button.
    assert ultra.assumed_state
    asyncio.run(ultra.async_stop_cover())
    assert hub.pulses == [("ultra-1001", 1, "Stop")]
    # Known to be standing still: a pulse would start it, so nothing is sent.
    for position in ("open", "closed"):
        hub.positions = {"ultra-1001": position}
        asyncio.run(ultra.async_stop_cover())
    assert len(hub.pulses) == 1
    for position in ("opening", "closing", "running"):
        hub.positions = {"ultra-1001": position}
        asyncio.run(ultra.async_stop_cover())
    assert hub.pulses[1:] == [("ultra-1001", 1, "Stop")] * 3
    asyncio.run(ultra.async_open_cover())
    assert hub.pulses[-1] == ("ultra-1001", 1, "Open/close")


def test_open_and_close_buttons_carry_the_gate_icons_and_only_move_the_gate_their_way() -> None:
    from custom_components.mycentsys_cloud.cloud import actions

    hub = _MovableCoordinator()
    by_uid = {entity.unique_id: entity for entity in _entities(button, hub)}
    for key in (SLIDER, SWING, "ultra-1001"):
        assert by_uid[f"{key}_open"].icon == "mdi:gate-open" and by_uid[f"{key}_close"].icon == "mdi:gate"
    trigger = actions.trigger_id("slider")

    def press(uid: str) -> None:
        asyncio.run(by_uid[uid].async_press())

    # Closed: Open sends the trigger, Close has nothing to do.
    hub.positions = {SLIDER: "closed", "ultra-1001": "closed"}
    press(f"{SLIDER}_close"), press("ultra-1001_close")
    assert not hub.sent and not hub.pulses
    press(f"{SLIDER}_open"), press("ultra-1001_open")
    assert hub.sent == [(SLIDER, trigger, "Open/close")] and hub.pulses == [("ultra-1001", 1, "Open/close")]
    # Open or already opening: Open has nothing to do, Close sends the trigger.
    for position in ("open", "opening"):
        hub.positions = {SLIDER: position, "ultra-1001": position}
        press(f"{SLIDER}_open"), press("ultra-1001_open")
    assert len(hub.sent) == 1 and len(hub.pulses) == 1
    press(f"{SLIDER}_close"), press("ultra-1001_close")
    assert len(hub.sent) == 2 and len(hub.pulses) == 2
    # Position not known (an ULTRA gate without a status input): both buttons are the remote's button.
    hub.positions = {}
    hub.data.modules["ultra-1001"] = dataclasses.replace(hub.data.modules["ultra-1001"], readings=None)
    press("ultra-1001_open"), press("ultra-1001_close")
    assert len(hub.pulses) == 4


def test_an_ultra_garage_door_gets_no_gate_buttons() -> None:
    hub = _MovableCoordinator()
    garage = _module(1001, "Home garage", [_io(1, "Gate TRG"), _io(4, "Garage Status", direction=1, on="Open", off="Closed", visible=True)])
    hub.data.modules["ultra-1001"] = dataclasses.replace(hub.data.modules["ultra-1001"], module=garage)
    ids = {entity.unique_id for entity in _entities(button, hub)}
    assert "ultra-1001_open" not in ids and "ultra-1001_close" not in ids


def test_an_ultra_with_a_garage_status_input_is_a_garage_door() -> None:
    hub = _MovableCoordinator()
    garage = _module(1001, "Home garage", [_io(1, "Gate TRG"), _io(4, "Garage Status", direction=1, on="Open", off="Closed", visible=True)])
    hub.data.modules["ultra-1001"] = dataclasses.replace(hub.data.modules["ultra-1001"], module=garage)
    ultra = {entity.unique_id: entity for entity in _entities(cover, hub)}["ultra-1001_cover"]
    assert ultra.device_class.value == "garage"
    assert ultra.device_info["model"] == "G-ULTRA 4G, Garage door"
    hub.positions = {"ultra-1001": "opening"}
    assert ultra.icon == "mdi:garage-open"


def test_package_loads_every_module() -> None:
    import importlib

    for name in ("config_flow", "coordinator", "fleet", "live_feed", "broker", "entity", "telemetry", "calling_codes", "learned"):
        importlib.import_module(f"custom_components.mycentsys_cloud.{name}")


# --- an ULTRA's balance follows its SIM ----------------------------------------------------------------


class _Registry:
    """The part of Home Assistant's entity registry the roster uses, over a fixed set of entries."""

    def __init__(self, *entries: tuple[str, str]) -> None:
        self.entries = {(domain, DOMAIN, unique_id): f"{domain}.{unique_id}" for domain, unique_id in entries}
        self.removed: list[str] = []

    def async_get_entity_id(self, domain: str, platform: str, unique_id: str) -> str | None:
        return self.entries.get((domain, platform, unique_id))

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.entries = {key: value for key, value in self.entries.items() if value != entity_id}


class _LiveCoordinator(FakeCoordinator):
    """A hub that has a Home Assistant behind it and can publish again."""

    def __init__(self, fleet: Fleet, codes: dict[str, int], registry: _Registry) -> None:
        super().__init__(fleet, codes)
        from homeassistant.core import HomeAssistant

        self.hass = HomeAssistant()
        self.hass.data["entity_registry"] = registry

    def publish(self) -> None:
        for listener in list(self._listeners):
            listener()


def _placed(platform, hub: _LiveCoordinator) -> list[Any]:
    """Set a platform up on a live hub; the list keeps growing as the roster places more."""
    entry = _entry()
    entry.runtime_data = hub
    added: list[Any] = []
    asyncio.run(platform.async_setup_entry(hub.hass, entry, lambda entities: added.extend(entities)))
    return added


def _balance_uids(entities: list[Any], key: str) -> set[str]:
    wanted = ("ultra_call_credits", "ultra_sms_credits", "ultra_balance", "refresh_tokens", "refresh_balance")
    return {entity.unique_id for entity in entities if entity.unique_id in {f"{key}_{name}" for name in wanted}}


def test_a_sim_with_a_network_balance_shows_that_and_no_tokens() -> None:
    data, codes = _data()
    record = data.modules["ultra-2002"]
    record.diagnostics = ModuleDiagnostics(2002, signal=5, link_status="Active", network_balance="Your balance is R12.50", airtime_at="2026/10/18 09:00:00")
    record.token_sim = False
    # An earlier version registered token sensors and a token button for every module.
    registry = _Registry(("sensor", "ultra-2002_ultra_call_credits"), ("sensor", "ultra-2002_ultra_sms_credits"), ("button", "ultra-2002_refresh_tokens"))
    hub = _LiveCoordinator(data, codes, registry)
    sensors, buttons = _placed(sensor, hub), _placed(button, hub)
    assert _balance_uids(sensors, "ultra-2002") == {"ultra-2002_ultra_balance"}
    assert _balance_uids(buttons, "ultra-2002") == {"ultra-2002_refresh_balance"}
    assert sorted(registry.removed) == ["button.ultra-2002_refresh_tokens", "sensor.ultra-2002_ultra_call_credits", "sensor.ultra-2002_ultra_sms_credits"]
    balance = next(entity for entity in sensors if entity.unique_id == "ultra-2002_ultra_balance")
    assert balance.translation_key == "ultra_balance" and balance.native_value == "Your balance is R12.50"
    assert balance.extra_state_attributes == {"reported": "2026/10/18 09:00:00"}
    refresh = next(entity for entity in buttons if entity.unique_id == "ultra-2002_refresh_balance")
    assert refresh.translation_key == "ultra_balance" and refresh.entity_category.value == "config"
    # The token SIM next to it is untouched.
    assert _balance_uids(sensors, "ultra-1001") == {"ultra-1001_ultra_call_credits", "ultra-1001_ultra_sms_credits"}
    assert _balance_uids(buttons, "ultra-1001") == {"ultra-1001_refresh_tokens"}


def test_the_balance_appears_once_the_sim_is_known_and_follows_a_change_of_sim() -> None:
    data, codes = _data()
    registry = _Registry()
    hub = _LiveCoordinator(data, codes, registry)
    sensors, buttons = _placed(sensor, hub), _placed(button, hub)
    record = data.modules["ultra-2002"]
    assert _balance_uids(sensors, "ultra-2002") == set() and _balance_uids(buttons, "ultra-2002") == set()

    record.diagnostics, record.token_sim = ModuleDiagnostics(2002, network_balance="R5.00"), False
    hub.publish()
    assert _balance_uids(sensors, "ultra-2002") == {"ultra-2002_ultra_balance"}
    assert _balance_uids(buttons, "ultra-2002") == {"ultra-2002_refresh_balance"}
    hub.publish()  # nothing is placed twice
    assert len([entity for entity in sensors if entity.unique_id == "ultra-2002_ultra_balance"]) == 1

    # The SIM is swapped for a token SIM: the balance goes, the tokens come.
    registry.entries[("sensor", DOMAIN, "ultra-2002_ultra_balance")] = "sensor.house_alarm_network_balance"
    registry.entries[("button", DOMAIN, "ultra-2002_refresh_balance")] = "button.house_alarm_refresh_network_balance"
    record.diagnostics, record.token_sim = ModuleDiagnostics(2002, call_credits=10, sms_credits=20), True
    hub.publish()
    assert sorted(registry.removed) == ["button.house_alarm_refresh_network_balance", "sensor.house_alarm_network_balance"]
    assert {"ultra-2002_ultra_call_credits", "ultra-2002_ultra_sms_credits"} <= _balance_uids(sensors, "ultra-2002")
    assert "ultra-2002_refresh_tokens" in _balance_uids(buttons, "ultra-2002")


def test_the_connection_icon_follows_the_connection(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for entity in _entities(sensor, coordinator)}
    assert by_uid["ultra-1001_ultra_link"].icon == "mdi:web"  # "Active"
    assert by_uid["ultra-2002_ultra_link"].icon == "mdi:web-off"  # nothing reported yet
    assert [sensor.link_icon(status) for status in ("active", " Active ", "Inactive", "", None)] == [
        "mdi:web",
        "mdi:web",
        "mdi:web-off",
        "mdi:web-off",
        "mdi:web-off",
    ]


def test_token_sensors_say_when_the_balance_was_reported(coordinator: FakeCoordinator) -> None:
    by_uid = {entity.unique_id: entity for entity in _entities(sensor, coordinator)}
    tokens = by_uid["ultra-1001_ultra_call_credits"]
    assert tokens.native_value == 65 and by_uid["ultra-1001_ultra_sms_credits"].native_value == 60
    assert tokens.extra_state_attributes is None  # this report carries no time
    assert by_uid["ultra-1001_ultra_signal"].extra_state_attributes is None


# --- following a balance request ------------------------------------------------------------------------


def _follow(shown: ModuleDiagnostics | None, reads: list[ModuleDiagnostics | None]) -> tuple[int, list[float], list[ModuleDiagnostics]]:
    """Run the hub's follow-up over a scripted series of status reads: (reads made, waits, what was kept)."""
    import types

    from custom_components.mycentsys_cloud import coordinator as hub_module

    waits: list[float] = []
    kept: list[ModuleDiagnostics] = []
    pending = list(reads)

    async def read(module: Any) -> ModuleDiagnostics | None:
        return pending.pop(0)

    async def nap(seconds: float) -> None:
        waits.append(seconds)

    hub = types.SimpleNamespace(
        module=lambda key: types.SimpleNamespace(device_id=7, key=key),
        _diagnostics={"ultra-7": shown} if shown else {},
        _read_diagnostics=read,
        _keep_diagnostics=lambda key, diagnostics: kept.append(diagnostics),
        async_update_listeners=lambda: None,
    )
    real_sleep = hub_module.asyncio.sleep
    hub_module.asyncio.sleep = nap
    try:
        asyncio.run(hub_module.AccountHub._balance_follow_up(hub, "ultra-7"))
    finally:
        hub_module.asyncio.sleep = real_sleep
    return len(reads) - len(pending), waits, kept


def test_a_balance_request_is_followed_every_five_seconds_for_half_a_minute() -> None:
    old = ModuleDiagnostics(7, call_credits=5, sms_credits=5)
    made, waits, kept = _follow(old, [old] * 10)
    assert made == 6 and waits == [5] * 6 and len(kept) == 6


def test_following_stops_as_soon_as_the_balance_is_in() -> None:
    old, loading, new = ModuleDiagnostics(7, call_credits=5, sms_credits=5), ModuleDiagnostics(7, network_balance="Loading"), ModuleDiagnostics(7, call_credits=4, sms_credits=5)
    # A changed value ends it.
    assert _follow(old, [old, new, new, new])[0] == 2
    # So does the same value coming back after the module was busy fetching it.
    assert _follow(old, [loading, loading, old, old])[0] == 3
    # A balance that was already being fetched when the button was pressed counts as busy.
    assert _follow(loading, [old, old])[0] == 1
    # A read that fails is skipped, and a module never read before is done at its first answer.
    assert _follow(old, [None, None, new, new])[0] == 3
    assert _follow(None, [ModuleDiagnostics(7, network_balance="R5.00"), None])[0] == 1


def test_each_balance_button_sends_its_own_request() -> None:
    import types

    data, codes = _data()
    record = data.modules["ultra-2002"]
    record.diagnostics, record.token_sim = ModuleDiagnostics(2002, network_balance="R5.00"), False
    hub = FakeCoordinator(data, codes)
    asked: list[tuple[str, int]] = []
    followed: list[str] = []

    async def tokens(device_id: int) -> None:
        asked.append(("tokens", device_id))

    async def general(device_id: int) -> None:
        asked.append(("general", device_id))

    hub.client = types.SimpleNamespace(ultra_tokens_refresh=tokens, ultra_airtime_refresh=general)
    hub.follow_balance = followed.append
    by_uid = {entity.unique_id: entity for entity in _entities(button, hub)}
    asyncio.run(by_uid["ultra-1001_refresh_tokens"].async_press())
    asyncio.run(by_uid["ultra-2002_refresh_balance"].async_press())
    assert asked == [("tokens", 1001), ("general", 2002)]
    assert followed == ["ultra-1001", "ultra-2002"]


def _sim_kind(module_flag: bool | None, reports: list[ModuleDiagnostics]) -> list[bool | None]:
    """What the hub makes of a module's SIM after each of a series of status reports."""
    import types

    from custom_components.mycentsys_cloud import coordinator as hub_module

    module = dataclasses.replace(_module(7, "Desk", [_io(1, "Relay")]), token_sim=module_flag)
    record = ModuleRecord(module=module)
    hub = types.SimpleNamespace(
        _diagnostics={}, _token_sims={}, data=Fleet(modules={module.key: record}), _show_on_device_page=lambda key, diagnostics: None
    )
    hub._sim_kind = lambda found: hub_module.AccountHub._sim_kind(hub, found)
    kinds: list[bool | None] = []
    for report in reports:
        hub_module.AccountHub._keep_diagnostics(hub, module.key, report)
        kinds.append(record.token_sim)
    return kinds


def test_the_sim_type_set_on_the_device_decides_and_a_pending_balance_never_means_airtime() -> None:
    loading, tokens, answer, nothing = (
        ModuleDiagnostics(7, network_balance="Loading"),
        ModuleDiagnostics(7, call_credits=3, sms_credits=4),
        ModuleDiagnostics(7, network_balance="Your balance is R5.00"),
        ModuleDiagnostics(7),
    )
    # The device's own SIM type wins over whatever its status shows.
    assert _sim_kind(True, [loading, answer, nothing]) == [True, True, True]
    assert _sim_kind(False, [loading, tokens]) == [False, False]
    # Without it the status is the only clue, and a balance being fetched or missing is no clue at all.
    assert _sim_kind(None, [loading, nothing]) == [None, None]
    assert _sim_kind(None, [loading, tokens, loading, nothing]) == [None, True, True, True]
    assert _sim_kind(None, [answer, loading]) == [False, False]


def test_smart_sensor_icons_follow_the_state() -> None:
    hub = _MovableCoordinator()
    data = hub.data
    by_uid = {entity.unique_id: entity for name in ("sensor", "binary_sensor") for entity in _entities(PLATFORMS[name], hub)}
    link = data.operators[SLIDER].operator.link

    # The cloud connection: a globe, struck through without one.
    reachable = by_uid[f"{SLIDER}_reachable"]
    data.operators[SLIDER].operator = dataclasses.replace(data.operators[SLIDER].operator, link=dataclasses.replace(link, online=True, rssi=-57))
    assert reachable.icon == "mdi:web"
    assert by_uid[f"{SLIDER}_wifi_signal"].icon == "mdi:wifi-strength-3"
    data.operators[SLIDER].operator = dataclasses.replace(data.operators[SLIDER].operator, link=dataclasses.replace(link, online=False, rssi=None))
    assert reachable.icon == "mdi:web-off"
    assert by_uid[f"{SLIDER}_wifi_signal"].icon == "mdi:wifi-strength-off-outline"

    # The operator status: the gate or the door as it stands; an unclassified operator keeps the default.
    hub.positions = {SLIDER: "open", GARAGE: "open", UNKNOWN: "open"}
    assert by_uid[f"{SLIDER}_position"].icon == "mdi:gate-open"
    assert by_uid[f"{GARAGE}_position"].icon == "mdi:garage-open"
    assert by_uid[f"{UNKNOWN}_position"].icon is None
    hub.positions = {SLIDER: "closed", SWING: "partly_open"}
    assert by_uid[f"{SLIDER}_position"].icon == "mdi:gate"
    assert by_uid[f"{SWING}_position"].icon == "mdi:gate-arrow-left"
    assert by_uid[f"{GARAGE}_position"].icon is None  # position not known

    # Nothing reported yet: the plain plug, battery and shield; Health keeps its fixed icon.
    assert by_uid[f"{SLIDER}_mains"].icon == "mdi:power-plug"
    assert by_uid[f"{SLIDER}_battery"].icon == "mdi:battery"
    assert by_uid[f"{SLIDER}_tamper"].icon == "mdi:shield-home"
    assert by_uid[f"{SLIDER}_health"].icon == "mdi:heart-pulse"


def test_an_alarm_io_shows_whether_the_alarm_is_armed() -> None:
    from custom_components.mycentsys_cloud.entity import armed_by_name

    # The name of the state decides; "Alarm" is not "arm".
    assert [armed_by_name(name) for name in ("Armed", "armed away", "ARM", "Disarmed", "Unarmed", "Not armed")] == [True, True, True, False, False, False]
    assert [armed_by_name(name) for name in ("Alarm", "No alarm", "Safe", "Triggered", "On", "", None)] == [None] * 7
    # An IO named after arming is armed while it is on, whatever its states are called.
    assert armed_by_name("On", "Arm status", True) is True and armed_by_name("Off", "Arm", False) is False
    assert armed_by_name("On", "Alarm", True) is None and armed_by_name("On", "Arm", None) is None

    data, codes = _data()
    data.modules["ultra-2002"] = ModuleRecord(
        module=_module(
            2002,
            "House alarm",
            [
                _io(1, "Arm", on="Armed", off="Disarmed", visible=True),
                _io(2, "Panic"),
                _io(3, "Alarm", direction=1, on="Triggered", off="Quiet", visible=True),
                _io(4, "Alarm status", direction=1, on="Armed", off="Disarmed", visible=True),
            ],
        ),
        readings=UltraReadings(2002, states=(88, 48, 89, 88, 48, 48)),
    )
    hub = FakeCoordinator(data, codes)
    by_uid = {entity.unique_id: entity for name in ("sensor", "switch", "button") for entity in _entities(PLATFORMS[name], hub)}
    status, arm, event = by_uid["ultra-2002_input_state_4"], by_uid["ultra-2002_latch_1"], by_uid["ultra-2002_input_state_3"]
    assert status.native_value == "Armed" and status.icon == "mdi:shield-home"
    assert arm.is_on is True and arm.icon == "mdi:shield-home"
    # An input that reports the alarm sounding, and a plain output, keep the icons of their names.
    assert event.icon == "mdi:alarm-light" and by_uid["ultra-2002_output_2"].icon == "mdi:alarm-light"

    data.modules["ultra-2002"].readings = UltraReadings(2002, states=(89, 48, 89, 89, 48, 48))
    assert status.native_value == "Disarmed" and status.icon == "mdi:shield-off"
    assert arm.is_on is False and arm.icon == "mdi:shield-off"

    # Nothing read yet: the icon of the name. And on a gate, a state named "Armed" changes nothing.
    data.modules["ultra-2002"].readings = None
    assert status.icon == "mdi:alarm-light" and arm.icon == "mdi:shield-home"
    gate_input = by_uid["ultra-1001_input_state_5"]
    assert gate_input.icon == gate_input._attr_icon


def test_using_a_shared_gate_tells_the_hub_so_its_remaining_uses_are_read_again() -> None:
    import types

    data, codes = _data()
    hub = FakeCoordinator(data, codes)
    used: list[int] = []
    told: list[str] = []

    async def use_grant(grant: Any, action: Any) -> None:
        used.append(action.id)

    hub.client = types.SimpleNamespace(use_grant=use_grant)
    hub.share_was_used = lambda: told.append("used")
    by_uid = {entity.unique_id: entity for name in ("cover", "button") for entity in _entities(PLATFORMS[name], hub)}
    asyncio.run(by_uid[f"access-{GUID}-{SCOPE}_cover"].async_open_cover())
    asyncio.run(by_uid[f"access-{GUID}-{SCOPE}_granted_78"].async_press())
    assert used == [77, 78] and told == ["used", "used"]
