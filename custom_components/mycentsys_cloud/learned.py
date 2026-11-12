"""What the integration learns about an account's operators and must still know after a restart.

An operator states its product code in its broadcasts. The account's listing can
lag behind the hardware, so the code it last stated is what tells a gate from a
garage door, and it has to be known before the first broadcast after a restart.

It is kept in Home Assistant's own storage, apart from the config entry. The
entry holds what the user set up, and Home Assistant reloads the integration
whenever the entry is written to; something learned while running belongs
elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LAYOUT = 1
_SECTION = "operator_codes"
# Codes arrive in a burst when the feed opens; one write a few seconds later covers them all.
_WRITE_AFTER_SECONDS = 5


def _codes_in(found: Any) -> dict[str, int]:
    """The usable serial -> code pairs of something read back, which may be anything."""
    if not isinstance(found, Mapping):
        return {}
    return {str(serial): code for serial, code in found.items() if isinstance(code, int) and not isinstance(code, bool)}


class OperatorCodes:
    """The product code each operator last stated, per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, _LAYOUT, f"{DOMAIN}.{entry_id}")
        self._codes: dict[str, int] = {}

    async def async_load(self, earlier: Any = None) -> None:
        """Read the codes back. ``earlier`` is what a build before this one left in the entry;
        anything it knows that the storage does not is taken over and written out."""
        stored = await self._store.async_load()
        kept = _codes_in(stored.get(_SECTION) if isinstance(stored, Mapping) else None)
        self._codes = {**_codes_in(earlier), **kept}
        if self._codes != kept:
            await self._store.async_save(self._document())

    def get(self, serial: str) -> int | None:
        return self._codes.get(serial)

    def note(self, serial: str, code: int | None) -> bool:
        """Remember a stated code; True when it is news."""
        if code is None or self._codes.get(serial) == code:
            return False
        self._codes[serial] = code
        self._store.async_delay_save(self._document, _WRITE_AFTER_SECONDS)
        return True

    async def async_forget(self) -> None:
        """Remove the stored codes, when the entry they belong to is removed."""
        self._codes = {}
        await self._store.async_remove()

    def _document(self) -> dict[str, Any]:
        return {_SECTION: dict(self._codes)}
