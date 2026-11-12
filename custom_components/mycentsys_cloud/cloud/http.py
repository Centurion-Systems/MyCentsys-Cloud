"""HTTP client for the MyCentsys cloud backends.

Three backends serve an account, all on the account's datacentre
(:mod:`.regions`):

* the **gateway**: one-time PIN sign-in that issues the long-lived session
  token, the SMART operator listing and overview, the broker client
  certificate, and the token that unlocks G-Web;
* **G-Web**: the ULTRA module configuration, IO states, diagnostics and
  activations, authenticated by a credential derived once per session;
* **access sharing**: gates other accounts shared with this number.

Rules the client keeps:

* every request has a bounded timeout;
* nothing it logs can identify a user: no numbers, tokens, credentials, bodies
  or query strings, only the operation name, status and body size;
* every response is parsed strictly before it reaches a caller;
* no push notification registration is ever sent, so the MyCentsys Remote app
  on the same number keeps receiving its notifications.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
import logging
from collections.abc import Iterable
from typing import Any, Final
from urllib.parse import quote

import aiohttp

from . import numbers
from .errors import (
    AccessDenied,
    ActionRefused,
    ApiError,
    AuthError,
    CloudError,
    ConfigurationRequired,
    DeviceOffline,
    ModelError,
    OtpRejected,
    OtpThrottled,
    TimeBarred,
    TransportError,
)
from .models import (
    AccessGrant,
    ClientBundle,
    CloudSnapshot,
    GrantedAction,
    ModuleDiagnostics,
    Operator,
    ROLE_ADMIN,
    ROLE_REMOTE,
    UltraModule,
    UltraReadings,
)
from .regions import Region, region as region_for

__all__ = [
    "CloudClient",
    "OTP_WHATSAPP",
    "OTP_SMS",
    "PIN_LANGUAGE",
    "INTEGRATION_NAME",
    "gweb_data",
]

_LOGGER = logging.getLogger(__name__)

# How the one-time PIN is delivered.
OTP_WHATSAPP: Final = 1
OTP_SMS: Final = 2
# ISO 639-2/T code of the PIN message.
PIN_LANGUAGE: Final = "eng"

# How this client introduces itself to the integration sign-in routes.
INTEGRATION_NAME: Final = "HomeAssistant"
USER_AGENT: Final = "MyCentsysCloud-HomeAssistant/1.0"

DEFAULT_TIMEOUT: Final = 20.0

# --- gateway routes -------------------------------------------------------------------------
_EP_INTEGRATION_SEND_OTP: Final = "/IntegrationSendOtp"
_EP_INTEGRATION_VALIDATE_OTP: Final = "/IntegrationValidateOtp"
_EP_GWEB_TOKEN: Final = "/GetGwebToken"
_EP_DEVICES_BY_REMOTE_USER: Final = "/GetDevicesByRemoteUserNumber"
_EP_DEVICES_BY_ADMIN: Final = "/GetDevicesByAdminNumber"
_EP_OPERATOR_OVERVIEW: Final = "/GetOperatorOverview"
_EP_CERTIFICATE: Final = "/GetCertificate"

# --- G-Web routes -----------------------------------------------------------------------------
_EP_GWEB_CREDENTIAL: Final = "/api/MCROTPNumb"
# The configuration with each module's SIM type, and the one without it for a backend that has not got that yet.
_EP_GWEB_CONFIGURATION: Final = "/api/MCRConfEnV6"
_EP_GWEB_CONFIGURATION_PLAIN: Final = "/api/MCRConfEnV5"
_EP_GWEB_IO_STATES: Final = "/api/MCRIOStatesV2"
_EP_GWEB_ACTIVATE: Final = "/api/MCRActEnV2"
_EP_GWEB_STATUS: Final = "/api/MCRStatusV2"
_EP_GWEB_TOKENS: Final = "/api/MCRTokensV2"

# The credential call only issues to these application names.
_GWEB_APPLICATION: Final = "MyCentsys Remote"
# The device description that personalises the credential; fixed so the
# credential is stable for the account.
_GWEB_DEVICE_INFO: Final = "HomeAssistant"
# The build type G-Web records against configuration reads.
_GWEB_APP_TYPE: Final = "production"
_GWEB_ALL_DEVICES: Final = "-1"
_GWEB_STATUS_READ: Final = "1"
_GWEB_STATUS_QUEUE_AIRTIME: Final = "3"

# G-Web answers these requests with a sentence rather than a document.
_MSG_ACTIVATION_QUEUED: Final = "Activation Queued Successfully"
_MSG_STATUS_QUEUED: Final = "Status Queued Successfully"
_MSG_DEVICE_OFFLINE: Final = "Device is Offline"
_MSG_TIME_BARRED: Final = "Number is time barred"
_MSG_CONFIG_REQUIRED: Final = "Config Required"
_MSG_NO_MODULES: Final = "No Buttons for this number"
_MSG_NO_IOS: Final = "No IO's For This Number"
_MSG_STATUS_UNAVAILABLE: Final = "Status not available"
_MSG_NOT_A_TOKEN_SIM: Final = "Not a Token SIM"

# --- access sharing routes --------------------------------------------------------------------
_EP_SHARED_ACCESSES: Final = "/api/AccessSharing/GetAccessesByUserNumber"
_EP_SHARED_SEND_ACTIVATION: Final = "/api/Activation/SendActivation"
_EP_SHARED_ACTIVATION_RESULT: Final = "/api/Activation/ActivationDeviceResult"

# ``ActionResponseInterfaceEnum``.
_SHARE_RESPONSE_SUCCESS: Final = 1
_SHARE_RESPONSE_LABELS: Final[dict[int, str]] = {
    0: "the request failed",
    1: "accepted",
    2: "the access was revoked",
    3: "the device was not recognised",
    4: "the access has expired",
    5: "the gate could not be reached",
    6: "the access must be refreshed in the app",
    7: "the access was revoked by an administrator",
}
# ``AccessLogResultInterfaceEnum.Success``: what the access log records after a sent action.
_ACCESS_LOG_SUCCESS: Final = 2


def _expect_list(data: Any, op: str) -> list[Any]:
    """A listing answer, or an :class:`ApiError` naming the operation."""
    if isinstance(data, list):
        return data
    raise ApiError(f"{op}: the listing was unreadable")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def gweb_data(*parts: Any) -> dict[str, str]:
    """The body of a secured G-Web call: base64 parts joined with ``|``.

    The caller's number is never a part; the server takes it from the bearer.
    """
    return {"Data": "|".join(_b64(str(part)) for part in parts)}


def _unwrap(text: str) -> Any:
    """A G-Web body: a JSON document, a JSON-encoded sentence, or plain text."""
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except ValueError:
        return stripped.strip('"')


def _response_member(data: Any) -> Any:
    """The ``response`` member of a gateway or sharing answer, when the answer is an object."""
    match data:
        case {"response": value}:
            return value
    return None


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class CloudClient:
    """One account's view of the backends. The caller owns the aiohttp session."""

    def __init__(
        self,
        http: aiohttp.ClientSession,
        number: str,
        *,
        region: Region | str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._http = http
        self.number = numbers.normalize(number)
        self.region = region if isinstance(region, Region) else region_for(region)
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._gweb_credential: str | None = None
        self._gweb_gives_sim_types = True

    # -- state -----------------------------------------------------------------------------

    @property
    def token(self) -> str | None:
        """The session token (keep it secret; it is the account)."""
        return self._token

    @property
    def gweb_credential(self) -> str | None:
        return self._gweb_credential

    def _bearer(self) -> str:
        """The session token for a signed request; raises when there is no session."""
        if not self._token:
            raise AuthError("Not signed in")
        return self._token

    # -- transport ---------------------------------------------------------------------------

    async def _call(
        self,
        method: str,
        url: str,
        *,
        label: str,
        body: Any = None,
        form: str | None = None,
        bearer: str | None = None,
        accept: tuple[int, ...] = (200,),
    ) -> tuple[int, str, dict[str, str]]:
        """Perform one request; return (status, text, headers) or raise.

        Statuses in ``accept`` are returned; 401 and 403 raise :class:`AuthError`;
        anything else raises :class:`ApiError`.
        """
        # JSON only: with "*/*" in the list the gateway returns a bare string result
        # (GetGwebToken) as text/plain instead of a JSON string.
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        data: Any = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = form
        try:
            async with self._http.request(
                method, url, json=body, data=data, headers=headers, timeout=self._timeout
            ) as response:
                text = await response.text()
                status = response.status
                response_headers = {key: value for key, value in response.headers.items()}
        except asyncio.TimeoutError as err:
            _LOGGER.debug("%s: timed out", label)
            raise TransportError(f"{label}: the backend did not answer in time") from err
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.debug("%s: transport error (%s)", label, err.__class__.__name__)
            raise TransportError(f"{label}: the backend could not be reached") from err
        _LOGGER.debug("%s: HTTP %s, %d bytes", label, status, len(text))
        if status in accept:
            return status, text, response_headers
        if status in (401, 403):
            raise AuthError(f"{label}: the session was rejected")
        raise ApiError(f"{label}: unexpected answer (HTTP {status})", status)

    @staticmethod
    def _json(text: str, op: str) -> Any:
        try:
            return json.loads(text)
        except ValueError as err:
            raise ApiError(f"{op}: the answer was not JSON") from err

    def _gateway(self, path: str) -> str:
        return self.region.gateway_url + path

    def _gweb(self, path: str) -> str:
        return self.region.gweb_url + path

    def _sharing(self, path: str) -> str:
        return self.region.access_sharing_url + path

    # -- sign-in (gateway) ----------------------------------------------------------------------

    @staticmethod
    def _retry_after(headers: dict[str, str]) -> int:
        for key, value in headers.items():
            if key.lower() == "retry-after":
                try:
                    return max(1, int(value))
                except ValueError:
                    break
        return 300

    def _otp_request(self, platform: int, language: str) -> dict[str, Any]:
        """The body of a PIN request, introducing this integration by name."""
        return dict(
            MobileNumber=self.number,
            OtpPlatform=int(platform),
            ThreeLetterIsoLanguageName=language,
            IntegrationName=INTEGRATION_NAME,
        )

    async def request_otp(self, *, platform: int = OTP_WHATSAPP, language: str = PIN_LANGUAGE) -> bool:
        """Ask for a one-time PIN. True when the backend accepted the request.

        The integration sign-in route needs no credential. It answers the same for
        every number, so True does not prove a PIN was sent: one only goes to a
        number registered in the MyCentsys Remote app.
        """
        status, text, headers = await self._call(
            "POST",
            self._gateway(_EP_INTEGRATION_SEND_OTP),
            label="IntegrationSendOtp",
            body=self._otp_request(platform, language),
            accept=(200, 429),
        )
        if status == 429:
            raise OtpThrottled(self._retry_after(headers))
        return self._json(text, "IntegrationSendOtp") is True

    async def redeem_otp(self, otp: str) -> str:
        """Exchange the PIN for the session token, which is kept on the client."""
        pin = otp.strip()
        if not pin.isdigit():
            raise OtpRejected("The one-time PIN is digits only")
        status, text, headers = await self._call(
            "POST",
            self._gateway(_EP_INTEGRATION_VALIDATE_OTP),
            label="IntegrationValidateOtp",
            body={"MobileNumber": self.number, "Otp": pin, "IntegrationName": INTEGRATION_NAME},
            accept=(200, 429),
        )
        if status == 429:
            raise OtpThrottled(self._retry_after(headers))
        return self._accept_token(self._json(text, "IntegrationValidateOtp"))

    def _accept_token(self, data: Any) -> str:
        token = _response_member(data)
        if not isinstance(token, str) or not token.strip():
            raise OtpRejected("The one-time PIN was not accepted")
        self._token = token.strip()
        self._gweb_credential = None
        return self._token

    # -- G-Web credential -------------------------------------------------------------------------

    async def fetch_gweb_credential(self) -> str:
        """Derive the G-Web bearer credential for this account.

        The gateway issues a token for the signed-in number; G-Web verifies it
        against the number and application named in the request and answers
        with the credential every secured G-Web call then carries.
        """
        _, text, _ = await self._call(
            "POST",
            self._gateway(_EP_GWEB_TOKEN) + "?mobileNumber=" + quote(self.number, safe=""),
            label="GetGwebToken",
            bearer=self._bearer(),
        )
        # The gateway answers with a JSON string, or with the bare token as text when
        # content negotiation picks text/plain; take either.
        try:
            gweb_token = json.loads(text)
        except ValueError:
            gweb_token = text.strip().strip('"')
        if not isinstance(gweb_token, str) or not gweb_token.strip():
            raise ApiError("GetGwebToken: no token was issued")
        composed = f"{_GWEB_APPLICATION}|{self.number}|{_GWEB_DEVICE_INFO}:{gweb_token.strip()}"
        _, text, _ = await self._call(
            "POST",
            self._gweb(_EP_GWEB_CREDENTIAL),
            label="GwebCredential",
            bearer=_b64(composed),
            form="",
        )
        credential = _unwrap(text)
        if not isinstance(credential, str) or not credential.strip():
            raise ApiError("GwebCredential: no credential was issued")
        credential = credential.strip()
        try:
            decoded = base64.b64decode(credential, validate=True).decode("ascii")
        except (ValueError, UnicodeError) as err:
            raise ApiError("GwebCredential: the credential is malformed") from err
        if decoded.count("|") != 1:
            raise ApiError("GwebCredential: the credential is malformed")
        self._gweb_credential = credential
        return credential

    async def _gweb_call(self, op: str, path: str, *parts: Any, accept: tuple[int, ...] = (200,)) -> tuple[int, str]:
        """A secured G-Web call. A rejected credential is derived once more and the call retried."""
        for attempt in (1, 2):
            credential = self._gweb_credential or await self.fetch_gweb_credential()
            status, text, _ = await self._call(
                "POST",
                self._gweb(path),
                label=op,
                bearer=credential,
                body=gweb_data(*parts),
                accept=(*accept, 401, 403),
            )
            if status == 401:
                self._gweb_credential = None
                if attempt == 1:
                    continue
                raise ApiError(f"{op}: the ULTRA backend rejected the credential", 401)
            if status == 403:
                raise AccessDenied(f"{op}: this number has no access to the device")
            return status, text
        raise ApiError(f"{op}: the ULTRA backend rejected the credential", 401)

    @staticmethod
    def _queue_outcome(op: str, answer: Any, queued: str) -> None:
        text = answer if isinstance(answer, str) else ""
        if text == queued:
            return
        if text == _MSG_DEVICE_OFFLINE:
            raise DeviceOffline("The device is offline")
        if text == _MSG_TIME_BARRED:
            raise TimeBarred("This number is time barred on the device")
        if text == _MSG_CONFIG_REQUIRED:
            raise ConfigurationRequired("The device configuration must be refreshed first")
        raise ActionRefused(f"{op}: the ULTRA backend refused the request")

    # -- ULTRA modules (G-Web) ----------------------------------------------------------------------

    async def ultra_modules(self) -> list[UltraModule]:
        """Every ULTRA module this number may use, with its IO configuration."""
        answer = _unwrap(await self._configuration_text())
        if isinstance(answer, str):
            if answer == _MSG_NO_MODULES or not answer:
                _LOGGER.info("UltraConfiguration: the ULTRA backend lists no devices for this number")
                return []
            try:
                answer = json.loads(answer)
            except ValueError as err:
                raise ApiError("UltraConfiguration: the configuration was unreadable") from err
        if not isinstance(answer, dict):
            raise ApiError("UltraConfiguration: the configuration was unreadable")
        configs = answer.get("DeviceConfigs")
        if configs is None:
            configs = next((value for key, value in answer.items() if key.lower() == "deviceconfigs"), [])
        entries = _expect_list(configs, "UltraConfiguration")
        modules: list[UltraModule] = []
        for item in entries:
            try:
                modules.append(UltraModule.from_configuration(item))
            except ModelError as err:
                # Names of the members only, never their values: enough to see what shape arrived.
                members = sorted(str(key) for key in item) if isinstance(item, dict) else type(item).__name__
                _LOGGER.warning("UltraConfiguration: skipping a device entry (%s); members: %s", err, members)
        if entries and not modules:
            _LOGGER.warning("UltraConfiguration: %d device entries arrived but none could be read", len(entries))
        elif not entries:
            _LOGGER.info("UltraConfiguration: no device entries in the answer; members: %s", sorted(answer))
        return modules

    async def _configuration_text(self) -> str:
        """The configuration document, with each module's SIM type wherever the backend can give it."""
        if self._gweb_gives_sim_types:
            status, text = await self._gweb_call(
                "UltraConfiguration", _EP_GWEB_CONFIGURATION, _GWEB_ALL_DEVICES, _GWEB_APP_TYPE, accept=(200, 404)
            )
            if status == 200:
                return text
            # Not there on this backend: ask for the plain configuration from here on.
            self._gweb_gives_sim_types = False
        _, text = await self._gweb_call("UltraConfiguration", _EP_GWEB_CONFIGURATION_PLAIN, _GWEB_ALL_DEVICES, _GWEB_APP_TYPE)
        return text

    async def ultra_readings(self, device_id: int, *, feedback_number: int | None = None) -> UltraReadings:
        """Live IO states of one module (``online`` is False when it is not connected)."""
        _, text = await self._gweb_call("UltraIoStates", _EP_GWEB_IO_STATES, int(device_id))
        answer = _unwrap(text)
        if isinstance(answer, str):
            if answer == _MSG_DEVICE_OFFLINE or not answer:
                return UltraReadings.offline(int(device_id), feedback_number=feedback_number)
            if answer == _MSG_NO_IOS:
                return UltraReadings(device_id=int(device_id), feedback_number=feedback_number)
            try:
                answer = json.loads(answer)
            except ValueError as err:
                raise ApiError("UltraIoStates: the answer was unreadable") from err
        try:
            return UltraReadings.from_states(int(device_id), answer, feedback_number=feedback_number)
        except ModelError as err:
            raise ApiError("UltraIoStates: the answer was unreadable") from err

    async def pulse_ultra(self, device_id: int, io_number: int) -> None:
        """Queue a pulse on one IO; raises when the module backend refuses."""
        _, text = await self._gweb_call("UltraActivate", _EP_GWEB_ACTIVATE, int(device_id), int(io_number))
        self._queue_outcome("UltraActivate", _unwrap(text), _MSG_ACTIVATION_QUEUED)

    async def ultra_diagnostics(self, device_id: int) -> ModuleDiagnostics | None:
        """Last synchronised diagnostics, or None for a module that never synchronised."""
        status, text = await self._gweb_call("UltraDiagnostics", _EP_GWEB_STATUS, int(device_id), _GWEB_STATUS_READ, accept=(200, 400))
        if status == 400:
            return None
        answer = _unwrap(text)
        if isinstance(answer, str):
            if answer in (_MSG_DEVICE_OFFLINE, _MSG_STATUS_UNAVAILABLE) or not answer:
                return None
            try:
                answer = json.loads(answer)
            except ValueError as err:
                raise ApiError("UltraDiagnostics: the answer was unreadable") from err
        try:
            return ModuleDiagnostics.from_status(int(device_id), answer)
        except ModelError as err:
            raise ApiError("UltraDiagnostics: the answer was unreadable") from err

    async def ultra_airtime_refresh(self, device_id: int) -> None:
        """Ask the module for its network balance: the general refresh (the answer arrives on a later diagnostics read)."""
        _, text = await self._gweb_call("UltraAirtime", _EP_GWEB_STATUS, int(device_id), _GWEB_STATUS_QUEUE_AIRTIME)
        self._queue_outcome("UltraAirtime", _unwrap(text), _MSG_STATUS_QUEUED)

    async def ultra_tokens_refresh(self, device_id: int) -> None:
        """Ask a module on a token SIM for its call and SMS tokens (the answer arrives on a later diagnostics read).

        A backend that does not have the token request yet, or that says the SIM
        holds no tokens, is asked for the general refresh instead.
        """
        status, text = await self._gweb_call("UltraTokens", _EP_GWEB_TOKENS, int(device_id), accept=(200, 404))
        answer = _unwrap(text) if status == 200 else None
        if status == 404 or answer == _MSG_NOT_A_TOKEN_SIM:
            await self.ultra_airtime_refresh(device_id)
            return
        self._queue_outcome("UltraTokens", answer, _MSG_STATUS_QUEUED)

    # -- SMART operators (gateway) ----------------------------------------------------------------------

    async def _operator_listing(self, path: str, parameter: str, op: str, role: str) -> list[Operator]:
        _, text, _ = await self._call(
            "POST",
            self._gateway(path) + f"?{parameter}=" + quote(self.number, safe=""),
            label=op,
            bearer=self._bearer(),
        )
        operators: list[Operator] = []
        for item in _expect_list(self._json(text, op), op):
            try:
                operators.append(Operator.from_listing(item, role=role))
            except ModelError:
                _LOGGER.debug("%s: skipping an operator entry with an unexpected shape", op)
        return operators

    async def remote_operators(self) -> list[Operator]:
        """Operators on which this number is a remote user."""
        return await self._operator_listing(_EP_DEVICES_BY_REMOTE_USER, "remoteUserNumber", "DevicesByRemoteUser", ROLE_REMOTE)

    async def admin_operators(self) -> list[Operator]:
        """Operators this number administers. Empty when the datacentre has no answer."""
        try:
            return await self._operator_listing(_EP_DEVICES_BY_ADMIN, "adminNumber", "DevicesByAdmin", ROLE_ADMIN)
        except ApiError as err:
            if err.status in (400, 404):
                return []
            raise

    async def cloud_snapshots(self, serials: Iterable[str]) -> list[CloudSnapshot]:
        """The gateway's last recorded state of each listed operator."""
        wanted = [serial for serial in serials if serial]
        if not wanted:
            return []
        status, text, _ = await self._call(
            "POST",
            self._gateway(_EP_OPERATOR_OVERVIEW),
            label="OperatorOverview",
            bearer=self._bearer(),
            body={"OperatorSerialNumbers": wanted},
            accept=(200, 404),
        )
        if status == 404:
            return []
        snapshots: list[CloudSnapshot] = []
        for item in _expect_list(self._json(text, "OperatorOverview"), "OperatorOverview"):
            try:
                snapshots.append(CloudSnapshot.from_overview(item))
            except ModelError:
                _LOGGER.debug("OperatorOverview: skipping an entry with an unexpected shape")
        return snapshots

    async def client_bundle(self) -> ClientBundle:
        """The broker client certificate bundle issued to the account."""
        _, text, _ = await self._call(
            "POST",
            self._gateway(_EP_CERTIFICATE),
            label="Certificate",
            bearer=self._bearer(),
            body={},
        )
        data = self._json(text, "Certificate")
        lowered = {str(key).lower(): value for key, value in data.items()} if isinstance(data, dict) else {}
        pfx_b64 = lowered.get("certificatepfxbase64")
        password = lowered.get("certificatepassword")
        if not isinstance(pfx_b64, str) or not pfx_b64.strip():
            raise ApiError("Certificate: no certificate was issued")
        try:
            pfx = base64.b64decode(pfx_b64.strip(), validate=True)
        except ValueError as err:
            raise ApiError("Certificate: the certificate was malformed") from err
        return ClientBundle(pfx=pfx, password=password if isinstance(password, str) else "")

    # -- access sharing ---------------------------------------------------------------------------------

    async def access_grants(self) -> list[AccessGrant]:
        """Gates shared with this number. Empty when the service has none."""
        status, text, _ = await self._call(
            "POST",
            self._sharing(_EP_SHARED_ACCESSES),
            label="SharedAccesses",
            bearer=self._bearer(),
            body={"Number": self.number},
            accept=(200, 204, 400, 404),
        )
        if status != 200 or not text.strip():
            return []
        data = self._json(text, "SharedAccesses")
        items: Any
        if isinstance(data, dict):
            items = next((value for key, value in data.items() if key.lower() == "sharedaccesses"), None)
            if items is None:
                items = next((value for value in data.values() if isinstance(value, list)), [])
        elif isinstance(data, list):
            items = data
        else:
            raise ApiError("SharedAccesses: the answer was unreadable")
        grants: list[AccessGrant] = []
        for item in items if isinstance(items, list) else []:
            try:
                grants.append(AccessGrant.from_share(item))
            except ModelError:
                _LOGGER.debug("SharedAccesses: skipping an entry with an unexpected shape")
        return grants

    def _grant_request(self, grant: AccessGrant, action: GrantedAction, timestamp: str) -> dict[str, Any]:
        """``SendActivation``: what the sharing service checks before it lets the pulse through."""
        return dict(
            PreviousTrigger=None,
            AccessGuid=grant.guid,
            Timestamp=timestamp,
            RequestingNumber=self.number,
            PhoneMacAddress=None,
            SharedAccessLastModifiedDateUtc=grant.modified_utc,
            UserSharedAccessLastModifiedDateUtc=grant.holder_modified_utc(self.number),
            TriggerId=action.io_number,
            TriggerName=action.name,
        )

    def _grant_outcome(self, grant: AccessGrant, action: GrantedAction, timestamp: str) -> dict[str, Any]:
        """``ActivationDeviceResult``: the access-log entry for a pulse that went through."""
        return dict(
            AccessGuid=grant.guid,
            ActivationTimestampUtc=timestamp,
            RequestingNumber=self.number,
            TriggerId=action.io_number,
            TriggerName=action.name,
            Result=_ACCESS_LOG_SUCCESS,
        )

    async def use_grant(self, grant: AccessGrant, action: GrantedAction) -> None:
        """Perform one action on a shared ULTRA gate.

        The sharing service authorises the request and enforces the share's
        limits, the module backend queues the pulse, and the result is reported
        back to the share's access log.
        """
        if not grant.is_ultra or grant.ultra_device_id is None:
            raise CloudError("Only shared GSM gates can be operated remotely")
        bearer = self._bearer()
        timestamp = _timestamp()
        _, text, _ = await self._call(
            "POST",
            self._sharing(_EP_SHARED_SEND_ACTIVATION),
            label="SharedActivation",
            bearer=bearer,
            body=self._grant_request(grant, action, timestamp),
            accept=(200, 202),
        )
        code = _response_member(self._json(text, "SharedActivation"))
        if code != _SHARE_RESPONSE_SUCCESS:
            reason = _SHARE_RESPONSE_LABELS.get(code, "the request was refused") if isinstance(code, int) else "the request was refused"
            raise ActionRefused(f"The shared gate did not accept the action: {reason}")
        await self.pulse_ultra(grant.ultra_device_id, action.io_number)
        try:
            await self._call(
                "POST",
                self._sharing(_EP_SHARED_ACTIVATION_RESULT),
                label="SharedActivationResult",
                bearer=bearer,
                body=self._grant_outcome(grant, action, timestamp),
                accept=(200, 202, 204),
            )
        except CloudError:
            _LOGGER.debug("SharedActivationResult: the access log could not be updated")
