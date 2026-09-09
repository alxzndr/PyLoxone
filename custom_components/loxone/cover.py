"""
Loxone Cover

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging
import random
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import (
    SENDDOMAIN,
    SERVICE_DISABLE_SUN_AUTOMATION,
    SERVICE_ENABLE_SUN_AUTOMATION,
    SERVICE_QUICK_SHADE,
    SUPPORT_QUICK_SHADE,
    SUPPORT_SUN_AUTOMATION,
)
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device, map_range
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

NEW_COVERS = "covers"

# Loxone ignores a `manualLamelle/<position>` command whose value equals the
# one currently in effect; a small random sub-percent delta (at most 0.9%,
# so the command still means "open"/"closed") forces the server to apply the
# command and echo the state back (PC-19).
_LAMELLE_JITTER_MIN = 0.000000001
_LAMELLE_JITTER_MAX = 0.009


def _state_uuid(states, name):
    """The uuid the structure file registers for `states[name]`, or None.

    Several Loxone control types carry optional states (a Window without
    `targetPosition`, a Jalousie without `shadePosition`, ...); using `.get()`
    here is what keeps the event handlers from raising KeyError (PC-16).
    """
    return states.get(name)


def _lamelle_command(base_position):
    """Build a `manualLamelle/<position>` command with an explicit fixed
    decimal format (3 digits — the tint is coarse) plus the sub-percent
    jitter that keeps Loxone from discarding the command (PC-19)."""
    position = base_position + random.uniform(_LAMELLE_JITTER_MIN, _LAMELLE_JITTER_MAX)
    return f"manualLamelle/{position:.3f}"


def gate_stop_command():
    """Command sent when a Gate `stop_cover` is requested (PC-07, VERIFY).

    The pre-fix code re-sent the *opposite* direction, which is certainly a
    direction reversal, not a stop. The intended semantics — and what
    `LoxoneJalousie.stop_cover` already does — is a real `stop`; Loxone gate
    controls expose it. A live-Miniserver check before merge is required to
    confirm the gate honours `stop`.
    """
    return "stop"


def gate_device_class(animation):
    """Map the Loxone `animation` detail to a HA device class (PC-15/PC-41)."""
    if animation == 0:
        return CoverDeviceClass.GARAGE
    if animation in (1, 2, 3):
        return CoverDeviceClass.GATE
    if animation in (4, 5):
        return CoverDeviceClass.DOOR
    return None


def jalousie_device_class(animation):
    """Map the Loxone `animation` detail to a HA device class (PC-15/PC-41)."""
    if animation == 0:
        return CoverDeviceClass.BLIND
    if animation in (1, 3):
        # 3 = Schlotterer Retrolux, not supported in newer firmware
        return CoverDeviceClass.SHUTTER
    if animation in (2, 4, 5):
        return CoverDeviceClass.CURTAIN
    if animation == 6:
        return CoverDeviceClass.AWNING
    return None


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> bool:
    """Set up the Loxone covers.

    The platform is entry-only; `async_setup_entry` does the work, and `True`
    marks the YAML platform as handled.
    """
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set Loxone covers."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []

    for cover in get_all(loxconfig, ["Jalousie", "Gate", "Window"]):
        cover = add_room_and_cat_to_value_values(loxconfig, cover)
        cover.update(
            {
                "hass": hass,
            }
        )
        if cover["type"] == "Gate":
            new_gate = LoxoneGate(**cover)
            entities.append(new_gate)
        elif cover["type"] == "Window":
            new_window = LoxoneWindow(**cover)
            entities.append(new_window)
        else:
            new_jalousie = LoxoneJalousie(**cover)
            entities.append(new_jalousie)

    miniserver.listeners.append(
        async_dispatcher_connect(hass, miniserver.async_signal_new_device(NEW_COVERS), async_add_entities)
    )
    async_add_entities(entities)

    # Only Jalousies expose these; `required_features` keeps the service from
    # reaching Gate/Window (which lack the methods — PC-14) and from Jalousies
    # without the capability (PC-27).
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_ENABLE_SUN_AUTOMATION,
        {},
        "enable_sun_automation",
        required_features=[SUPPORT_SUN_AUTOMATION],
    )

    platform.async_register_entity_service(
        SERVICE_DISABLE_SUN_AUTOMATION,
        {},
        "disable_sun_automation",
        required_features=[SUPPORT_SUN_AUTOMATION],
    )

    platform.async_register_entity_service(
        SERVICE_QUICK_SHADE,
        {},
        "quick_shade",
        required_features=[SUPPORT_QUICK_SHADE],
    )


class LoxoneGate(LoxoneEntity, CoverEntity):
    """Loxone Gate"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]
        self._position_uuid = _state_uuid(self.states, "position")
        self._state_uuid = _state_uuid(self.states, "active")
        self._position = None
        self._closed = True
        self._is_opening = False
        self._is_closing = False
        self.type = "Gate"
        self._animation = 0
        if "animation" in self.details:
            self._animation = self.details["animation"]
        self._attr_device_info = get_or_create_device(self.unique_id, self.name, self.type, self.room)

    @property
    def supported_features(self):
        """Flag supported features."""
        return CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return gate_device_class(self._animation)

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self._position

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self._closed

    @property
    def is_closing(self):
        """Return if the cover is closing."""
        return self._is_closing

    @property
    def is_opening(self):
        """Return if the cover is opening."""
        return self._is_opening

    def open_cover(self, **kwargs):
        """Open the cover."""
        if self._position == 100.0:
            return
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="open"))
        self.schedule_update_ha_state()

    def close_cover(self, **kwargs):
        """Close the cover."""
        if self._position == 0:
            return
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="close"))
        self.schedule_update_ha_state()

    def stop_cover(self, **kwargs):
        """Stop the cover (PC-07)."""
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=gate_stop_command()))
        self.schedule_update_ha_state()

    async def event_handler(self, e):
        data = e.data
        position_seen = self._position_uuid is not None and self._position_uuid in data
        state_seen = self._state_uuid is not None and self._state_uuid in data
        if not (position_seen or state_seen):
            return

        if position_seen:
            self._position = float(e.data[self._position_uuid]) * 100.0
            self._closed = self._position == 0

        if state_seen:
            self._is_closing = False
            self._is_opening = False

            if e.data[self._state_uuid] == -1:
                self._is_closing = True
            elif e.data[self._state_uuid] == 1:
                self._is_opening = True
        self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


