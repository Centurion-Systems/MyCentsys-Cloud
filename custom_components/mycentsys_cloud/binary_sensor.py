"""Binary sensors: whether a SMART operator is connected to the cloud, and ULTRA inputs as booleans.

An operator's faults are not split into one sensor each: the Health sensor
says whether anything needs attention and lists what in its attributes.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud.models import UltraChannel
from .coordinator import AccountHub, CloudConfigEntry
from .entity import ChannelEntity, OperatorEntity, Roster
from .fleet import Fleet
from .telemetry import connection_icon


async def async_setup_entry(hass: HomeAssistant, entry: CloudConfigEntry, add_entities: AddEntitiesCallback) -> None:
    hub: AccountHub = entry.runtime_data

    def build(fleet: Fleet) -> list[BinarySensorEntity]:
        sensors: list[BinarySensorEntity] = [CloudConnectionSensor(hub, serial) for serial in fleet.operators]
        for key, record in fleet.modules.items():
            sensors += [InputOnSensor(hub, key, channel) for channel in record.module.status_inputs]
        return sensors

    Roster(hub, add_entities, build).keep_level(entry)


class CloudConnectionSensor(OperatorEntity, BinarySensorEntity):
    """Whether the operator is connected to the MyCentsys cloud, as the listing reports it."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "reachable"

    def __init__(self, hub: AccountHub, serial: str) -> None:
        super().__init__(hub, serial)
        self._attr_unique_id = f"{serial}_reachable"

    @property
    def is_on(self) -> bool | None:
        record = self.record
        return record.operator.link.online if record else None

    @property
    def icon(self) -> str:
        return connection_icon(self.is_on)


class InputOnSensor(ChannelEntity, BinarySensorEntity):
    """A status input of an ULTRA module as a plain boolean.

    The named-state sensor is the primary view of an input; this twin is for
    automations that want on/off, and starts disabled.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(self, hub: AccountHub, key: str, channel: UltraChannel) -> None:
        super().__init__(hub, key, channel)
        self._attr_unique_id = f"{key}_input_on_{channel.number}"
        self._attr_name = f"{self._attr_name} on"

    @property
    def is_on(self) -> bool | None:
        return self.is_on_now
