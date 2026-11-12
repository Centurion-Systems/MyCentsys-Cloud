"""Activation ids per family and product-code classification."""

from __future__ import annotations

from mycentsys_cloud_protocol import actions, products


def test_gate_and_garage_triggers_differ() -> None:
    # Sending the garage "run" id to a gate would toggle holiday lock, so the
    # families must never share a trigger id.
    assert actions.trigger_id("slider") == 34
    assert actions.trigger_id("swing") == 34
    assert actions.trigger_id("garage") == 1
    assert actions.trigger_id("unknown") is None


def test_tamper_ids_per_family() -> None:
    assert (actions.tamper_arm_id("slider"), actions.tamper_disarm_id("slider"), actions.tamper_clear_id("slider")) == (25, 26, 27)
    assert (actions.tamper_arm_id("garage"), actions.tamper_disarm_id("garage"), actions.tamper_clear_id("garage")) == (7, 8, 9)
    assert actions.tamper_arm_id("swing") is None
    assert actions.tamper_clear_id("siren") == 2


def test_keys_are_unique_within_a_family() -> None:
    for family in products.FAMILIES:
        keys = [item.key for item in actions.activations_for(family)]
        ids = [item.id for item in actions.activations_for(family)]
        assert len(keys) == len(set(keys)), family
        assert len(ids) == len(set(ids)), family


def test_gate_bitmask_positions_are_complete() -> None:
    assert sorted(item.bitmask_position for item in actions.GATE_ACTIVATIONS) == list(range(16))
    assert sorted(item.bitmask_position for item in actions.SWING_ACTIVATIONS) == list(range(13))
    assert actions.activation("swing", "tamper_arm") is None


def test_lookup_helpers() -> None:
    keep_open = actions.activation("garage", "keep_open")
    assert keep_open and keep_open.id == 4 and keep_open.toggles_mode
    assert actions.label_for("slider", 35) == "Pedestrian"
    assert actions.label_for("slider", 999) == "Action 999"


def test_external_to_internal_code_mapping() -> None:
    assert products.to_internal_code(25) == 25  # VX_CORE_PLUS, shared range
    assert products.to_internal_code("43") == 0x2D  # DX_CORE_PLUS
    assert products.to_internal_code(39) == 0x29  # SDO5SMART_PLUS
    assert products.to_internal_code(0) is None
    assert products.to_internal_code("abc") is None
    assert products.to_internal_code(None) is None


def test_family_classification() -> None:
    assert products.family_for_code(43) == "slider"  # D10
    assert products.family_for_code(41) == "slider"  # D5 Evo
    assert products.family_for_code(42) == "slider"  # D3
    assert products.family_for_code(32) == "slider"  # D4
    assert products.family_for_code(25) == "swing"
    assert products.family_for_code(39) == "garage"
    assert products.family_for_code(40) == "siren"
    assert products.family_for_code(11) is None  # DX_CORE, not a Plus operator
    assert products.is_gate_family("swing") and not products.is_gate_family("garage")


def test_product_names() -> None:
    assert products.product_name(43) == "D10 Smart"
    assert products.product_name(39) == "SDO5 Smart"
    assert products.product_name(1) is None
