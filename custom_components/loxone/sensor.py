"""
Loxone Sensors

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import json
import logging
import re
from dataclasses import replace
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

# WP-6.5: the newer metering controls expose the same register set as the
# legacy ``Meter`` (actual power, running totals, storage level), so they
# run the same sub-state loop with the classification table above.  Only
# the registers actually present in a control's ``states`` yield entities,
# so a control with fewer registers simply yields fewer sub-sensors.
METER_FAMILY_TYPES = ("Meter", "EnergyManager", "EnergyManager2", "PowerUnit", "Wallbox")

# #461: the analog sub-readings a PresenceDetector control publishes
# alongside its presence signal, as ``state key -> (entity name, Loxone
# format)``.  Presence detectors without light/sound hardware advertise
# no such state, so only the present states yield a sub-sensor; the
# format fixes the unit (lux / dB), which drives the device class via
# the unit table below (illuminance matches ``lx``, noise gets a plain
# numeric measurement).  WP-6.1 keeps the *presence* device linkage in
# the binary_sensor platform: the sub-sensors here carry the parent
# control's device identifiers, so HA merges them into the same device.
PRESENCE_SUB_SENSOR_SPECS: dict[str, tuple[str, str]] = {
    "illuminance": ("Illuminance", "%.0f lx"),
    "noise": ("Noise", "%.0f dB"),
}
# The model name the binary_sensor platform stamps on the presence
# device (`self.type` = "presence"), so a structure file emits one
# merged device instead of two.
PRESENCE_DEVICE_MODEL = "presence"

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


def presence_sub_sensor_kwargs(control: dict, config_entry) -> list[dict]:
    """``LoxoneSensor`` kwargs for the illuminance/noise sub-states of a
    PresenceDetector control (#461).

    One sub-sensor dict per advertised sub-state: short entity name
    (the device is named after the parent control), the *parent*
    control's uuid as ``parent_id``, and device info built from the
    parent's identifiers so the sub-sensors land on the same device as
    the presence binary sensor (same house pattern as the Meter and
    IRoomControllerV2 sub-sensors).  Every ``states``/``details`` lookup
    is guarded with ``.get()``: a structure file without the states
    yields *no* sub-sensors instead of aborting the platform.  The
    return order is the insertion order of ``PRESENCE_SUB_SENSOR_SPECS``.
    """
    states = control.get("states")
    if not isinstance(states, dict):
        return []
    uuid_action = control.get("uuidAction")
    if not isinstance(uuid_action, str) or not uuid_action:
        # PC-05: device_info_for needs the parent uuid for the shared
        # device identifiers; without it there is no device to attach to.
        return []
    room = control.get("room", "")
    kwargs_list: list[dict] = []
    for state_name, (name, default_format) in PRESENCE_SUB_SENSOR_SPECS.items():
        uuid = states.get(state_name)
        if not isinstance(uuid, str) or not uuid:
            continue
        kwargs_list.append(
            {
                "parent_id": uuid_action,
                "uuidAction": uuid,
                "type": "analog",
                "room": room,
                "cat": control.get("cat", ""),
                "name": name,
                "details": {"format": default_format},
                "device_info": device_info_for(
                    config_entry, uuid_action, control.get("name", ""), PRESENCE_DEVICE_MODEL, room
                ),
                "config_entry": config_entry,
            }
        )
    return kwargs_list


def meter_device_model(control: dict) -> str:
    """Device model string for a Meter-family control (WP-6.5).

    A legacy ``Meter`` may carry a free-form ``details.type`` (e.g.
    ``"Module Meter"``) which historically produced ``"<Type> Meter"``;
    the other family members are modelled by their control type name.
    """
    control_type = control.get("type")
    if control_type == "Meter":
        details = control.get("details")
        legacy = details.get("type") if isinstance(details, dict) else None
        if isinstance(legacy, str) and legacy:
            return legacy.capitalize() + " Meter"
        return "Meter"
    if isinstance(control_type, str) and control_type:
        return control_type
    return "Meter"


def meter_device_info(control: dict, config_entry) -> dict | None:
    """Shared device info for the registers of one Meter-family control
    (WP-6.5).

    All sub-registers of one control carry the parent control's own
    ``(DOMAIN, uuidAction)`` identifier and name/model, so the device
    registry merges them into a single device (PS-20, same house
    pattern as the IRoomControllerV2 and presence sub-sensors).  Without
    a usable ``uuidAction`` this returns ``None`` and the register falls
    back to its own device.
    """
    uuid_action = control.get("uuidAction")
    if not isinstance(uuid_action, str) or not uuid_action:
        return None
    return device_info_for(
        config_entry,
        uuid_action,
        control.get("name", ""),
        meter_device_model(control),
        control.get("room", ""),
    )


def meter_sub_sensor_kwargs(control: dict, config_entry) -> list[dict]:
    """``LoxoneMeterSensor`` kwargs for the registers of a Meter-family
    control (WP-6.5): ``Meter``, ``EnergyManager``, ``EnergyManager2``,
    ``PowerUnit`` and ``Wallbox``.

    One sub-sensor dict per *advertised* register: only the registers
    present (as a state uuid) in the control's ``states`` yield kwargs.
    Every ``states``/``details`` lookup is guarded with ``.get()``: a
    truncated structure file yields fewer (or no) sub-sensors instead of
    aborting the platform (PS-08).  The return order is the insertion
    order of ``METER_STATE_CLASSES``.
    """
    states = control.get("states")
    if not isinstance(states, dict):
        return []
    details = control.get("details")
    details = details if isinstance(details, dict) else {}
    device_info = meter_device_info(control, config_entry)
    kwargs_list: list[dict] = []
    for state_key, (device_class, state_class) in METER_STATE_CLASSES.items():
        uuid = states.get(state_key)
        if not isinstance(uuid, str) or not uuid:
            continue
        kwargs_list.append(
            {
                "device_info": device_info,
                "parent_id": control.get("uuidAction", ""),
                "uuidAction": uuid,
                "type": "analog",
                "room": control.get("room", ""),
                "cat": control.get("cat", ""),
                # WP-5.1: short sub-entity name — the device is named
                # after the parent control, so no control-name prefix
                # (CORE-26).
                "name": METER_NAME_SUFFIX[state_key],
                "details": {"format": details.get(METER_FORMAT_KEYS[state_key], "%.1f")},
                "device_class": device_class,
                "state_class": state_class,
                "config_entry": config_entry,
            }
        )
    return kwargs_list


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
    _hass: HomeAssistant,
    config: ConfigType,
    async_add_devices: AddEntitiesCallback,
    _discovery_info: DiscoveryInfoType | None = None,
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

    for sensor in iter_controls(hass, config_entry, ["TextInput", "InfoOnlyText"]):
        try:
            sensor.update({"config_entry": config_entry})
            entities.append(LoxoneTextSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping %s control %s", sensor.get("type", "TextInput"), sensor.get("name", "?"))

    # WP-6.5: the Meter family (Meter + EnergyManager/EnergyManager2/
    # PowerUnit/Wallbox) all expose the same register set, so one loop
    # over ``METER_FAMILY_TYPES`` and the pure ``meter_sub_sensor_kwargs``
    # helper creates every register.
    for sensor in iter_controls(hass, config_entry, list(METER_FAMILY_TYPES)):
        _LOGGER.debug("Found Meter-family control: %s", sensor.get("name"))
        try:
            for subsensor in meter_sub_sensor_kwargs(sensor, config_entry):
                entities.append(LoxoneMeterSensor(**subsensor))
        except Exception:
            # One bad control must not abort the whole sensor platform
            # (PS-08).
            _LOGGER.exception("Skipping %s control %s", sensor.get("type", "Meter"), sensor.get("name", "?"))

    # #461: PresenceDetector illuminance/noise sub-sensors.  The analog
    # sub-readings live on the sensor platform (a LoxoneSensor added via
    # the binary_sensor platform would be pinned to the *binary_sensor*
    # domain); they share the parent control's device identifiers, so
    # the device registry keeps them on the presence device.
    for sensor in iter_controls(hass, config_entry, "PresenceDetector"):
        try:
            for sub in presence_sub_sensor_kwargs(sensor, config_entry):
                entities.append(LoxoneSensor(**sub))
        except Exception:
            # One bad control must not abort the whole sensor platform
            # (PS-08).
            _LOGGER.exception("Skipping PresenceDetector control %s", sensor.get("name", "?"))

    # WP-6.6 / PS-26: Tracker controls report their entries as a JSON
    # list on the ``entries`` state (pattern: LoxoneClimateController).
    # recursive=True — real structure files also nest a Tracker under an
    # Alarm's subControls (its ``sensors`` state), not just top level.
    for sensor in iter_controls(hass, config_entry, "Tracker", recursive=True):
        try:
            sensor.update({"config_entry": config_entry})
            entities.append(LoxoneTrackerSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping Tracker control %s", sensor.get("name", "?"))

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
            device_info = device_info_for(
                config_entry, irc["uuidAction"], irc["name"], "RoomControllerV2", irc.get("room", "")
            )

            if "overrideReason" in states:
                entities.append(
                    LoxoneRoomControllerOverrideSensor(
                        # WP-5.1: short sub-entity name — the device is named
                        # after the room controller (CORE-26).
                        name="Override Reason",
                        uuid=states["overrideReason"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )

            if "comfortTemperature" in states:
                entities.append(
                    LoxoneRoomControllerTemperatureSensor(
                        name="Comfort Temperature",
                        uuid=states["comfortTemperature"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )

            if "comfortTemperatureCool" in states:
                entities.append(
                    LoxoneRoomControllerTemperatureSensor(
                        name="Comfort Temperature (Cool)",
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
        # Device-less (YAML) entry: the entity carries the control name
        # itself (there is no device to inherit it from); an unnamed
        # sensor falls back to the default name.
        name = kwargs.pop("name", None)
        self._attr_state_class = kwargs.pop("state_class", None)
        self._attr_device_class = kwargs.pop("device_class", None)
        self._attr_native_unit_of_measurement = kwargs.pop("unit_of_measurement", None)
        self._attr_native_value = None  # Initialize state
        # Must be after the kwargs.pop functions!
        super().__init__(**kwargs)
        self._attr_name = name or DEFAULT_NAME
        # CORE-26: the unique id is set as an attribute (not via the
        # deleted ``cached_property`` override).  A YAML sensor without a
        # name still gets a usable unique id (PS-07): the uuidAction
        # alone, or "uuidAction-name" when a name is given.  Done after
        # ``super()`` because the base constructor sets ``_attr_unique_id``
        # from the bare uuidAction.
        uuid = kwargs.get("uuidAction")
        self._attr_unique_id = f"{uuid}-{name}" if (uuid is not None and name) else uuid

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
        # CORE-26: the per-instance unique id replaces the deleted
        # ``cached_property`` override (same string, stored as an
        # ``_attr_unique_id`` attribute).
        self._attr_unique_id = f"{self._miniserver_serial}-loxone_keep_alive_sensor_uuid"
        # PS-20: attach to the Miniserver host device (identifiers
        # (DOMAIN, serial)); a structure file without a serial yields
        # ``device_info is None`` and a device-less entity (no
        # ``(DOMAIN, None)`` identifier).
        if device_info is not None:
            self._attr_device_info = device_info
        self._attr_native_value = None

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
        # CORE-26: the per-instance unique id replaces the deleted
        # ``cached_property`` override (same string, stored as an
        # ``_attr_unique_id`` attribute).
        self._attr_unique_id = f"{self._miniserver_serial}-loxone_software_version_uuid"
        # PS-20: ``software_version_string`` handles list-form *and*
        # string-form versions (the old join split the string into
        # characters); an unusable value stays ``None`` (HA renders
        # unknown) instead of the literal string "unknown".
        parsed = software_version_string(version)
        self._attr_native_value = parsed if parsed else None
        if device_info is not None:
            self._attr_device_info = device_info


class LoxoneTextSensor(LoxoneEntity, SensorEntity):
    """Representation of a Text Sensor."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # WP-6.6: writable TextInput vs read-only InfoOnlyText — both
        # report the ``text`` state, but an InfoOnlyText must accept no
        # write command (the control is an output).
        self.type = "InfoOnlyText" if kwargs.get("type") == "InfoOnlyText" else "TextInput"
        self._state = None
        self._state_uuid = self.states.get("text") or self.uuidAction
        # CORE-20 / device link: a fresh device built from the control's
        # own identity (previously no device at all).
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, kwargs.get("room", "")
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
        if self.type == "InfoOnlyText":
            # Read-only control: a write would raise on the Miniserver;
            # refuse it here instead.
            _LOGGER.warning("Ignoring write to read-only InfoOnlyText '%s'", self._lox_name)
            return
        self._send(f"{value}")
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


def tracker_entries(raw: Any) -> list[str] | None:
    """Normalise a Tracker control's raw ``entries`` stream value (WP-6.6).

    Intended semantics (VERIFY against a live Miniserver before this is
    assumed right): the stream pushes a JSON array of names/ids — e.g.
    the sensor names an Alarm's ``sensors`` tracker currently holds —
    delivered as an already-parsed list or as the JSON string of it.
    Returns the entries coerced to strings in order.  Returns ``None``
    for a missing, empty or unparseable payload so the caller keeps its
    last known list.  Non-scalar entries (lists/dicts) are dropped.
    """
    value: Any = raw
    if isinstance(value, str):
        text = value.strip()
        if not text.startswith("["):
            return None
        try:
            value = json.loads(text)
        except ValueError:
            return None
    if not isinstance(value, (list, tuple)):
        return None
    return [str(item) for item in value if isinstance(item, (str, int, float, bool))]


class LoxoneTrackerSensor(LoxoneEntity, SensorEntity):
    """Tracker control (WP-6.6, PS-26): its ``entries`` JSON list rendered
    as a comma-joined name summary, the entry list as extra attributes
    (pattern: ``LoxoneClimateController``'s JSON-list handling)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = "Tracker"
        states = kwargs.get("states")
        self._entries_uuid = (
            states.get("entries") if isinstance(states, dict) and isinstance(states.get("entries"), str) else None
        ) or self.uuidAction
        self._entries: list[str] = []
        self._attr_native_value: str | None = None
        # CORE-20: a fresh device built from the control's own identity.
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, kwargs.get("room", "")
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the ``entries`` stream (falls back to uuidAction).
        return frozenset({self._entries_uuid, self.uuidAction})

    @callback
    def event_handler(self, e):
        if self._entries_uuid in e:
            parsed = tracker_entries(e[self._entries_uuid])
            if parsed is None:
                # A malformed / absent payload keeps the last list.
                return
            self._entries = parsed
            # An empty tracker reads as unknown (HA rejects an empty
            # sensor state string).
            self._attr_native_value = ", ".join(self._entries) if self._entries else None
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "entries": list(self._entries),
            "count": len(self._entries),
            "state_uuid": self._entries_uuid,
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
        # WP-5.1: sub-sensors (Meter registers, Ventilation fan readings)
        # keep their short name; the device is named after the parent
        # control (CORE-26).
        if kwargs.get("parent_id"):
            self._attr_name = self._lox_name
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

        # Device class is detected from unit/category/name;
        # per-entity overrides remain possible via HA's entry-specific
        # customization (customizing the entity's device_class/type in
        # configuration.yaml still wins over the automatic match).
        desc = match_sensor_description(
            unit=self._attr_native_unit_of_measurement,
            name=self._lox_name,
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
            and not _metering_indicated(self._lox_name, kwargs.get("cat", ""))
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
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
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
    """A register (Actual/Total/Total Neg/Level) of a Meter-family
    control (WP-6.5).  Register construction and the shared device link
    live in the pure ``meter_sub_sensor_kwargs`` / ``meter_device_info``
    helpers; this class keeps the ``LoxoneSensor`` behaviour (per-register
    class overrides from the setup, analog state updates)."""


class LoxoneRoomControllerTemperatureSensor(SensorEntity):
    """Sensor for IRoomControllerV2 comfort temperature states."""

    _attr_has_entity_name = True
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

    _attr_has_entity_name = True
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

        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
        )

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
