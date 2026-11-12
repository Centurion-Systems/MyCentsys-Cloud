"""The fleet records the hub publishes and the entities read."""

from __future__ import annotations

from ha_stub import load_integration

load_integration()

from custom_components.mycentsys_cloud.cloud.models import AccessGrant, ModuleDiagnostics, Operator, UltraModule  # noqa: E402
from custom_components.mycentsys_cloud.fleet import Fleet, GrantRecord, ModuleRecord, OperatorRecord, entry_title, firmware_line, hardware_line, scope_of, spaced_number  # noqa: E402


def _operator(serial: str, code: int | None = None) -> OperatorRecord:
    raw = {"serialNumber": serial, "deviceName": f"Gate {serial}"}
    if code:
        raw["productCode"] = code
    return OperatorRecord(Operator.from_listing(raw))


def _fleet() -> Fleet:
    module = UltraModule.from_configuration({"DeviceId": 9, "DeviceName": "Shed", "IOConfigs": [{"IONumber": 1, "IOName": "Pump"}]})
    grant = AccessGrant.from_share({"guid": "g-1", "deviceName": "Estate"})
    return Fleet(
        operators={"S1": _operator("S1", 41), "S2": _operator("S2")},
        modules={"ultra-9": ModuleRecord(module)},
        grants={"shared-g-1": GrantRecord(grant)},
    )


def test_operator_family_prefers_what_the_operator_reported() -> None:
    record = _operator("S1", 41)  # listed as a D5 Evo slider
    assert record.family == "slider" and record.is_gate and not record.is_garage
    record.reported_code = 39  # ...but it broadcast itself as an SDO5
    assert record.family == "garage" and record.is_garage and not record.is_gate
    unknown = _operator("S2")
    assert unknown.family is None and not unknown.is_gate and not unknown.is_garage


def test_fleet_keys_labels_and_membership() -> None:
    fleet = _fleet()
    assert len(fleet) == 4
    assert "ultra-9" in fleet and "shared-g-1" in fleet and "nope" not in fleet
    assert list(fleet.records()) == ["S1", "S2", "ultra-9", "shared-g-1"]
    assert fleet.labels() == {"S1": "Gate S1 (SMART)", "S2": "Gate S2 (SMART)", "ultra-9": "Shed (I/O device)", "shared-g-1": "Estate (shared)"}


def test_a_scope_keeps_one_kind_of_device_or_the_chosen_keys() -> None:
    fleet = _fleet()
    assert fleet.within("all") is fleet
    assert fleet.within("all", ["S2"]) is fleet  # keys only count for "chosen"
    assert fleet.within("chosen", None) is fleet
    assert fleet.within("chosen", []) is fleet
    chosen = fleet.within("chosen", ["S2", "shared-g-1", "gone"])
    assert list(chosen.records()) == ["S2", "shared-g-1"]
    assert chosen.modules == {}
    # A share of a SMART gate goes with the SMART operators; this one names no module.
    assert list(fleet.within("smart").records()) == ["S1", "S2", "shared-g-1"]
    assert list(fleet.within("ultra").records()) == ["ultra-9"]


def test_the_stored_scope_and_what_older_options_mean() -> None:
    assert scope_of({}) == "all" and scope_of(None) == "all"
    assert scope_of({"devices": ["S1"]}) == "chosen"  # a bare list of keys
    assert scope_of({"device_scope": "ultra", "devices": ["S1"]}) == "ultra"
    assert scope_of({"device_scope": "nonsense"}) == "all"


def test_an_entry_is_named_after_its_number() -> None:
    assert entry_title("+27820000000") == "MyCentsys Devices: +27820000000"
    assert entry_title("") == "MyCentsys Devices"
    assert entry_title(None) == "MyCentsys Devices"


def test_device_page_lines_of_a_module() -> None:
    full = ModuleDiagnostics(9, number="27820000000", firmware="1.5.1", network="4G", antenna="external")
    assert firmware_line(full) == "1.5.1; Number: +27 82 000 0000"
    assert hardware_line(full) == "Antenna: External; Connection: 4G"
    assert firmware_line(None) is None and hardware_line(None) is None
    assert firmware_line(ModuleDiagnostics(9)) is None and hardware_line(ModuleDiagnostics(9)) is None
    assert firmware_line(ModuleDiagnostics(9, firmware="1.5.1")) == "1.5.1"
    assert firmware_line(ModuleDiagnostics(9, number="27820000000")) == "Number: +27 82 000 0000"
    assert hardware_line(ModuleDiagnostics(9, network="4G")) == "Connection: 4G"
    assert hardware_line(ModuleDiagnostics(9, number="27820000000")) is None  # the number is on the firmware line


def test_numbers_are_shown_with_their_calling_code_and_in_groups() -> None:
    assert spaced_number("27820000000") == "+27 82 000 0000"
    assert spaced_number("+27 82 000 0000") == "+27 82 000 0000"
    assert spaced_number("61400000000") == "+61 400 000 000"
    assert spaced_number("12020000000") == "+1 202 000 0000"
    assert spaced_number("447700900123") == "+44 770 090 0123"
    assert spaced_number("") is None and spaced_number(None) is None


def test_an_operator_names_both_firmware_versions_unless_it_is_a_garage_door() -> None:
    raw = {"serialNumber": "S9", "productCode": 41, "deviceHardware": {"coreFirmwareVersion": "4.1.0.0", "bluetoothFirmwareVersion": "2.0.3.0"}}
    gate = OperatorRecord(Operator.from_listing(raw))
    assert gate.firmware == "Core: 4.1.0.0; Wireless: 2.0.3.0"
    gate.reported_code = 39  # it broadcast itself as a garage door operator
    assert gate.firmware == "4.1.0.0"
    core_only = OperatorRecord(Operator.from_listing({"serialNumber": "S8", "deviceHardware": {"coreFirmwareVersion": "3.0.0.0"}}))
    assert core_only.firmware == "3.0.0.0"
    assert OperatorRecord(Operator.from_listing({"serialNumber": "S7"})).firmware is None
