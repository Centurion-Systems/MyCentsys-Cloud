"""Broker session with SMART operators: connect, authenticate, activate, listen.

Operators and apps meet on an MQTT v5 broker secured with mutual TLS; the
cloud issues the client certificate. Each operator owns a topic tree under its
serial number:

======================================  ===========================================
``<serial>/connectionRequest``          ask the operator for a session (QoS 2)
``<serial>/connectionRequestResponse``  one byte: 0xFF when the operator accepts
``<serial>/userRemoteTrigger``          activation packets (QoS 0)
``<serial>/userRemoteTriggerResponse``  the operator's answers
``<serial>/disconnect``                 release the session (QoS 2)
``<serial>/response``                   reply topic for the above
``<serial>/deviceOverview``             the operator's periodic status (passive)
======================================  ===========================================

Every message the app publishes carries its client id as a user property and a
response topic, so the operator can answer the right client. The operator
answers with its serial number as the first user property.

This module is synchronous and thread-based (paho's network loop); callers in an
event loop run it in an executor. Every wait is bounded by a timeout.

Security notes
--------------
* Certificate material is kept in memory; the PKCS#12 bundle is converted
  through a private temporary directory only because the TLS stack needs
  files, and the files are removed as soon as the context is loaded.
* The broker's certificate is always verified: against the CA chain inside the
  bundle, or against a CA the caller supplies. Connecting without
  verification is refused unless the caller opts in explicitly, and the
  opt-in is logged every time.
* Phone numbers, keys and payload bytes are never logged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import queue
import ssl
import tempfile
import threading
import time
from typing import Any, Final, Protocol

from . import wire
from .overview import DeviceAnnouncement, Overview, decode_overview_message
from .wire import ActivationResult

__all__ = [
    "bundled_ca_pem",
    "BrokerTls",
    "ActivationOutcome",
    "OperatorSession",
    "OverviewListener",
    "SessionError",
    "ConnectionAnswer",
    "CONNECTION_ACCEPTED",
    "topic",
]

_LOGGER = logging.getLogger(__name__)

# Response from <serial>/connectionRequestResponse: the first payload byte.
CONNECTION_ACCEPTED: Final = 0xFF
_CONNECTION_USER_CONNECTED: Final = 0x00
_CONNECTION_FILE_SYSTEM_BUSY: Final = 0x01

CONNECT_TIMEOUT: Final = 10.0
# The app waits 1.5 to 2 s. Home Assistant often sits on a slower home line, hence the wider margin.
CONNECTION_REQUEST_TIMEOUT: Final = 8.0
AUTH_TIMEOUT: Final = 8.0
ACTIVATION_TIMEOUT: Final = 8.0
SUBSCRIBE_TIMEOUT: Final = 5.0
PUBLISH_TIMEOUT: Final = 5.0

_QOS_AT_MOST_ONCE: Final = 0
_QOS_AT_LEAST_ONCE: Final = 1
_QOS_EXACTLY_ONCE: Final = 2

_CLIENT_ID_PROPERTY: Final = "ClientId"


class SessionError(RuntimeError):
    """The broker could not be reached or the session is unusable."""


def topic(serial_number: str, leaf: str) -> str:
    """``<serial>/<leaf>`` with the serial validated against topic injection."""
    serial = serial_number.strip()
    if not serial or any(char in serial for char in "/+#\0") or not serial.isalnum():
        raise ValueError("Operator serial number is not a valid topic segment")
    return f"{serial}/{leaf}"


# --- TLS ---------------------------------------------------------------------------------

_BUNDLED_CA_PATH: Final = Path(__file__).resolve().parent / "certs" / "broker_ca.pem"


def bundled_ca_pem() -> bytes:
    """The public certificate of the authority that signs the brokers' certificates.

    Shipped with the package so the broker is always verified, even when the
    issued client bundle carries no chain. Reads a file: call it off the event loop.
    """
    return _BUNDLED_CA_PATH.read_bytes()


@dataclass(frozen=True, slots=True)
class BrokerTls:
    """TLS settings built from the cloud-issued PKCS#12 bundle."""

    context: ssl.SSLContext
    verifies_peer: bool
    certificate_not_after: Any  # datetime, kept opaque to avoid an import for typing only

    @classmethod
    def from_pkcs12(
        cls,
        bundle: bytes,
        password: str | bytes | None,
        *,
        ca_pem: bytes | str | None = None,
        allow_unverified: bool = False,
    ) -> "BrokerTls":
        """Build a client TLS context from a PKCS#12 bundle, keeping key material off disk.

        The broker certificate is verified against ``ca_pem`` when given, plus
        any CA chain found in the bundle. With neither, :class:`SessionError`
        is raised unless ``allow_unverified`` is True.

        The private key and certificate are written to a private temporary
        directory just long enough for the TLS stack to load them, then
        overwritten and removed.
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12

        secret = password.encode() if isinstance(password, str) else password
        key, certificate, extra = pkcs12.load_key_and_certificates(bundle, secret or None)
        if key is None or certificate is None:
            raise SessionError("The certificate bundle holds no client key pair")

        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
        chain = [cert.public_bytes(serialization.Encoding.PEM) for cert in (extra or [])]
        bundle_ca = b"".join(cert for cert in chain if cert != cert_pem)
        supplied = (ca_pem.encode() if isinstance(ca_pem, str) else bytes(ca_pem or b"")).strip()
        trusted = supplied + (b"\n" if supplied and bundle_ca else b"") + bundle_ca

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # The broker certificates carry no authority key identifier, which strict
        # X.509 checking rejects; chain verification against the CA is unaffected.
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict:
            context.verify_flags &= ~strict
        # The broker is addressed by IP and its certificate names the service, so
        # host-name matching cannot apply; chain verification against the CA still does.
        context.check_hostname = False
        if trusted:
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(cadata=trusted.decode())
        elif allow_unverified:
            _LOGGER.warning(
                "Connecting to the broker without verifying its certificate: no CA was supplied "
                "and the issued bundle carries none"
            )
            context.verify_mode = ssl.CERT_NONE
        else:
            raise SessionError(
                "No CA to verify the broker against: supply ca_pem or opt in to an unverified connection"
            )

        with tempfile.TemporaryDirectory(prefix="mycentsys-tls-") as folder:
            key_path = os.path.join(folder, "key.pem")
            cert_path = os.path.join(folder, "cert.pem")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            for path, data in ((key_path, key_pem), (cert_path, cert_pem + bundle_ca)):
                descriptor = os.open(path, flags, 0o600)
                try:
                    os.write(descriptor, data)
                finally:
                    os.close(descriptor)
            try:
                context.load_cert_chain(cert_path, key_path)
            finally:
                # Scrub before the directory is removed so the key does not linger on disk.
                for path in (key_path, cert_path):
                    try:
                        size = os.path.getsize(path)
                        with open(path, "r+b") as handle:
                            handle.write(b"\0" * size)
                    except OSError:
                        pass
        not_after = getattr(certificate, "not_valid_after_utc", None) or certificate.not_valid_after
        return cls(context=context, verifies_peer=bool(trusted), certificate_not_after=not_after)


# --- MQTT client abstraction -----------------------------------------------------------------


class MqttClientLike(Protocol):
    """The slice of paho's client the session uses (lets tests supply a fake)."""

    on_connect: Any
    on_disconnect: Any
    on_message: Any

    def connect(self, host: str, port: int, keepalive: int, *args: Any, **kwargs: Any) -> Any: ...
    def disconnect(self, *args: Any, **kwargs: Any) -> Any: ...
    def loop_start(self) -> Any: ...
    def loop_stop(self) -> Any: ...
    def subscribe(self, topic: Any, qos: int = 0, *args: Any, **kwargs: Any) -> Any: ...
    def unsubscribe(self, topic: Any, *args: Any, **kwargs: Any) -> Any: ...
    def publish(self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False, properties: Any = None) -> Any: ...


