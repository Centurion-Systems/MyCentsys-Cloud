"""Status overview decoding for sliders, swings and garage door operators.

Fixtures are built field by field from the operators' struct layouts, so a
layout mistake in the decoder shows up as a wrong named field here.
"""

from __future__ import annotations

import struct

import pytest

from mycentsys_cloud_protocol import overview as ov


def slider_v2(
    *, battery=1350, flags1=0, flags2=0, condition=0, gate=1, irbo=3, irbc=8, xmr=17, power=0, input_mv=13800
) -> bytes:
    return struct.pack(
        "<HBBIIIIHBBBBBBBBHHH",
        battery, 0, 25, flags1, flags2, condition, 0, 0, gate, irbo, irbc, xmr, power, 1, 2, 0, 0, 0, input_mv
    )


def slider_v3(*, flags3=0, lock=1, **kw) -> bytes:
    body = slider_v2(**kw)
    # V3: byte 2 is a pad, byte 29 (futureUse1 in V2) is the gate lock, then flags3 and 24 spare bytes.
    body = body[:29] + bytes([lock]) + body[30:]
    return body + struct.pack("<I", flags3) + bytes(24)


def swing_v1(*, battery=2710, flags1=0, flags2=0, condition=0, gate=4, power=1, double=0xFF, input_mv=27500) -> bytes:
    motor = struct.pack("<BBhi", gate, 0, 100, 2000)
    return (
        struct.pack("<BBHIIII", 1, 30, battery, flags1, flags2, condition, 12)
        + motor
        + motor
        + struct.pack("<BBBBHBBBBHHH", gate, 2, 7, 1, 0, power, 1, 2, double, 0, 0, input_mv)
    )


def swing_v2(*, flags3=0, **kw) -> bytes:
    return swing_v1(**kw) + struct.pack("<I", flags3) + bytes(8)


def garage_v3(*, flags=0, battery=245, condition=0, door=3, irbc=1, xmr=18, power=0) -> bytes:
    return struct.pack("<IHBBHBBBBBBBBBBHH", flags, battery, condition, 0, 0, 0, 0, door, irbc, xmr, power, 1, 2, 0, 0, 0, 0)


def garage_v4(*, flags2=0, **kw) -> bytes:
    return garage_v3(**kw) + struct.pack("<I", flags2) + bytes(16)


def test_layout_sizes_match_the_operators() -> None:
    assert len(slider_v2()) == 36
    assert len(slider_v3()) == 64
    assert len(swing_v1()) == 52
    assert len(swing_v2()) == 64
    assert len(garage_v3()) == 24
    assert len(garage_v4()) == 44


def test_slider_fields_and_derived_states() -> None:
    decoded = ov.decode_overview(slider_v2(flags1=(1 << 1) | (1 << 7), flags2=1 << (39 - 32)), family="slider", firmware="1.0.0.0")
    assert decoded.layout == "slider_v2"
    assert decoded.state is ov.OperatorState.CLOSED
    assert decoded.battery_volts == 13.5
    assert decoded.input_volts == 13.8
    assert decoded.mains is ov.MainsStatus.NORMAL
    assert decoded.mains_failed  # flag bit 1
    assert decoded.on_battery
    assert decoded.tamper is ov.TamperState.ARMED
    assert decoded.opening_beam == "clear"
    assert decoded.closing_beam == "obstructed"
    assert decoded.holiday_lock
    assert decoded.active_flags == ["mains_failed", "collision", "holiday_lock"]
    assert decoded.problems == ["Collision"]  # mains state is information, the power tile reports it
    assert not decoded.healthy
    assert decoded.battery is ov.BatteryStatus.GOOD


def test_slider_v3_selected_by_firmware_and_reads_third_flag_word() -> None:
    payload = slider_v3(flags3=1 << (66 - 64), lock=2, gate=4, xmr=18)
    decoded = ov.decode_overview(payload, family="slider", firmware="2.1.0.0")
    assert decoded.layout == "slider_v3"
    assert decoded.state is ov.OperatorState.OPENING and decoded.state.is_moving
    assert decoded.gate_lock_raw == 2
    assert decoded.tamper is ov.TamperState.DISARMED
    assert "siren_tamper_triggered" in decoded.active_flags
    # Relayed siren flags are notices, not operator faults, as in the app's health view.
    assert "Siren tamper alarm triggered" in decoded.notices and "Siren tamper alarm triggered" not in decoded.problems


