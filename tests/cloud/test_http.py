"""The HTTP client, driven against a fake aiohttp session.

Pins the contracts: which routes are called with which bodies and bearers,
sign-in without any built-in credential, throttling, credential re-derivation, every G-Web
sentence, and that nothing identifying is logged.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import json
import logging
import pathlib
import re
from typing import Any

import aiohttp
import pytest

from mycentsys_cloud_protocol import http
from mycentsys_cloud_protocol.errors import (
    AccessDenied,
    ActionRefused,
    ApiError,
    AuthError,
    ConfigurationRequired,
    DeviceOffline,
    OtpRejected,
    OtpThrottled,
    TimeBarred,
    TransportError,
)
from mycentsys_cloud_protocol.models import AccessGrant

NUMBER = "+27820000000"
TOKEN = "session-token"


@dataclass
class Call:
    method: str
    url: str
    headers: dict[str, str]
    json: Any
    data: Any

    @property
    def path(self) -> str:
        return self.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]

    @property
    def bearer(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth[len("Bearer ") :] if auth.startswith("Bearer ") else None


@dataclass
class Answer:
    status: int = 200
    body: Any = ""
    headers: dict[str, str] = field(default_factory=dict)
    raise_error: Exception | None = None

    @property
    def text(self) -> str:
        return self.body if isinstance(self.body, str) else json.dumps(self.body)


class _Response:
    def __init__(self, answer: Answer) -> None:
        self.status = answer.status
        self.headers = answer.headers
        self._answer = answer

    async def text(self) -> str:
        return self._answer.text

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeSession:
    """Answers each route from a queue; records every call."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.answers: dict[str, list[Answer]] = {}

    def on(self, path: str, *answers: Answer) -> None:
        self.answers.setdefault(path, []).extend(answers)

    def request(self, method: str, url: str, *, headers=None, json=None, data=None, timeout=None):
        call = Call(method, url, dict(headers or {}), json, data)
        self.calls.append(call)
        queue = self.answers.get(call.path)
        if not queue:
            raise AssertionError(f"unexpected call to {call.path}")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if answer.raise_error is not None:
            raise answer.raise_error
        assert timeout is not None, "every request must carry a timeout"
        return _Response(answer)


def _client(session: FakeSession, *, token: str | None = TOKEN) -> http.CloudClient:
    return http.CloudClient(session, NUMBER, region="za", token=token)  # type: ignore[arg-type]


def _run(coro):
    return asyncio.run(coro)


def _gweb_json(document: Any) -> str:
    """A G-Web answer: a JSON string whose content is itself JSON."""
    return json.dumps(json.dumps(document))


def _credential() -> str:
    return base64.b64encode(b"key|cipher").decode()


def _decode_parts(call: Call) -> list[str]:
    return [base64.b64decode(part).decode() for part in call.json["Data"].split("|")]


# --- sign-in ------------------------------------------------------------------------------------


def test_send_otp_uses_the_integration_route_without_a_credential() -> None:
    session = FakeSession()
    session.on("IntegrationSendOtp", Answer(200, True))
    client = _client(session, token=None)
    assert _run(client.request_otp(platform=http.OTP_SMS)) is True
    call = session.calls[0]
    assert call.path == "IntegrationSendOtp"
    assert call.bearer is None
    assert call.json == {"MobileNumber": NUMBER, "OtpPlatform": 2, "ThreeLetterIsoLanguageName": "eng", "IntegrationName": "HomeAssistant"}


def test_sign_in_has_no_fallback_to_a_credentialed_route() -> None:
    session = FakeSession()
    session.on("IntegrationSendOtp", Answer(404, ""))
    session.on("IntegrationValidateOtp", Answer(404, ""))
    client = _client(session, token=None)
    with pytest.raises(ApiError):
        _run(client.request_otp())
    with pytest.raises(ApiError):
        _run(client.redeem_otp("1234"))
    assert [call.path for call in session.calls] == ["IntegrationSendOtp", "IntegrationValidateOtp"]
    assert all(call.bearer is None for call in session.calls)
    assert client.token is None


