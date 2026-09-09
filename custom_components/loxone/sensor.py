"""
Loxone Sensors

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import json
import logging
import re
from dataclasses import replace
from functools import cached_property
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import (
    CONF_STATE_CLASS,
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_UNIT_OF_MEASUREMENT,
    LIGHT_LUX,
    PERCENTAGE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfRatio,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import LoxoneEntity
from .const import (
    CONF_ACTIONID,
    DEVICE_TYPE_ANALOG,
    DOMAIN,
    ERROR_VALUE,
    EVENT,
    THROTTLE_KEEP_ALIVE_TIME,
    loxone_climate_demand_signal,
)
from .helpers import clean_unit, device_info_for, get_miniserver_type, iter_controls, software_version_string
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Loxone Sensor"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ACTIONID): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
        vol.Optional(CONF_DEVICE_CLASS): cv.string,
        vol.Optional(CONF_STATE_CLASS): cv.string,
    }
)

# IRoomControllerV2 override-reason codes, as slugs for translation
# (``entity.sensor.loxone.override_reason.state.<slug>``). A single ``unknown``
# slug covers every other/odd code instead of minting a ``"Unknown (n)"``
# display string per code (CORE-22).
OVERRIDE_REASON_SLUGS = {
    0: "none",
    1: "presence",
    2: "window_open",
    3: "comfort_override",
    4: "eco_override",
    5: "eco_plus_override",
    6: "prepare_heat_up",
    7: "prepare_cool_down",
    8: "overridden_by_source",
    14: "fixed",
}
OVERRIDE_REASON_UNKNOWN = "unknown"

# Meter sub-sensor classification (PS-21). A blanket TOTAL_INCREASING on every
# kWh/L-formatted value put "Consumption today" counters and the `totalNeg`
# register in the energy dashboard, where resets show up as spikes.
METER_STATE_CLASSES = {
    "actual": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "total": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    "totalNeg": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    "storage": (SensorDeviceClass.ENERGY, SensorStateClass.MEASUREMENT),
}
METER_FORMAT_KEYS = {
    "actual": "actualFormat",
    "total": "totalFormat",
    "totalNeg": "totalFormat",
    "storage": "storageFormat",
}
METER_NAME_SUFFIX = {
    "actual": "Actual",
    "total": "Total",
    "totalNeg": "Total Neg",
    "storage": "Level",
}

# A plain InfoOnlyAnalog that counts total energy/water deserves
# ``TOTAL_INCREASING`` only when its name/category actually says it is a
# meter. Anything else (e.g. "Consumption today") is a resetting value and
# gets MEASUREMENT (PS-21).
METERING_KEYWORDS = ("total", "meter", "zähler", "zaehler", "compteur", "counter")


class LoxoneEntityDescription(SensorEntityDescription, frozen_or_thawed=True):
    """
    Describes a Loxone sensor entity.

    Acts as a classification object: carries matching criteria (which Loxone
    units/keywords trigger this description) and the resulting classification
    (device_class, state_class). Presentation details (actual unit, precision)
    come from the Loxone format string via _attr_* in __init__.
    """

    loxone_format_strings: tuple[str, ...]
    category_keywords: tuple[str, ...] = ()
    name_keywords: tuple[str, ...] = ()


SENSOR_TYPES: tuple[LoxoneEntityDescription, ...] = (
    LoxoneEntityDescription(
        key="temperature",
        loxone_format_strings=(UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    LoxoneEntityDescription(
        key="wind_speed",
        loxone_format_strings=(UnitOfSpeed.KILOMETERS_PER_HOUR,),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.WIND_SPEED,
    ),
    LoxoneEntityDescription(
        key="energy",
        loxone_format_strings=(
            UnitOfEnergy.KILO_WATT_HOUR,
            UnitOfEnergy.WATT_HOUR,
            UnitOfEnergy.MEGA_WATT_HOUR,
        ),
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    LoxoneEntityDescription(
        key="power",
        loxone_format_strings=(UnitOfPower.WATT, UnitOfPower.KILO_WATT),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    LoxoneEntityDescription(
        key="volume_flow_rate",
        loxone_format_strings=(
            UnitOfVolumeFlowRate.LITERS_PER_HOUR,
            UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        ),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
    ),
    LoxoneEntityDescription(
        key="water",
        loxone_format_strings=(UnitOfVolume.LITERS,),
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.WATER,
    ),
    LoxoneEntityDescription(
        key="illuminance",
        loxone_format_strings=(LIGHT_LUX, "Lx", "lx", "lux"),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ILLUMINANCE,
    ),
    LoxoneEntityDescription(
        key="carbon_dioxide",
        loxone_format_strings=(UnitOfRatio.PARTS_PER_MILLION,),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CO2,
    ),
    LoxoneEntityDescription(
        key="humidity",
        loxone_format_strings=(PERCENTAGE,),
        category_keywords=("vlhkost", "humidity", "feucht", "humidité"),
        name_keywords=("vlhkost", "humidity", "feucht", "humidité"),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.HUMIDITY,
    ),
    LoxoneEntityDescription(
        key="battery",
        loxone_format_strings=(PERCENTAGE,),
        name_keywords=("batt", "akku", "battery"),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    ),
)

UNAMBIGUOUS_UNITS: frozenset[str] = frozenset(
    u
    for desc in SENSOR_TYPES
    if not desc.category_keywords and not desc.name_keywords
    for u in desc.loxone_format_strings
)
"""Units that map to exactly one device class without needing keyword disambiguation."""

# The Loxone format spec types that carry a *numeric* reading. ``s`` is the
# only string-typed format; state_class may only be advertised for numeric
# values, or HA raises a ValueError when a text string arrives (PS-09).
_NUMERIC_EXCLUDED_TYPES = frozenset({"s", ""})


def _is_numeric_format(lox_format: Any) -> bool:
    """True when the Loxone format string carries a numeric value type."""
    if not isinstance(lox_format, str):
        return False
    match = re.search(r"%[-+0 #]*\d*(?:\.\d*)?[a-zA-Z%]+", lox_format)
    if not match:
        return False
    return match.group(0)[-1] not in _NUMERIC_EXCLUDED_TYPES


def _analog_value(value: Any) -> Any:
    """Normalise a raw InfoOnlyAnalog stream value for ``native_value``.

    ``None`` and the Miniserver's error sentinel (``ERROR_VALUE`` == -1)
    mean "no reading" and map to ``None`` (HA shows ``unknown``);
    everything else is passed through unchanged (PS-09).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) == ERROR_VALUE:
        return None
    return value