def _paho_client(client_id: str, tls: BrokerTls | None) -> MqttClientLike:
    import paho.mqtt.client as paho

    client = paho.Client(
        callback_api_version=paho.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=paho.MQTTv5,
    )
    if tls is not None:
        client.tls_set_context(tls.context)
    return client  # type: ignore[return-value]


def _publish_properties(client_id: str, response_topic: str) -> Any:
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties

    properties = Properties(PacketTypes.PUBLISH)
    properties.ResponseTopic = response_topic
    properties.UserProperty = (_CLIENT_ID_PROPERTY, client_id)
    return properties


def _user_properties(message: Any) -> list[tuple[str, str]]:
    properties = getattr(message, "properties", None)
    raw = getattr(properties, "UserProperty", None) if properties is not None else None
    if raw is None:
        return []
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[0], str):
        return [raw]
    return [tuple(item) for item in raw if isinstance(item, (tuple, list)) and len(item) == 2]


def _sender_serial(message: Any) -> str | None:
    """The operator's serial from the first user property of its reply."""
    properties = _user_properties(message)
    return properties[0][1] if properties else None


@dataclass(frozen=True, slots=True)
class ConnectionAnswer:
    code: int | None  # None when no answer arrived in time

    @property
    def accepted(self) -> bool:
        return self.code == CONNECTION_ACCEPTED

    @property
    def busy(self) -> bool:
        return self.code in (_CONNECTION_USER_CONNECTED, _CONNECTION_FILE_SYSTEM_BUSY)


