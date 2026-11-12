"""The broker session, driven against a fake client that plays the operator."""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
import threading
from typing import Any

import pytest

from mycentsys_cloud_protocol import mqtt, wire

SERIAL = "AB12CD34EF56"
MAC = "A1:B2:C3:D4:E5:F6"
PHONE = "27820000000"
KEY = wire.derive_key(wire.mac_key_bytes(MAC))


@dataclass
class FakeProperties:
    UserProperty: Any = None
    ResponseTopic: str | None = None


@dataclass
class FakeMessage:
    topic: str
    payload: bytes
    properties: FakeProperties = field(default_factory=FakeProperties)


@dataclass
class PublishInfo:
    rc: int = 0

    def wait_for_publish(self, timeout: float | None = None) -> None:
        return None


class FakeOperatorClient:
    """A broker and operator in one: answers publishes the way a SMART operator would."""

    def __init__(self, client_id: str, tls: Any) -> None:
        self.client_id = client_id
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_subscribe = None
        self.connect_calls = 0
        self.loop_started_after_connect: bool | None = None
        self.published: list[tuple[str, bytes, int, Any]] = []
        self.subscriptions: list[str] = []
        self.unsubscriptions: list[str] = []
        self.connect_answer: bytes | None = bytes([mqtt.CONNECTION_ACCEPTED])
        self.auth_result = wire.AuthResult.OK
        self.session_key = 0xCAFEBABE
        self.activation_results: list[tuple[int, int]] = [(3, wire.ActivationResult.OK)]  # (config version, result)
        self.reply_serial = SERIAL
        self.silent = False
        self.refuse_connect = False

    # paho surface -----------------------------------------------------------------------
    def connect(self, host: str, port: int, keepalive: int, **kwargs: Any) -> None:
        self.connect_calls += 1
        reason = 135 if self.refuse_connect else 0
        threading.Thread(target=lambda: self.on_connect(self, None, {}, reason), daemon=True).start()

    def disconnect(self) -> None:
        if self.on_disconnect:
            self.on_disconnect(self, None)

    def loop_start(self) -> None:
        # paho's loop thread needs a host to reconnect to, so connect must come first.
        self.loop_started_after_connect = self.connect_calls > 0

    def loop_stop(self) -> None:
        pass

    def subscribe(self, topic: str, qos: int = 0):
        self.subscriptions.append(topic)
        mid = len(self.subscriptions)
        if getattr(self, "on_subscribe", None):
            threading.Thread(target=lambda: self.on_subscribe(self, None, mid, [0]), daemon=True).start()
        return (0, mid)

    def unsubscribe(self, topic: str) -> None:
        self.unsubscriptions.append(topic)

    def publish(self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False, properties: Any = None) -> PublishInfo:
        self.published.append((topic, bytes(payload or b""), qos, properties))
        if not self.silent:
            threading.Thread(target=self._answer, args=(topic, bytes(payload or b""), properties), daemon=True).start()
        return PublishInfo()

    # the operator's side --------------------------------------------------------------------
    def _reply(self, topic: str, payload: bytes) -> None:
        self.on_message(self, None, FakeMessage(topic, payload, FakeProperties(UserProperty=[("ClientId", self.reply_serial)])))

    def _answer(self, topic: str, payload: bytes, properties: Any) -> None:
        leaf = topic.split("/", 1)[1]
        if leaf == "connectionRequest":
            if self.connect_answer is not None:
                self._reply(properties.ResponseTopic, self.connect_answer)
            return
        if leaf == "disconnect":
            self._reply(properties.ResponseTopic, b"\x01")
            return
        if leaf != "userRemoteTrigger":
            return
        packet = wire.parse_packet(payload, KEY)
        if packet.packet_type == wire.PacketType.AUTH_REQUEST:
            lower, mid, upper = struct.unpack("<III", packet.payload)
            assert wire.unpack_phone_number(lower, mid, upper) == PHONE
            body = struct.pack("<BBhI", self.auth_result, 0, 0, self.session_key)
            self._reply(properties.ResponseTopic, wire.build_packet(wire.PacketType.AUTH_RESPONSE, body, KEY))
        elif packet.packet_type == wire.PacketType.ACTIVATION_REQUEST:
            version, _pad, activation_id, session_key = struct.unpack("<BBhI", packet.payload)
            assert session_key == self.session_key
            config_version, result = self.activation_results.pop(0)
            if result == wire.ActivationResult.CONFIGURATION_MISMATCH and version == config_version:
                result = wire.ActivationResult.OK
            body = struct.pack("<BBh", config_version, result, 0)
            self._reply(properties.ResponseTopic, wire.build_packet(wire.PacketType.ACTIVATION_RESPONSE, body, KEY))