class LoxoneWindow(LoxoneEntity, CoverEntity):
    # pylint: disable=no-self-use
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]
        self._position_uuid = _state_uuid(self.states, "position")
        self._direction_uuid = _state_uuid(self.states, "direction")
        self._target_position_uuid = _state_uuid(self.states, "targetPosition")
        self._position = None
        self._target_position = None
        self._closed = True
        self._direction = 0

        self.type = "Window"
        self._attr_device_info = get_or_create_device(self.unique_id, self.name, self.type, self.room)

    async def event_handler(self, e):
        data = e.data
        position_seen = self._position_uuid is not None and self._position_uuid in data
        direction_seen = self._direction_uuid is not None and self._direction_uuid in data
        target_seen = self._target_position_uuid is not None and self._target_position_uuid in data
        if not (position_seen or direction_seen or target_seen):
            return

        if position_seen:
            self._position = float(e.data[self._position_uuid]) * 100.0
            self._closed = self._position == 0

        if direction_seen:
            self._direction = e.data[self._direction_uuid]

        if target_seen:
            self._target_position = float(e.data[self._target_position_uuid]) * 100.0

        self.schedule_update_ha_state()

    @property
    def current_cover_position(self):
        """Return current position of cover.

        None is unknown, 0 is closed, 100 is fully open.
        """
        return self._position

    @property
    def target_position(self):
        return self._target_position

    @property
    def extra_state_attributes(self):
        """
        Return device specific state attributes.
        Implemented by platform classes.
        """
        device_att = {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
            "target_position": self.target_position,
        }
        return device_att

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return CoverDeviceClass.WINDOW

    @property
    def is_closing(self):
        """Return if the cover is closing."""
        if self._direction == -1:
            return True
        return False

    @property
    def is_opening(self):
        """Return if the cover is opening."""
        if self._direction == 1:
            return True
        return False

    @property
    def is_closed(self):
        return self._closed

    def open_cover(self, **kwargs: Any) -> None:
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="fullopen"))

    def close_cover(self, **kwargs: Any) -> None:
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="fullclose"))

    def stop_cover(self, **kwargs):
        """Stop the cover (PC-07): a real stop, regardless of direction.

        Previously a closing window was sent `fullopen` and an opening one
        `fullclose` — running the window to the opposite end instead of
        stopping it (JoDehli/PyLoxone#501).
        """
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="stop"))

    def set_cover_position(self, **kwargs):
        """Return the current tilt position of the cover."""
        position = kwargs.get(ATTR_POSITION)
        self.hass.bus.fire(
            SENDDOMAIN,
            dict(uuid=self.uuidAction, value="moveToPosition/{}".format(position)),
        )


