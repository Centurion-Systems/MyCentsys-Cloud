"""Product codes and operator families.

Operators identify themselves with a product code in two numberings:

* the *external* code, which is what the cloud device listing and the live
  telemetry headers carry;
* the *internal* code the firmware libraries use. The two agree up to 0x1B;
  above that the internal list has two extra entries, so every later external
  code maps to internal code + 2.

Only the SMART "Plus" operators speak the cloud remote protocol. They fall into
three families that matter to a user: sliders, swings and garage door
operators (plus the wireless siren accessory).
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "FAMILY_SLIDER",
    "FAMILY_SWING",
    "FAMILY_GARAGE",
    "FAMILY_SIREN",
    "FAMILIES",
    "to_internal_code",
    "family_for_code",
    "family_for_internal_code",
    "is_gate_family",
    "product_name",
]

FAMILY_SLIDER: Final = "slider"
FAMILY_SWING: Final = "swing"
FAMILY_GARAGE: Final = "garage"
FAMILY_SIREN: Final = "siren"
FAMILIES: Final = (FAMILY_SLIDER, FAMILY_SWING, FAMILY_GARAGE, FAMILY_SIREN)

_LAST_SHARED_CODE = 0x1B
_INTERNAL_OFFSET = 2

# Internal codes of the operators that speak the cloud remote protocol.
_INTERNAL_FAMILIES: Final[dict[int, str]] = {
    0x19: FAMILY_SWING,  # VX_CORE_PLUS
    0x22: FAMILY_SLIDER,  # D4_CORE_PLUS
    0x29: FAMILY_GARAGE,  # SDO5SMART_PLUS
    0x2A: FAMILY_SIREN,  # WIRELESS_SIREN
    0x2B: FAMILY_SLIDER,  # D5ES_CORE_PLUS
    0x2C: FAMILY_SLIDER,  # D3_CORE_PLUS
    0x2D: FAMILY_SLIDER,  # DX_CORE_PLUS
}

_INTERNAL_NAMES: Final[dict[int, str]] = {
    0x19: "VECTOR Smart",
    0x22: "D4 Smart",
    0x29: "SDO5 Smart",
    0x2A: "Wireless siren",
    0x2B: "D5 Evo Smart",
    0x2C: "D3 Smart",
    0x2D: "D10 Smart",
}


def to_internal_code(external_code: int | str | None) -> int | None:
    """Map a cloud/telemetry product code to the firmware's internal numbering."""
    try:
        code = int(str(external_code).strip(), 10)
    except (TypeError, ValueError):
        return None
    if code <= 0:
        return None
    if code <= _LAST_SHARED_CODE:
        return code
    return code + _INTERNAL_OFFSET


def family_for_internal_code(internal_code: int | None) -> str | None:
    if internal_code is None:
        return None
    return _INTERNAL_FAMILIES.get(internal_code)


def family_for_code(external_code: int | str | None) -> str | None:
    """Operator family for a cloud/telemetry product code, or None if not a Plus operator."""
    return family_for_internal_code(to_internal_code(external_code))


def is_gate_family(family: str | None) -> bool:
    return family in (FAMILY_SLIDER, FAMILY_SWING)


def product_name(external_code: int | str | None) -> str | None:
    """Marketing name for a Plus operator's product code, or None."""
    return _INTERNAL_NAMES.get(to_internal_code(external_code) or -1)
