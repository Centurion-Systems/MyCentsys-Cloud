"""Broker TLS context from the cloud-issued PKCS#12 bundle."""

from __future__ import annotations

import datetime
import logging
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from mycentsys_cloud_protocol import mqtt


def _cert(name: str, issuer_key=None, issuer_name=None, *, ca: bool = False):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name or subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    cert = builder.sign(issuer_key or key, hashes.SHA256())
    return key, cert


def _bundle(*, with_ca: bool) -> tuple[bytes, bytes]:
    ca_key, ca_cert = _cert("Test Broker CA", ca=True)
    client_key, client_cert = _cert("client", ca_key, ca_cert.subject)
    data = pkcs12.serialize_key_and_certificates(
        b"client",
        client_key,
        client_cert,
        [ca_cert] if with_ca else None,
        serialization.BestAvailableEncryption(b"secret"),
    )
    return data, ca_cert.public_bytes(serialization.Encoding.PEM)


def test_bundle_with_ca_verifies_the_broker() -> None:
    data, _ca = _bundle(with_ca=True)
    tls = mqtt.BrokerTls.from_pkcs12(data, "secret")
    assert tls.verifies_peer
    assert tls.context.verify_mode == ssl.CERT_REQUIRED
    assert tls.context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert tls.context.check_hostname is False
    assert tls.certificate_not_after > datetime.datetime.now(datetime.timezone.utc)


def test_bundle_without_ca_needs_a_supplied_ca() -> None:
    data, ca_pem = _bundle(with_ca=False)
    with pytest.raises(mqtt.SessionError):
        mqtt.BrokerTls.from_pkcs12(data, "secret")
    tls = mqtt.BrokerTls.from_pkcs12(data, "secret", ca_pem=ca_pem)
    assert tls.verifies_peer
    assert tls.context.verify_mode == ssl.CERT_REQUIRED


def test_unverified_connection_is_an_explicit_logged_choice(caplog: pytest.LogCaptureFixture) -> None:
    data, _ca = _bundle(with_ca=False)
    with caplog.at_level(logging.WARNING):
        tls = mqtt.BrokerTls.from_pkcs12(data, "secret", allow_unverified=True)
    assert not tls.verifies_peer
    assert tls.context.verify_mode == ssl.CERT_NONE
    assert "without verifying" in caplog.text


def test_wrong_password_is_rejected() -> None:
    data, _ca = _bundle(with_ca=True)
    with pytest.raises(ValueError):
        mqtt.BrokerTls.from_pkcs12(data, "wrong")


def test_supplied_ca_and_bundle_chain_are_both_trusted() -> None:
    data, ca_pem = _bundle(with_ca=True)
    _other_key, other_ca = _cert("Another CA", ca=True)
    tls = mqtt.BrokerTls.from_pkcs12(data, "secret", ca_pem=other_ca.public_bytes(serialization.Encoding.PEM))
    assert tls.verifies_peer
    subjects = {cert["subject"][0][0][1] for cert in tls.context.get_ca_certs()}
    assert {"Another CA", "Test Broker CA"} <= subjects


def test_bundled_broker_ca_is_a_usable_authority() -> None:
    pem = mqtt.bundled_ca_pem()
    authority = x509.load_pem_x509_certificate(pem)
    assert authority.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    data, _ca = _bundle(with_ca=False)
    tls = mqtt.BrokerTls.from_pkcs12(data, "secret", ca_pem=pem)
    assert tls.verifies_peer
    assert tls.context.verify_mode == ssl.CERT_REQUIRED
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    assert not (tls.context.verify_flags & strict)
