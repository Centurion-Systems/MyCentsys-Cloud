"""Phone numbers, datacentre regions and the broker identity."""

from __future__ import annotations

import pytest

from mycentsys_cloud_protocol import numbers, regions


def test_normalize_keeps_international_numbers() -> None:
    assert numbers.normalize("+27 82 000 0000") == "+27820000000"
    assert numbers.normalize("0027820000000") == "+27820000000"
    assert numbers.normalize("082 000 0000") == "0820000000"


def test_to_international_drops_the_trunk_prefix() -> None:
    assert numbers.to_international("082 000 0000", 27) == "+27820000000"
    assert numbers.to_international("0400 000 000", 61) == "+61400000000"
    assert numbers.to_international("+27820000000", 27) == "+27820000000"
    assert numbers.to_international("0027820000000", 27) == "+27820000000"
    assert numbers.to_international("27820000000", 27) == "+27820000000"


def test_to_international_country_specific_prefixes() -> None:
    assert numbers.to_international("01 55 0000 0000", 52) == "+525500000000"
    assert numbers.to_international("06 20 000 0000", 36) == "+36200000000"
    assert numbers.to_international("02 0000 0000", 976) == "+97600000000"
    # Italy keeps its leading zero.
    assert numbers.to_international("06 0000 0000", 39) == "+390600000000"


def test_to_international_requires_a_dial_code() -> None:
    with pytest.raises(ValueError):
        numbers.to_international("0820000000", "")


def test_same_number_compares_digits() -> None:
    assert numbers.same_number("+27 82 000 0000", "27820000000")
    assert not numbers.same_number("+27820000000", "+27820000001")
    assert not numbers.same_number("", "")
    assert numbers.digits("+27 (0)82") == "27082"


def test_regions_are_complete_and_distinct() -> None:
    assert set(regions.DATACENTRES) == {"za", "au"}
    za, au = regions.DATACENTRES["za"], regions.DATACENTRES["au"]
    for field in ("gateway_url", "gweb_url", "access_sharing_url", "broker_host"):
        assert getattr(za, field) != getattr(au, field)
        assert getattr(za, field) and getattr(au, field)
    assert za.broker_port == au.broker_port == 8880
    assert za.gateway_url.startswith("https://") and au.gateway_url.startswith("https://")


def test_region_lookup_falls_back_to_the_default() -> None:
    assert regions.region("au").key == "au"
    assert regions.region("AU").key == "au"
    assert regions.region(None).key == regions.HOME_DATACENTRE == "za"
    assert regions.region("mars").key == "za"


def test_broker_client_ids_never_equal_the_apps() -> None:
    number = "+27820000000"
    command = regions.client_id(number)
    listener = regions.client_id(number, listener=True)
    assert command == "mcr:+27820000000:mcc"
    assert listener == "mcr:+27820000000:mcc-feed"
    assert command != f"mcr:{number}" and listener != f"mcr:{number}"
    assert command != listener