@dataclass(frozen=True, slots=True)
class ActivationOutcome:
    """What came back from a remote activation attempt."""

    result: int
    message: str
    config_version: int | None = None
    connection: ConnectionAnswer | None = None

    @property
    def ok(self) -> bool:
        return self.result == ActivationResult.OK

    @staticmethod
    def of(result: int, config_version: int | None = None, connection: ConnectionAnswer | None = None) -> "ActivationOutcome":
        return ActivationOutcome(result, wire.ACTIVATION_RESULT_MESSAGES.get(result, "Unknown result"), config_version, connection)


# --- the connected session ---------------------------------------------------------------------


class _Mailbox:
    """Per-topic inbox so each wait only sees replies to its own request."""

    def __init__(self) -> None:
        self._queues: dict[str, queue.Queue[Any]] = {}
        self._lock = threading.Lock()

    def open(self, topic_name: str) -> None:
        with self._lock:
            self._queues[topic_name] = queue.Queue()

    def close(self, topic_name: str) -> None:
        with self._lock:
            self._queues.pop(topic_name, None)

    def deliver(self, topic_name: str, message: Any) -> bool:
        with self._lock:
            inbox = self._queues.get(topic_name)
        if inbox is None:
            return False
        inbox.put(message)
        return True

    def wait(self, topic_name: str, timeout: float) -> Any | None:
        with self._lock:
            inbox = self._queues.get(topic_name)
        if inbox is None:
            return None
        try:
            return inbox.get(timeout=timeout)
        except queue.Empty:
            return None


