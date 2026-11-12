"""What the sign-in and device forms accept, and what they store."""

from __future__ import annotations

import pytest

from ha_stub import load_integration

load_integration()

from custom_components.mycentsys_cloud import config_flow as flow  # noqa: E402


@pytest.mark.parametrize(
    ("typed", "accepted"),
    [
        ("0820000000", "0820000000"),
        ("  820000000 ", "820000000"),
        ("27820000000", "27820000000"),
        ("082 000 0000", None),
        ("+27820000000", None),
        ("082-000-0000", None),
        ("o820000000", None),
        ("", None),
        (None, None),
    ],
)
def test_the_number_field_takes_digits_only(typed, accepted) -> None:
    assert flow.digits_only(typed) == accepted


def test_the_number_is_joined_to_the_country_code() -> None:
    assert flow._international("0820000000", "ZA") == "+27820000000"
    assert flow._international("820000000", "ZA") == "+27820000000"
    assert flow._international("0400000000", "AU") == "+61400000000"
    assert flow._international("123", "ZA") == ""  # too short to be a number
    assert flow._international("0820000000", "??") == ""


def test_the_datacentre_follows_the_country_code() -> None:
    assert flow.datacentre_for_country("AU") == "au"
    assert flow.datacentre_for_country("NZ") == "au"
    assert flow.datacentre_for_country("ZA") == "za"
    assert flow.datacentre_for_country("GB") == "za"


def test_a_scope_is_stored_and_the_list_only_counts_when_choosing() -> None:
    assert flow.device_choice({"device_scope": "all", "devices": ["S1"]}) == ({"device_scope": "all", "devices": []}, None)
    assert flow.device_choice({"device_scope": "smart"}) == ({"device_scope": "smart", "devices": []}, None)
    assert flow.device_choice({"device_scope": "ultra"}) == ({"device_scope": "ultra", "devices": []}, None)
    assert flow.device_choice({"device_scope": "chosen", "devices": ["S1", "ultra-9"]}) == ({"device_scope": "chosen", "devices": ["S1", "ultra-9"]}, None)
    assert flow.device_choice({}) == ({"device_scope": "all", "devices": []}, None)


def test_sign_in_opens_on_the_devices_the_user_chooses() -> None:
    assert flow.SIGN_IN_DEVICE_SCOPE == "chosen"
    fields = flow._devices_fields({"S1": "Gate (SMART)"}, flow.SIGN_IN_DEVICE_SCOPE, [])
    scope = next(marker for marker in fields if (marker.schema if hasattr(marker, "schema") else marker.key) == "device_scope").default
    assert (scope() if callable(scope) else scope) == "chosen"


def test_choosing_devices_needs_at_least_one() -> None:
    options, failure = flow.device_choice({"device_scope": "chosen", "devices": []})
    assert failure == "nothing_chosen" and options["devices"] == []


def test_the_forms_ask_for_nothing_but_what_is_needed() -> None:
    account = flow._account_form().schema
    assert [marker.schema if hasattr(marker, "schema") else marker.key for marker in account] == ["country", "account_number", "pin_delivery"]
    devices = flow._devices_fields({"S1": "Gate (SMART)", "ultra-9": "Shed (I/O module)"}, "chosen", ["ultra-9", "gone"])
    assert [marker.schema if hasattr(marker, "schema") else marker.key for marker in devices] == ["device_scope", "devices"]
    chosen_default = next(marker for marker in devices if (marker.schema if hasattr(marker, "schema") else marker.key) == "devices").default
    assert (chosen_default() if callable(chosen_default) else chosen_default) == ["ultra-9"]
