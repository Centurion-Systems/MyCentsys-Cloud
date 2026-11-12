"""The operator's status broadcast mapped onto the entities' vocabularies."""

from __future__ import annotations

from ha_stub import load_integration

load_integration()

from custom_components.mycentsys_cloud import telemetry  # noqa: E402
from custom_components.mycentsys_cloud.cloud import overview as ov  # noqa: E402
from custom_components.mycentsys_cloud.cloud.models import CloudSnapshot, TamperReport  # noqa: E402


def _flag_bits(family: str, *keys: str) -> int:
    table = {ov.FAMILY_SLIDER: ov._SLIDER_FLAGS, ov.FAMILY_SWING: ov._SWING_FLAGS, ov.FAMILY_GARAGE: ov._GARAGE_FLAGS}[family]
    value = 0
    for bit, (key, _label, _info) in table.items():
        if key in keys:
            value |= 1 << bit
    return value


def _overview(family: str = ov.FAMILY_SLIDER, *, state=ov.OperatorState.CLOSED, flags=(), mains=ov.MainsStatus.NORMAL, xmr=18, temperature=25) -> ov.Overview:
    table = {ov.FAMILY_SLIDER: ov._SLIDER_FLAGS, ov.FAMILY_SWING: ov._SWING_FLAGS, ov.FAMILY_GARAGE: ov._GARAGE_FLAGS}[family]
    return ov.Overview(
        family=family,
        layout="test",
        state=state,
        raw_state=0,
        battery_raw=2760,
        battery_volts=27.6,
        input_voltage_raw=None,
        temperature_raw=temperature,
        mains=mains,
        raw_power=0,
        opening_beam_raw=None,
        closing_beam_raw=1,
        xmr_raw=None if family == ov.FAMILY_SWING else xmr,
        notification_flags=_flag_bits(family, *flags),
        condition_flags=0,
        seconds_remaining=0,
        timer_flags=0,
        _flags=table,
    )


def test_position_vocabulary_matches_the_sensor_options() -> None:
    assert telemetry.position_label(_overview(state=ov.OperatorState.OPENING)) == "opening"
    assert telemetry.position_label(_overview(ov.FAMILY_GARAGE, state=ov.OperatorState.LEARNING)) == "learning"
    assert telemetry.position_label(_overview(state=ov.OperatorState.LOST)) == "position_lost"
    assert telemetry.position_label(_overview(state=ov.OperatorState.UNKNOWN)) is None
    assert telemetry.position_label(None) is None
    for label in ("open", "closed", "partly_open", "partly_closed", "opening", "closing", "learning", "position_lost"):
        assert label in telemetry.POSITION_OPTIONS


def test_cloud_position() -> None:
    assert telemetry.cloud_position_label(CloudSnapshot("S", position=ov.OperatorState.CLOSED)) == "closed"
    assert telemetry.cloud_position_label(CloudSnapshot("S")) is None


def test_mains_tile() -> None:
    assert telemetry.power_label(_overview()) == "normal"
    assert telemetry.power_label(_overview(mains=ov.MainsStatus.LOW)) == "low"
    assert telemetry.power_label(_overview(flags=("mains_failed",))) == "off"
    assert telemetry.power_label(_overview(ov.FAMILY_GARAGE, mains=ov.MainsStatus.OFF)) == "off"
    assert telemetry.power_label(_overview(mains=ov.MainsStatus.COMMS_OFF)) == "unknown"
    assert telemetry.power_label(None) is None
    assert set(("normal", "low", "off", "unknown")) <= set(telemetry.POWER_OPTIONS)


def test_health_and_problems() -> None:
    healthy = _overview()
    assert telemetry.health_label(healthy) == "good"
    assert telemetry.problems(healthy) == []
    stalled = _overview(flags=("gate_stalled", "holiday_lock"))
    assert telemetry.health_label(stalled) == "attention"
    assert telemetry.problems(stalled) == ["Gate stalled"]  # the mode is not a fault


