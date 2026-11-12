"""The broker side of a SMART operator: the client bundle, activations and the feed.

Everything blocking (bundle conversion, the MQTT exchange) runs in an
executor; the hub and the entities only see coroutines. The broker is always
verified: against the authority certificate shipped with the integration,
plus any chain the issued client bundle carries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .cloud.http import CloudClient
from .cloud.mqtt import (
    ActivationOutcome,
    BrokerTls,
    OperatorSession,
    OverviewListener,
    SessionError,
    bundled_ca_pem,
)
from .cloud.overview import DeviceAnnouncement
from .cloud.regions import Region, client_id
from .const import CLIENT_BUNDLE_SECONDS

_LOGGER = logging.getLogger(__name__)

BroadcastCallback = Callable[[DeviceAnnouncement, Any], None]


class SmartBroker:
    """Broker sessions for one account."""

    def __init__(self, hass: HomeAssistant, client: CloudClient, region: Region) -> None:
        self._hass = hass
        self._client = client
        self._region = region
        self._tls: BrokerTls | None = None
        self._tls_good_until = 0.0
        self._tls_lock = asyncio.Lock()
        # The broker keeps one session per client id, so activations are serialised.
        self._send_lock = asyncio.Lock()

    @property
    def number(self) -> str:
        return self._client.number

    # -- the client bundle ----------------------------------------------------------------------

    def forget_bundle(self) -> None:
        """Throw the client bundle away; whoever connects next asks the gateway for a new one."""
        self._tls = None
        self._tls_good_until = 0.0

    async def async_tls(self) -> BrokerTls:
        """The TLS settings for a broker session, fetching the bundle when needed."""
        async with self._tls_lock:
            if self._tls is not None and time.monotonic() < self._tls_good_until:
                return self._tls
            bundle = await self._client.client_bundle()
            self._tls = await self._hass.async_add_executor_job(_tls_from_bundle, bundle.pfx, bundle.password)
            self._tls_good_until = time.monotonic() + CLIENT_BUNDLE_SECONDS
            return self._tls

    # -- activations -----------------------------------------------------------------------------

    async def async_send(self, serial: str, mac: str, activation_id: int) -> ActivationOutcome:
        """Send an activation to an operator and wait for its verdict.

        A session the broker refuses is retried once with a freshly issued
        bundle, which is the usual cure for a rotated certificate.
        """
        async with self._send_lock:
            for last_try in (False, True):
                tls = await self.async_tls()
                try:
                    return await self._hass.async_add_executor_job(
                        _exchange, self._region, client_id(self.number), tls, serial, mac, self.number, activation_id
                    )
                except SessionError:
                    if last_try:
                        raise
                    _LOGGER.debug("Broker session failed; retrying with a fresh client bundle")
                    self.forget_bundle()
            raise SessionError("Broker session failed")

    # -- the live feed ----------------------------------------------------------------------------

    async def async_open_feed(
        self, serials: Iterable[str], families: dict[str, str], on_broadcast: BroadcastCallback
    ) -> OverviewListener:
        """Connect the passive broadcast feed for these operators."""
        tls = await self.async_tls()
        feed = OverviewListener(
            self._region.broker_host,
            self._region.broker_port,
            client_id(self.number, listener=True),
            tls,
            on_broadcast,
            families=families,
        )
        await self._hass.async_add_executor_job(feed.start, list(serials))
        return feed

    async def async_close_feed(self, feed: OverviewListener) -> None:
        await self._hass.async_add_executor_job(feed.stop)


def _tls_from_bundle(pfx: bytes, password: str) -> BrokerTls:
    """Blocking: convert the issued bundle into a verifying TLS context."""
    return BrokerTls.from_pkcs12(pfx, password, ca_pem=bundled_ca_pem())


def _exchange(
    region: Region, session_id: str, tls: BrokerTls, serial: str, mac: str, number: str, activation_id: int
) -> ActivationOutcome:
    """Blocking: one connected session, one activation, released."""
    with OperatorSession(region.broker_host, region.broker_port, session_id, tls) as session:
        return session.activate(serial, mac, number, activation_id)
