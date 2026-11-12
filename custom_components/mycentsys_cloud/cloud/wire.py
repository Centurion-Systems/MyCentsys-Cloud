"""Secure remote-activation packets for Centurion SMART operators.

A remote activation is a short exchange of fixed-layout, little-endian packets:

1. the app proves who it is with an *authentication request* carrying its phone
   number; the operator answers with a one-time *session key*;
2. the app sends an *activation request* naming the action and echoing the
   session key; the operator answers with a result code.

Every packet is a 4-byte clear header followed by a keyed payload. The payload
key is an 8-byte operator-specific key: a fixed base combined with the first
four bytes of the operator's MAC address. Nothing in this module performs I/O.

Security notes
--------------
* The keying is the operator's own scheme and is not a confidentiality
  boundary on its own; the transport (mutually authenticated TLS to the
  broker, see :mod:`.mqtt`) is. Keys and payloads are therefore never logged.
* All decoders validate lengths and raise :class:`WireError` on anything
  malformed, so a bad or hostile message cannot crash the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct

__all__ = [
    "ALGORITHM_VERSION",
    "KEY_VERSION",
    "HEADER_LEN",
    "KEY_LEN",
    "MAC_KEY_BYTES",
    "PacketType",
    "AuthResult",
    "ActivationResult",
    "ACTIVATION_RESULT_MESSAGES",
    "WireError",
    "Packet",
    "AuthResponse",
    "ActivationResponse",
    "mac_key_bytes",
    "derive_key",
    "keystream_xor",
    "pack_phone_number",
    "unpack_phone_number",
    "build_packet",
    "parse_packet",
    "encode_auth_request",
    "decode_auth_response",
    "encode_activation_request",
    "decode_activation_response",
]

ALGORITHM_VERSION = 1
KEY_VERSION = 1
HEADER_LEN = 4
KEY_LEN = 8
MAC_KEY_BYTES = 4

# Fixed half of the operator key; the other half comes from the operator's MAC.
_BASE_KEY = bytes.fromhex("38983fba4dbfab9c")

_HEADER = struct.Struct("<BBBB")
_AUTH_REQUEST = struct.Struct("<III")  # lower, mid, upper phone digits
_AUTH_RESPONSE = struct.Struct("<BBhI")  # result, pad, pad, session key
_ACTIVATION_REQUEST = struct.Struct("<BBhI")  # config version, pad, activation id, session key
_ACTIVATION_RESPONSE = struct.Struct("<BBh")  # config version, result, pad

_PHONE_CHUNK_DIGITS = 8
_PHONE_MAX_DIGITS = 3 * _PHONE_CHUNK_DIGITS


class WireError(ValueError):
    """A packet or field could not be encoded or decoded."""


class PacketType(IntEnum):
    """Third header byte: what the keyed payload holds."""

    NACK = 0x00
    AUTH_REQUEST = 0x01
    AUTH_RESPONSE = 0x02
    ACTIVATION_REQUEST = 0x03
    ACTIVATION_RESPONSE = 0x04
    SET_TIME_REQUEST = 0x05
    SET_TIME_RESPONSE = 0x06


class AuthResult(IntEnum):
    OK = 0x01
    FAILED = 0x02


class ActivationResult(IntEnum):
    """Operator's answer to an activation request (and a few client-side codes)."""

    OK = 0x01
    SESSION_KEY_MISMATCH = 0x02
    UNKNOWN_ACTION = 0x03
    TIMEOUT = 0x04
    FAILED = 0x05
    USER_NOT_AUTHENTICATED = 0x06
    CONFIGURATION_MISMATCH = 0x07
    TIME_BARRED = 0x08
    CANCELLED = 0x09
    DEVICE_OFFLINE = 0x0A
    NO_INTERNET = 0x0B
    IN_PROGRESS = 0x0C
    ACCESS_SHARE_EXPIRED = 0x0D
    DEVICE_ALREADY_CONNECTED = 0x0E


ACTIVATION_RESULT_MESSAGES: dict[int, str] = {
    ActivationResult.OK: "Action sent",
    ActivationResult.SESSION_KEY_MISMATCH: "Session key mismatch",
    ActivationResult.UNKNOWN_ACTION: "Unknown action",
    ActivationResult.TIMEOUT: "Timed out",
    ActivationResult.FAILED: "Action failed",
    ActivationResult.USER_NOT_AUTHENTICATED: "Number not registered on this operator",
    ActivationResult.CONFIGURATION_MISMATCH: "Operator configuration changed",
    ActivationResult.TIME_BARRED: "Number is time barred",
    ActivationResult.CANCELLED: "Cancelled",
    ActivationResult.DEVICE_OFFLINE: "Operator is offline",
    ActivationResult.NO_INTERNET: "No internet connection",
    ActivationResult.IN_PROGRESS: "Another action is in progress",
    ActivationResult.ACCESS_SHARE_EXPIRED: "Shared access has expired",
    ActivationResult.DEVICE_ALREADY_CONNECTED: "Operator is busy with another connection",
}


@dataclass(frozen=True, slots=True)
class Packet:
    packet_type: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class AuthResponse:
    result: int
    session_key: int

    @property
    def ok(self) -> bool:
        return self.result == AuthResult.OK


@dataclass(frozen=True, slots=True)
class ActivationResponse:
    config_version: int
    result: int

    @property
    def ok(self) -> bool:
        return self.result == ActivationResult.OK

    @property
    def message(self) -> str:
        return ACTIVATION_RESULT_MESSAGES.get(self.result, f"Unknown result {self.result}")


# --- keys -----------------------------------------------------------------------------