def test_tamper_tile() -> None:
    assert telemetry.tamper_label(_overview(xmr=17)) == "armed"
    assert telemetry.tamper_label(_overview(xmr=18)) == "disarmed"
    assert telemetry.tamper_label(_overview(xmr=2)) == "disabled"
    assert telemetry.tamper_label(_overview(xmr=0)) == "unknown"
    assert telemetry.tamper_label(_overview(xmr=18, flags=("tamper_alarm",))) == "triggered"
    assert telemetry.tamper_label(_overview(ov.FAMILY_SWING)) == "disabled"
    assert telemetry.tamper_armed(_overview(xmr=17)) is True
    assert telemetry.tamper_armed(_overview(xmr=18)) is False
    assert telemetry.tamper_armed(_overview(xmr=18, flags=("tamper_alarm",))) is True
    assert telemetry.tamper_armed(_overview(ov.FAMILY_SWING)) is None
    assert telemetry.cloud_tamper_label(CloudSnapshot("S", tamper=TamperReport.ACTIVATED)) == "triggered"
    assert telemetry.cloud_tamper_label(CloudSnapshot("S", tamper=TamperReport.CLEARED)) == "disarmed"
    assert telemetry.cloud_tamper_label(CloudSnapshot("S")) is None


# --- icons that follow the state ----------------------------------------------------------------------


def test_the_connection_shows_a_globe_struck_through_without_one() -> None:
    assert telemetry.connection_icon(True) == "mdi:web"
    assert telemetry.connection_icon(False) == "mdi:web-off" and telemetry.connection_icon(None) == "mdi:web-off"


def test_the_battery_icon_shows_a_low_faulty_or_missing_battery() -> None:
    assert telemetry.battery_icon(None) == "mdi:battery"
    assert telemetry.battery_icon(_overview()) == "mdi:battery"
    assert telemetry.battery_icon(_overview(flags=("battery_low",))) == "mdi:battery-low"
    assert telemetry.battery_icon(_overview(flags=("replace_battery",))) == "mdi:battery-alert"
    assert telemetry.battery_icon(_overview(flags=("battery_usage",))) == "mdi:battery"  # running on it is not a fault


def test_the_plug_is_struck_through_while_the_mains_is_off() -> None:
    assert [telemetry.power_icon(label) for label in ("normal", "low", "off", "unknown", None)] == [
        "mdi:power-plug",
        "mdi:power-plug",
        "mdi:power-plug-off",
        "mdi:power-plug",
        "mdi:power-plug",
    ]


def test_the_operator_status_shows_the_gate_or_the_door_as_it_stands() -> None:
    def gate(label: str) -> str | None:
        return telemetry.position_icon(label, gate=True, garage=False)

    def door(label: str) -> str | None:
        return telemetry.position_icon(label, gate=False, garage=True)

    assert (gate("open"), gate("closed")) == ("mdi:gate-open", "mdi:gate")
    assert gate("partly_open") == gate("partly_closed") == gate("opening") == "mdi:gate-arrow-left"
    assert gate("closing") == "mdi:gate-arrow-right"
    assert gate("position_lost") == gate("learning") == "mdi:gate-alert"
    assert (door("open"), door("closed"), door("partly_open"), door("position_lost")) == ("mdi:garage-open", "mdi:garage", "mdi:garage-open", "mdi:garage-alert")
    # An unknown position, and an operator that is neither yet, keep Home Assistant's icon.
    assert gate("unknown") is None and door("unknown") is None
    assert telemetry.position_icon("open", gate=False, garage=False) is None
    # Every position a gate can report is either pictured or deliberately left alone.
    assert {label for label in telemetry.POSITION_OPTIONS if gate(label) is None} == {"unknown"}


def test_wifi_bars_follow_the_signal_strength() -> None:
    assert [telemetry.wifi_icon(rssi) for rssi in (-40, -50, -57, -60, -65, -75, -80, -81, -95)] == [
        "mdi:wifi-strength-4",
        "mdi:wifi-strength-4",
        "mdi:wifi-strength-3",
        "mdi:wifi-strength-3",
        "mdi:wifi-strength-2",
        "mdi:wifi-strength-1",
        "mdi:wifi-strength-1",
        "mdi:wifi-strength-outline",
        "mdi:wifi-strength-outline",
    ]
    assert telemetry.wifi_icon(None) == "mdi:wifi-strength-off-outline" and telemetry.wifi_icon("n/a") == "mdi:wifi-strength-off-outline"


def test_the_shield_is_struck_through_while_the_tamper_alarm_is_off() -> None:
    assert [telemetry.tamper_icon(label) for label in ("armed", "triggered", "disarmed", "disabled", "unknown", None)] == [
        "mdi:shield-home",
        "mdi:shield-home",
        "mdi:shield-off",
        "mdi:shield-off",
        "mdi:shield-home",
        "mdi:shield-home",
    ]
