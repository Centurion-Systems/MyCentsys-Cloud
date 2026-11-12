"""The typed views of the backend documents."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from mycentsys_cloud_protocol import models
from mycentsys_cloud_protocol.errors import ModelError
from mycentsys_cloud_protocol.overview import OperatorState

# --- reading members --------------------------------------------------------------------------


def test_fields_read_members_regardless_of_case() -> None:
    fields = models.Fields({"DeviceName": "Gate", "count": "12", "On": 1, "Volts": "12.6 V", "child": {"x": 1}, "list": [1, {"a": 2}]}, "T")
    assert fields.text("devicename") == "Gate"
    assert fields.maybe_text("missing") is None
    assert fields.integer("COUNT") == 12
    assert fields.flag("on") is True
    assert fields.decimal("volts") == 12.6
    assert fields.nested("child").integer("x") == 1
    assert fields.nested("missing").integer("x") is None
    assert fields.sequence("list") == [1, {"a": 2}]
    assert [entry.integer("a") for entry in fields.records("list")] == [2]


def test_fields_are_strict_about_types_and_lenient_about_absence() -> None:
    fields = models.Fields({"n": "twelve", "b": "maybe", "t": True}, "T")
    with pytest.raises(ModelError):
        fields.integer("n")
    assert fields.loose_integer("n") is None
    with pytest.raises(ModelError):
        fields.flag("b")
    with pytest.raises(ModelError):
        fields.text("t")
    assert fields.integer("absent") is None and fields.flag("absent") is None
    with pytest.raises(ModelError):
        models.Fields(["not", "an", "object"], "T")


def test_parse_utc() -> None:
    assert models.parse_utc("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert models.parse_utc("2026-01-01T00:00:00") == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert models.parse_utc("2026-01-01T02:00:00+02:00") == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert models.parse_utc("nope") is None
    assert models.parse_utc(None) is None


# --- GatewayApi: operators ----------------------------------------------------------------------


def _listing(**overrides):
    base = {
        "productType": 3,
        "productCode": 43,
        "serialNumber": "000000000000000004211987",
        "deviceName": "Front gate",
        "lattitude": "-26.1",
        "longitude": "28.0",
        "warrantyVoid": False,
        "faultyDevice": False,
        "deviceHardware": {"coreFirmwareVersion": "4.1.0.0"},
        "macAddress": "A1:B2:C3:D4:E5:F6",
        "isWifiDevice": True,
        "deviceWiFiStatus": {"isOnline": True, "lastBackendConnectionDate": "2026-10-18T10:00:00+00:00", "wifiRssi": -61},
    }
    base.update(overrides)
    return base


def test_operator_reads_the_listing() -> None:
    operator = models.Operator.from_listing(_listing())
    assert operator.serial == "000000000000000004211987"
    assert operator.label == "Front gate"
    assert operator.product_code == 43
    assert operator.wifi_capable is True
    assert operator.mac == "A1:B2:C3:D4:E5:F6"
    assert operator.link.online is True
    assert operator.link.rssi == -61
    assert operator.link.last_contact == "2026-10-18T10:00:00+00:00"
    assert operator.firmware == "4.1.0.0"
    assert operator.latitude == "-26.1"
    assert operator.remote_linked and not operator.administered


def test_operator_family_comes_from_the_listed_product_code() -> None:
    assert models.Operator.from_listing(_listing(productCode=43)).drives == "gate"  # D10
    assert models.Operator.from_listing(_listing(productCode=39)).drives == "garage"  # SDO5
    assert models.Operator.from_listing(_listing(productCode=25)).family == "swing"  # VANTAGE
    assert models.Operator.from_listing(_listing(productCode=41)).family == "slider"  # D5 Evo
    assert models.Operator.from_listing(_listing(productCode=0)).drives is None
    bare = _listing()
    del bare["productCode"]
    assert models.Operator.from_listing(bare).product_code is None


def test_operator_tolerates_pascal_case_and_missing_optionals() -> None:
    operator = models.Operator.from_listing({"SerialNumber": "S1", "DeviceName": "Gate"})
    assert operator.serial == "S1"
    assert operator.link.online is None
    assert operator.wifi_capable is False


def test_operator_without_a_serial_or_with_a_bad_code_is_rejected() -> None:
    with pytest.raises(ModelError):
        models.Operator.from_listing({"deviceName": "Gate"})
    with pytest.raises(ModelError):
        models.Operator.from_listing(_listing(productCode="garage"))


def test_combined_listings_keep_remote_entries_first_and_merge_roles() -> None:
    remote = [models.Operator.from_listing(_listing(serialNumber="A", deviceName="remote view"))]
    admin = [
        models.Operator.from_listing(_listing(serialNumber="A", deviceName="admin view"), role=models.ROLE_ADMIN),
        models.Operator.from_listing(_listing(serialNumber="B"), role=models.ROLE_ADMIN),
    ]
    combined = models.combine_listings(remote, admin)
    assert [operator.serial for operator in combined] == ["A", "B"]
    assert combined[0].label == "remote view"
    assert combined[0].roles == {models.ROLE_REMOTE, models.ROLE_ADMIN}
    assert combined[1].administered and not combined[1].remote_linked


# --- GatewayApi: operator overview ------------------------------------------------------------------


def test_cloud_snapshot_decodes_the_enums() -> None:
    snapshot = models.CloudSnapshot.from_overview(
        {
            "operatorSerialNumber": "S1",
            "operatorStatus": 2,
            "powerSupplyStatus": 0,
            "closingBeamStatus": 3,
            "openingBeamStatus": 0,
            "theftAlarmState": 1,
            "notificationFlags": 12345678901234567890,
        }
    )
    assert snapshot.position is OperatorState.CLOSED
    assert snapshot.position_label == "closed" and snapshot.position_known
    assert snapshot.supply is models.SupplyState.NORMAL
    assert snapshot.tamper is models.TamperReport.CLEARED
    assert snapshot.flags == 12345678901234567890


def test_cloud_snapshot_unknown_values_are_unknown_not_errors() -> None:
    snapshot = models.CloudSnapshot.from_overview({"operatorSerialNumber": "S1", "operatorStatus": 99, "theftAlarmState": 7})
    assert snapshot.position is OperatorState.UNKNOWN and not snapshot.position_known
    assert snapshot.tamper is None
    assert snapshot.supply is models.SupplyState.UNKNOWN
    with pytest.raises(ModelError):
        models.CloudSnapshot.from_overview({"operatorStatus": 1})


# --- G-Web: module configuration ----------------------------------------------------------------------


def _io(number, name, *, direction=0, on="", off="", visible=False):
    return {
        "IONumber": number,
        "IOName": name,
        "IODirection": direction,
        "OnStateName": on,
        "OffStateName": off,
        "StateNameVisible": visible,
        "ButtonImageId": 1,
    }


def _module(ios, **overrides):
    base = {
        "DeviceImei": "351234567890123",
        "DeviceName": "Home",
        "DeviceOnline": 1,
        "DeviceId": 1001,
        "DeviceType": 16,
        "DeviceAdmin": 1,
        "IOCount": len(ios),
        "RefreshPermissionVersion": 7,
        "IsSharedAccess": False,
        "IOConfigs": ios,
    }
    base.update(overrides)
    return base


GATE_IOS = [
    _io(1, "Gate TRG"),
    _io(2, "Pedestrian"),
    _io(3, "Lights", on="On", off="Off", visible=True),
    _io(4, "Gate Status", direction=1, on="Open", off="Closed", visible=True),
    _io(5, "Mains", direction=1, on="Present", off="Failed", visible=True),
]


def test_channel_roles_follow_the_app() -> None:
    module = models.UltraModule.from_configuration(_module(GATE_IOS))
    assert {channel.number: channel.role for channel in module.channels} == {
        1: models.ChannelRole.MOMENTARY,
        2: models.ChannelRole.MOMENTARY,
        3: models.ChannelRole.LATCHING,
        4: models.ChannelRole.FEEDBACK,
        5: models.ChannelRole.STATUS,
    }
    assert module.channels[3].feedback_for == "gate"


def test_gate_status_on_another_io_is_a_plain_input() -> None:
    module = models.UltraModule.from_configuration(_module([_io(2, "Gate Status", direction=1, visible=True)]))
    assert module.channels[0].role == models.ChannelRole.STATUS
    assert module.feedback is None


def test_module_model_and_key() -> None:
    module = models.UltraModule.from_configuration(_module(GATE_IOS))
    assert module.key == "ultra-1001"
    assert module.model == "G-ULTRA 4G"
    assert module.online is True and module.is_admin is True
    assert module.config_version == 7
    assert module.imei == "351234567890123"


def test_gate_module_profile() -> None:
    module = models.UltraModule.from_configuration(_module(GATE_IOS))
    assert module.drives_gate
    assert module.trigger is not None and module.trigger.number == 1
    assert module.feedback is not None and module.feedback.number == 4
    assert [channel.number for channel in module.status_inputs] == [5]
    assert module.profile == models.ModuleProfile.GATE and module.profile_label == "Gate"
    assert not module.is_garage_door


def test_trigger_is_chosen_among_outputs_only() -> None:
    module = models.UltraModule.from_configuration(_module([_io(4, "Gate Status", direction=1, visible=True), _io(1, "Pulse")]))
    assert module.trigger is not None and module.trigger.number == 1


def test_door_status_or_a_door_named_output_makes_a_garage_door() -> None:
    wired = models.UltraModule.from_configuration(_module([_io(1, "Garage"), _io(4, "Door Status", direction=1, on="Open", off="Closed", visible=True)]))
    assert wired.is_garage_door and wired.profile == models.ModuleProfile.GARAGE and wired.profile_label == "Garage door"
    named = models.UltraModule.from_configuration(_module([_io(1, "GDO"), _io(2, "Light")]))
    assert named.drives_gate and named.is_garage_door
    # The status input decides, whatever the trigger output is called.
    for status in ("Garage Status", "Garage Door Status", "GARAGE STATUS"):
        garage = models.UltraModule.from_configuration(_module([_io(1, "Gate TRG"), _io(4, status, direction=1, visible=True)]))
        assert garage.feedback is not None and garage.is_garage_door and garage.profile == models.ModuleProfile.GARAGE
    gate = models.UltraModule.from_configuration(_module([_io(1, "Garage"), _io(4, "Gate Status", direction=1, visible=True)]))
    assert not gate.is_garage_door
    # Only IO 4 is the position input; "Garage" on another input is an ordinary status.
    other = models.UltraModule.from_configuration(_module([_io(1, "Gate TRG"), _io(3, "Garage Status", direction=1, visible=True)]))
    assert other.feedback is None and not other.is_garage_door


def test_alarm_module_has_a_panel_but_no_cover() -> None:
    module = models.UltraModule.from_configuration(
        _module(
            [
                _io(1, "Arm", on="Armed", off="Disarmed", visible=True),
                _io(2, "Panic"),
                _io(3, "Alarm", direction=1, on="Triggered", off="Quiet", visible=True),
            ]
        )
    )
    assert not module.drives_gate and module.trigger is None
    assert module.profile == models.ModuleProfile.ALARM
    assert module.alarm_arm is not None and module.alarm_arm.number == 1
    assert module.alarm_event is not None and module.alarm_event.number == 3


def test_alarm_panel_needs_a_latching_arm_output() -> None:
    module = models.UltraModule.from_configuration(_module([_io(1, "Arm"), _io(2, "Siren")]))
    assert module.profile == models.ModuleProfile.ALARM
    assert module.alarm_arm is None


def test_fence_and_generic_profiles() -> None:
    fence = models.UltraModule.from_configuration(_module([_io(1, "Fence alarm", on="On", off="Off", visible=True)]))
    assert fence.profile == models.ModuleProfile.FENCE and fence.profile_label == "Electric fence"
    pump = models.UltraModule.from_configuration(_module([_io(1, "Pump", on="Running", off="Stopped", visible=True)]))
    assert pump.profile == models.ModuleProfile.GENERIC and pump.profile_label == "I/O device"


def test_placeholder_io_entry_is_dropped_and_an_id_is_required() -> None:
    assert models.UltraModule.from_configuration(_module([{"IOName": "", "IONumber": 0}])).channels == ()
    with pytest.raises(ModelError):
        models.UltraModule.from_configuration(_module([], DeviceId=0))


def test_state_labels_use_the_configured_names() -> None:
    channel = models.UltraChannel.read(models.Fields(_io(3, "Lights", on="Lit", off="Dark", visible=True), "IO"))
    assert channel.state_label(88) == "Lit"
    assert channel.state_label(89) == "Dark"
    assert channel.state_label(48) is None
    bare = models.UltraChannel.read(models.Fields(_io(3, "Lights", visible=True), "IO"))
    assert (bare.on_label, bare.off_label) == ("On", "Off")


# --- G-Web: IO states -------------------------------------------------------------------------------------


def _states(*ids):
    return {"DeviceId": 1001, "RefreshPermissionVersion": 7, "IOList": [{"IOStateID": value} for value in ids]}


def test_readings_are_positional() -> None:
    readings = models.UltraReadings.from_states(1001, _states(88, 89, 48, 51, 90, 48), feedback_number=4)
    assert readings.online and readings.config_version == 7
    assert readings.is_on(1) is True
    assert readings.is_on(2) is False
    assert readings.is_on(3) is None  # 48 is a position id, not on/off
    assert readings.is_on(7) is None and readings.state_of(0) is None
    assert readings.position == "closed"


def test_position_comes_only_from_the_feedback_io() -> None:
    # Unconfigured IOs idle at 48 ("opening"); without a feedback IO there is no position.
    assert models.UltraReadings.from_states(1001, _states(48, 48, 48, 48, 48, 48)).position is None
    assert models.UltraReadings.from_states(1001, _states(48, 48, 48, 49, 48, 48), feedback_number=4).position == "open"
    assert models.UltraReadings.from_states(1001, _states(0, 0, 0, 50), feedback_number=4).position == "closing"


def test_offline_readings_know_nothing() -> None:
    readings = models.UltraReadings.offline(1001, feedback_number=4)
    assert not readings.online
    assert readings.is_on(1) is None and readings.position is None


def test_malformed_state_entries_keep_their_place() -> None:
    readings = models.UltraReadings.from_states(1001, {"IOList": [{"IOStateID": 88}, "junk", {"IOStateID": "x"}, {"IOStateID": 90}]})
    assert readings.states == (88, None, None, 90)
    assert readings.is_on(4) is True


def test_two_state_rule() -> None:
    assert models.channel_is_on(88) is True
    assert models.channel_is_on(99) is False
    assert models.channel_is_on(100) is None
    assert models.channel_is_on(None) is None


# --- G-Web: diagnostics ------------------------------------------------------------------------------------


def test_diagnostics_parse() -> None:
    diagnostics = models.ModuleDiagnostics.from_status(
        1001,
        {
            "Voltage": "12.6 V",
            "Signal": 19,
            "Firmware": "1.2.3",
            "Antenna": 1,
            "OnlineStatus": "Active",
            "Airtime": "Call: 12 SMS: 34",
            "AirtimeLastUpdated": "2026/10/18 09:00:00",
            "Number": "+27820000000",
            "ConnectionType": "4G",
            "LastSynced": "2026/10/18 09:05:00",
        },
    )
    assert diagnostics.supply_volts == 12.6
    assert diagnostics.signal == 19
    assert diagnostics.antenna == "external"
    assert diagnostics.link_status == "Active"
    assert diagnostics.network == "4G"
    assert (diagnostics.call_credits, diagnostics.sms_credits) == (12, 34)
    assert diagnostics.uses_tokens is True and diagnostics.network_balance is None and not diagnostics.balance_pending
    assert diagnostics.synced_at == "2026/10/18 09:05:00"


def test_diagnostics_without_airtime_or_signal() -> None:
    diagnostics = models.ModuleDiagnostics.from_status(1001, {"Airtime": "", "Signal": "n/a", "Antenna": 0})
    assert diagnostics.uses_tokens is None and diagnostics.network_balance is None
    assert diagnostics.signal is None
    assert diagnostics.antenna == "internal"
    assert models.ModuleDiagnostics.from_status(1, {"Antenna": "External"}).antenna == "external"
    assert models.ModuleDiagnostics.from_status(1, {"Antenna": 7}).antenna is None


# --- AccessSharingService -------------------------------------------------------------------------------------

NUMBER = "+27820000000"
OWNER = "+27820000001"
GUID = "3f2c9a10-5b7e-4d21-9c44-0a1b2c3d4e5f"

_PAYLOAD = json.dumps(
    {
        "Operator": {"Uuid": "22222222-2222-2222-2222-222222222222", "Name": "Estate gate", "ProductCode": 0},
        "Activations": [
            {"Id": 77, "Name": "OpenCloseDescription", "TriggerId": 34, "IoNumber": 1},
            {"Id": 78, "Name": "PedestrianDescription", "TriggerId": 35, "IoNumber": 2},
        ],
    }
)


def _share(**overrides):
    base = {
        "version": 1,
        "accessRevoked": False,
        "shareDeviceType": 1,
        "guid": GUID,
        "type": 1,
        "deviceName": "Estate gate",
        "startTimeUtc": "2021-03-01T00:00:00",
        "endTimeUtc": "2098-12-31T00:00:00",
        "maximumTriggerCount": 2,
        "ultraDeviceId": 555,
        "smartSerialNumber": None,
        "lastModifiedDateUtc": "2025-06-15T00:00:00",
        "serializedSharedAccessPayload": _PAYLOAD,
        "users": [{"accepted": True, "currentTriggerCount": 0, "user": {"number": NUMBER, "lastModifiedDateUtc": "2024-01-02T00:00:00"}}],
        "owner": {"name": "Owner", "user": {"number": OWNER}},
        "accessRevokedByAdministrator": False,
    }
    base.update(overrides)
    return base


def test_grant_reads_the_nested_payload() -> None:
    grant = models.AccessGrant.from_share(_share())
    assert grant.key == f"access-{GUID}"
    assert grant.is_ultra and grant.ultra_device_id == 555
    assert grant.gate_action is not None and grant.gate_action.id == 77
    assert [action.id for action in grant.extra_actions] == [78]
    assert grant.action(78).io_number == 2 and grant.action(1) is None
    assert grant.holder_modified_utc(NUMBER) == "2024-01-02T00:00:00"
    assert grant.granted_by(OWNER) and not grant.granted_by(NUMBER)
    assert grant.expires_at == datetime(2098, 12, 31, tzinfo=timezone.utc)
    assert grant.modified_utc == "2025-06-15T00:00:00"


def test_grant_state_gives_the_first_reason_it_cannot_be_used() -> None:
    state = lambda **changes: models.AccessGrant.from_share(_share(**changes)).state_for(NUMBER)  # noqa: E731
    assert state() == models.GrantState.ACTIVE
    assert state(accessRevoked=True) == models.GrantState.REVOKED
    assert state(accessRevokedByAdministrator=True) == models.GrantState.REVOKED
    assert state(endTimeUtc="2020-01-02T00:00:00") == models.GrantState.EXPIRED
    assert state(startTimeUtc="2099-01-02T00:00:00") == models.GrantState.NOT_STARTED
    assert state(users=[{"currentTriggerCount": 2, "user": {"number": NUMBER}}]) == models.GrantState.EXHAUSTED
    assert models.AccessGrant.from_share(_share()).usable_by(NUMBER)
    assert not models.AccessGrant.from_share(_share(accessRevoked=True)).usable_by(NUMBER)


def test_triggers_left_per_holder() -> None:
    depleted = models.AccessGrant.from_share(_share(users=[{"currentTriggerCount": 2, "user": {"number": NUMBER}}]))
    assert depleted.triggers_left(NUMBER) == 0
    assert depleted.triggers_left("+27820000009") == 2  # an unknown holder has used nothing
    unlimited = models.AccessGrant.from_share(_share(maximumTriggerCount=None))
    assert unlimited.triggers_left(NUMBER) is None and unlimited.usable_by(NUMBER)


def test_grant_at_a_given_moment() -> None:
    grant = models.AccessGrant.from_share(_share())
    assert grant.state_for(NUMBER, at=datetime(2019, 1, 1, tzinfo=timezone.utc)) == models.GrantState.NOT_STARTED
    assert grant.state_for(NUMBER, at=datetime(2100, 1, 1, tzinfo=timezone.utc)) == models.GrantState.EXPIRED


def test_smart_share_is_not_ultra() -> None:
    grant = models.AccessGrant.from_share(_share(shareDeviceType=2, ultraDeviceId=None, smartSerialNumber="000000000000000004211987"))
    assert not grant.is_ultra and grant.smart_serial == "000000000000000004211987"


def test_grant_without_a_guid_is_rejected_and_a_bad_payload_yields_no_actions() -> None:
    with pytest.raises(ModelError):
        models.AccessGrant.from_share(_share(guid=""))
    grant = models.AccessGrant.from_share(_share(serializedSharedAccessPayload="{not json"))
    assert grant.actions == () and grant.gate_action is None
    nested = models.AccessGrant.from_share(_share(serializedSharedAccessPayload=json.loads(_PAYLOAD)))
    assert len(nested.actions) == 2


# --- GatewayApi: certificate ------------------------------------------------------------------------------------


def test_client_bundle_never_shows_its_material() -> None:
    bundle = models.ClientBundle(pfx=b"secret-bytes", password="hunter2")
    assert "secret" not in repr(bundle)
    assert "hunter2" not in repr(bundle)


def test_a_sim_without_tokens_reports_what_its_network_answered() -> None:
    answer = "Your balance is R12.50.\nValid until 2026/12/01\n1) Buy bundles\n2) Recharge"
    diagnostics = models.ModuleDiagnostics.from_status(7, {"Airtime": answer, "AirtimeLastUpdated": "2026/10/18 09:00:00"})
    assert diagnostics.network_balance == "Your balance is R12.50. Valid until 2026/12/01"
    assert diagnostics.uses_tokens is False and not diagnostics.balance_pending
    assert (diagnostics.call_credits, diagnostics.sms_credits) == (None, None)
    # The menu can also follow on the same line, and line breaks can arrive spelled out.
    assert models.network_reply("Bal R5.00 1) Recharge 2) Bundles") == "Bal R5.00"
    assert models.network_reply("Bal R5.00\\n1) Recharge") == "Bal R5.00"
    assert models.network_reply("   ") is None
    assert len(models.network_reply("x" * 400)) == 255


def test_a_balance_that_is_still_being_fetched_says_nothing_about_the_sim() -> None:
    for text in ("Loading", "loading...", " LOADING "):
        diagnostics = models.ModuleDiagnostics.from_status(7, {"Airtime": text})
        assert diagnostics.balance_pending and diagnostics.uses_tokens is None, text
    arrived = models.ModuleDiagnostics.from_status(7, {"Airtime": "Call: 3 SMS: 4"})
    assert arrived.balance_mark != models.ModuleDiagnostics.from_status(7, {"Airtime": "Loading"}).balance_mark
    assert arrived.balance_mark == models.ModuleDiagnostics.from_status(7, {"Airtime": "Call: 3 SMS: 4"}).balance_mark


def test_the_configuration_says_which_sim_a_module_has() -> None:
    def module(**extra: Any) -> models.UltraModule:
        return models.UltraModule.from_configuration({**_module(GATE_IOS), **extra})

    assert module(SimType=0).token_sim is True
    assert module(SimType=1).token_sim is False and module(SimType="2").token_sim is False
    # A configuration that does not carry the type, or carries "not reported", leaves it open.
    assert module().token_sim is None and module(SimType=-1).token_sim is None and module(SimType="").token_sim is None
