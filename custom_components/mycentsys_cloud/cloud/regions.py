"""Datacentre regions and the broker identity this integration uses.

Centurion runs one set of backends for South Africa (which also serves every
country except Australia and New Zealand) and one for Australia and New Zealand.
An account lives on exactly one of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "Region",
    "DATACENTRES",
    "HOME_DATACENTRE",
    "region",
    "BROKER_PORT",
    "client_id",
]

BROKER_PORT: Final = 8880


@dataclass(frozen=True, slots=True)
class Region:
    """Every host an account on this datacentre talks to."""

    key: str
    gateway_url: str
    gweb_url: str
    access_sharing_url: str
    broker_host: str
    broker_port: int = BROKER_PORT


DATACENTRES: Final[dict[str, Region]] = {
    "za": Region(
        key="za",
        gateway_url="https://centsys.southafricanorth.cloudapp.azure.com:4445",
        gweb_url="https://www.gweb.co.za",
        access_sharing_url="https://www.gweb.co.za:4446",
        broker_host="20.87.192.195",
    ),
    "au": Region(
        key="au",
        gateway_url="https://centsys.australiaeast.cloudapp.azure.com:4445",
        gweb_url="https://www.g-web.com.au",
        access_sharing_url="https://www.g-web.com.au:4446",
        broker_host="20.213.185.54",
    ),
}

HOME_DATACENTRE: Final = "za"


def region(key: str | None) -> Region:
    """The region for a stored key; unknown or missing keys fall back to the default."""
    if key:
        found = DATACENTRES.get(key.strip().lower())
        if found is not None:
            return found
    return DATACENTRES[HOME_DATACENTRE]


# The broker tells sessions apart by client id alone (the client certificate is
# shared), and a second session with an id already in use takes the first one
# over. The app connects as ``mcr:<number>``, so this integration always adds a
# suffix of its own and the two never displace each other.
_CLIENT_ID_PREFIX: Final = "mcr:"
_COMMAND_SUFFIX: Final = ":mcc"
_LISTENER_SUFFIX: Final = ":mcc-feed"


def client_id(number: str, *, listener: bool = False) -> str:
    """``mcr:<number>:mcc`` for command sessions, ``mcr:<number>:mcc-feed`` for the listener."""
    suffix = _LISTENER_SUFFIX if listener else _COMMAND_SUFFIX
    return f"{_CLIENT_ID_PREFIX}{number.strip()}{suffix}"