def _metering_indicated(name: str, category: str) -> bool:
    """True when the name/category names a running meter (PS-21)."""
    lowered = f"{name} {category}".lower()
    return any(kw in lowered for kw in METERING_KEYWORDS)


def match_sensor_description(
    unit: str,
    name: str = "",
    category: str = "",
) -> LoxoneEntityDescription | None:
    """
    Find the first matching sensor description for a Loxone sensor.

    Unambiguous units (°C, kWh, ppm, …) match immediately.
    Ambiguous units (%) require a keyword hit in name or category.
    Returns None if no description matches.
    """
    name_lower = name.lower()
    cat_lower = category.lower()
    for desc in SENSOR_TYPES:
        if unit not in desc.loxone_format_strings:
            continue
        if not desc.category_keywords and not desc.name_keywords:
            return desc
        cat_match = any(kw in cat_lower for kw in desc.category_keywords)
        name_match = any(kw in name_lower for kw in desc.name_keywords)
        if cat_match or name_match:
            return desc
    return None


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_devices: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Sensor from yaml"""
    # Devices from yaml
    if config:
        # Setup all Sensors in Yaml-File
        new_sensor = LoxoneCustomSensor(**config)
        async_add_devices([new_sensor], update_before_add=True)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    miniserver = get_miniserver_from_hass(hass, config_entry)

    loxconfig = miniserver.lox_config.json

    # PS-20: the keep-alive and version sensors belong to the Miniserver
    # host device (identifiers = (DOMAIN, serial)), not to no device at
    # all.  Skipped (as before) when the structure file has no serial /
    # software version.
    ms_device_info: DeviceInfo | None = None
    if miniserver.serial:
        ms_device_info = device_info_for(
            config_entry,
            miniserver.serial,
            miniserver.name,
            get_miniserver_type(miniserver.miniserver_type),
        )
    entities: list[Any] = [LoxoneKeepAliveSensor(miniserver.serial, ms_device_info)]

    if "softwareVersion" in loxconfig:
        entities.append(LoxoneVersionSensor(miniserver.serial, loxconfig["softwareVersion"], ms_device_info))

    for sensor in iter_controls(hass, config_entry, "InfoOnlyAnalog"):
        try:
            sensor.update({"type": "analog", "config_entry": config_entry})
            entities.append(LoxoneSensor(**sensor))
        except Exception:
            # One bad control must not abort the whole sensor platform
            # (PS-08).
            _LOGGER.exception("Skipping InfoOnlyAnalog control %s", sensor.get("name", "?"))

    for sensor in iter_controls(hass, config_entry, "TextInput"):
        try:
            sensor.update({"config_entry": config_entry})
            entities.append(LoxoneTextSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping TextInput control %s", sensor.get("name", "?"))

    for sensor in iter_controls(hass, config_entry, "Meter"):
        _LOGGER.debug("Found Meter: %s", sensor.get("name"))
        try:
            device_info = LoxoneMeterSensor.create_device_info_from_sensor(sensor, config_entry)
            for state_key in METER_STATE_CLASSES:
                if state_key not in sensor.get("states", {}):
                    continue
                format_value = sensor.get("details", {}).get(METER_FORMAT_KEYS[state_key], "%.1f")
                subsensor = {
                    "device_info": device_info,
                    "parent_id": sensor["uuidAction"],
                    "uuidAction": sensor["states"][state_key],
                    "type": "analog",
                    "room": sensor.get("room", ""),
                    "cat": sensor.get("cat", ""),
                    "name": f"{sensor['name']} {METER_NAME_SUFFIX[state_key]}",
                    "details": {"format": format_value},
                    "device_class": METER_STATE_CLASSES[state_key][0],
                    "state_class": METER_STATE_CLASSES[state_key][1],
                    "config_entry": config_entry,
                }
                entities.append(LoxoneMeterSensor(**subsensor))
        except Exception:
            _LOGGER.exception("Skipping Meter control %s", sensor.get("name", "?"))

    # Climate controller demand sensors
    for ctrl_type in ("ClimateController", "ClimateControllerUS"):
        for ctrl in iter_controls(hass, config_entry, ctrl_type):
            try:
                ctrl_kwargs = {**ctrl, "type": "climate_controller", "hass": hass, "config_entry": config_entry}
                entities.append(LoxoneClimateController(**ctrl_kwargs))
            except Exception:
                _LOGGER.exception("Skipping %s control %s", ctrl_type, ctrl.get("name", "?"))

    # IRoomControllerV2 sub-sensors: override reason + comfort temperatures
    for irc in iter_controls(hass, config_entry, "IRoomControllerV2"):
        try:
            states = irc.get("states", {})
            device_info = device_info_for(config_entry, irc["uuidAction"], irc["name"], "RoomControllerV2", irc.get("room", ""))

            if "overrideReason" in states:
                entities.append(
                    LoxoneRoomControllerOverrideSensor(
                        name=f"{irc['name']} Override Reason",
                        uuid=states["overrideReason"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )

            if "comfortTemperature" in states:
                entities.append(
                    LoxoneRoomControllerTemperatureSensor(
                        name=f"{irc['name']} Comfort Temperature",
                        uuid=states["comfortTemperature"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )

            if "comfortTemperatureCool" in states:
                entities.append(
                    LoxoneRoomControllerTemperatureSensor(
                        name=f"{irc['name']} Comfort Temperature Cool",
                        uuid=states["comfortTemperatureCool"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )
        except Exception:
            _LOGGER.exception("Skipping IRoomControllerV2 control %s", irc.get("name", "?"))

    # CORE-17: the old code subscribed to an ``async_signal_new_device``
    # signal that no code path ever sent and leaked the unsubscribe on
    # ``MiniServer.listeners`` (never iterated).  Sensors are created
    # exclusively from the structure file.
    async_add_entities(entities, update_before_add=True)


class LoxoneCustomSensor(LoxoneEntity, SensorEntity):
    def __init__(self, **kwargs):
        self._attr_name = kwargs.pop("name", None)
        self._attr_state_class = kwargs.pop("state_class", None)
        self._attr_device_class = kwargs.pop("device_class", None)
        self._attr_native_unit_of_measurement = kwargs.pop("unit_of_measurement", None)
        self._attr_native_value = None  # Initialize state
        # Must be after the kwargs.pop functions!
        super().__init__(**kwargs)

    @cached_property
    def unique_id(self) -> str:
        """Return a unique ID.

        A YAML sensor without a name still gets a usable unique id (PS-07):
        the uuidAction alone, or uuidAction+name when a name is given.
        """
        name = self._attr_name
        if name:
            return f"{self.uuidAction}-{name}"
        return self.uuidAction

    @callback
    def event_handler(self, e):
        if self.uuidAction in e:
            data = e[self.uuidAction]
            if isinstance(data, (list, dict)):
                data = str(data)
                if len(data) >= 255:
                    self._attr_native_value = data[:255]
                else:
                    self._attr_native_value = data
            else:
                self._attr_native_value = data

            self.async_write_ha_state()

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        if self._attr_native_unit_of_measurement in ["None", "none", "-"]:
            return None
        return self._attr_native_unit_of_measurement

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {**self._attr_extra_state_attributes}


class LoxoneKeepAliveSensor(LoxoneEntity, SensorEntity):
    _attr_name = "Loxone Last Keep Alive Message"
    _attr_icon = "mdi:information-outline"
    _attr_unique_id = "loxone_keep_alive_sensor_uuid"
    _attr_device_class = SensorDeviceClass.TIMESTAMP  # tell HA this is a timestamp
    # PS-20: diagnostic on the Miniserver device, hidden in the UI by
    # default (it is a connection heartbeat, not a measurement).
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, miniserver_serial, device_info: DeviceInfo | None = None, **kwargs):
        super().__init__(**kwargs)
        self._miniserver_serial = miniserver_serial
        # PS-20: attach to the Miniserver host device (identifiers
        # (DOMAIN, serial)); a structure file without a serial yields
        # ``device_info is None`` and a device-less entity (no
        # ``(DOMAIN, None)`` identifier).
        if device_info is not None:
            self._attr_device_info = device_info
        self._attr_native_value = None

    @cached_property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self._miniserver_serial}-{self._attr_unique_id}"

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the special keep-alive stream injected by the coordinator.
        return frozenset({"keep_alive"})

    @callback
    def event_handler(self, e):
        if e.get("keep_alive") == "received":
            now = dt_util.utcnow()
            # only update if at least 60 seconds passed since last update
            if self._attr_native_value is not None:
                time_since_last = (now - self._attr_native_value).total_seconds()
                if time_since_last < THROTTLE_KEEP_ALIVE_TIME:
                    # too soon, skip this update
                    return

            # update the timestamp
            self._attr_native_value = now
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {**self._attr_extra_state_attributes}


class LoxoneVersionSensor(LoxoneEntity, SensorEntity):
    _attr_name = "Loxone Software Version"
    _attr_icon = "mdi:information-outline"
    _attr_unique_id = "loxone_software_version_uuid"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, miniserver_serial, version, device_info: DeviceInfo | None = None, **kwargs):
        super().__init__(**kwargs)
        self._miniserver_serial = miniserver_serial
        # PS-20: ``software_version_string`` handles list-form *and*
        # string-form versions (the old join split the string into
        # characters); an unusable value stays ``None`` (HA renders
        # unknown) instead of the literal string "unknown".
        parsed = software_version_string(version)
        self._attr_native_value = parsed if parsed else None
        if device_info is not None:
            self._attr_device_info = device_info

    @cached_property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self._miniserver_serial}-{self._attr_unique_id}"


class LoxoneTextSensor(LoxoneEntity, SensorEntity):
    """Representation of a Text Sensor."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = "TextInput"
        self._state = None
        self._state_uuid = self.states.get("text") or self.uuidAction
        # CORE-20 / device link: a fresh device built from the control's
        # own identity (previously no device at all).
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self.name, self.type, kwargs.get("room", "")
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the ``text`` state stream (falls back to uuidAction).
        return frozenset({self._state_uuid, self.uuidAction})

    @callback
    def event_handler(self, e):
        if self._state_uuid in e:
            self._state = _analog_value(e[self._state_uuid])
            if self._state is not None:
                self._state = str(self._state)
            self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    async def async_set_value(self, value):
        """Set new value."""
        self._send(f"{value}")
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


