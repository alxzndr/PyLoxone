"""
Loxone Buttons

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LoxoneEntity
from .helpers import get_or_create_device, iter_controls

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    entities = []

    for button_entity in iter_controls(hass, config_entry, "Pushbutton"):
        try:
            entities.append(LoxoneButton(**button_entity))
        except Exception:
            # One bad control must not abort the whole button platform.
            _LOGGER.exception("Skipping Pushbutton control %s", button_entity.get("name", "?"))

    async_add_entities(entities)


class LoxoneButton(LoxoneEntity, ButtonEntity):
    """Representation of a Loxone pushbutton.

    A press is an event, not state: the entity never carries a state of its
    own, so it must not override the ``ButtonEntity.state`` property (which
    is ``@final`` and hides the last-press echo) (PS-16). The last press is
    exposed as a ``last_pressed`` attribute instead.
    """

    _attr_should_poll = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_icon = None
        self._attr_unique_id = self.uuidAction
        self._press_uuid = (
            getattr(self, "states", {}).get("active") if isinstance(getattr(self, "states", None), dict) else None
        )
        self._attr_state = None
        self._state_value = None
        self._last_pressed = None

        self.type = "Pushbutton"
        # LoxoneEntity builds the device_info from the platform defaults in
        # abstract, so mirror the other platforms here (PS-16: the bespoke
        # DeviceInfo in button.py diverged from them).
        self._attr_device_info = get_or_create_device(self.unique_id, self.name, self.type, self.room)

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._attr_icon

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the press echo (``active``) stream.
        return frozenset({self._press_uuid}) if isinstance(self._press_uuid, str) and self._press_uuid else frozenset()

    @callback
    def event_handler(self, event):
        """Record the Miniserver's press echo (``active`` stream)."""
        if not self._press_uuid or self._press_uuid not in event:
            return
        active = event[self._press_uuid]
        new_state = True if active == 1.0 else False
        if new_state != self._attr_state:
            self._attr_state = new_state
            self._state_value = active
            if new_state:
                self._last_pressed = dt_util.utcnow().isoformat()
            self.async_write_ha_state()

    def press(self, **kwargs):
        """Press the button."""
        self._send("pulse")
        self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "state_uuid": self._press_uuid,
            "new_state": self._attr_state,
            "state_value": self._state_value,
            "last_pressed": self._last_pressed,
            "device_type": self.type,
        }