class OperatorSession:
    """One MQTT connection used to send remote activations to operators.

    Blocking; create it in a worker thread. ``client_factory`` exists so tests
    can substitute a fake client; production uses paho.
    """

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        tls: BrokerTls | None,
        *,
        client_factory: Callable[[str, BrokerTls | None], MqttClientLike] = _paho_client,
        keepalive: int = 30,
    ) -> None:
        if not client_id or len(client_id) > 64:
            raise ValueError("A client id of 1 to 64 characters is required")
        self._host = host
        self._port = port
        self._client_id = client_id
        self._tls = tls
        self._keepalive = keepalive
        self._client = client_factory(client_id, tls)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_subscribe = self._on_subscribe
        self._connected = threading.Event()
        self._mailbox = _Mailbox()
        self._acked_subscriptions: set[int] = set()
        self._subscription_acked = threading.Condition()

    # -- lifecycle --------------------------------------------------------------------------

    def connect(self, timeout: float = CONNECT_TIMEOUT) -> None:
        # Connect before starting the network loop: the loop thread reconnects on its
        # own when it finds no connection, and without a host that attempt fails.
        try:
            self._client.connect(self._host, self._port, self._keepalive, clean_start=True)
        except TypeError:
            # Fakes and older clients without MQTT v5 keyword arguments.
            self._client.connect(self._host, self._port, self._keepalive)
        except OSError as err:
            raise SessionError(f"Broker connection failed: {err.__class__.__name__}") from err
        self._client.loop_start()
        if not self._connected.wait(timeout):
            self.close()
            raise SessionError("Broker did not accept the connection in time")

    def close(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()
            self._connected.clear()

    def __enter__(self) -> "OperatorSession":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # -- callbacks (paho thread) -------------------------------------------------------------

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        if _reason_ok(reason_code):
            self._connected.set()
        else:
            _LOGGER.warning("Broker refused the session: %s", reason_code)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any) -> None:
        self._connected.clear()

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        if not self._mailbox.deliver(message.topic, message):
            _LOGGER.debug("Unsolicited message on %s ignored", message.topic)

    def _on_subscribe(self, client: Any, userdata: Any, mid: int, *args: Any) -> None:
        with self._subscription_acked:
            self._acked_subscriptions.add(mid)
            self._subscription_acked.notify_all()

    # -- plumbing -----------------------------------------------------------------------------

    def _publish(self, topic_name: str, payload: bytes, qos: int, response_topic: str) -> None:
        properties = _publish_properties(self._client_id, response_topic)
        info = self._client.publish(topic_name, payload, qos=qos, properties=properties)
        rc = getattr(info, "rc", 0)
        if rc not in (0, None):
            raise SessionError(f"Publish to {topic_name} failed with code {rc}")
        wait = getattr(info, "wait_for_publish", None)
        if callable(wait) and qos > _QOS_AT_MOST_ONCE:
            try:
                wait(timeout=PUBLISH_TIMEOUT)
            except (RuntimeError, ValueError) as err:
                raise SessionError("Publish was not acknowledged by the broker") from err

    def _subscribe(self, topics: Iterable[str]) -> None:
        """Subscribe and wait for the broker to acknowledge, so no reply can slip past."""
        pending: set[int] = set()
        for name in topics:
            self._mailbox.open(name)
            result = self._client.subscribe(name, qos=_QOS_AT_LEAST_ONCE)
            mid = result[1] if isinstance(result, tuple) and len(result) > 1 else None
            if isinstance(mid, int):
                pending.add(mid)
        if not pending:
            return
        deadline = time.monotonic() + SUBSCRIBE_TIMEOUT
        with self._subscription_acked:
            while not pending <= self._acked_subscriptions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _LOGGER.warning("Broker did not acknowledge the reply subscriptions in time; continuing")
                    break
                self._subscription_acked.wait(remaining)
            self._acked_subscriptions -= pending

    def _unsubscribe(self, topics: Iterable[str]) -> None:
        for name in topics:
            self._mailbox.close(name)
            try:
                self._client.unsubscribe(name)
            except Exception:  # noqa: BLE001 - best effort during teardown
                _LOGGER.debug("Unsubscribe from %s failed", name)

    # -- the activation exchange -----------------------------------------------------------------

    def request_connection(self, serial_number: str, timeout: float = CONNECTION_REQUEST_TIMEOUT) -> ConnectionAnswer:
        """Ask the operator for a session. Caller must have subscribed to its reply topic."""
        reply_topic = topic(serial_number, "connectionRequestResponse")
        self._publish(topic(serial_number, "connectionRequest"), b"", _QOS_EXACTLY_ONCE, reply_topic)
        message = self._mailbox.wait(reply_topic, timeout)
        if message is None:
            return ConnectionAnswer(None)
        payload = bytes(message.payload or b"")
        # An empty answer means another client holds the operator.
        return ConnectionAnswer(payload[0] if payload else _CONNECTION_USER_CONNECTED)

    def release(self, serial_number: str) -> None:
        """Tell the operator the session is over (only after an accepted connection)."""
        try:
            self._publish(topic(serial_number, "disconnect"), b"", _QOS_EXACTLY_ONCE, topic(serial_number, "response"))
        except SessionError:
            _LOGGER.debug("Release of %s was not acknowledged", serial_number)

    def _exchange(self, serial_number: str, key: bytes, packet_type: int, payload: bytes, timeout: float) -> wire.Packet | None:
        """Send one keyed packet and return the operator's keyed answer, or None on timeout."""
        reply_topic = topic(serial_number, "userRemoteTriggerResponse")
        self._publish(
            topic(serial_number, "userRemoteTrigger"),
            wire.build_packet(packet_type, payload, key),
            _QOS_AT_MOST_ONCE,
            reply_topic,
        )
        remaining = timeout
        end = time.monotonic() + timeout
        while remaining > 0:
            message = self._mailbox.wait(reply_topic, remaining)
            if message is None:
                return None
            sender = _sender_serial(message)
            if sender is not None and sender != serial_number:
                remaining = end - time.monotonic()
                continue
            try:
                return wire.parse_packet(bytes(message.payload or b""), key)
            except wire.WireError:
                _LOGGER.debug("Discarding malformed reply from %s", serial_number)
                remaining = end - time.monotonic()
        return None

    def activate(
        self,
        serial_number: str,
        mac_address: str | bytes,
        phone_number: str,
        activation_id: int,
        *,
        config_version: int = 0,
        retry_on_config_mismatch: bool = True,
    ) -> ActivationOutcome:
        """Run the full exchange: connect, authenticate, activate, release.

        A configuration-mismatch answer carries the operator's current
        configuration version; the request is repeated once with it, which is
        what a user would otherwise do by hand.
        """
        if not self.connected:
            raise SessionError("Session is not connected")
        key = wire.derive_key(wire.mac_key_bytes(mac_address))
        topics = (
            topic(serial_number, "connectionRequestResponse"),
            topic(serial_number, "userRemoteTriggerResponse"),
            topic(serial_number, "response"),
        )
        self._subscribe(topics)
        accepted = False
        try:
            answer = self.request_connection(serial_number)
            if answer.code is None:
                return ActivationOutcome.of(ActivationResult.TIMEOUT, connection=answer)
            if answer.busy:
                return ActivationOutcome.of(ActivationResult.DEVICE_ALREADY_CONNECTED, connection=answer)
            if not answer.accepted:
                return ActivationOutcome.of(ActivationResult.FAILED, connection=answer)
            accepted = True

            reply = self._exchange(serial_number, key, wire.PacketType.AUTH_REQUEST, wire.encode_auth_request(phone_number), AUTH_TIMEOUT)
            if reply is None:
                return ActivationOutcome.of(ActivationResult.TIMEOUT, connection=answer)
            if reply.packet_type != wire.PacketType.AUTH_RESPONSE:
                return ActivationOutcome.of(ActivationResult.USER_NOT_AUTHENTICATED, connection=answer)
            try:
                auth = wire.decode_auth_response(reply.payload)
            except wire.WireError:
                return ActivationOutcome.of(ActivationResult.FAILED, connection=answer)
            if not auth.ok:
                return ActivationOutcome.of(ActivationResult.USER_NOT_AUTHENTICATED, connection=answer)

            version = config_version
            for attempt in range(2):
                reply = self._exchange(
                    serial_number,
                    key,
                    wire.PacketType.ACTIVATION_REQUEST,
                    wire.encode_activation_request(version, activation_id, auth.session_key),
                    ACTIVATION_TIMEOUT,
                )
                if reply is None:
                    return ActivationOutcome.of(ActivationResult.TIMEOUT, version, answer)
                if reply.packet_type != wire.PacketType.ACTIVATION_RESPONSE:
                    return ActivationOutcome.of(ActivationResult.FAILED, version, answer)
                try:
                    activation = wire.decode_activation_response(reply.payload)
                except wire.WireError:
                    return ActivationOutcome.of(ActivationResult.FAILED, version, answer)
                if (
                    activation.result == ActivationResult.CONFIGURATION_MISMATCH
                    and retry_on_config_mismatch
                    and attempt == 0
                    and activation.config_version != version
                ):
                    version = activation.config_version
                    continue
                return ActivationOutcome.of(activation.result, activation.config_version, answer)
            return ActivationOutcome.of(ActivationResult.FAILED, version, answer)
        finally:
            if accepted:
                self.release(serial_number)
            self._unsubscribe(topics)