class LoxoneSensor(LoxoneEntity, SensorEntity):
    """Representation of a Loxone Sensor."""

    def __init__(self, **kwargs):
        # Register-level classification from the Meter setup (PS-21); popped
        # here so the generic kwarg loop in LoxoneEntity does not try to
        # setattr them as plain attributes.
        forced_device_info = kwargs.pop("device_info", None)
        forced_device_class = kwargs.pop("device_class", None)
        forced_state_class = kwargs.pop("state_class", None)
        super().__init__(**kwargs)
        # CORE-20: a forced device (fan sub-sensors pass the parent's,
        # Meter sub-sensors pass the meter's own) wins over the default.
        self._forced_device_info = forced_device_info
        details = getattr(self, "details", None)
        details = details if isinstance(details, dict) else {}
        lox_format = details.get("format", "")
        self._attr_should_poll = False
        self._attr_native_unit_of_measurement = clean_unit(lox_format) if isinstance(lox_format, str) else None
        self._parent_id = kwargs.get("parent_id")

        # PS-25: a format with an explicit ``.0`` still has a real precision
        # of 0 digits (``if precision:`` treated 0 as "none").
        precision = self._parse_digits_after_decimal(lox_format)
        if precision is not None:
            self._attr_suggested_display_precision = precision

        # Device class is detected automatically from unit/category/name.
        # To override for a specific entity, use HA's customize in configuration.yaml:
        #   homeassistant:
        #     customize:
        #       sensor.my_sensor:
        #         device_class: battery
        desc = match_sensor_description(
            unit=self._attr_native_unit_of_measurement,
            name=self.name,
            category=kwargs.get("cat", ""),
        )

        # Per-register classifications from the Meter setup (PS-21) win over
        # the unit-based match.
        if desc is not None and (forced_device_class is not None or forced_state_class is not None):
            desc = replace(
                desc,
                device_class=forced_device_class or desc.device_class,
                state_class=forced_state_class or desc.state_class,
            )

        # A plain (non-Meter) kWh/L value only becomes TOTAL_INCREASING when
        # the name/category indicates a real meter; resetting values like
        # "Consumption today" would otherwise show spikes in the energy
        # dashboard (PS-21).
        if (
            desc is not None
            and forced_state_class is None
            and desc.state_class == SensorStateClass.TOTAL_INCREASING
            and not _metering_indicated(self.name, kwargs.get("cat", ""))
        ):
            desc = replace(desc, state_class=SensorStateClass.MEASUREMENT)

        numeric = _is_numeric_format(lox_format)
        if desc is not None:
            if not numeric and desc.state_class is not None:
                # Text values must not advertise a numeric state_class,
                # or HA raises when a text string is published (PS-09).
                desc = replace(desc, state_class=None)
            self.entity_description = desc
        elif numeric:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        # PS-14: the group table matches the "Sensor analog" constant
        # (the old extras value appended "_sensor" to it, and the old
        # device identifier used the parent id for sub-sensors, so the
        # shared dict was seeded by whichever sibling was built first).
        self.type = DEVICE_TYPE_ANALOG
        self._attr_device_info = self._forced_device_info or device_info_for(
            kwargs.get("config_entry"), self.unique_id, self.name, self.type, self.room
        )

    def _parse_digits_after_decimal(self, format_string: Any):
        """Parse digits after the decimal point from the format string."""
        if not isinstance(format_string, str):
            return None
        pattern = r"\.(\d+)"
        match = re.search(pattern, format_string)
        if match:
            digits = int(match.group(1))
            return digits
        return None

    @callback
    def event_handler(self, e):
        if self.uuidAction in e:
            self._attr_native_value = _analog_value(e[self.uuidAction])
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