def mac_key_bytes(mac: str | bytes | bytearray) -> bytes:
    """The four MAC bytes that personalise the key.

    Accepts the MAC as the cloud reports it (``"A1:B2:C3:D4:E5:F6"``, dashes or
    no separators) or as raw bytes. Only the first four bytes are used.
    """
    if isinstance(mac, (bytes, bytearray)):
        raw = bytes(mac)
    else:
        cleaned = mac.strip().replace(":", "").replace("-", "")
        if len(cleaned) % 2 or not cleaned:
            raise WireError("MAC address is not a hex byte string")
        try:
            raw = bytes.fromhex(cleaned)
        except ValueError as err:
            raise WireError("MAC address is not hexadecimal") from err
    if len(raw) < MAC_KEY_BYTES:
        raise WireError("MAC address is too short")
    return raw[:MAC_KEY_BYTES]


def derive_key(mac_bytes: bytes) -> bytes:
    """Operator key: base key with its leading bytes combined with the MAC bytes."""
    mac_bytes = bytes(mac_bytes)
    if not 1 <= len(mac_bytes) <= KEY_LEN:
        raise WireError("MAC key bytes must be 1 to 8 bytes")
    key = bytearray(_BASE_KEY)
    for index, value in enumerate(mac_bytes):
        key[index] ^= value
    return bytes(key)


def keystream_xor(data: bytes, key: bytes) -> bytes:
    """Apply the repeating-key XOR used for payloads (its own inverse)."""
    if len(key) != KEY_LEN:
        raise WireError("Key must be 8 bytes")
    return bytes(byte ^ key[index % KEY_LEN] for index, byte in enumerate(data))


# --- phone number -----------------------------------------------------------------------


def _pack_chunk(digits: str) -> int:
    """Eight decimal digits into a 32-bit word, one nibble each, first digit lowest."""
    value = 0
    for index, char in enumerate(digits.rjust(_PHONE_CHUNK_DIGITS, "0")):
        value |= int(char) << (index * 4)
    return value


def _unpack_chunk(value: int) -> str:
    return "".join(str((value >> (index * 4)) & 0xF) for index in range(_PHONE_CHUNK_DIGITS))


def pack_phone_number(number: str) -> tuple[int, int, int]:
    """Split a phone number into the (lower, mid, upper) words the operator expects.

    The number's digits are right-aligned over three words of eight nibbles:
    ``upper`` holds the last eight digits, ``mid`` the eight before, ``lower``
    the rest. Any ``+``, spaces or punctuation are ignored.
    """
    digits = "".join(char for char in number if char.isdigit())
    if not digits:
        raise WireError("Phone number has no digits")
    if len(digits) > _PHONE_MAX_DIGITS:
        raise WireError("Phone number is too long")
    upper = digits[-_PHONE_CHUNK_DIGITS:]
    mid = digits[-2 * _PHONE_CHUNK_DIGITS : -_PHONE_CHUNK_DIGITS]
    lower = digits[: -2 * _PHONE_CHUNK_DIGITS] if len(digits) > 2 * _PHONE_CHUNK_DIGITS else ""
    return _pack_chunk(lower), _pack_chunk(mid), _pack_chunk(upper)


def unpack_phone_number(lower: int, mid: int, upper: int) -> str:
    """Inverse of :func:`pack_phone_number` (leading zero padding is dropped)."""
    return (_unpack_chunk(lower) + _unpack_chunk(mid) + _unpack_chunk(upper)).lstrip("0")


# --- packets --------------------------------------------------------------------------------


def build_packet(packet_type: int, payload: bytes, key: bytes) -> bytes:
    """Clear header plus keyed payload, ready to publish."""
    header = _HEADER.pack(ALGORITHM_VERSION, KEY_VERSION, int(packet_type), 0)
    return header + keystream_xor(payload, key)


def parse_packet(raw: bytes, key: bytes) -> Packet:
    """Split a received packet into its type and un-keyed payload."""
    raw = bytes(raw)
    if len(raw) < HEADER_LEN:
        raise WireError("Packet shorter than its header")
    algorithm, key_version, packet_type, _reserved = _HEADER.unpack_from(raw)
    if algorithm != ALGORITHM_VERSION or key_version != KEY_VERSION:
        raise WireError(f"Unsupported packet scheme {algorithm}/{key_version}")
    return Packet(packet_type, keystream_xor(raw[HEADER_LEN:], key))


def encode_auth_request(number: str) -> bytes:
    lower, mid, upper = pack_phone_number(number)
    return _AUTH_REQUEST.pack(lower, mid, upper)


def decode_auth_response(payload: bytes) -> AuthResponse:
    if len(payload) < _AUTH_RESPONSE.size:
        raise WireError("Authentication response is too short")
    result, _pad, _pad2, session_key = _AUTH_RESPONSE.unpack_from(payload)
    return AuthResponse(result, session_key)


def encode_activation_request(config_version: int, activation_id: int, session_key: int) -> bytes:
    if not 0 <= config_version <= 0xFF:
        raise WireError("Configuration version must fit in a byte")
    if not -0x8000 <= activation_id <= 0x7FFF:
        raise WireError("Activation id must fit in 16 bits")
    if not 0 <= session_key <= 0xFFFFFFFF:
        raise WireError("Session key must fit in 32 bits")
    return _ACTIVATION_REQUEST.pack(config_version, 0, activation_id, session_key)


def decode_activation_response(payload: bytes) -> ActivationResponse:
    if len(payload) < _ACTIVATION_RESPONSE.size:
        raise WireError("Activation response is too short")
    config_version, result, _pad = _ACTIVATION_RESPONSE.unpack_from(payload)
    return ActivationResponse(config_version, result)
