"""The generated country table and the datacentre it selects."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "custom_components" / "mycentsys_cloud" / "calling_codes.py"
_spec = importlib.util.spec_from_file_location("mycentsys_calling_codes", _SRC)
assert _spec and _spec.loader
calling_codes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calling_codes)


def test_australia_and_new_zealand_use_the_au_datacentre() -> None:
    assert calling_codes.datacentre_for_country("AU") == "au"
    assert calling_codes.datacentre_for_country("NZ") == "au"
    assert calling_codes.datacentre_for_country(" au ") == "au"


def test_every_other_country_uses_south_africa() -> None:
    assert calling_codes.datacentre_for_country("ZA") == "za"
    assert calling_codes.datacentre_for_country("GB") == "za"
    assert calling_codes.datacentre_for_country(calling_codes.HOME_COUNTRY) == "za"
    assert calling_codes.datacentre_for_country(None) == "za"
    assert calling_codes.datacentre_for_country("") == "za"


def test_table_is_generated_well_formed() -> None:
    codes = [entry.code for entry in calling_codes.CALLING_CODES]
    assert len(codes) == len(set(codes)) > 200
    assert all(len(code) == 2 and code.isupper() for code in codes)
    assert all(entry.dial > 0 and entry.name for entry in calling_codes.CALLING_CODES)
    names = [entry.name for entry in calling_codes.CALLING_CODES]
    assert names[0] < names[-1]  # alphabetical by name
    assert ("ZA", "South Africa", 27) in calling_codes.CALLING_CODES


def test_lookup_by_code() -> None:
    assert calling_codes.country("za") == calling_codes.Country("ZA", "South Africa", 27)
    assert calling_codes.country("NZ").dial == 64
    assert calling_codes.country("XK").name == "Kosovo"
    assert calling_codes.country("??") is None
    assert calling_codes.country(None) is None