def _reason_ok(reason_code: Any) -> bool:
    if reason_code is None or reason_code == 0:
        return True
    is_failure = getattr(reason_code, "is_failure", None)
    if is_failure is not None:
        return not is_failure
    try:
        return int(reason_code) == 0
    except (TypeError, ValueError):
        return False


# --- passive status listener ---------------------------------------------------------------------


OverviewCallback = Callable[[DeviceAnnouncement, "Overview | None"], None]


class OverviewListener:
    """Subscribe to operators' status topics and hand each decoded overview to a callback.

    Uses its own client id (the broker allows one session per id), stays
    connected, and never publishes. The callback runs on the network thread
    and must be quick.
    """

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        tls: BrokerTls | None,
        callback: OverviewCallback,
        *,
        families: dict[str, str] | None = None,
        client_factory: Callable[[str, BrokerTls | None], MqttClientLike] = _paho_client,
        keepalive: int = 60,
    ) -> None:
        self._host = host
        self._port = port
        self._keepalive = keepalive
        self._callback = callback
        self._families = dict(families or {})
        self._serials: set[str] = set()
        self._lock = threading.Lock()
        self._client = client_factory(client_id, tls)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._connected = threading.Event()

    def start(self, serial_numbers: Iterable[str], timeout: float = CONNECT_TIMEOUT) -> None:
        with self._lock:
            self._serials = {serial for serial in serial_numbers if serial}
        try:
            self._client.connect(self._host, self._port, self._keepalive, clean_start=True)
        except TypeError:
            self._client.connect(self._host, self._port, self._keepalive)
        except OSError as err:
            raise SessionError(f"Broker connection failed: {err.__class__.__name__}") from err
        self._client.loop_start()
        if not self._connected.wait(timeout):
            self.stop()
            raise SessionError("Broker did not accept the listener in time")

    def stop(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()
            self._connected.clear()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def watch(self, serial_number: str) -> None:
        with self._lock:
            self._serials.add(serial_number)
        if self.connected:
            self._client.subscribe(topic(serial_number, "deviceOverview"), qos=_QOS_AT_LEAST_ONCE)

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        if not _reason_ok(reason_code):
            _LOGGER.warning("Broker refused the listener: %s", reason_code)
            return
        self._connected.set()
        with self._lock:
            serials = sorted(self._serials)
        # (Re)subscribe on every connect so a broker reconnect restores the feed.
        for serial in serials:
            self._client.subscribe(topic(serial, "deviceOverview"), qos=_QOS_AT_LEAST_ONCE)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any) -> None:
        self._connected.clear()

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        if not str(message.topic).endswith("/deviceOverview"):
            return
        serial_from_topic = str(message.topic).split("/", 1)[0]
        with self._lock:
            if serial_from_topic not in self._serials:
                return
        try:
            announcement, decoded = decode_overview_message(
                _user_properties(message),
                bytes(message.payload or b""),
                family=self._families.get(serial_from_topic),
            )
        except Exception:  # noqa: BLE001 - a bad message must not kill the network thread
            _LOGGER.debug("Ignoring undecodable overview from %s", serial_from_topic)
            return
        if announcement.serial_number != serial_from_topic:
            _LOGGER.debug("Overview on %s claims another serial; ignored", serial_from_topic)
            return
        try:
            self._callback(announcement, decoded)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Overview callback failed for %s", serial_from_topic)
