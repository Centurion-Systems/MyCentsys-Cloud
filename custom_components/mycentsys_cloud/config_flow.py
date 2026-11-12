"""Sign-in and options.

The account is a mobile number, proven by a one-time PIN delivered over
WhatsApp or SMS. Sign-in asks for the country code and the number, requests
the PIN, redeems it for the session token, then asks which devices Home
Assistant should include. The datacentre is never asked for: it follows from
the country code. Signing in again reuses the same PIN exchange.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_COUNTRY, CONF_DEVICES, CONF_REGION, CONF_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .calling_codes import CALLING_CODES, HOME_COUNTRY, country, datacentre_for_country
from .cloud.errors import CloudError, OtpRejected, OtpThrottled
from .cloud.http import CloudClient
from .cloud.models import combine_listings
from .cloud.numbers import to_international
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_DEVICE_SCOPE,
    CONF_PIN_DELIVERY,
    CONF_ULTRA_POLL_INTERVAL,
    DEFAULT_PIN_DELIVERY,
    DEFAULT_ULTRA_POLL_INTERVAL,
    DEVICE_SCOPES,
    DOMAIN,
    MAX_ULTRA_POLL_INTERVAL,
    MIN_ULTRA_POLL_INTERVAL,
    PIN_BY_SMS,
    PIN_BY_WHATSAPP,
    SCOPE_ALL,
    SCOPE_CHOSEN,
    SIGN_IN_DEVICE_SCOPE,
)
from .fleet import entry_title, scope_of

_PIN_CHANNELS = {str(PIN_BY_WHATSAPP): "WhatsApp", str(PIN_BY_SMS): "SMS"}
_LOGGER = logging.getLogger(__name__)

_DIGITS = re.compile(r"[0-9]+")
_MIN_NUMBER_LENGTH = 8

# Error keys of strings.json.
_FAIL_CONNECT = "cannot_connect"
_FAIL_DIGITS = "digits_only"
_FAIL_NUMBER = "number_not_valid"
_FAIL_PIN = "pin_rejected"
_FAIL_NOT_SENT = "pin_not_sent"
_FAIL_THROTTLED = "pin_throttled"
_FAIL_NOTHING_CHOSEN = "nothing_chosen"


def _country_code_dropdown() -> selector.SelectSelector:
    """Calling codes with their country, searchable by either."""
    options = [selector.SelectOptionDict(value=entry.code, label=f"+{entry.dial}  {entry.name}") for entry in CALLING_CODES]
    return selector.SelectSelector(selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN))


def _number_field() -> selector.TextSelector:
    """A telephone field, so a phone shows its number pad."""
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TEL))


def _account_form() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_COUNTRY, default=HOME_COUNTRY): _country_code_dropdown(),
            vol.Required(CONF_ACCOUNT_NUMBER): _number_field(),
            vol.Required(CONF_PIN_DELIVERY, default=str(DEFAULT_PIN_DELIVERY)): vol.In(_PIN_CHANNELS),
        }
    )


def _pin_form() -> vol.Schema:
    return vol.Schema({vol.Required("pin"): str})


def _devices_fields(known: Mapping[str, str], scope: str, chosen: list[str]) -> dict[Any, Any]:
    """The two device fields: what to include, and the searchable list used by ``chosen``.

    The list starts empty unless devices were chosen before, so picking a few
    out of many is a matter of typing part of a name and ticking it. Sign-in
    opens on ``chosen``; the options show what the entry holds.
    """
    scopes = selector.SelectSelector(
        selector.SelectSelectorConfig(options=list(DEVICE_SCOPES), mode=selector.SelectSelectorMode.LIST, translation_key=CONF_DEVICE_SCOPE)
    )
    devices = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=key, label=label) for key, label in known.items()],
            multiple=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    return {
        vol.Required(CONF_DEVICE_SCOPE, default=scope): scopes,
        vol.Optional(CONF_DEVICES, default=[key for key in chosen if key in known]): devices,
    }


def device_choice(user_input: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """The options a submitted device form stands for, and the error key when it cannot be accepted."""
    scope = user_input.get(CONF_DEVICE_SCOPE)
    scope = scope if scope in DEVICE_SCOPES else SCOPE_ALL
    chosen = list(user_input.get(CONF_DEVICES) or []) if scope == SCOPE_CHOSEN else []
    failure = _FAIL_NOTHING_CHOSEN if scope == SCOPE_CHOSEN and not chosen else None
    return {CONF_DEVICE_SCOPE: scope, CONF_DEVICES: chosen}, failure


def digits_only(typed: Any) -> str | None:
    """The number as typed when it is made of digits alone, else None."""
    text = str(typed or "").strip()
    return text if _DIGITS.fullmatch(text) else None


def _international(local_number: str, country_code: str) -> str:
    """The account number in international form, or an empty string when it cannot be formed."""
    entry = country(country_code)
    if entry is None:
        return ""
    try:
        number = to_international(local_number, entry.dial)
    except ValueError:
        return ""
    return number if number.lstrip("+").isdigit() and len(number) >= _MIN_NUMBER_LENGTH else ""


class _PinExchange:
    """One PIN exchange against one datacentre; the flows share it."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self.client: CloudClient | None = None
        self.sent = False
        self.token: str | None = None

    async def request(self, number: str, datacentre: str, channel: int) -> str | None:
        """Ask for a PIN; the error key when it could not be sent."""
        self.client = CloudClient(async_get_clientsession(self._hass), number, region=datacentre)
        try:
            accepted = await self.client.request_otp(platform=channel)
        except OtpThrottled:
            return _FAIL_THROTTLED
        except CloudError:
            return _FAIL_CONNECT
        self.sent = accepted
        return None if accepted else _FAIL_NOT_SENT

    async def redeem(self, pin: str) -> str | None:
        """Exchange an entered PIN for the session token (kept on ``token``); the error key on failure."""
        if self.client is None:
            return _FAIL_CONNECT
        try:
            self.token = await self.client.redeem_otp(pin)
        except OtpThrottled:
            return _FAIL_THROTTLED
        except OtpRejected:
            return _FAIL_PIN
        except CloudError:
            return _FAIL_CONNECT
        return None

    async def discover(self) -> dict[str, str]:
        """Every device linked to the signed-in number, as fleet key -> picker label."""
        client = self.client
        if client is None:
            return {}
        found: dict[str, str] = {}
        try:
            for operator in combine_listings(await client.remote_operators(), await client.admin_operators()):
                found[operator.serial] = f"{operator.label or operator.serial} (SMART)"
        except CloudError as err:
            _LOGGER.warning("Device picker: SMART operators could not be listed (%s)", err)
        try:
            for module in await client.ultra_modules():
                found[module.key] = f"{module.label or module.key} ({module.profile_label})"
        except CloudError as err:
            _LOGGER.warning("Device picker: ULTRA devices could not be listed (%s)", err)
        try:
            for grant in await client.access_grants():
                if not grant.granted_by(client.number):
                    found[grant.key] = f"{grant.device_label or grant.guid} (shared)"
        except CloudError as err:
            _LOGGER.warning("Device picker: shared gates could not be listed (%s)", err)
        return found


