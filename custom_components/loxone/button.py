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
from .intercom import INTERCOM_TYPES, intercom_device_info, intercom_output_kwargs
from .miniserver import get_miniserver_from_hass

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

    # WP-6.6 / PS-26: UpDownDigital is a virtual up/down rocker with no
    # states at all — each side gets its own button; a press sends the
    # ``UpOn`` / ``DownOn`` command.
    for button_entity in iter_controls(hass, config_entry, "UpDownDigital"):
        try:
            up = dict(button_entity, direction="up")
            down = dict(button_entity, direction="down")
            entities.append(LoxoneUpDownDigitalButton(**up))
            entities.append(LoxoneUpDownDigitalButton(**down))
        except Exception:
            _LOGGER.exception("Skipping UpDownDigital control %s", button_entity.get("name", "?"))

    # Intercom blocks: every output is a pushbutton (structure file v16,
    # pp. 91/93) -- a press sends ``pulse``; ``answer`` stops the bell.
    loxconfig = get_miniserver_from_hass(hass, config_entry).lox_config.json
    for intercom in iter_controls(hass, config_entry, list(INTERCOM_TYPES)):
        try:
            entities.extend(
                LoxoneIntercomOutputButton(**kwargs)
                for kwargs in intercom_output_kwargs(intercom, config_entry, loxconfig)
            )
            if intercom_device_info(intercom, config_entry) is not None:
                entities.append(LoxoneIntercomAnswerButton(config_entry=config_entry, **intercom))
        except Exception:
            _LOGGER.exception("Skipping %s control %s", intercom.get("type", "?"), intercom.get("name", "?"))

    async_add_entities(entities)


class LoxoneButton(LoxoneEntity, ButtonEntity):
    """
    Representation of a Loxone pushbutton.

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
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

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

    async def async_press(self, **_kwargs) -> None:
        """Press the button."""
        self._send("pulse")
        self.async_schedule_update_ha_state()

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


class LoxoneUpDownDigitalButton(LoxoneEntity, ButtonEntity):
    """
    One side of an ``UpDownDigital`` rocker (WP-6.6, PS-26).

    An UpDownDigital control is a virtual up/down input: it has *no* state
    streams, only the commands ``UpOn`` / ``UpOff`` / ``DownOn`` /
    ``DownOff`` (the rocker is inverted-matching: pushing one side sets the
    other off).  A press therefore sends that side's ``<Side>On`` command
    the entity has no state itself and tracks no press echo (there is none
    to track) — callers must not expect a state change either way.
    """

    _attr_should_poll = False

    def __init__(self, **kwargs):
        direction = kwargs.pop("direction", "up")
        super().__init__(**kwargs)
        self._direction = "up" if direction == "up" else "down"
        # Stable per-side unique id (the structure file's own sub-control
        # convention is ``"<parent>/<key>"``).
        self._attr_unique_id = f"{self.uuidAction}/{self._direction}"
        # WP-5.1: short sub-entity name — the device is named after the
        # rocker (CORE-26).
        self._attr_name = "Up" if self._direction == "up" else "Down"
        self._attr_icon = "mdi:arrow-up-bold" if self._direction == "up" else "mdi:arrow-down-bold"
        self._command = "UpOn" if self._direction == "up" else "DownOn"
        self.type = "UpDownDigital"
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the control has no state streams to subscribe to.
        return frozenset()

    async def async_press(self, **_kwargs) -> None:
        """Handle a press (sends ``UpOn`` / ``DownOn``)."""
        self._send(self._command)
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "command": self._command,
            "device_type": self.type,
        }


class LoxoneIntercomOutputButton(LoxoneButton):
    """
    One output of an ``Intercom`` / ``IntercomV2`` block (a door opener, a
    light, ...).

    The structure file types these outputs as ``Pushbutton``: a press sends
    ``pulse`` -- the output fires once, as the intercom's own key does.
    (Up to 1.0.0 they were switches whose turn-on sent ``on`` and held the
    output until a turn-off.)  The unique id is the output's
    ``uuidAction``, the name the output's own, and the entity sits on the
    intercom's device.  The ``active`` press echo is optional.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._parent_id = kwargs.get("parent_id")
        # WP-5.1: sub-entity of the intercom device -- its own short name.
        self._attr_name = self._lox_name
        self._attr_device_info = kwargs.get("device_info") or get_or_create_device(
            self.unique_id, self._lox_name, self.type, self.room
        )


class LoxoneIntercomAnswerButton(LoxoneEntity, ButtonEntity):
    """
    "Answer" on an intercom: sends ``answer``, which deactivates the bell
    (structure file v16, ``Intercom`` and ``IntercomV2`` commands).
    """

    _attr_should_poll = False
    _attr_name = "Answer"
    _attr_icon = "mdi:bell-off"

    def __init__(self, **kwargs):
        config_entry = kwargs.pop("config_entry", None)
        super().__init__(**kwargs)
        self._attr_unique_id = f"{self.uuidAction}/answer"
        self.type = "IntercomAnswer"
        self._attr_device_info = intercom_device_info(kwargs, config_entry)

    def _state_uuids(self) -> frozenset[str]:
        # A command-only entity: no state stream.
        return frozenset()

    async def async_press(self, **_kwargs) -> None:
        """Send ``answer`` to the intercom."""
        self._send("answer")

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {**self._attr_extra_state_attributes, "device_type": self.type}