def test_no_credential_ships_in_the_source() -> None:
    # A JSON Web Token in the source would be a credential anyone could read in the public repository.
    repository = pathlib.Path(http.__file__).resolve().parents[3]
    token = re.compile(r"eyJ[A-Za-z0-9_-]{10,}")
    sources = [*repository.glob("custom_components/**/*.py"), *repository.glob("scripts/*")]
    assert sources, "the integration sources were not found"
    offenders = [path.name for path in sources if path.is_file() and token.search(path.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == []


def test_otp_throttling_surfaces_the_retry_after() -> None:
    session = FakeSession()
    session.on("IntegrationSendOtp", Answer(429, "", {"Retry-After": "120"}))
    with pytest.raises(OtpThrottled) as info:
        _run(_client(session, token=None).request_otp())
    assert info.value.retry_after == 120


def test_validate_otp_rejects_an_empty_token() -> None:
    session = FakeSession()
    session.on("IntegrationValidateOtp", Answer(200, {"response": ""}))
    client = _client(session, token=None)
    with pytest.raises(OtpRejected):
        _run(client.redeem_otp("1234"))
    assert client.token is None
    with pytest.raises(OtpRejected):
        _run(client.redeem_otp("12a4"))


def test_validate_otp_sends_the_integration_name() -> None:
    session = FakeSession()
    session.on("IntegrationValidateOtp", Answer(200, {"response": TOKEN}))
    client = _client(session, token=None)
    assert _run(client.redeem_otp(" 1234 ")) == TOKEN
    assert session.calls[0].json == {"MobileNumber": NUMBER, "Otp": "1234", "IntegrationName": "HomeAssistant"}


def test_calls_without_a_session_fail_as_auth_errors() -> None:
    client = _client(FakeSession(), token=None)
    with pytest.raises(AuthError):
        _run(client.remote_operators())


# --- gateway ------------------------------------------------------------------------------------


def test_devices_listing_uses_the_bearer_and_query() -> None:
    session = FakeSession()
    session.on("GetDevicesByRemoteUserNumber", Answer(200, [{"serialNumber": "S1", "deviceName": "Gate"}, {"deviceName": "no serial"}]))
    devices = _run(_client(session).remote_operators())
    assert [device.serial for device in devices] == ["S1"]
    call = session.calls[0]
    assert call.method == "POST"
    assert call.bearer == TOKEN
    assert "remoteUserNumber=%2B27820000000" in call.url


def test_admin_listing_is_optional() -> None:
    session = FakeSession()
    session.on("GetDevicesByAdminNumber", Answer(404, ""))
    assert _run(_client(session).admin_operators()) == []


def test_rejected_session_is_an_auth_error() -> None:
    session = FakeSession()
    session.on("GetDevicesByRemoteUserNumber", Answer(401, ""))
    with pytest.raises(AuthError):
        _run(_client(session).remote_operators())


def test_transport_failures_are_transport_errors() -> None:
    session = FakeSession()
    session.on("GetDevicesByRemoteUserNumber", Answer(raise_error=aiohttp.ClientConnectionError("boom")))
    with pytest.raises(TransportError):
        _run(_client(session).remote_operators())
    session = FakeSession()
    session.on("GetDevicesByRemoteUserNumber", Answer(raise_error=asyncio.TimeoutError()))
    with pytest.raises(TransportError):
        _run(_client(session).remote_operators())


def test_operator_overview_posts_the_serials() -> None:
    session = FakeSession()
    session.on("GetOperatorOverview", Answer(200, [{"operatorSerialNumber": "S1", "operatorStatus": 1}]))
    statuses = _run(_client(session).cloud_snapshots(["S1", ""]))
    assert statuses[0].position_label == "open"
    assert session.calls[0].json == {"OperatorSerialNumbers": ["S1"]}
    assert _run(_client(session).cloud_snapshots([])) == []


def test_certificate_is_decoded() -> None:
    session = FakeSession()
    session.on("GetCertificate", Answer(200, {"certificatePfxBase64": base64.b64encode(b"pfx").decode(), "certificatePassword": "pw"}))
    certificate = _run(_client(session).client_bundle())
    assert certificate.pfx == b"pfx" and certificate.password == "pw"
    session = FakeSession()
    session.on("GetCertificate", Answer(200, {"certificatePfxBase64": ""}))
    with pytest.raises(ApiError):
        _run(_client(session).client_bundle())


# --- G-Web --------------------------------------------------------------------------------------


def _with_gweb(session: FakeSession) -> None:
    session.on("GetGwebToken", Answer(200, json.dumps("gweb-token")))
    session.on("api/MCROTPNumb", Answer(200, json.dumps(_credential())))


def test_gweb_credential_derivation() -> None:
    session = FakeSession()
    _with_gweb(session)
    client = _client(session)
    assert _run(client.fetch_gweb_credential()) == _credential()
    token_call, credential_call = session.calls
    assert token_call.bearer == TOKEN
    assert "mobileNumber=%2B27820000000" in token_call.url
    composed = base64.b64decode(credential_call.bearer).decode()
    assert composed == f"MyCentsys Remote|{NUMBER}|HomeAssistant:gweb-token"
    assert credential_call.json is None


def test_gweb_token_answered_as_plain_text_is_accepted() -> None:
    # The gateway returns a bare string result as text/plain when the Accept header
    # admits "*/*"; the client asks for JSON only and still copes with plain text.
    session = FakeSession()
    session.on("GetGwebToken", Answer(200, "gweb-token"))
    session.on("api/MCROTPNumb", Answer(200, json.dumps(_credential())))
    client = _client(session)
    assert _run(client.fetch_gweb_credential()) == _credential()
    token_call, credential_call = session.calls
    assert token_call.headers.get("Accept") == "application/json"
    composed = base64.b64decode(credential_call.bearer).decode()
    assert composed.endswith(":gweb-token")


def test_gweb_credential_must_be_well_formed() -> None:
    session = FakeSession()
    session.on("GetGwebToken", Answer(200, json.dumps("gweb-token")))
    session.on("api/MCROTPNumb", Answer(200, json.dumps(base64.b64encode(b"no-separator").decode())))
    with pytest.raises(ApiError):
        _run(_client(session).fetch_gweb_credential())


def test_gweb_body_is_pipe_joined_base64_without_the_number() -> None:
    assert http.gweb_data(12, "production") == {"Data": "MTI=|cHJvZHVjdGlvbg=="}


def test_configuration_read_and_parse() -> None:
    session = FakeSession()
    _with_gweb(session)
    document = {"DeviceCount": 1, "DeviceConfigs": [{"DeviceId": 7, "DeviceName": "Home", "DeviceType": 16, "IOConfigs": [{"IONumber": 1, "IOName": "Gate"}]}]}
    document["DeviceConfigs"][0]["SimType"] = 0
    session.on("api/MCRConfEnV6", Answer(200, _gweb_json(document)))
    client = _client(session)
    modules = _run(client.ultra_modules())
    assert [module.device_id for module in modules] == [7] and modules[0].token_sim is True
    call = session.calls[-1]
    assert call.bearer == _credential()
    assert _decode_parts(call) == ["-1", "production"]
    assert "player" not in json.dumps(call.json).lower()


def test_no_modules_sentence_means_an_empty_list() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRConfEnV6", Answer(200, json.dumps("No Buttons for this number")))
    assert _run(_client(session).ultra_modules()) == []


def test_a_backend_without_sim_types_is_asked_for_the_plain_configuration() -> None:
    session = FakeSession()
    _with_gweb(session)
    document = {"DeviceCount": 1, "DeviceConfigs": [{"DeviceId": 7, "DeviceName": "Home", "DeviceType": 16, "IOConfigs": [{"IONumber": 1, "IOName": "Gate"}]}]}
    session.on("api/MCRConfEnV6", Answer(404, "<html>not found</html>"))
    session.on("api/MCRConfEnV5", Answer(200, _gweb_json(document)), Answer(200, _gweb_json(document)))
    client = _client(session)
    modules = _run(client.ultra_modules())
    assert [module.device_id for module in modules] == [7] and modules[0].token_sim is None
    assert _decode_parts(session.calls[-1]) == ["-1", "production"]
    # Asked once; after that the plain configuration is read straight away.
    _run(client.ultra_modules())
    assert [call.path for call in session.calls].count("api/MCRConfEnV6") == 1


def test_rejected_credential_is_derived_again_once() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRIOStatesV2", Answer(401, ""), Answer(200, _gweb_json({"DeviceId": 7, "RefreshPermissionVersion": 3, "IOList": [{"IOStateID": 88}]})))
    client = _client(session)
    status = _run(client.ultra_readings(7, feedback_number=4))
    assert status.online and status.is_on(1) is True and status.config_version == 3
    paths = [call.path for call in session.calls]
    assert paths.count("GetGwebToken") == 2
    assert paths.count("api/MCRIOStatesV2") == 2


def test_persistent_rejection_is_an_api_error_not_a_reauth() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRIOStatesV2", Answer(401, ""))
    with pytest.raises(ApiError):
        _run(_client(session).ultra_readings(7))


def test_forbidden_module_is_access_denied() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRIOStatesV2", Answer(403, ""))
    with pytest.raises(AccessDenied):
        _run(_client(session).ultra_readings(7))


def test_offline_module_is_an_offline_status() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRIOStatesV2", Answer(200, json.dumps("Device is Offline")))
    status = _run(_client(session).ultra_readings(7, feedback_number=4))
    assert not status.online and status.feedback_number == 4


def test_activation_sentences() -> None:
    for sentence, error in (
        ("Device is Offline", DeviceOffline),
        ("Number is time barred", TimeBarred),
        ("Config Required", ConfigurationRequired),
        ("Something else", ActionRefused),
    ):
        session = FakeSession()
        _with_gweb(session)
        session.on("api/MCRActEnV2", Answer(200, json.dumps(sentence)))
        with pytest.raises(error):
            _run(_client(session).pulse_ultra(7, 2))
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRActEnV2", Answer(200, json.dumps("Activation Queued Successfully")))
    _run(_client(session).pulse_ultra(7, 2))
    assert _decode_parts(session.calls[-1]) == ["7", "2"]


def test_diagnostics_and_airtime() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRStatusV2", Answer(200, _gweb_json({"Voltage": "13.1", "Signal": 20, "Airtime": "Call: 1 SMS: 2"})), Answer(200, json.dumps("Status Queued Successfully")))
    client = _client(session)
    diag = _run(client.ultra_diagnostics(7))
    assert diag is not None and diag.supply_volts == 13.1 and diag.call_credits == 1
    assert _decode_parts(session.calls[-1]) == ["7", "1"]
    _run(client.ultra_airtime_refresh(7))
    assert _decode_parts(session.calls[-1]) == ["7", "3"]


def test_tokens_have_a_request_of_their_own() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRTokensV2", Answer(200, json.dumps("Status Queued Successfully")))
    _run(_client(session).ultra_tokens_refresh(7))
    assert session.calls[-1].path == "api/MCRTokensV2" and _decode_parts(session.calls[-1]) == ["7"]
    assert session.calls[-1].bearer == _credential()


def test_a_token_request_falls_back_to_the_general_refresh() -> None:
    # A backend without the token request, and a SIM that turns out to hold no tokens.
    for answer in (Answer(404, "<html>not found</html>"), Answer(200, json.dumps("Not a Token SIM"))):
        session = FakeSession()
        _with_gweb(session)
        session.on("api/MCRTokensV2", answer)
        session.on("api/MCRStatusV2", Answer(200, json.dumps("Status Queued Successfully")))
        _run(_client(session).ultra_tokens_refresh(7))
        assert session.calls[-1].path == "api/MCRStatusV2" and _decode_parts(session.calls[-1]) == ["7", "3"]


def test_a_token_request_to_an_offline_device_says_so() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRTokensV2", Answer(200, json.dumps("Device is Offline")))
    with pytest.raises(DeviceOffline):
        _run(_client(session).ultra_tokens_refresh(7))
    assert all(call.path != "api/MCRStatusV2" for call in session.calls)


def test_never_synchronised_module_has_no_diagnostics() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/MCRStatusV2", Answer(400, "Status not available"))
    assert _run(_client(session).ultra_diagnostics(7)) is None


# --- access sharing --------------------------------------------------------------------------------


def _share(ultra: bool = True) -> dict[str, Any]:
    return {
        "guid": "3f2c9a10-5b7e-4d21-9c44-0a1b2c3d4e5f",
        "deviceName": "Estate",
        "shareDeviceType": 1 if ultra else 2,
        "ultraDeviceId": 555 if ultra else None,
        "lastModifiedDateUtc": "2025-06-15T00:00:00",
        "serializedSharedAccessPayload": json.dumps({"Activations": [{"Id": 1, "Name": "OpenCloseDescription", "TriggerId": 34, "IoNumber": 1}]}),
        "users": [{"currentTriggerCount": 0, "user": {"number": NUMBER, "lastModifiedDateUtc": "2024-01-02T00:00:00"}}],
    }


def test_shared_accesses_listing() -> None:
    session = FakeSession()
    session.on("api/AccessSharing/GetAccessesByUserNumber", Answer(200, {"sharedAccesses": [_share()]}))
    shares = _run(_client(session).access_grants())
    assert len(shares) == 1 and shares[0].is_ultra
    assert session.calls[0].json == {"Number": NUMBER}
    session = FakeSession()
    session.on("api/AccessSharing/GetAccessesByUserNumber", Answer(404, ""))
    assert _run(_client(session).access_grants()) == []


def test_shared_trigger_sequence() -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("api/Activation/SendActivation", Answer(200, {"response": 1}))
    session.on("api/MCRActEnV2", Answer(200, json.dumps("Activation Queued Successfully")))
    session.on("api/Activation/ActivationDeviceResult", Answer(200, ""))
    share = AccessGrant.from_share(_share())
    _run(_client(session).use_grant(share, share.gate_action))
    paths = [call.path for call in session.calls]
    assert paths == ["api/Activation/SendActivation", "GetGwebToken", "api/MCROTPNumb", "api/MCRActEnV2", "api/Activation/ActivationDeviceResult"]
    send = session.calls[0].json
    assert send["AccessGuid"] == share.guid
    assert send["TriggerId"] == 1 and send["TriggerName"] == "OpenCloseDescription"
    assert send["UserSharedAccessLastModifiedDateUtc"] == "2024-01-02T00:00:00"
    assert send["PhoneMacAddress"] is None
    assert _decode_parts(session.calls[3]) == ["555", "1"]
    assert session.calls[4].json["Result"] == 2


def test_shared_trigger_refusal_stops_before_the_module() -> None:
    session = FakeSession()
    session.on("api/Activation/SendActivation", Answer(200, {"response": 4}))
    share = AccessGrant.from_share(_share())
    with pytest.raises(ActionRefused) as info:
        _run(_client(session).use_grant(share, share.gate_action))
    assert "expired" in str(info.value)
    assert len(session.calls) == 1


def test_smart_share_cannot_be_triggered() -> None:
    share = AccessGrant.from_share(_share(ultra=False))
    with pytest.raises(Exception):
        _run(_client(FakeSession()).use_grant(share, share.gate_action))


# --- hygiene ----------------------------------------------------------------------------------------


def test_logs_never_carry_numbers_tokens_or_bodies(caplog: pytest.LogCaptureFixture) -> None:
    session = FakeSession()
    _with_gweb(session)
    session.on("GetDevicesByRemoteUserNumber", Answer(200, [{"serialNumber": "S1"}]))
    session.on("api/MCRActEnV2", Answer(200, json.dumps("Device is Offline")))
    client = _client(session)
    with caplog.at_level(logging.DEBUG):
        _run(client.remote_operators())
        with pytest.raises(DeviceOffline):
            _run(client.pulse_ultra(7, 1))
    text = caplog.text
    for secret in ("27820000000", TOKEN, "gweb-token", _credential(), "S1"):
        assert secret not in text


def test_no_push_registration_anywhere() -> None:
    import inspect

    source = inspect.getsource(http)
    assert "playerid" not in source.lower().replace("_", "")
    assert "onesignal" not in source.lower()
