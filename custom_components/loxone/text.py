"""
Loxone Texts

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []

    for text_entity in get_all(loxconfig, ["TextInput"]):
        text_entity = add_room_and_cat_to_value_values(loxconfig, text_entity)
        text_entity.update(
            {
                "config_entry": config_entry,
            }
        )
        new_text = LoxoneText(**text_entity)
        entities.append(new_text)

    async_add_entities(entities)


class LoxoneText(LoxoneEntity, TextEntity):
    """Representation of a loxone text"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the Loxone text."""
        self._state = STATE_UNKNOWN
        self._icon = None
        self._assumed = False
        self._native_value = None

        self.type = "TextInput"
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    @property
    def native_value(self):
        """Return the native_min_value to use for device if any."""
        return self._native_value

    @property
    def assumed_state(self):
        """Return if the state is based on assumptions."""
        return self._assumed

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the action stream the handler reads (TextInput controls
        # publish their value on ``uuidAction``).
        return frozenset({self.uuidAction}) if isinstance(self.uuidAction, str) and self.uuidAction else frozenset()

    @callback
    def event_handler(self, e):
        if self.uuidAction in e:
            data = e[self.uuidAction]
            if isinstance(data, (list, dict)):
                data = str(data)
            if isinstance(data, str):
                self._native_value = data[:255]
            else:
                self._native_value = data
            self._state = data

            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            **self._attr_extra_state_attributes,
            "state_uuid": self.states["text"],
            "device_type": self.type,
        }

    async def async_set_value(self, value: str):
        """Set new value."""
        self._send(f"{value}")
        self.async_schedule_update_ha_state()