def make_session(client: FakeOperatorClient) -> mqtt.OperatorSession:
    session = mqtt.OperatorSession("broker.example", 8880, "mcr:27820000000:mcc", None, client_factory=lambda *_: client)
    session.connect(timeout=2)
    return session


def test_topic_validation() -> None:
    assert mqtt.topic(SERIAL, "deviceOverview") == f"{SERIAL}/deviceOverview"
    for bad in ("", "AB/CD", "AB+", "#", "AB CD"):
        with pytest.raises(ValueError):
            mqtt.topic(bad, "x")


def test_successful_activation_exchange() -> None:
    client = FakeOperatorClient("x", None)
    with make_session(client) as session:
        outcome = session.activate(SERIAL, MAC, PHONE, 34, config_version=3)

    assert outcome.ok
    assert outcome.message == "Action sent"
    assert outcome.config_version == 3
    assert client.loop_started_after_connect is True
    # The connection request only goes out once the reply subscriptions are acknowledged.
    assert len(client.subscriptions) == 3
    assert outcome.connection and outcome.connection.accepted

    topics = [item[0] for item in client.published]
    assert topics == [
        f"{SERIAL}/connectionRequest",
        f"{SERIAL}/userRemoteTrigger",
        f"{SERIAL}/userRemoteTrigger",
        f"{SERIAL}/disconnect",
    ]
    # QoS and reply plumbing as the operators expect.
    assert [item[2] for item in client.published] == [2, 0, 0, 2]
    assert client.published[0][3].ResponseTopic == f"{SERIAL}/connectionRequestResponse"
    assert client.published[1][3].ResponseTopic == f"{SERIAL}/userRemoteTriggerResponse"
    assert client.published[3][3].ResponseTopic == f"{SERIAL}/response"
    assert ("ClientId", "mcr:27820000000:mcc") in client.published[0][3].UserProperty
    # Reply topics subscribed before the request and released after.
    assert set(client.subscriptions) == {
        f"{SERIAL}/connectionRequestResponse",
        f"{SERIAL}/userRemoteTriggerResponse",
        f"{SERIAL}/response",
    }
    assert set(client.unsubscriptions) == set(client.subscriptions)


def test_config_mismatch_is_retried_once_with_the_operator_version() -> None:
    client = FakeOperatorClient("x", None)
    client.activation_results = [(9, wire.ActivationResult.CONFIGURATION_MISMATCH), (9, wire.ActivationResult.OK)]
    with make_session(client) as session:
        outcome = session.activate(SERIAL, MAC, PHONE, 34, config_version=1)

    assert outcome.ok
    assert outcome.config_version == 9
    requests = [item for item in client.published if item[0].endswith("userRemoteTrigger")]
    assert len(requests) == 3  # auth + two activation attempts


def test_busy_operator_is_reported_and_not_released() -> None:
    client = FakeOperatorClient("x", None)
    client.connect_answer = b""  # another client holds the operator
    with make_session(client) as session:
        outcome = session.activate(SERIAL, MAC, PHONE, 34)

    assert outcome.result == wire.ActivationResult.DEVICE_ALREADY_CONNECTED
    assert not any(item[0].endswith("/disconnect") for item in client.published)
    assert not any(item[0].endswith("userRemoteTrigger") for item in client.published)


