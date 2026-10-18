"""
Loxone Events

The doorbell of an ``Intercom`` / ``IntercomV2`` block as an event entity.

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .helpers import iter_controls
from .intercom import INTERCOM_TYPES, intercom_device_info, intercom_state_uuid

_LOGGER = logging.getLogger(__name__)

EVENT_RING = "ring"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    entities = []
    for intercom in iter_controls(hass, config_entry, list(INTERCOM_TYPES)):
        try:
            if intercom_state_uuid(intercom, "bell") and intercom_device_info(intercom, config_entry) is not None:
                entities.append(LoxoneIntercomDoorbell(config_entry=config_entry, **intercom))
        except Exception:
            # One bad control must not abort the whole event platform.
            _LOGGER.exception("Skipping %s control %s", intercom.get("type", "?"), intercom.get("name", "?"))
    async_add_entities(entities)


def _ringing(value) -> bool | None:
    """Decode the ``bell`` state (0 = not ringing, 1 = ringing); ``None`` if unreadable."""
    try:
        return float(value) >= 0.5
    except (TypeError, ValueError):
        return None


class LoxoneIntercomDoorbell(LoxoneEntity, EventEntity):
    """
    The intercom's doorbell: a ``ring`` event each time ``bell`` rises 0 -> 1.

    Only an *observed* rising edge is a ring.  The first ``bell`` value of
    every Miniserver session -- after Home Assistant starts, after an entry
    reload and after each reconnect, when the Miniserver replays all its
    current states -- is a baseline and never fires: a replayed ``1`` may be
    a ring that is already being handled (or a bell stuck at 1), and a
    doorbell automation must not run for a value it did not see change.
    A ring that starts and stops during a connection outage is lost, as the
    outage itself is reported (the entity is unavailable meanwhile).
    """

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_RING]
    _attr_name = "Doorbell"

    def __init__(self, **kwargs):
        config_entry = kwargs.pop("config_entry", None)
        super().__init__(**kwargs)
        self._bell_uuid = intercom_state_uuid(kwargs, "bell")
        self._attr_unique_id = f"{self.uuidAction}/bell"
        self.type = "IntercomDoorbell"
        self._attr_device_info = intercom_device_info(kwargs, config_entry)
        # Last decoded ``bell`` value of the current session; ``None`` until
        # the session's first value (the baseline) arrives.
        self._ringing: bool | None = None

    def _state_uuids(self) -> frozenset[str]:
        return frozenset({self._bell_uuid}) if self._bell_uuid else frozenset()

    @callback
    def _on_connection_state(self, connected: bool) -> None:
        if not connected:
            # The next session replays ``bell``: that value is a new baseline.
            self._ringing = None
        super()._on_connection_state(connected)

    @callback
    def event_handler(self, e) -> None:
        if not self._bell_uuid or self._bell_uuid not in e:
            return
        ringing = _ringing(e[self._bell_uuid])
        if ringing is None:
            return
        previous, self._ringing = self._ringing, ringing
        if ringing and previous is False:
            self._trigger_event(EVENT_RING)
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {**self._attr_extra_state_attributes, "device_type": self.type}
