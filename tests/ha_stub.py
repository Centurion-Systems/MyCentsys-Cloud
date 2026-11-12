"""A minimal stand-in for the Home Assistant modules the integration imports.

The test suite runs without a Home Assistant install. This installs just enough
of ``homeassistant`` (and ``voluptuous``, when absent) into ``sys.modules`` for
the integration package to import, so the platforms' entity factories and
unique ids can be exercised for real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntFlag
import importlib
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(_module(parent), child, module)
    return module


def _callback(func):
    return func


class HomeAssistantError(Exception):
    pass


class ConfigEntryAuthFailed(HomeAssistantError):
    pass


class EntityCategory(str, Enum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True, kw_only=True)
class EntityDescription:
    key: str
    translation_key: str | None = None
    device_class: Any = None
    entity_category: Any = None
    entity_registry_enabled_default: bool = True
    icon: str | None = None
    name: Any = None
    options: list[str] | None = None
    native_unit_of_measurement: str | None = None
    state_class: Any = None
    suggested_display_precision: int | None = None


class Entity:
    _attr_unique_id: str | None = None
    _attr_name: Any = None
    _attr_has_entity_name: bool = False
    _attr_icon: str | None = None
    _attr_translation_key: str | None = None
    _attr_entity_category: Any = None
    _attr_entity_registry_enabled_default: bool = True
    _attr_device_class: Any = None
    _attr_assumed_state: bool = False
    _attr_supported_features: Any = 0

    @property
    def supported_features(self) -> Any:
        return self._attr_supported_features
    _attr_options: list[str] | None = None
    _attr_state_class: Any = None
    entity_description: Any = None
    hass: Any = None

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def name(self) -> Any:
        if self._attr_name is not None:
            return self._attr_name
        description = self.entity_description
        return getattr(description, "name", None) if description else None

    @property
    def translation_key(self) -> str | None:
        if self._attr_translation_key is not None:
            return self._attr_translation_key
        description = self.entity_description
        return getattr(description, "translation_key", None) if description else None

    @property
    def icon(self) -> str | None:
        if self._attr_icon is not None:
            return self._attr_icon
        description = self.entity_description
        return getattr(description, "icon", None) if description else None

    @property
    def entity_category(self) -> Any:
        if self._attr_entity_category is not None:
            return self._attr_entity_category
        description = self.entity_description
        return getattr(description, "entity_category", None) if description else None

    @property
    def entity_registry_enabled_default(self) -> bool:
        if "_attr_entity_registry_enabled_default" in type(self).__dict__ or hasattr(self, "__dict__") and "_attr_entity_registry_enabled_default" in self.__dict__:
            return self._attr_entity_registry_enabled_default
        description = self.entity_description
        if description is not None:
            return description.entity_registry_enabled_default
        return self._attr_entity_registry_enabled_default

    @property
    def device_class(self) -> Any:
        if self._attr_device_class is not None:
            return self._attr_device_class
        description = self.entity_description
        return getattr(description, "device_class", None) if description else None

    @property
    def assumed_state(self) -> bool:
        return self._attr_assumed_state

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return None

    @property
    def device_info(self) -> Any:
        return None

    async def async_added_to_hass(self) -> None:
        return None

    async def async_will_remove_from_hass(self) -> None:
        return None

    async def async_update(self) -> None:
        return None


class _Subscriptable:
    def __class_getitem__(cls, item):
        return cls


class DataUpdateCoordinator(_Subscriptable):
    def __init__(self, hass, logger, *, name: str, update_interval=None) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data: Any = None
        self.last_update_success = True
        self._listeners: list[Any] = []

    def async_add_listener(self, update_callback, context=None):
        self._listeners.append(update_callback)

        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    def async_update_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_request_refresh(self) -> None:
        return None

    async def async_config_entry_first_refresh(self) -> None:
        self.data = await self._async_update_data()

    async def async_shutdown(self) -> None:
        return None


class UpdateFailed(HomeAssistantError):
    pass


class CoordinatorEntity(Entity, _Subscriptable):
    def __init__(self, coordinator, context=None) -> None:
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success)


class DeviceInfo(dict):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class IssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class CoverDeviceClass(str, Enum):
    GARAGE = "garage"
    GATE = "gate"


class CoverEntityFeature(IntFlag):
    OPEN = 1
    CLOSE = 2
    STOP = 8


class CoverEntity(Entity):
    pass


class ButtonEntity(Entity):
    pass


class SwitchEntity(Entity):
    pass


class SwitchEntityDescription(EntityDescription):
    pass


class SensorDeviceClass(str, Enum):
    ENUM = "enum"
    TIMESTAMP = "timestamp"
    SIGNAL_STRENGTH = "signal_strength"
    VOLTAGE = "voltage"
    TEMPERATURE = "temperature"


class SensorStateClass(str, Enum):
    MEASUREMENT = "measurement"


class SensorEntity(Entity):
    @property
    def options(self) -> list[str] | None:
        if self._attr_options is not None:
            return self._attr_options
        description = self.entity_description
        return getattr(description, "options", None) if description else None


class SensorEntityDescription(EntityDescription):
    pass


class BinarySensorDeviceClass(str, Enum):
    CONNECTIVITY = "connectivity"
    PROBLEM = "problem"


class BinarySensorEntity(Entity):
    pass


class BinarySensorEntityDescription(EntityDescription):
    pass


class AlarmControlPanelEntityFeature(IntFlag):
    ARM_AWAY = 2


class AlarmControlPanelState(str, Enum):
    DISARMED = "disarmed"
    ARMED_AWAY = "armed_away"
    TRIGGERED = "triggered"


class AlarmControlPanelEntity(Entity):
    _attr_code_arm_required = True


class UnitOfElectricPotential(str, Enum):
    VOLT = "V"


class UnitOfTemperature(str, Enum):
    CELSIUS = "°C"


class ConfigEntry(_Subscriptable):
    def __init__(self, *, entry_id: str = "entry", data: dict | None = None, options: dict | None = None) -> None:
        self.entry_id = entry_id
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.runtime_data: Any = None
        self._unload: list[Any] = []

    def async_on_unload(self, func) -> None:
        self._unload.append(func)

    def add_update_listener(self, func):
        return func


class ConfigFlow:
    def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
        cls.DOMAIN = domain


class OptionsFlow:
    pass


ConfigFlowResult = dict


class HomeAssistant:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


class Store:
    """Home Assistant's storage helper, kept in memory: one document per key, in ``hass.data``."""

    def __init__(self, hass: Any, version: int, key: str) -> None:
        self.key = key
        self._documents = hass.data.setdefault("storage", {})
        self.writes = hass.data.setdefault("storage_writes", [])

    async def async_load(self) -> Any:
        return self._documents.get(self.key)

    async def async_save(self, data: Any) -> None:
        self._documents[self.key] = data
        self.writes.append(self.key)

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None:
        self._documents[self.key] = data_func()
        self.writes.append(self.key)

    async def async_remove(self) -> None:
        self._documents.pop(self.key, None)


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class _Selector:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs


def _install_voluptuous() -> None:
    try:
        import voluptuous  # noqa: F401

        return
    except ImportError:
        pass
    vol = _module("voluptuous")

    class _Marker:
        def __init__(self, key: Any, default: Any = None, **kwargs: Any) -> None:
            self.key = key
            self.default = default

        def __hash__(self) -> int:
            return hash(self.key)

        def __eq__(self, other: Any) -> bool:
            return isinstance(other, _Marker) and other.key == self.key

    class Schema:
        def __init__(self, schema: Any, **kwargs: Any) -> None:
            self.schema = schema

    def _passthrough(*args: Any, **kwargs: Any) -> Any:
        return args[0] if args else None

    vol.Schema = Schema
    vol.Required = _Marker
    vol.Optional = _Marker
    vol.In = _passthrough
    vol.All = _passthrough
    vol.Coerce = _passthrough
    vol.Range = _passthrough


def install() -> None:
    """Install the stubs (idempotent)."""
    if "homeassistant" in sys.modules and getattr(sys.modules["homeassistant"], "_mycentsys_stub", False):
        return
    ha = _module("homeassistant")
    ha._mycentsys_stub = True  # type: ignore[attr-defined]

    core = _module("homeassistant.core")
    core.HomeAssistant = HomeAssistant
    core.callback = _callback

    exceptions = _module("homeassistant.exceptions")
    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed

    const = _module("homeassistant.const")
    const.EntityCategory = EntityCategory
    const.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"
    const.CONF_COUNTRY = "country"
    const.CONF_DEVICES = "devices"
    const.CONF_EMAIL = "email"
    const.CONF_NAME = "name"
    const.CONF_REGION = "region"
    const.CONF_TOKEN = "token"
    const.UnitOfElectricPotential = UnitOfElectricPotential
    const.UnitOfTemperature = UnitOfTemperature

    config_entries = _module("homeassistant.config_entries")
    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigFlow = ConfigFlow
    config_entries.ConfigFlowResult = ConfigFlowResult
    config_entries.OptionsFlow = OptionsFlow

    helpers = _module("homeassistant.helpers")
    issue_registry = _module("homeassistant.helpers.issue_registry")
    issue_registry.IssueSeverity = IssueSeverity
    issue_registry.async_create_issue = lambda *args, **kwargs: None
    issue_registry.async_delete_issue = lambda *args, **kwargs: None
    aiohttp_client = _module("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: None
    update_coordinator = _module("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    device_registry = _module("homeassistant.helpers.device_registry")
    device_registry.DeviceInfo = DeviceInfo
    device_registry.async_get = lambda hass: None
    device_registry.async_entries_for_config_entry = lambda registry, entry_id: []
    storage = _module("homeassistant.helpers.storage")
    storage.Store = Store
    entity_registry = _module("homeassistant.helpers.entity_registry")
    # A test that wants a registry puts one in ``hass.data`` under this name.
    entity_registry.async_get = lambda hass: hass.data.get("entity_registry")
    entity = _module("homeassistant.helpers.entity")
    entity.Entity = Entity
    entity_platform = _module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = Any
    config_validation = _module("homeassistant.helpers.config_validation")
    config_validation.multi_select = lambda options: options
    selector = _module("homeassistant.helpers.selector")
    selector.SelectSelector = _Selector
    selector.SelectSelectorConfig = _Selector
    selector.SelectOptionDict = dict
    selector.SelectSelectorMode = types.SimpleNamespace(DROPDOWN="dropdown", LIST="list")
    selector.TextSelector = _Selector
    selector.TextSelectorConfig = _Selector
    selector.TextSelectorType = types.SimpleNamespace(TEL="tel")
    helpers.issue_registry = issue_registry

    components = _module("homeassistant.components")
    cover = _module("homeassistant.components.cover")
    cover.CoverDeviceClass = CoverDeviceClass
    cover.CoverEntity = CoverEntity
    cover.CoverEntityFeature = CoverEntityFeature
    button = _module("homeassistant.components.button")
    button.ButtonEntity = ButtonEntity
    switch = _module("homeassistant.components.switch")
    switch.SwitchEntity = SwitchEntity
    switch.SwitchEntityDescription = SwitchEntityDescription
    sensor = _module("homeassistant.components.sensor")
    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorEntity = SensorEntity
    sensor.SensorEntityDescription = SensorEntityDescription
    sensor.SensorStateClass = SensorStateClass
    binary_sensor = _module("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorEntityDescription = BinarySensorEntityDescription
    alarm = _module("homeassistant.components.alarm_control_panel")
    alarm.AlarmControlPanelEntity = AlarmControlPanelEntity
    alarm.AlarmControlPanelEntityFeature = AlarmControlPanelEntityFeature
    alarm.AlarmControlPanelState = AlarmControlPanelState
    del components

    util = _module("homeassistant.util")
    dt = _module("homeassistant.util.dt")
    dt.parse_datetime = _parse_datetime
    util.dt = dt

    _install_voluptuous()


def load_integration():
    """Import the integration package with the stubs in place."""
    install()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("custom_components.mycentsys_cloud")