class MyCentsysConfigFlow(ConfigFlow, domain=DOMAIN):
    """Country code and number -> PIN -> devices, with signing in again on the same exchange."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "MyCentsysOptionsFlow":
        return MyCentsysOptionsFlow()

    def __init__(self) -> None:
        self._account: dict[str, Any] = {
            CONF_COUNTRY: HOME_COUNTRY,
            CONF_REGION: datacentre_for_country(HOME_COUNTRY),
            CONF_PIN_DELIVERY: DEFAULT_PIN_DELIVERY,
        }
        self._pin: _PinExchange | None = None
        self._target: ConfigEntry | None = None
        self._found: dict[str, str] = {}

    @property
    def _exchange(self) -> _PinExchange:
        if self._pin is None:
            self._pin = _PinExchange(self.hass)
        return self._pin

    @property
    def _account_number(self) -> str:
        return self._account.get(CONF_ACCOUNT_NUMBER) or ""

    async def _request_pin(self) -> str | None:
        if not self._account_number:
            return _FAIL_CONNECT
        return await self._exchange.request(self._account_number, self._account[CONF_REGION], self._account[CONF_PIN_DELIVERY])

    def _form(
        self,
        step_id: str,
        schema: vol.Schema,
        failure: str | None = None,
        *,
        field: str = "base",
        entered: Mapping[str, Any] | None = None,
        **placeholders: str,
    ) -> ConfigFlowResult:
        """Show a step; ``failure`` is an error key, shown under ``field`` or across the form."""
        if entered:
            schema = self.add_suggested_values_to_schema(schema, entered)
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors={field: failure} if failure else {},
            description_placeholders=placeholders or None,
        )

    # -- first sign-in ------------------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self._form("user", _account_form())
        typed = digits_only(user_input.get(CONF_ACCOUNT_NUMBER))
        if typed is None:
            return self._form("user", _account_form(), _FAIL_DIGITS, field=CONF_ACCOUNT_NUMBER, entered=user_input)
        country_code = user_input[CONF_COUNTRY]
        number = _international(typed, country_code)
        if not number:
            return self._form("user", _account_form(), _FAIL_NUMBER, field=CONF_ACCOUNT_NUMBER, entered=user_input)
        self._account.update(
            {
                CONF_COUNTRY: country_code,
                CONF_ACCOUNT_NUMBER: number,
                CONF_REGION: datacentre_for_country(country_code),
                CONF_PIN_DELIVERY: int(user_input.get(CONF_PIN_DELIVERY, DEFAULT_PIN_DELIVERY)),
            }
        )
        await self.async_set_unique_id(number)
        self._abort_if_unique_id_configured()
        failure = await self._request_pin()
        if failure:
            return self._form("user", _account_form(), failure, entered=user_input)
        return await self.async_step_pin()

    async def async_step_pin(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self._form("pin", _pin_form())
        failure = await self._exchange.redeem(user_input["pin"])
        if failure:
            return self._form("pin", _pin_form(), failure)
        self._account[CONF_TOKEN] = self._exchange.token
        return await self.async_step_devices()

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask which of the linked devices to include; with none linked yet there is nothing to ask."""
        title = entry_title(self._account_number)
        if user_input is None:
            self._found = await self._exchange.discover()
            if not self._found:
                return self.async_create_entry(title=title, data=dict(self._account), options={CONF_DEVICE_SCOPE: SCOPE_ALL, CONF_DEVICES: []})
            return self._devices_form()
        options, failure = device_choice(user_input)
        if failure:
            return self._devices_form(failure, user_input)
        return self.async_create_entry(title=title, data=dict(self._account), options=options)

    def _devices_form(self, failure: str | None = None, entered: Mapping[str, Any] | None = None) -> ConfigFlowResult:
        schema = vol.Schema(_devices_fields(self._found, SIGN_IN_DEVICE_SCOPE, []))
        return self._form("devices", schema, failure, field=CONF_DEVICES, entered=entered, count=str(len(self._found)))

    # -- signing in again ---------------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._target = self._get_reauth_entry()
        self._account.update(
            {
                CONF_ACCOUNT_NUMBER: entry_data[CONF_ACCOUNT_NUMBER],
                CONF_PIN_DELIVERY: int(entry_data.get(CONF_PIN_DELIVERY, DEFAULT_PIN_DELIVERY)),
                CONF_REGION: entry_data.get(CONF_REGION) or datacentre_for_country(entry_data.get(CONF_COUNTRY)),
            }
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        number = self._account_number
        if user_input is None or not self._exchange.sent:
            return self._form("reauth_confirm", _pin_form(), await self._request_pin(), number=number)
        failure = await self._exchange.redeem(user_input["pin"])
        if failure or self._target is None:
            return self._form("reauth_confirm", _pin_form(), failure or _FAIL_CONNECT, number=number)
        self.hass.config_entries.async_update_entry(self._target, data={**self._target.data, CONF_TOKEN: self._exchange.token})
        await self.hass.config_entries.async_reload(self._target.entry_id)
        return self.async_abort(reason="reauth_successful")


class MyCentsysOptionsFlow(OptionsFlow):
    """Which devices to include and how often an idle ULTRA module is read."""

    def _known_devices(self) -> dict[str, str]:
        """Everything the hub discovered on its last poll, plus anything already chosen."""
        hub = getattr(self.config_entry, "runtime_data", None)
        known = dict(getattr(hub, "discovered", None) or {})
        for key in self.config_entry.options.get(CONF_DEVICES) or []:
            known.setdefault(key, key)
        return known

    def _settings_form(self, failure: str | None = None, entered: Mapping[str, Any] | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        fields = _devices_fields(self._known_devices(), scope_of(options), list(options.get(CONF_DEVICES) or []))
        interval = options.get(CONF_ULTRA_POLL_INTERVAL, DEFAULT_ULTRA_POLL_INTERVAL)
        fields[vol.Required(CONF_ULTRA_POLL_INTERVAL, default=interval)] = vol.All(
            vol.Coerce(int), vol.Range(min=MIN_ULTRA_POLL_INTERVAL, max=MAX_ULTRA_POLL_INTERVAL)
        )
        schema = vol.Schema(fields)
        if entered:
            schema = self.add_suggested_values_to_schema(schema, entered)
        return self.async_show_form(step_id="init", data_schema=schema, errors={CONF_DEVICES: failure} if failure else {})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self._settings_form()
        chosen, failure = device_choice(user_input)
        if failure:
            return self._settings_form(failure, user_input)
        chosen[CONF_ULTRA_POLL_INTERVAL] = int(user_input.get(CONF_ULTRA_POLL_INTERVAL, DEFAULT_ULTRA_POLL_INTERVAL))
        return self.async_create_entry(title="", data=chosen)