def test_no_answer_to_connection_request_times_out() -> None:
    client = FakeOperatorClient("x", None)
    client.connect_answer = None
    mqtt_timeout = mqtt.CONNECTION_REQUEST_TIMEOUT
    try:
        mqtt.CONNECTION_REQUEST_TIMEOUT = 0.05
        with make_session(client) as session:
            outcome = session.activate(SERIAL, MAC, PHONE, 34)
    finally:
        mqtt.CONNECTION_REQUEST_TIMEOUT = mqtt_timeout
    assert outcome.result == wire.ActivationResult.TIMEOUT
    assert outcome.connection and outcome.connection.code is None


def test_authentication_failure() -> None:
    client = FakeOperatorClient("x", None)
    client.auth_result = wire.AuthResult.FAILED
    with make_session(client) as session:
        outcome = session.activate(SERIAL, MAC, PHONE, 34)

    assert outcome.result == wire.ActivationResult.USER_NOT_AUTHENTICATED
    assert any(item[0].endswith("/disconnect") for item in client.published), "accepted sessions are released"


def test_replies_from_other_operators_are_ignored() -> None:
    client = FakeOperatorClient("x", None)
    client.reply_serial = "OTHER0000001"
    original = mqtt.AUTH_TIMEOUT
    try:
        mqtt.AUTH_TIMEOUT = 0.1
        with make_session(client) as session:
            outcome = session.activate(SERIAL, MAC, PHONE, 34)
    finally:
        mqtt.AUTH_TIMEOUT = original
    assert outcome.result == wire.ActivationResult.TIMEOUT


def test_broker_refusal_raises() -> None:
    client = FakeOperatorClient("x", None)
    client.refuse_connect = True
    session = mqtt.OperatorSession("broker.example", 8880, "mcr:1:mcc", None, client_factory=lambda *_: client)
    with pytest.raises(mqtt.SessionError):
        session.connect(timeout=0.2)


def test_client_id_is_required() -> None:
    with pytest.raises(ValueError):
        mqtt.OperatorSession("h", 1, "", None, client_factory=lambda *_: FakeOperatorClient("", None))


def test_overview_listener_decodes_and_filters() -> None:
    client = FakeOperatorClient("x", None)
    seen: list[tuple[str, Any]] = []
    listener = mqtt.OverviewListener("h", 1, "mcr:1:mcc-feed", None, lambda ann, ov: seen.append((ann.serial_number, ov)), client_factory=lambda *_: client)
    listener.start([SERIAL], timeout=2)
    assert f"{SERIAL}/deviceOverview" in client.subscriptions

    body = b"\x00" * 24  # garage V3 sized block, all zero
    props = FakeProperties(UserProperty=[("ClientId", SERIAL), ("PC", "39"), ("FW", "1.0.0.0"), ("Name", "Garage"), ("MAC", MAC)])
    client.on_message(client, None, FakeMessage(f"{SERIAL}/deviceOverview", b"\x00\x00\x00\x00" + body, props))
    # A message for a serial we do not watch is dropped, as is one whose body claims another serial.
    client.on_message(client, None, FakeMessage("ZZ99/deviceOverview", b"\x00" * 28, FakeProperties(UserProperty=[("ClientId", "ZZ99"), ("PC", "39")])))
    client.on_message(client, None, FakeMessage(f"{SERIAL}/deviceOverview", b"\x00" * 28, FakeProperties(UserProperty=[("ClientId", "ZZ99"), ("PC", "39")])))

    assert len(seen) == 1
    serial, decoded = seen[0]
    assert serial == SERIAL
    assert decoded is not None and decoded.family == "garage"

    listener.watch("CD34EF56AB12")
    assert "CD34EF56AB12/deviceOverview" in client.subscriptions
    listener.stop()
    assert not listener.connected
