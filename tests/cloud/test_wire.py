"""Secure activation packet format: keys, phone packing, packet build/parse."""

from __future__ import annotations

import struct

import pytest

from mycentsys_cloud_protocol import wire

MAC = "A1:B2:C3:D4:E5:F6"


def test_mac_key_bytes_accepts_common_forms() -> None:
    assert wire.mac_key_bytes(MAC) == bytes.fromhex("a1b2c3d4")
    assert wire.mac_key_bytes("a1-b2-c3-d4-e5-f6") == bytes.fromhex("a1b2c3d4")
    assert wire.mac_key_bytes("A1B2C3D4E5F6") == bytes.fromhex("a1b2c3d4")
    assert wire.mac_key_bytes(bytes.fromhex("a1b2c3d4e5f6")) == bytes.fromhex("a1b2c3d4")


@pytest.mark.parametrize("bad", ["", "zz:zz:zz:zz", "A1B2C3", "A1B2C"])
def test_mac_key_bytes_rejects_garbage(bad: str) -> None:
    with pytest.raises(wire.WireError):
        wire.mac_key_bytes(bad)


def test_derive_key_mixes_only_the_mac_bytes() -> None:
    key = wire.derive_key(bytes.fromhex("a1b2c3d4"))
    assert len(key) == wire.KEY_LEN
    # Deriving twice with the same MAC bytes is stable; a different MAC changes only the head.
    assert key == wire.derive_key(bytes.fromhex("a1b2c3d4"))
    other = wire.derive_key(bytes.fromhex("00000000"))
    assert key[4:] == other[4:]
    assert key[:4] != other[:4]


def test_keystream_xor_round_trips() -> None:
    key = wire.derive_key(wire.mac_key_bytes(MAC))
    data = bytes(range(23))
    assert wire.keystream_xor(wire.keystream_xor(data, key), key) == data
    with pytest.raises(wire.WireError):
        wire.keystream_xor(data, b"short")


def test_phone_packing_matches_operator_layout() -> None:
    # Eleven digits: the last eight land in "upper", one nibble per digit, first digit lowest.
    lower, mid, upper = wire.pack_phone_number("+27 82 000 0000")
    assert lower == 0
    assert mid == 0x87200000
    assert upper == 0x00000002
    assert wire.unpack_phone_number(lower, mid, upper) == "27820000000"


def test_phone_packing_long_number_uses_all_three_words() -> None:
    digits = "123456789012345678901"  # 21 digits
    lower, mid, upper = wire.pack_phone_number(digits)
    assert wire.unpack_phone_number(lower, mid, upper) == digits
    assert lower == 0x54321000  # "12345" right-aligned into eight nibbles
    assert mid == 0x32109876  # "67890123", first digit lowest
    assert upper == 0x10987654  # "45678901"


@pytest.mark.parametrize("bad", ["", "+", "1" * 25])
def test_phone_packing_rejects_bad_numbers(bad: str) -> None:
    with pytest.raises(wire.WireError):
        wire.pack_phone_number(bad)


def test_build_and_parse_packet() -> None:
    key = wire.derive_key(wire.mac_key_bytes(MAC))
    payload = wire.encode_auth_request("27820000000")
    packet = wire.build_packet(wire.PacketType.AUTH_REQUEST, payload, key)

    assert packet[:4] == bytes([1, 1, 1, 0])
    assert len(packet) == 4 + 12
    assert packet[4:] != payload  # keyed

    parsed = wire.parse_packet(packet, key)
    assert parsed.packet_type == wire.PacketType.AUTH_REQUEST
    assert parsed.payload == payload


def test_parse_packet_rejects_short_or_foreign_scheme() -> None:
    key = wire.derive_key(wire.mac_key_bytes(MAC))
    with pytest.raises(wire.WireError):
        wire.parse_packet(b"\x01\x01", key)
    with pytest.raises(wire.WireError):
        wire.parse_packet(b"\x02\x01\x02\x00" + b"\x00" * 8, key)


def test_auth_response_decoding() -> None:
    payload = struct.pack("<BBhI", 1, 0, 0, 0xDEADBEEF)
    response = wire.decode_auth_response(payload)
    assert response.ok
    assert response.session_key == 0xDEADBEEF
    assert not wire.decode_auth_response(struct.pack("<BBhI", 2, 0, 0, 0)).ok
    with pytest.raises(wire.WireError):
        wire.decode_auth_response(b"\x01\x00")


def test_activation_request_layout_and_response() -> None:
    request = wire.encode_activation_request(7, 34, 0x01020304)
    assert request == bytes([7, 0]) + (34).to_bytes(2, "little") + (0x01020304).to_bytes(4, "little")

    response = wire.decode_activation_response(struct.pack("<BBh", 9, 7, 0))
    assert response.config_version == 9
    assert response.result == wire.ActivationResult.CONFIGURATION_MISMATCH
    assert not response.ok
    assert response.message == "Operator configuration changed"
    assert wire.decode_activation_response(struct.pack("<BBh", 0, 1, 0)).ok


@pytest.mark.parametrize(
    "config_version, activation_id, session_key",
    [(256, 34, 0), (0, 70000, 0), (0, 34, 1 << 32)],
)
def test_activation_request_rejects_out_of_range(config_version: int, activation_id: int, session_key: int) -> None:
    with pytest.raises(wire.WireError):
        wire.encode_activation_request(config_version, activation_id, session_key)


def test_every_result_code_has_a_message() -> None:
    for result in wire.ActivationResult:
        assert wire.ACTIVATION_RESULT_MESSAGES[result]