def test_d10_uses_the_old_layout_below_4_1() -> None:
    # D10 (DX_CORE_PLUS, external code 43) moved to the V3 layout at 4.1, later than other sliders.
    assert ov.decode_overview(slider_v2(), family="slider", firmware="2.5.0.0", external_code=43).layout == "slider_v2"
    assert ov.decode_overview(slider_v3(), family="slider", firmware="4.1.0.0", external_code=43).layout == "slider_v3"
    assert ov.decode_overview(slider_v3(), family="slider", firmware="2.5.0.0", external_code=41).layout == "slider_v3"


def test_layout_falls_back_to_payload_length() -> None:
    assert ov.decode_overview(slider_v3(), family="slider").layout == "slider_v3"
    assert ov.decode_overview(slider_v2(), family="slider").layout == "slider_v2"
    # Firmware claims the new layout but the bytes are the old size: read what fits.
    assert ov.decode_overview(slider_v2(), family="slider", firmware="9.0").layout == "slider_v2"
    with pytest.raises(ov.OverviewError):
        ov.decode_overview(b"\x00" * 10, family="slider")
    with pytest.raises(ov.OverviewError):
        ov.decode_overview(slider_v2(), family="toaster")


def test_swing_fields() -> None:
    decoded = ov.decode_overview(swing_v2(flags1=(1 << 0) | (1 << 3), flags2=1 << (39 - 32), flags3=1 << (41 - 64 + 32)), family="swing", firmware="2.1")
    assert decoded.layout == "swing_v2"
    assert decoded.state is ov.OperatorState.OPENING
    assert decoded.battery_volts == 27.1
    assert decoded.mains is ov.MainsStatus.LOW
    assert decoded.mains_failed
    assert decoded.battery is ov.BatteryStatus.LOW
    assert decoded.is_double_gate is True
    assert decoded.gate_lock_raw == 1
    assert decoded.tamper is ov.TamperState.UNKNOWN  # swings have no tamper input
    assert decoded.keep_open
    assert decoded.opening_beam == "clear" and decoded.closing_beam == "obstructed"
    assert "Battery low" not in decoded.problems and "Keep open on" not in decoded.problems


def test_swing_v1_layout() -> None:
    decoded = ov.decode_overview(swing_v1(), family="swing", firmware="1.4")
    assert decoded.layout == "swing_v1"
    assert decoded.input_volts == 27.5


def test_garage_fields_and_scaling() -> None:
    decoded = ov.decode_overview(garage_v4(flags=(1 << 9) | (1 << 14) | (1 << 16), condition=0b111, flags2=1 << (44 - 32)), family="garage", firmware="1.3.0.0")
    assert decoded.layout == "garage_v4"
    assert decoded.state is ov.OperatorState.CLOSED
    assert decoded.is_garage
    assert decoded.battery_volts == 24.5  # garage reports tenths of a volt
    assert decoded.input_volts is None
    assert decoded.opening_beam is None and decoded.closing_beam == "idle"
    assert decoded.battery is ov.BatteryStatus.LOW
    assert decoded.tamper is ov.TamperState.DISARMED
    assert decoded.tamper_triggered
    assert decoded.mains_failed
    assert decoded.holiday_lock and decoded.autoclose_override and decoded.standing_open
    assert "siren_battery_critical" in decoded.active_flags
    assert decoded.problems == ["Tamper alarm triggered"]
    assert "Siren battery critical" in decoded.notices and "Mains disconnected" in decoded.notices


def test_garage_mains_off_and_no_batteries() -> None:
    decoded = ov.decode_overview(garage_v3(power=3, flags=1 << 23, door=6), family="garage", firmware="1.0")
    assert decoded.layout == "garage_v3"
    assert decoded.mains is ov.MainsStatus.OFF and decoded.mains_failed
    assert decoded.battery is ov.BatteryStatus.NONE
    assert decoded.state is ov.OperatorState.LEARNING


