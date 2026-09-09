"""Support for Loxone binary sensors."""

from __future__ import annotations

import logging
from typing import Literal, final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import DEVICE_TYPE_BINARY_SENSOR
from .helpers import device_info_for, iter_controls

_LOGGER = logging.getLogger(__name__)
DEFAULT_NAME = "Loxone Binary Sensor"

LOXONE_DEVICE_CLASS_MAP: dict[str, BinarySensorDeviceClass] = {
    "presence": BinarySensorDeviceClass.PRESENCE,
    "smoke": BinarySensorDeviceClass.SMOKE,
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_devices: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Sensor from yaml"""
    # Devices from yaml
    if config != {}:
        # Here setup all Sensors in Yaml-File
        new_sensor = LoxoneCustomBinarySensor(**config)
        async_add_devices([new_sensor])
        return True
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    entities = []

    for sensor in iter_controls(hass, config_entry, "InfoOnlyDigital"):
        try:
            sensor.update({"type": "digital", "config_entry": config_entry})
            entities.append(LoxoneDigitalSensor(**sensor))
        except Exception:
            # One bad control must not abort the whole binary_sensor platform
            # (PS-04).
            _LOGGER.exception("Skipping InfoOnlyDigital control %s", sensor.get("name", "?"))

    for sensor in iter_controls(hass, config_entry, "PresenceDetector"):
        try:
            sensor.update({"type": "presence", "config_entry": config_entry})
            entities.append(LoxoneDigitalSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping PresenceDetector control %s", sensor.get("name", "?"))

    for sensor in iter_controls(hass, config_entry, "SmokeAlarm"):
        try:
            sensor.update({"type": "smoke", "config_entry": config_entry})
            entities.append(LoxoneDigitalSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping SmokeAlarm control %s", sensor.get("name", "?"))

    # CORE-17: the old code connected each platform to an
    # ``async_signal_new_device`` dispatcher signal that was never sent
    # (``async_dispatcher_send`` had no callers) and appended the
    # unsubscribes to ``MiniServer.listeners``, which was never iterated,
    # so the subscriptions leaked on every reload.  Entities are created
    # exclusively from the structure file; there is nothing to subscribe to.
    async_add_entities(entities)


class LoxoneDigitalSensor(LoxoneEntity, BinarySensorEntity):
    """Representation of a binary Loxone device."""

    _attr_is_on: bool | None = None
    _attr_state: None = None
    _attr_available = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_state = STATE_UNKNOWN
        self._attr_is_on = STATE_UNKNOWN
        self._from_loxone_config = False

        if "type" in kwargs and "room" in kwargs and "cat" in kwargs and hasattr(self, "states"):
            self._from_loxone_config = True
            # PS-04: the elif-chain matters: a SmokeAlarm is read from its
            # smoke *level* (any level > 0 is a smoke event); `areAlarmSignalsOff`
            # tells whether the beeper is muted, which is a different signal.
            # Same for presence; a digital control without an `active` state
            # falls back to the echoed uuidAction. All lookups are `.get()`
            # so a missing state cannot abort the platform.
            if self.type == "smoke":
                self._state_uuid = self.states.get("level") or self.uuidAction
            elif self.type == "presence":
                self._state_uuid = self.states.get("active") or self.uuidAction
            elif "active" in self.states:
                self._state_uuid = self.states.get("active") or self.uuidAction
            else:
                self._state_uuid = self.uuidAction
        else:
            self._state_uuid = self.uuidAction

        self._state = STATE_UNKNOWN
        self._format = self._get_format(kwargs.get("details", {}).get("format", ""))
        self._parent_id = kwargs.get("parent_id", None)
        self._on_state = STATE_ON
        self._off_state = STATE_OFF
        self._attr_available = True
        if self.type in LOXONE_DEVICE_CLASS_MAP:
            self._attr_device_class = LOXONE_DEVICE_CLASS_MAP[self.type]
        else:
            self._attr_device_class = None

        # PC-04: ``parent_id`` only marks "sub-sensor of another control";
        # the device linkage happens via the parent's ``device_info``
        # (passed as kwarg).  The old code overwrote ``self.uuidAction``
        # with the parent id here, so ``unique_id`` — defined as
        # ``self.uuidAction`` — collided with the parent's and the shared
        # device dict (CORE-20) was seeded with the sub-sensor's
        # name/model (upstream PR #513).  The state uuid stays the
        # unique id.
        if self._from_loxone_config:
            # CORE-20: a fresh ``_attr_device_info`` per entity; a
            # sub-sensor inherits the *parent* control's device, a
            # standalone control builds its own.
            parent_device_info = kwargs.get("device_info")
            self._attr_device_info = parent_device_info or device_info_for(
                kwargs.get("config_entry"),
                self.unique_id,
                self.name,
                self.type,
                self.room,
            )
            # PS-14: the group table matches the single "digital_sensor"
            # constant; the individual Loxone subtype (digital/presence/
            # smoke) lives in the extra attribute and in device_class.
            self._attr_extra_state_attributes.update(
                {
                    "state_uuid": self._state_uuid,
                    "device_type": DEVICE_TYPE_BINARY_SENSOR,
                    "device_type_sub": self.type,
                }
            )
        else:
            self._attr_extra_state_attributes.update(
                {
                    "device_type": DEVICE_TYPE_BINARY_SENSOR,
                }
            )

    def _state_uuids(self) -> frozenset[str]:
        """CORE-27: the state stream(s) this entity reacts to.

        (``_state_uuid`` was computed in ``__init__`` for the
        PS-04 read semantics; it doubles as the subscription set.)
        """
        uuid = getattr(self, "_state_uuid", None)
        return frozenset({uuid}) if isinstance(uuid, str) and uuid else frozenset()

    @callback
    def event_handler(self, e):
        if self._state_uuid in e:
            value = e[self._state_uuid]
            # PS-04: react to the *level*/active value itself, not the
            # stale two-valued comparison (a sub-normal non-1.0 value such as
            # a fraction or a missing sentinel used to read as "off").
            if isinstance(value, bool):
                on = value
            elif isinstance(value, (int, float)):
                on = value > 0
            elif isinstance(value, str) and value.strip() != "":
                on = value.strip().lower() != "off"
            else:
                on = False
            self._state = self._on_state if on else self._off_state
            if not self._attr_available:
                self._attr_available = True
            self.async_write_ha_state()

    @final
    @property
    def state(self) -> Literal["on", "off"] | None:
        """Return the state of the binary sensor."""
        if (is_on := self.is_on) is None:
            return None
        return STATE_ON if is_on else STATE_OFF

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        return self._state == self._on_state


class LoxoneCustomBinarySensor(LoxoneEntity, BinarySensorEntity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name = kwargs["name"]
        self._state = STATE_UNKNOWN
        self._on_state = STATE_ON
        self._off_state = STATE_OFF

        if "uuidAction" in kwargs:
            self.uuidAction = kwargs["uuidAction"]
        else:
            self.uuidAction = ""

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the action stream the handler reads (may be empty, in
        # which case the entity never updates from the wire).
        return frozenset({self.uuidAction}) if isinstance(self.uuidAction, str) and self.uuidAction else frozenset()

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        return self._state == self._on_state

    @property
    def state(self) -> Literal["on", "off"] | None:
        """Return the state of the binary sensor."""
        if (is_on := self.is_on) is None:
            return None
        return STATE_ON if is_on else STATE_OFF

    @callback
    def event_handler(self, e):
        if self.uuidAction in e:
            data = e[self.uuidAction]
            if data == 1.0:
                self._state = self._on_state
            else:
                self._state = self._off_state
            self.async_write_ha_state()

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name
