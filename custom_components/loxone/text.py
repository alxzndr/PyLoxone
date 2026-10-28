"""
Loxone Texts

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

# HA rejects a text state longer than the entity's max (default 255).
MAX_TEXT_LENGTH = 255


def text_input_command(value: str) -> str:
    """
    The wire payload for a ``TextInput`` command (WP-7.3, upstream 6911e14;
    relaxed by the WP-7.8 review).

    The spec command is the new text itself, sent as one path segment of
    ``jdev/sps/io/<uuid>/<value>`` (structure file v16, TextInput:
    "Commands: {text}").  Only what breaks that path is percent-encoded —
    ``%`` (first, so an existing ``%2F`` cannot pass for an escape), ``/``,
    ``?``, ``#``; everything else goes out raw, including non-ASCII text
    ("Küche 21°C"), which went out raw before WP-7.3.  Not ``quote()``: it
    always encodes non-ASCII characters, whatever ``safe`` says.  WP-7.3 encoded the whole value, which
    also encoded spaces, ``(``, ``)``, ``;``, ``,`` and ``'`` — but the
    structure file says ``{uriEncodedReason}`` *explicitly* where it wants
    encoding (Alarm, Wallbox names) and TextInput's command is plain
    ``{text}``, upstream ``6911e14`` does not encode, and the README
    recipe sends ``SET(Ap;Fav;4)`` raw through
    ``loxone.event_websocket_command`` on real hardware.  Pending live
    check ``docs/review/LIVE-MINISERVER-CHECKS.md`` #31.
    """
    return value.replace("%", "%25").replace("/", "%2F").replace("?", "%3F").replace("#", "%23")


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
        self._icon = None
        self._assumed = False
        # Unknown until the Miniserver sends the "text" state (PS-09,
        # upstream 6911e14).
        self._attr_native_value = None

        self.type = "TextInput"
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)
        # The block's maxLength detail bounds what the VI accepts; HA's
        # text platform refuses longer values before they hit the wire.
        # Only set when known: Home Assistant's ``max`` is
        # ``min(native_max, 255)`` and ``None`` breaks it.
        max_length = self._max_length(kwargs.get("details"))
        if max_length is not None:
            self._attr_native_max = max_length

    @staticmethod
    def _max_length(details) -> int | None:
        """``native_max`` from a positive-integer ``maxLength`` detail, capped at 255."""
        max_length = details.get("maxLength") if isinstance(details, dict) else None
        if isinstance(max_length, int) and not isinstance(max_length, bool) and max_length > 0:
            return min(max_length, MAX_TEXT_LENGTH)
        return None

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    @property
    def assumed_state(self):
        """Return if the state is based on assumptions."""
        return self._assumed

    def _value_uuid(self) -> str | None:
        """
        The stream that carries the block's text value.

        PS-03 / upstream 6911e14: the value arrives as the ``text`` state,
        which has its own uuid — not under the control's ``uuidAction``
        (the WP-3.2 comment claiming otherwise was wrong; the structure
        file lists ``text`` as the only TextInput state, and no state is
        published under a control's own ``uuidAction``).  Only a TextInput
        without a ``text`` state falls back to ``uuidAction``.
        """
        states = getattr(self, "states", None)
        uuid = states.get("text") if isinstance(states, dict) else None
        if isinstance(uuid, str) and uuid:
            return uuid
        return self.uuidAction if isinstance(self.uuidAction, str) and self.uuidAction else None

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the action stream the handler reads.
        uuid = self._value_uuid()
        return frozenset({uuid}) if uuid else frozenset()

    @callback
    def event_handler(self, e):
        uuid = self._value_uuid()
        if uuid is not None and uuid in e:
            value = e[uuid]
            if not isinstance(value, str):
                value = str(value)
            # A state longer than the entity's ``max`` (``native_max``,
            # here the block's maxLength, else 255) makes HA's
            # ``TextEntity.state`` raise ValueError and the entity goes
            # stale — cut at the entity's own max, not at 255 (WP-7.8).
            self._attr_native_value = value[: self.max]
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """
        Return device specific state attributes.

        Implemented by platform classes.
        """
        states = getattr(self, "states", None)
        return {
            **self._attr_extra_state_attributes,
            "state_uuid": states.get("text") if isinstance(states, dict) else None,
            "device_type": self.type,
        }

    async def async_set_value(self, value: str):
        """Set new value."""
        self._send(text_input_command(f"{value}"))
        self.async_schedule_update_ha_state()