class LoxoneJalousie(LoxoneEntity, CoverEntity):
    """Loxone Jalousie"""

    # pylint: disable=no-self-use
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]

        # PC-36: read the state uuids out of the shared structure but never
        # write back into it (the old code injected `""` sentinel keys).
        self._position_uuid = _state_uuid(self.states, "position")
        self._shade_position_uuid = _state_uuid(self.states, "shadePosition")
        self._up_uuid = _state_uuid(self.states, "up")
        self._down_uuid = _state_uuid(self.states, "down")
        self._auto_info_text_uuid = _state_uuid(self.states, "autoInfoText")
        self._auto_state_uuid = _state_uuid(self.states, "autoState")
        self._target_position_uuid = _state_uuid(self.states, "targetPosition")
        self._position = 0
        self._position_loxone = -1
        self._tilt_position_loxone = 1
        self._target_position = None
        self._set_position = None
        self._set_tilt_position = None
        self._tilt_position = None
        self._requested_closing = True
        self._unsub_listener_cover = None
        self._unsub_listener_cover_tilt = None
        self._is_opening = False
        self._is_closing = False
        self._animation = 0
        self._is_automatic = False
        self._auto_text = ""
        self._auto_state = 0

        if "isAutomatic" in self.details:
            self._is_automatic = self.details["isAutomatic"]
        if "animation" in self.details:
            self._animation = self.details["animation"]

        self._closed = self.current_cover_position <= 0

        self.type = "Jalousie"
        self._attr_device_info = get_or_create_device(self.unique_id, self.name, self.type, self.room)

    @property
    def supported_features(self):
        """Flag supported features."""
        supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP

        if self.current_cover_position is not None:
            supported_features |= CoverEntityFeature.SET_POSITION

        if self.current_cover_tilt_position is not None and self.device_class == CoverDeviceClass.BLIND:
            supported_features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
                | SUPPORT_QUICK_SHADE
            )

        if self._is_automatic:
            supported_features |= SUPPORT_SUN_AUTOMATION

        return supported_features

    async def event_handler(self, e):
        data = e.data
        any_relevant = any(
            uuid is not None and uuid in data
            for uuid in (
                self._position_uuid,
                self._shade_position_uuid,
                self._up_uuid,
                self._down_uuid,
                self._auto_info_text_uuid,
                self._auto_state_uuid,
            )
        )
        target_seen = (
            self._is_automatic and self._target_position_uuid is not None and self._target_position_uuid in data
        )
        if not any_relevant and not target_seen:
            return

        if self._position_uuid is not None and self._position_uuid in data:
            self._position_loxone = float(e.data[self._position_uuid]) * 100.0
            self._position = map_range(self._position_loxone, 0, 100, 100, 0)

            if self._position == 0:
                self._closed = True
            else:
                self._closed = False

        if self._shade_position_uuid is not None and self._shade_position_uuid in data:
            self._tilt_position_loxone = float(e.data[self._shade_position_uuid]) * 100.0
            self._tilt_position = map_range(self._tilt_position_loxone, 0, 100, 100, 0)
        if target_seen:
            target_position_loxone = float(e.data[self._target_position_uuid]) * 100.0
            self._target_position = map_range(target_position_loxone, 0, 100, 100, 0)

        if self._up_uuid is not None and self._up_uuid in data:
            self._is_opening = e.data[self._up_uuid]

        if self._down_uuid is not None and self._down_uuid in data:
            self._is_closing = e.data[self._down_uuid]

        if self._auto_info_text_uuid is not None and self._auto_info_text_uuid in data:
            self._auto_text = e.data[self._auto_info_text_uuid]

        if self._auto_state_uuid is not None and self._auto_state_uuid in data:
            self._auto_state = e.data[self._auto_state_uuid]

        self.schedule_update_ha_state()

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self._position

    @property
    def current_cover_tilt_position(self):
        """Return the current tilt/slat position of the cover."""
        if self.device_class == CoverDeviceClass.BLIND:
            return self._tilt_position
        return None

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self._closed

    @property
    def is_closing(self):
        """Return if the cover is closing."""
        return self._is_closing

    @property
    def is_opening(self):
        """Return if the cover is opening."""
        return self._is_opening

    @property
    def target_position(self):
        return self._target_position

    @property
    def device_class(self) -> CoverDeviceClass | None:
        """Return the class of this device, from component DEVICE_CLASSES."""
        return jalousie_device_class(self._animation)

    @property
    def animation(self):
        # PC-15: the value is read once in `__init__`; `details` may not carry
        # the key at all.
        return self._animation

    @property
    def is_automatic(self):
        return self._is_automatic

    @property
    def auto(self):
        if self._is_automatic and self._auto_state:
            return STATE_ON
        else:
            return STATE_OFF

    @property
    def is_sun_automation_enabled(self) -> bool | None:
        """Return if sun automation is enabled"""
        return self.auto

    @property
    def shade_position_as_text(self):
        """Returns shade position as text"""
        if self.current_cover_tilt_position == 100 and self.current_cover_position < 10:
            return "shading on"
        else:
            return " "

    @property
    def extra_state_attributes(self):
        """
        Return device specific state attributes.
        Implemented by platform classes.
        """
        device_att = {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
            "current_position": self.current_cover_position,
            "current_shade_mode": self.shade_position_as_text,
            "current_position_loxone_style": round(self._position_loxone, 0),
        }

        if self._is_automatic:
            device_att.update(
                {
                    "automatic_text": self._auto_text,
                    "auto_state": self.auto,
                    "is_sun_automation_enabled": self.is_sun_automation_enabled,
                    "target_position": self.target_position,
                }
            )

        return device_att

    def close_cover(self, **kwargs):
        """Close the cover."""
        if self._position == 0:
            return
        elif self._position is None:
            self._closed = True
            self.schedule_update_ha_state()
            return

        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="FullDown"))
        self.schedule_update_ha_state()

    def open_cover(self, **kwargs):
        """Open the cover."""
        if self._position == 100.0:
            return
        elif self._position is None:
            self._closed = False
            self.schedule_update_ha_state()
            return
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="FullUp"))
        self.schedule_update_ha_state()

    def stop_cover(self, **kwargs):
        """Stop the cover."""
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="stop"))

    def set_cover_position(self, **kwargs):
        """Return the current tilt position of the cover."""
        position = kwargs.get(ATTR_POSITION)
        mapped_pos = map_range(position, 0, 100, 100, 0)
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=f"manualPosition/{mapped_pos}"))

    def open_cover_tilt(self, **kwargs):
        """Open the cover slats (shade open)."""
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=_lamelle_command(0.0)))

    def stop_cover_tilt(self, **kwargs):
        """Stop the cover."""
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="stop"))

    def close_cover_tilt(self, **kwargs):
        """Close the cover slats (shade closed)."""
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=_lamelle_command(100.0)))

    def set_cover_tilt_position(self, **kwargs):
        """Move the cover tilt to a specific position."""
        tilt_position = kwargs.get(ATTR_TILT_POSITION)
        mapped_pos = map_range(tilt_position, 0, 100, 100, 0)
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=_lamelle_command(mapped_pos)))

    async def enable_sun_automation(self, **kwargs: Any) -> None:
        """Enable the sun automation (PC-14: run on the event loop)."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="auto"))

    async def disable_sun_automation(self, **kwargs: Any) -> None:
        """Disable the sun automation (PC-14: run on the event loop)."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="NoAuto"))

    async def quick_shade(self, **kwargs: Any) -> None:
        """Move the slats to the Loxone-computed shade position."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="shade"))
