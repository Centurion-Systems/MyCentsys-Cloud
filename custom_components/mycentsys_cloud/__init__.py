"""MyCentsys Cloud: Centurion Systems gates and garage doors in Home Assistant."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_ACCOUNT_NUMBER, DOMAIN, EARLIER_TITLE_PREFIXES, PLATFORMS
from .coordinator import AccountHub, CloudConfigEntry
from .fleet import entry_title
from .learned import OperatorCodes


async def async_setup_entry(hass: HomeAssistant, entry: CloudConfigEntry) -> bool:
    """Sign the account in, take the first fleet poll, then bring the platforms up."""
    number = entry.data.get(CONF_ACCOUNT_NUMBER)
    if number and entry.title in {f"{prefix}{number}" for prefix in EARLIER_TITLE_PREFIXES}:
        # Still named the way an earlier build did it; a name the user typed is left alone.
        hass.config_entries.async_update_entry(entry, title=entry_title(number))
    hub = AccountHub(hass, entry)
    await hub.async_restore()
    await hub.async_config_entry_first_refresh()
    entry.runtime_data = hub
    _forget_deselected_devices(hass, entry, hub)
    await hass.config_entries.async_forward_entry_setups(entry, list(PLATFORMS))
    hub.start_background_work()
    # Options (device selection, the live feed, the module cadence) take effect on a reload.
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CloudConfigEntry) -> bool:
    """Stop the background work before the platforms go, so nothing keeps talking to the backends."""
    if not await hass.config_entries.async_unload_platforms(entry, list(PLATFORMS)):
        return False
    hub: AccountHub = entry.runtime_data
    hub.clear_issues()
    await hub.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: CloudConfigEntry) -> None:
    """Forget what was learned about the account's operators along with the entry."""
    await OperatorCodes(hass, entry.entry_id).async_forget()


async def _reload(hass: HomeAssistant, entry: CloudConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _forget_deselected_devices(hass: HomeAssistant, entry: CloudConfigEntry, hub: AccountHub) -> None:
    """Detach registry devices the user no longer includes, so they do not linger as orphans."""
    registry = dr.async_get(hass)
    owned = hub.owned_identifiers()
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        mine = {value for domain, value in device.identifiers if domain == DOMAIN}
        if mine and mine.isdisjoint(owned):
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
