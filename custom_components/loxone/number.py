"""
Loxone Numbers

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .helpers import clean_unit, get_or_create_device, iter_controls
from .sensor import _is_numeric_format, match_sensor_description

_LOGGER = logging.getLogger(__name__)

# A Loxone Slider that ships no step defaults to integer-like steps.
DEFAULT_SLIDER_STEP = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    entities = []

    for number_entity in iter_controls(hass, config_entry, "Slider"):
        try:
            new_number = LoxoneNumber(**number_entity)
            entities.append(new_number)
        except Exception:
            # One bad control must not abort the whole number platform.
            _LOGGER.exception("Skipping Slider control %s", number_entity.get("name", "?"))

    async_add_entities(entities)


class LoxoneNumber(LoxoneEntity, NumberEntity):
    """Representation of a loxone number"""

    _attr_available = False
    _attr_native_value: float | None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the Loxone number."""
        self._icon = None
        self._assumed = False
        # PS-15: the native value starts as None (HA shows `unknown`), not
        # the STATE_UNKNOWN *string*.
        self._attr_native_value = None
        details = getattr(self, "details", None)
        details = details if isinstance(details, dict) else {}
        self._min_value = details.get("min")
        self._max_value = details.get("max")
        if self._min_value is None or self._max_value is None:
            # An entity without a (min, max) range cannot be set up at all.
            raise ValueError(f"Slider {getattr(self, 'name', '?')} has no min/max details")
        self._native_step = details.get("step", DEFAULT_SLIDER_STEP)

        # PS-15: listen on the *value* state (advertised in
        # extra_state_attributes), not on the action uuid.
        states = getattr(self, "states", None)
        states = states if isinstance(states, dict) else {}
        self._value_uuid = states.get("value") or self.uuidAction

        # Unit / device class / precision follow the same rules as the
        # analog sensors (PS-15: before, details["format"] was ignored).
        lox_format = details.get("format", "")
        numeric = _is_numeric_format(lox_format)
        if isinstance(lox_format, str) and lox_format:
            unit = clean_unit(lox_format) or None
            self._attr_native_unit_of_measurement = unit
            desc = match_sensor_description(
                unit=unit,
                name=self._lox_name or "",
                category=kwargs.get("cat", ""),
            )
            if desc is not None and numeric:
                self.entity_description = desc
            precision = self._parse_digits_after_decimal(lox_format)
            if precision is not None:
                self._attr_suggested_display_precision = precision

        self.type = "Slider"
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    @staticmethod
    def _parse_digits_after_decimal(format_string):
        if not isinstance(format_string, str):
            return None
        if index := format_string.find("."):
            rest = format_string[index + 1 :]
            if rest[:1].isdigit():
                end = 0
                while end < len(rest) and rest[end].isdigit():
                    end += 1
                return int(rest[:end])
        return None

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    @property
    def native_max_value(self):
        """Return the native_max_value to use for device if any."""
        return self._max_value

    @property
    def native_min_value(self):
        """Return the native_min_value to use for device if any."""
        return self._min_value

    @property
    def native_step(self):
        """Return the native_step to use for device if any."""
        return self._native_step

    @property
    def assumed_state(self):
        """Return if the state is based on assumptions."""
        return self._assumed

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the value stream the handler reads (plus the action
        # stream it writes to, which may differ).
        return frozenset({self._value_uuid, self.uuidAction})

    @callback
    def event_handler(self, e):
        if self._value_uuid in e:
            data = e[self._value_uuid]
            if isinstance(data, (int, float)) and not isinstance(data, bool):
                self._attr_native_value = data
                self._attr_available = True
            else:
                # Non-numeric garbage on a number stream: keep the last
                # valid value instead of publishing it as a native value.
                _LOGGER.debug("Ignoring non-numeric Slider value %r", data)
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """
        Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            **self._attr_extra_state_attributes,
            "state_uuid": self._value_uuid,
            "device_type": self.type,
            "platform": "loxone",
        }

    async def async_set_native_value(self, value: float):
        """Set new value."""
        self._send(f"{value}")
        self.async_schedule_update_ha_state()