def test_unknown_flag_bits_are_reported_not_dropped() -> None:
    decoded = ov.decode_overview(slider_v2(flags1=1 << 29, flags2=1 << (57 - 32)), family="slider")
    assert decoded.unknown_flags == [57]
    assert "origin_sensor" in decoded.active_flags


def test_beam_condition_table() -> None:
    assert ov.beam_condition(0) == "disabled"
    assert ov.beam_condition(1) == "idle"
    assert ov.beam_condition(3) == "clear"
    assert ov.beam_condition(8) == "obstructed"
    assert ov.beam_condition(13) == "not_connected"
    assert ov.beam_condition(18) == "wiring_error"
    assert ov.beam_condition(22) == "sleep"
    assert ov.beam_condition(99) is None
    assert ov.beam_condition(None) is None


def test_firmware_version_parsing() -> None:
    assert ov.parse_firmware_version("4.1.0.0") == (4, 1)
    assert ov.parse_firmware_version("2") == (2, 0)
    assert ov.parse_firmware_version("") is None
    assert ov.parse_firmware_version("beta") is None


def test_overview_message_decoding() -> None:
    props = [("ClientId", "AB12CD34EF56"), ("PC", "43"), ("FW", "4.1.0.0"), ("Name", "Front gate"), ("MAC", "A1:B2:C3:D4:E5:F6"), ("SysConfig", "2")]
    announcement, decoded = ov.decode_overview_message(props, b"\x00\x00\x00\x00" + slider_v3())
    assert announcement.serial_number == "AB12CD34EF56"
    assert announcement.product_code == 43
    assert announcement.firmware_version == "4.1.0.0"
    assert announcement.name == "Front gate"
    assert announcement.mac_address == "A1:B2:C3:D4:E5:F6"
    assert announcement.sys_config == 2
    assert decoded is not None and decoded.layout == "slider_v3"


def test_overview_message_without_decodable_body_still_announces() -> None:
    props = [("ClientId", "AB12CD34EF56"), ("PC", "43"), ("FW", "4.1.0.0")]
    announcement, decoded = ov.decode_overview_message(props, b"\x00\x00\x00\x00\x01")
    assert announcement.serial_number == "AB12CD34EF56"
    assert decoded is None
    # Unknown product: announced, not decoded.
    _, decoded = ov.decode_overview_message([("ClientId", "X"), ("PC", "11")], b"\x00" * 40)
    assert decoded is None
    with pytest.raises(ov.OverviewError):
        ov.decode_overview_message([("PC", "43")], b"\x00" * 40)


def test_beam_conditions_never_count_as_faults() -> None:
    # Beam states and beam self-tests, on every family, stay out of the health roll-up.
    slider = ov.decode_overview(slider_v2(irbo=8, irbc=22, flags1=(1 << 19) | (1 << 20), flags2=(1 << (36 - 32)) | (1 << (40 - 32))), family="slider")
    assert slider.problems == [] and slider.opening_beam == "obstructed" and slider.closing_beam == "sleep"
    swing = ov.decode_overview(swing_v1(flags2=(1 << (32 - 32)) | (1 << (33 - 32)) | (1 << (36 - 32))), family="swing")
    assert swing.problems == []
    garage = ov.decode_overview(garage_v3(irbc=0, flags=(1 << 17) | (1 << 18)), family="garage")
    assert garage.problems == [] and garage.closing_beam == "disabled"
    assert "Beams error" in garage.notices


def test_disconnected_or_miswired_beams_are_faults() -> None:
    slider = ov.decode_overview(slider_v2(irbo=13, irbc=18), family="slider")
    assert slider.problems == ["Opening beam not connected", "Closing beam wiring error"]
    garage = ov.decode_overview(garage_v3(irbc=13), family="garage")
    assert garage.problems == ["Closing beam not connected"]
    assert ov.decode_overview(garage_v3(irbc=3), family="garage").problems == []
