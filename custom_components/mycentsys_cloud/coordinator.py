"""The account hub: keeps the :class:`Fleet` current and carries out actions.

One hub serves one config entry (one account on one datacentre). It owns the
cloud client and the broker session factory and runs every cadence the
integration has:

* the cloud poll (listings and snapshots) on the coordinator's own interval;
* the ULTRA cadences: configuration on demand, IO states on an idle interval
  with a short burst after an action, diagnostics only while wanted;
* the shared-gate refresh;
* the live feed of operator broadcasts, always on, which it ingests off the network thread.

Entities never talk to the backends directly: they read the fleet and call
the ``async_*`` actions here, which turn every failure into a
:class:`HomeAssistantError` carrying the operator's or backend's own reason.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES, CONF_REGION, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .broker import SmartBroker
from .cloud.errors import AuthError, CloudError, ConfigurationRequired
from .cloud.http import CloudClient
from .cloud.models import ModuleDiagnostics, UltraModule, UltraReadings, combine_listings
from .cloud.mqtt import ActivationOutcome, SessionError
from .cloud.numbers import digits
from .cloud.overview import DeviceAnnouncement, Overview
from .cloud.regions import region as region_for
from .const import (
    AFTER_ACTION_REFRESH_DELAYS,
    BALANCE_FOLLOW_UP_READS,
    BALANCE_FOLLOW_UP_SECONDS,
    CLOUD_POLL_SECONDS,
    CONF_ACCOUNT_NUMBER,
    CONF_ULTRA_POLL_INTERVAL,
    DEFAULT_ULTRA_POLL_INTERVAL,
    DOMAIN,
    EARLIER_OPERATOR_CODES,
    GRANTS_POLL_SECONDS,
    LIVE_POSITION_SECONDS,
    MAX_ULTRA_POLL_INTERVAL,
    MIN_ULTRA_POLL_INTERVAL,
    MODULE_BURST_MAX_SECONDS,
    MODULE_BURST_SECONDS,
    MODULE_BURST_SETTLE_READS,
    MODULE_BURST_STEP_SECONDS,
    MODULE_DIAGNOSTICS_SECONDS,
    SUPPORTED_DEVICES_URL,
)
from .fleet import Fleet, GrantRecord, ModuleRecord, OperatorRecord, firmware_line, hardware_line, scope_of
from .learned import OperatorCodes
from .telemetry import cloud_position_label, position_label

_LOGGER = logging.getLogger(__name__)

# Position labels that mean the gate or door is still travelling.
_MOVING_POSITIONS = frozenset({"opening", "closing", "running"})

type CloudConfigEntry = ConfigEntry["AccountHub"]

# A repair issue id is one of these followed by the entry id.
_ISSUE_NO_DEVICES = "no_devices"
_ISSUE_READONLY_SHARE = "shared_readonly"


# The time of something that was never taken. The monotonic clock starts near zero when the
# machine does, so zero itself would not count as old on a machine that has only just started.
_NEVER = float("-inf")


class _Stamped:
    """A value with the monotonic time it was taken, for cadences and expiries."""

    __slots__ = ("at", "value")

    def __init__(self, value: Any = None, at: float = _NEVER) -> None:
        self.value = value
        self.at = at

    def older_than(self, seconds: float) -> bool:
        return time.monotonic() - self.at >= seconds

    def expire(self) -> None:
        """Make the value count as old from now on, however short the machine has been running."""
        self.at = _NEVER


class AccountHub(DataUpdateCoordinator[Fleet]):
    """Everything one account knows and can do, for the entities of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: CloudConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=CLOUD_POLL_SECONDS))
        self.entry = entry
        self.region = region_for(entry.data.get(CONF_REGION))
        self.client = CloudClient(
            async_get_clientsession(hass), entry.data[CONF_ACCOUNT_NUMBER], region=self.region, token=entry.data[CONF_TOKEN]
        )
        self.broker = SmartBroker(hass, self.client, self.region)
        # Everything discovered on the last poll, before the user's selection: key -> picker label.
        self.discovered: dict[str, str] = {}

        # SMART operators.
        self.codes = OperatorCodes(hass, entry.entry_id)
        self._broadcasts: dict[str, Overview] = {}
        self._live_positions: dict[str, _Stamped] = {}
        self._broadcast_waiters: dict[str, asyncio.Event] = {}
        self._feed: Any = None

        # ULTRA modules.
        self._modules: tuple[UltraModule, ...] = ()
        self._modules_loaded = False
        self._reload_modules = False
        self._module_load_failed = False
        self._readings: dict[str, UltraReadings] = {}
        self._diagnostics: dict[str, ModuleDiagnostics] = {}
        # What a module's status reports have shown about its SIM, for a module whose
        # configuration does not say; remembered because a report made while a
        # balance is being fetched shows nothing.
        self._token_sims: dict[str, bool] = {}
        self._diagnostics_read = _Stamped()
        self._diagnostics_wanted: set[str] = set()
        self._bursting: set[str] = set()
        self._idle_poll_running = False

        # Shared gates.
        self._grants = _Stamped(value=None)

        self._jobs: set[asyncio.Task] = set()

    # -- identity ---------------------------------------------------------------------------------

    @property
    def number(self) -> str:
        return self.client.number

    @property
    def module_poll_seconds(self) -> int:
        raw = self.entry.options.get(CONF_ULTRA_POLL_INTERVAL, DEFAULT_ULTRA_POLL_INTERVAL)
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            seconds = DEFAULT_ULTRA_POLL_INTERVAL
        return min(MAX_ULTRA_POLL_INTERVAL, max(MIN_ULTRA_POLL_INTERVAL, seconds))

    # -- lifecycle --------------------------------------------------------------------------------

    def spawn(self, job: Coroutine[Any, Any, Any], *, name: str) -> None:
        """Run a background job that belongs to this entry and dies with it."""
        task = self.hass.async_create_background_task(job, name=f"{DOMAIN}.{name}")
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    def start_background_work(self) -> None:
        """Start the live feed and the idle module poll."""
        from .live_feed import LiveFeed

        self._feed = LiveFeed(self)
        self._feed.start()
        self._idle_poll_running = True
        self.spawn(self._idle_module_poll(), name="module_poll")

    async def async_shutdown(self) -> None:
        feed, self._feed = self._feed, None
        if feed is not None:
            await feed.async_stop()
        while self._jobs:
            self._jobs.pop().cancel()
        await super().async_shutdown()

    @property
    def feed_connected(self) -> bool:
        """Whether operator broadcasts are arriving live right now."""
        return bool(self._feed is not None and self._feed.active)

    # -- the poll ----------------------------------------------------------------------------------

    async def _async_update_data(self) -> Fleet:
        fleet = Fleet()
        try:
            operators = combine_listings(await self.client.remote_operators(), await self.client.admin_operators())
            snapshots = {snapshot.serial: snapshot for snapshot in await self.client.cloud_snapshots(op.serial for op in operators)}
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CloudError as err:
            raise UpdateFailed(str(err)) from err

        for operator in operators:
            fleet.operators[operator.serial] = OperatorRecord(
                operator=operator,
                snapshot=snapshots.get(operator.serial),
                broadcast=self._broadcasts.get(operator.serial),
                reported_code=self.codes.get(operator.serial),
            )
        admin_only = sum(1 for operator in operators if not operator.remote_linked)
        if admin_only:
            _LOGGER.debug("%d operator(s) are administered but not remote-user linked", admin_only)

        await self._load_modules_if_needed()
        if not self._idle_poll_running:
            await self._read_all_module_states()
        await self._read_wanted_diagnostics_if_due()
        for module in self._modules:
            fleet.modules[module.key] = ModuleRecord(
                module=module,
                readings=self._readings.get(module.key),
                diagnostics=self._diagnostics.get(module.key),
                token_sim=self._sim_kind(module),
            )

        readonly_share = False
        for grant in await self._grants_if_due():
            # The owner already has the gate through the listing; their own share would double it.
            if grant.granted_by(self.number):
                continue
            fleet.grants[grant.key] = GrantRecord(grant=grant)
            readonly_share = readonly_share or not grant.is_ultra

        self._raise_or_clear_issue(_ISSUE_NO_DEVICES, "nothing_linked", when=len(fleet) == 0)
        self._raise_or_clear_issue(_ISSUE_READONLY_SHARE, "shared_operator_view_only", when=readonly_share)
        self.discovered = fleet.labels()
        options = self.entry.options
        chosen = fleet.within(scope_of(options), options.get(CONF_DEVICES))
        if self._feed is not None:
            self._feed.follow(list(chosen.operators))
        return chosen

    # -- repairs ------------------------------------------------------------------------------------

    def _issue_id(self, suffix: str) -> str:
        return f"{suffix}_{self.entry.entry_id}"

    def _raise_or_clear_issue(self, suffix: str, translation_key: str, *, when: bool) -> None:
        if not when:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(suffix))
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(suffix),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            learn_more_url=SUPPORTED_DEVICES_URL,
        )

    def clear_issues(self) -> None:
        for suffix in (_ISSUE_NO_DEVICES, _ISSUE_READONLY_SHARE):
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(suffix))

    def owned_identifiers(self) -> set[str]:
        """The device-registry identifier values this entry currently owns."""
        scope = digits(self.number)
        fleet = self.data or Fleet()
        return set(fleet.operators) | set(fleet.modules) | {f"{key}-{scope}" for key in fleet.grants}

    # -- SMART operators: classification and position ------------------------------------------------

    async def async_restore(self) -> None:
        """Bring back what was learned before the restart; once, ahead of the first poll.

        A build before this one kept the product codes in the entry. They are
        taken over into the integration's storage and removed from the entry.
        """
        await self.codes.async_load(self.entry.data.get(EARLIER_OPERATOR_CODES))
        if EARLIER_OPERATOR_CODES in self.entry.data:
            kept = {key: value for key, value in self.entry.data.items() if key != EARLIER_OPERATOR_CODES}
            self.hass.config_entries.async_update_entry(self.entry, data=kept)

    def _note_announced_code(self, serial: str, code: int | None) -> None:
        if not self.codes.note(serial, code):
            return
        record = self.data.operators.get(serial) if self.data else None
        if record is not None:
            record.reported_code = code

    def reported_code(self, serial: str) -> int | None:
        return self.codes.get(serial)

    def note_live_position(self, key: str, label: str | None) -> None:
        """Record (or clear) a position seen live, and let the entities know."""
        if label is None:
            self._live_positions.pop(key, None)
        else:
            self._live_positions[key] = _Stamped(label, time.monotonic())
        self.async_update_listeners()

    def live_position(self, key: str) -> str | None:
        """A position seen live within the last few seconds, else None."""
        stamped = self._live_positions.get(key)
        if stamped is None:
            return None
        if stamped.older_than(LIVE_POSITION_SECONDS):
            del self._live_positions[key]
            return None
        return stamped.value

    def position_of(self, serial: str) -> str | None:
        """The best current position of an operator: live, then the cloud snapshot, then its last broadcast."""
        live = self.live_position(serial)
        if live is not None:
            return live
        record = self.data.operators.get(serial) if self.data else None
        if record is None:
            return None
        return cloud_position_label(record.snapshot) or position_label(record.broadcast)

    # -- SMART operators: broadcasts --------------------------------------------------------------------

    def ingest_broadcast(self, announcement: DeviceAnnouncement, overview: Overview | None) -> None:
        """Take in one operator broadcast (on the event loop)."""
        serial = announcement.serial_number
        self._note_announced_code(serial, announcement.product_code)
        if overview is None:
            return
        self._broadcasts[serial] = overview
        record = self.data.operators.get(serial) if self.data else None
        if record is not None:
            record.broadcast = overview
        waiter = self._broadcast_waiters.get(serial)
        if waiter is not None:
            waiter.set()
        self.note_live_position(serial, position_label(overview))

    def ingest_from_thread(self, announcement: DeviceAnnouncement, overview: Overview | None) -> None:
        """Hand a broadcast received on the network thread to the event loop."""
        self.hass.loop.call_soon_threadsafe(self.ingest_broadcast, announcement, overview)

    async def wait_for_broadcast(self, serial: str, timeout: float) -> Overview | None:
        """The operator's next broadcast within ``timeout`` seconds, or None."""
        waiter = self._broadcast_waiters.setdefault(serial, asyncio.Event())
        waiter.clear()
        try:
            await asyncio.wait_for(waiter.wait(), timeout)
        except asyncio.TimeoutError:
            return None
        return self._broadcasts.get(serial)

    # -- SMART operators: actions ------------------------------------------------------------------------

    async def async_send_operator(self, serial: str, activation_id: int, *, what: str) -> ActivationOutcome:
        """Send one activation to an operator; raises a user-facing error when it was not carried out."""
        record = self.data.operators.get(serial) if self.data else None
        if record is None:
            raise HomeAssistantError("This operator is no longer available.")
        if not record.operator.mac:
            raise HomeAssistantError("The operator has no network identity in the device list yet; cannot send the action.")
        try:
            outcome = await self.broker.async_send(serial, record.operator.mac, activation_id)
        except SessionError as err:
            raise HomeAssistantError(f"{what} could not reach the operator: {err}") from err
        except AuthError as err:
            raise HomeAssistantError(f"{what} failed: sign in again.") from err
        except CloudError as err:
            raise HomeAssistantError(f"{what} failed: {err}") from err
        if not outcome.ok:
            raise HomeAssistantError(f"{what} was not accepted: {outcome.message}.")
        self._follow_action_on_cloud(serial)
        return outcome

    def _follow_action_on_cloud(self, serial: str) -> None:
        """While the live feed is down, re-read the cloud snapshot a few times so the cycle shows."""
        if self.feed_connected:
            return
        self.spawn(self._refresh_after_delays(), name=f"after_action.{serial}")

    async def _refresh_after_delays(self) -> None:
        for delay in AFTER_ACTION_REFRESH_DELAYS:
            await asyncio.sleep(delay)
            await self.async_request_refresh()

    # -- ULTRA modules ---------------------------------------------------------------------------------

    def module(self, key: str) -> UltraModule | None:
        return next((module for module in self._modules if module.key == key), None)

    def request_module_reload(self) -> None:
        """Re-read every module's configuration on the next poll, and bring that poll forward."""
        self._reload_modules = True
        self.hass.async_create_task(self.async_request_refresh())

    def note_module_error(self, err: Exception) -> None:
        if isinstance(err, ConfigurationRequired):
            self.request_module_reload()

    async def async_pulse_module(self, key: str, io_number: int, *, what: str) -> None:
        """Pulse one IO of a module and follow the module closely; raises a user-facing error."""
        module = self.module(key)
        if module is None:
            raise HomeAssistantError("This device is no longer available.")
        try:
            await self.client.pulse_ultra(module.device_id, io_number)
        except AuthError as err:
            raise HomeAssistantError(f"{what} failed: sign in again.") from err
        except CloudError as err:
            self.note_module_error(err)
            raise HomeAssistantError(f"{what} failed: {err}") from err
        self.start_module_burst(key)

    async def _load_modules_if_needed(self) -> None:
        if self._modules_loaded and not self._reload_modules:
            return
        try:
            self._modules = tuple(await self.client.ultra_modules())
        except CloudError as err:
            # Loud the first time, quiet while it keeps failing: without the configuration
            # every ULTRA module stays hidden, so the cause must show in a normal log.
            level = logging.DEBUG if self._module_load_failed else logging.WARNING
            _LOGGER.log(level, "ULTRA device configuration could not be read (%s); ULTRA devices stay hidden until it succeeds", err)
            self._module_load_failed = True
            return
        if self._module_load_failed:
            _LOGGER.info("ULTRA device configuration read again: %d device(s)", len(self._modules))
        self._module_load_failed = False
        self._modules_loaded, self._reload_modules = True, False

    async def _read_module_states(self, module: UltraModule) -> UltraReadings | None:
        feedback = module.feedback
        try:
            return await self.client.ultra_readings(module.device_id, feedback_number=feedback.number if feedback else None)
        except CloudError as err:
            _LOGGER.debug("ULTRA IO-state read failed for device %s: %s", module.key, err.__class__.__name__)
            return None

    def _keep_readings(self, module: UltraModule, readings: UltraReadings) -> None:
        self._readings[module.key] = readings
        record = self.data.modules.get(module.key) if self.data else None
        if record is not None:
            record.readings = readings
        versions = (readings.config_version, module.config_version)
        if None not in versions and versions[0] != versions[1]:
            self._reload_modules = True

    async def _read_all_module_states(self) -> None:
        for module in self._modules:
            readings = await self._read_module_states(module)
            if readings is not None:
                self._keep_readings(module, readings)

    async def _idle_module_poll(self) -> None:
        while True:
            await asyncio.sleep(self.module_poll_seconds)
            if not self._modules:
                continue
            try:
                await self._read_all_module_states()
            except Exception as err:  # noqa: BLE001 - a poll must not end the loop
                _LOGGER.debug("ULTRA status poll failed: %s", err.__class__.__name__)
                continue
            self.async_update_listeners()

    def start_module_burst(self, key: str) -> None:
        """Follow a module every few seconds for a short while after an action; one burst per module."""
        module = self.module(key)
        if module is None or key in self._bursting:
            return
        self._bursting.add(key)
        self.spawn(self._burst(module), name=f"burst.{key}")

    async def _burst(self, module: UltraModule) -> None:
        """Read every few seconds for the burst window; while the position input still
        reports movement keep going (bounded), then take a few settled readings so the
        final position is the one shown."""
        started = time.monotonic()
        settled_reads = 0
        try:
            while True:
                readings = await self._read_module_states(module)
                if readings is not None:
                    self._keep_readings(module, readings)
                    self.note_live_position(module.key, readings.position)
                elapsed = time.monotonic() - started
                moving = readings is not None and readings.position in _MOVING_POSITIONS
                if moving:
                    settled_reads = 0
                elif elapsed >= MODULE_BURST_SECONDS:
                    settled_reads += 1
                if elapsed >= MODULE_BURST_MAX_SECONDS:
                    break
                if not moving and elapsed >= MODULE_BURST_SECONDS and settled_reads > MODULE_BURST_SETTLE_READS:
                    break
                await asyncio.sleep(MODULE_BURST_STEP_SECONDS)
        finally:
            self._bursting.discard(module.key)
            self.note_live_position(module.key, None)

    def want_diagnostics(self, key: str) -> None:
        """A diagnostic sensor of this module is enabled: read its diagnostics now and on the cadence."""
        if key in self._diagnostics_wanted:
            return
        self._diagnostics_wanted.add(key)
        self.spawn(self._read_diagnostics_now(key), name=f"diagnostics.{key}")

    def drop_diagnostics(self, key: str) -> None:
        self._diagnostics_wanted.discard(key)

    async def _read_diagnostics(self, module: UltraModule) -> ModuleDiagnostics | None:
        try:
            return await self.client.ultra_diagnostics(module.device_id)
        except CloudError as err:
            _LOGGER.debug("ULTRA status read failed for device %s: %s", module.key, err.__class__.__name__)
            return None

    def _keep_diagnostics(self, key: str, diagnostics: ModuleDiagnostics) -> None:
        self._diagnostics[key] = diagnostics
        seen = diagnostics.uses_tokens
        if seen is not None:
            self._token_sims[key] = seen
        record = self.data.modules.get(key) if self.data else None
        if record is not None:
            record.diagnostics = diagnostics
            record.token_sim = self._sim_kind(record.module)
        self._show_on_device_page(key, diagnostics)

    def _sim_kind(self, module: UltraModule) -> bool | None:
        """True for a token SIM, False for a SIM with a network balance, None while neither is known.

        The SIM type set up on the module decides. Only where the backend does
        not pass that on is the status report used: tokens or a network's answer
        seen there. A balance that is still being fetched, or none at all, never
        makes a module a network-balance one.
        """
        if module.token_sim is not None:
            return module.token_sim
        return self._token_sims.get(module.key)

    def _show_on_device_page(self, key: str, diagnostics: ModuleDiagnostics) -> None:
        """Put a module's firmware and hardware line on its device page, where they change rarely."""
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, key)}) if registry is not None else None
        if device is None:
            return
        firmware, hardware = firmware_line(diagnostics), hardware_line(diagnostics)
        if (device.sw_version, device.hw_version) != (firmware, hardware):
            registry.async_update_device(device.id, sw_version=firmware, hw_version=hardware)

    async def _read_diagnostics_now(self, key: str) -> None:
        module = self.module(key)
        if module is None:
            return
        diagnostics = await self._read_diagnostics(module)
        if diagnostics is not None:
            self._keep_diagnostics(key, diagnostics)
            self.async_update_listeners()

    async def _read_wanted_diagnostics_if_due(self) -> None:
        if not self._diagnostics_wanted:
            return
        if self._diagnostics and not self._diagnostics_read.older_than(MODULE_DIAGNOSTICS_SECONDS):
            return
        self._diagnostics_read = _Stamped(True, time.monotonic())
        for module in self._modules:
            if module.key not in self._diagnostics_wanted:
                continue
            diagnostics = await self._read_diagnostics(module)
            if diagnostics is not None:
                self._keep_diagnostics(module.key, diagnostics)

    def follow_balance(self, key: str) -> None:
        """After a balance request, read the diagnostics again until the answer is in."""
        self.spawn(self._balance_follow_up(key), name=f"balance.{key}")

    async def _balance_follow_up(self, key: str) -> None:
        """Read every few seconds for half a minute. Done as soon as a pending balance has
        turned into a value, or the balance differs from the one shown when it was asked for."""
        module = self.module(key)
        if module is None:
            return
        shown = self._diagnostics.get(key) or ModuleDiagnostics(module.device_id)
        was_pending = shown.balance_pending
        for _ in range(BALANCE_FOLLOW_UP_READS):
            await asyncio.sleep(BALANCE_FOLLOW_UP_SECONDS)
            diagnostics = await self._read_diagnostics(module)
            if diagnostics is None:
                continue
            self._keep_diagnostics(key, diagnostics)
            self._diagnostics_read = _Stamped(True, time.monotonic())
            self.async_update_listeners()
            if diagnostics.balance_pending:
                was_pending = True
            elif was_pending or diagnostics.balance_mark != shown.balance_mark:
                return

    # -- shared gates -----------------------------------------------------------------------------------

    def share_was_used(self) -> None:
        """A shared gate was just used from here: read the shares again now, so its remaining uses are current."""
        self._grants.expire()
        self.hass.async_create_task(self.async_request_refresh())

    async def _grants_if_due(self) -> list[Any]:
        if self._grants.value is not None and not self._grants.older_than(GRANTS_POLL_SECONDS):
            return self._grants.value
        try:
            grants = await self.client.access_grants()
        except CloudError as err:
            _LOGGER.debug("Shared-access refresh failed: %s", err)
            return self._grants.value or []
        self._grants = _Stamped(grants, time.monotonic())
        return grants


HubCallback = Callable[[], None]