class LoxoneMeterSensor(LoxoneSensor, SensorEntity):
    @staticmethod
    def create_device_info_from_sensor(sensor, config_entry=None) -> DeviceInfo:
        # PS-20 device link: the meter device carries its own (DOMAIN,
        # uuid) identifier, the control's name and model, and links to
        # the Miniserver host device; all sub-registers share it.
        try:
            # For legacy Meter
            model = sensor["details"]["type"].capitalize() + " Meter"
        except KeyError, TypeError:
            model = "Meter"
        return device_info_for(config_entry, sensor["uuidAction"], sensor["name"], model, sensor.get("room", ""))


class LoxoneRoomControllerTemperatureSensor(SensorEntity):
    """Sensor for IRoomControllerV2 comfort temperature states."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, name: str, uuid: str, device_info: DeviceInfo, parent_uuid: str):
        self._attr_name = name
        self._uuid = uuid
        self._attr_unique_id = uuid
        self._attr_device_info = device_info
        self._attr_native_value = None
        self._parent_uuid = parent_uuid

    async def async_added_to_hass(self):
        """Subscribe to Loxone events."""

        @callback
        def _on_bus_message(event) -> None:
            # CORE-27: handlers take the plain {uuid: value} dict on every
            # path (the dispatcher slice or the bus event's data).
            self.event_handler(event.data)

        self.async_on_remove(self.hass.bus.async_listen(EVENT, _on_bus_message))

    @callback
    def event_handler(self, e):
        if self._uuid in e:
            self._attr_native_value = _analog_value(e[self._uuid])
            self.async_write_ha_state()


class LoxoneRoomControllerOverrideSensor(SensorEntity):
    """Sensor for IRoomControllerV2 override reason.

    The values are slugs that resolve through
    ``entity.sensor.loxone.override_reason.state.<slug>`` (CORE-22).
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "override_reason"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, name: str, uuid: str, device_info: DeviceInfo, parent_uuid: str):
        self._attr_name = name
        self._uuid = uuid
        self._attr_unique_id = uuid
        self._attr_device_info = device_info
        self._attr_native_value = OVERRIDE_REASON_SLUGS[0]
        self._attr_options = [*OVERRIDE_REASON_SLUGS.values(), OVERRIDE_REASON_UNKNOWN]
        self._parent_uuid = parent_uuid

    async def async_added_to_hass(self):
        """Subscribe to Loxone events."""

        @callback
        def _on_bus_message(event) -> None:
            # CORE-27: handlers take the plain {uuid: value} dict on every
            # path (the dispatcher slice or the bus event's data).
            self.event_handler(event.data)

        self.async_on_remove(self.hass.bus.async_listen(EVENT, _on_bus_message))

    @callback
    def event_handler(self, e):
        if self._uuid in e:
            try:
                code = int(float(e[self._uuid]))
            except TypeError, ValueError:
                return
            slug = OVERRIDE_REASON_SLUGS.get(code, OVERRIDE_REASON_UNKNOWN)
            if slug not in self._attr_options:
                self._attr_options = [*self._attr_options, slug]
            self._attr_native_value = slug
            self.async_write_ha_state()


