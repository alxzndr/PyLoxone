"""
Loxone Switches

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .helpers import add_room_and_cat_to_value_values, device_info_for, get_or_create_device, iter_controls
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

    for switch_entity in iter_controls(
        hass, config_entry, ["Switch", "TimedSwitch", "Intercom", "IRoomControllerV2", "LightControllerV2"]
    ):
        try:
            if switch_entity["type"] in ["Switch"]:
                new_switch = LoxoneSwitch(**switch_entity)
                entities.append(new_switch)

            elif switch_entity["type"] == "TimedSwitch":
                new_switch = LoxoneTimedSwitch(**switch_entity)
                entities.append(new_switch)

            elif switch_entity["type"] == "Intercom":
                for sub_name in switch_entity.get("subControls", {}) or {}:
                    subcontrol = switch_entity["subControls"][sub_name]
                    subcontrol = add_room_and_cat_to_value_values(loxconfig, subcontrol)
                    # WP-5.1: the sub-control keeps its *own* name — the
                    # device is named after the Intercom (its payload is
                    # passed alongside), so prefixing the master name
                    # would duplicate it in the UI (has_entity_name).
                    # PS-05: a sub-control without an `active` state cannot
                    # report at all -- skip it instead of raising on every event.
                    if not subcontrol.get("states", {}).get("active"):
                        _LOGGER.warning(
                            "Skipping Intercom sub-control %s: no 'active' state",
                            subcontrol.get("name", sub_name),
                        )
                        continue

                    subcontrol["parent_id"] = switch_entity.get("uuidAction")
                    subcontrol["device_info"] = device_info_for(
                        config_entry,
                        switch_entity.get("uuidAction"),
                        switch_entity.get("name"),
                        "Intercom",
                        switch_entity.get("room", ""),
                    )
                    new_switch = LoxoneIntercomSubControl(**subcontrol)
                    entities.append(new_switch)
            elif switch_entity["type"] == "IRoomControllerV2":
                states = switch_entity.get("states", {})
                if "overrideEntries" in states:
                    override_kwargs = {**switch_entity, "type": "RoomControllerOverride"}
                    entities.append(LoxoneRoomControllerOverride(**override_kwargs))
            elif switch_entity["type"] == "LightControllerV2":
                # PS-06: the guard and the constructor must resolve the same
                # key: the presence state uuid lives in `states` (reading the
                # top-level key was either dead code or a KeyError that
                # aborted the whole platform).
                if "presence" in switch_entity.get("states", {}):
                    override_kwargs = {**switch_entity, "type": "PresenceDetectionSwitch"}
                    entities.append(LoxoneLightPresenceSwitch(**override_kwargs))
        except Exception:
            # One bad control must not abort the whole switch platform.
            _LOGGER.exception("Skipping %s control %s", switch_entity.get("type", "?"), switch_entity.get("name", "?"))
    async_add_entities(entities)


class LoxoneTimedSwitch(LoxoneEntity, SwitchEntity):
    """Representation of a loxone switch"""

    _attr_available = False
    _attr_is_on: bool | None = None
    _attr_state: None = None
    _attr_assumed_state: None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_is_on = None
        self._icon = None
        self._delay_remain = 0.0
        self._delay_time_total = 0.0

        if "deactivationDelay" in self.states:
            self._deactivation_delay = self.states["deactivationDelay"]
        else:
            self._deactivation_delay = ""

        if "deactivationDelayTotal" in self.states:
            self._deactivation_delay_total = self.states["deactivationDelayTotal"]
        else:
            self._deactivation_delay_total = ""

        self.type = "TimeSwitch"
        # WP-5.1: primary entity — the device (named after the control)
        # provides the display name; nothing to store in ``_attr_name``.
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    def turn_on(self, **_kwargs):
        """Turn the switch on."""
        self._do_turn_on()

    async def async_turn_on(self, **_kwargs):
        """HA's switch domain dispatches the service to ``async_turn_on``
        only; running in the event loop keeps the outbound send's
        background task valid."""
        self._do_turn_on()

    def _do_turn_on(self):
        if not self._attr_is_on:
            self._send("pulse")
            self._attr_is_on = True
            self.schedule_update_ha_state()

    def turn_off(self, **_kwargs):
        """Turn the device off."""
        self._do_turn_off()

    async def async_turn_off(self, **_kwargs):
        self._do_turn_off()

    def _do_turn_off(self):
        self._send("off")
        self._attr_is_on = False
        self.schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the two delay streams the handler reacts to.
        return frozenset(
            uuid
            for uuid in (self._deactivation_delay, self._deactivation_delay_total)
            if isinstance(uuid, str) and uuid
        )

    @callback
    def event_handler(self, e):
        """Handle timed-switch events and update state attributes."""
        should_update = False

        # If we're currently unavailable but incoming data contains relevant keys,
        # schedule an async update immediately (preserves original behavior).
        if not self._attr_available and (self._deactivation_delay in e or self._deactivation_delay_total in e):
            self.async_write_ha_state()

        if self._deactivation_delay in e:
            # Preserve original comparison to 0.0
            self._attr_is_on = False if e[self._deactivation_delay] == 0.0 else True
            self._delay_remain = int(e[self._deactivation_delay])
            should_update = True

        if self._deactivation_delay_total in e:
            self._delay_time_total = int(e[self._deactivation_delay_total])
            should_update = True

        if should_update:
            # Make entity available if it wasn't and schedule a final update
            if not self._attr_available:
                self._attr_available = True
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        state_dict = {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }

        if self._attr_is_on == False:
            state_dict.update({"delay_time_total": str(self._delay_time_total)})

        else:
            state_dict.update(
                {
                    "delay": str(self._delay_remain),
                    "delay_time_total": str(self._delay_time_total),
                }
            )
        return state_dict


class LoxoneSwitch(LoxoneEntity, SwitchEntity):
    """Representation of a loxone switch"""

    _attr_available = False
    _attr_is_on: bool | None = None
    _attr_state: None = None
    _attr_assumed_state: None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_is_on = None

        """Initialize the Loxone switch."""
        self._icon = None
        self._assumed = False

        self.type = "Switch"
        # WP-5.1: primary entity — the device (named after the control)
        # provides the display name; nothing to store in ``_attr_name``.
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    def turn_on(self, **_kwargs):
        """Turn the switch on."""
        self._do_turn_on()

    async def async_turn_on(self, **_kwargs):
        """HA's switch domain dispatches the service to ``async_turn_on``
        only; running in the event loop keeps the outbound send's
        background task valid."""
        self._do_turn_on()

    def _do_turn_on(self):
        if not self._attr_is_on:
            self._send("On")
            self._attr_is_on = True
            self.schedule_update_ha_state()

    def turn_off(self, **_kwargs):
        """Turn the device off."""
        self._do_turn_off()

    async def async_turn_off(self, **_kwargs):
        self._do_turn_off()

    def _do_turn_off(self):
        if self._attr_is_on:
            self._send("Off")
            self._attr_is_on = False
            self.schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the action stream plus the optional `active` state
        # stream the handler's availability flip reacts to.
        state_uuid = self.states.get("active")
        return frozenset(uuid for uuid in (self.uuidAction, state_uuid) if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, event):
        # PS-05: resolve the state uuid once via .get(); a control that
        # happens to lack `active` must not raise on every event.
        state_uuid = self.states.get("active")
        if self.uuidAction in event or (state_uuid and state_uuid in event):
            if not self._attr_available:
                self.async_write_ha_state()
            if state_uuid and state_uuid in event:
                self._attr_is_on = event[state_uuid]

            if not self._attr_available:
                self._attr_available = True
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            "uuid": self.uuidAction,
            # PS-05: .get() -- a missing `active` state is not an error.
            "state_uuid": self.states.get("active"),
            "room": self.room,
            "category": self.cat,
            "device_type": self.type,
            "platform": "loxone",
        }


class LoxoneIntercomSubControl(LoxoneSwitch):
    def __init__(self, **kwargs):
        LoxoneSwitch.__init__(self, **kwargs)

        self.type = "IntercomSubControl"
        # WP-5.1: sub-entity of the Intercom device — carries only its
        # own (short) name and the master's device payload (CORE-26).
        self._parent_id = kwargs.get("parent_id")
        self._attr_name = self._lox_name
        self._attr_device_info = kwargs.get("device_info") or get_or_create_device(
            self.unique_id, self._lox_name, self.type, self.room
        )

    def turn_on(self, **_kwargs):
        """Turn the switch on."""
        self._do_turn_on()

    async def async_turn_on(self, **_kwargs):
        """Switch-domain service dispatch (event loop)."""
        self._do_turn_on()

    def _do_turn_on(self):
        if not self._attr_is_on:
            self._send("on")
            self._attr_is_on = True
            self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            "uuid": self.uuidAction,
            "state_uuid": self.states["active"],
            "room": self.room,
            "category": self.cat,
            "device_type": self.type,
            "platform": "loxone",
        }


class LoxoneRoomControllerOverride(LoxoneEntity, SwitchEntity):
    """Switch to trigger/stop comfort override on IRoomControllerV2."""

    _attr_available = True
    _attr_is_on = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._override_uuid = self.states.get("overrideEntries")
        # WP-5.1: sub-entity short name — the device is named after the
        # room controller, so only the suffix is stored (CORE-26).
        self._attr_name = "Comfort Override"
        self.type = "RoomControllerOverride"

        # Use uuidAction (not unique_id) so this groups with the climate entity
        self._attr_device_info = get_or_create_device(self.uuidAction, self._lox_name, "RoomControllerV2", self.room)
        self._attr_unique_id = f"{self.uuidAction}_override"

    def turn_on(self, **_kwargs):
        """Trigger comfort override (mode 1)."""
        self._do_turn_on()

    async def async_turn_on(self, **_kwargs):
        """Switch-domain service dispatch (event loop)."""
        self._do_turn_on()

    def _do_turn_on(self):
        self._send("override/1")
        self._attr_is_on = True
        self.schedule_update_ha_state()

    def turn_off(self, **_kwargs):
        """Stop the active override."""
        self._do_turn_off()

    async def async_turn_off(self, **_kwargs):
        """Switch-domain service dispatch (event loop)."""
        self._do_turn_off()

    def _do_turn_off(self):
        self._send("stopOverride")
        self._attr_is_on = False
        self.schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the overrideEntries stream the handler reacts to.
        return (
            frozenset({self._override_uuid})
            if isinstance(self._override_uuid, str) and self._override_uuid
            else frozenset()
        )

    @callback
    def event_handler(self, e):
        if self._override_uuid and self._override_uuid in e:
            raw = e[self._override_uuid]
            try:
                entries = json.loads(raw) if isinstance(raw, str) else raw
                self._attr_is_on = isinstance(entries, list) and len(entries) > 0
            except json.JSONDecodeError, TypeError:
                self._attr_is_on = False
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            "uuid": self.uuidAction,
            "room": self.room,
            "device_type": self.type,
            "platform": "loxone",
        }


class LoxoneLightPresenceSwitch(LoxoneSwitch):
    """Representation of a light controller presence detection switch."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, **kwargs):
        # PS-06: same key as the setup guard (states["presence"]), and a
        # .get() so a missing state skips via the setup's try/except instead
        # of a KeyError here.
        self._presence_id = kwargs.get("states", {}).get("presence")
        super().__init__(**kwargs)
        # WP-5.1: sub-entity short name — the device is named after the
        # light controller, so only the suffix is stored (CORE-26).
        self._attr_device_info = get_or_create_device(self.uuidAction, self._lox_name, "LightControllerV2", self.room)
        self._attr_name = "Presence Detection"
        self._attr_unique_id = self._presence_id

    def async_turn_on(self, **_kwargs: Any) -> None:
        self._send("on", uuid=self.uuidAction + "/presence")
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self._send("off", uuid=self.uuidAction + "/presence")
        self.async_schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the presence stream the (inherited) handler reacts to.
        return (
            frozenset({self._presence_id}) if isinstance(self._presence_id, str) and self._presence_id else frozenset()
        )

    @callback
    def event_handler(self, event):
        request_update = False
        if self._presence_id in event:
            active = event[self._presence_id]
            new_state = True if int(active) & 2 else False
            if new_state != self._attr_is_on:
                self._attr_is_on = new_state
                request_update = True
            if not self._attr_available:
                self._attr_available = True

        if request_update:
            self.async_write_ha_state()