class LoxoneClimateController(LoxoneEntity, SensorEntity):
    """Climate controller sensor that fires the per-room demand signals
    for IRoomControllerV2 (PS-18: entry-scoped dispatcher signal replacing
    the global CLIMATE_EVENT bus event).

    Reads the control list from the ClimateController's state and publishes
    each room's demand (1 = heating, -1 = cooling, 0 = idle) to that
    room's own per-uuid signal.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._stateAttribUuids = kwargs.get("states", {})
        self._stateAttribValues = {}
        self._heat_demand = 0
        self._cool_demand = 0
        self.type = "ClimateController"

        self._attr_device_info = device_info_for(kwargs.get("config_entry"), self.unique_id, self.name, self.type, self.room)

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: every monitored state stream of the climate controller.
        return frozenset(uuid for uuid in self._stateAttribUuids.values() if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, e):
        update = False
        coordinator = self._connection_coordinator()
        entry_id = coordinator.config_entry.entry_id if coordinator is not None else None

        for key in set(self._stateAttribUuids.values()) & e.keys():
            raw = e[key]
            # Parse JSON control lists from the Miniserver
            if isinstance(raw, str) and raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    self._stateAttribValues[key] = parsed
                    # PS-18: fan out heat/cool demand per room via that
                    # room's own per-uuid dispatcher signal — entry B's
                    # ClimateController can no longer flip entry A's rooms.
                    heat_count = 0
                    cool_count = 0
                    for control in parsed:
                        demand = control.get("demand", 0)
                        if demand == 1:
                            heat_count += 1
                        elif demand == -1:
                            cool_count += 1
                        room_uuid = control.get("uuid")
                        if entry_id is not None and isinstance(room_uuid, str) and room_uuid:
                            async_dispatcher_send(self.hass, loxone_climate_demand_signal(entry_id, room_uuid), demand)
                    self._heat_demand = heat_count
                    self._cool_demand = cool_count
                except (json.JSONDecodeError, TypeError, KeyError) as err:
                    _LOGGER.debug("ClimateController JSON parse error: %s", err)
            else:
                self._stateAttribValues[key] = raw
            update = True

        if update:
            self.schedule_update_ha_state()

    @property
    def native_value(self):
        """Return summary state."""
        if self._heat_demand > 0:
            return f"Heating ({self._heat_demand})"
        if self._cool_demand > 0:
            return f"Cooling ({self._cool_demand})"
        return "Idle"

    @property
    def extra_state_attributes(self):
        """Return detailed demand attributes."""
        return {
            **self._attr_extra_state_attributes,
            "heat_demand": self._heat_demand,
            "cool_demand": self._cool_demand,
            "device_type": self.type,
        }
