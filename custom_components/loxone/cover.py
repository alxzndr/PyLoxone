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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import (
    SERVICE_DISABLE_SUN_AUTOMATION,
    SERVICE_ENABLE_SUN_AUTOMATION,
    SERVICE_QUICK_SHADE,
    SUPPORT_QUICK_SHADE,
    SUPPORT_SUN_AUTOMATION,
)
from .helpers import add_room_and_cat_to_value_values, device_info_for, get_all, map_range
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

# Loxone ignores a `manualLamelle/<position>` command whose value equals the
# one currently in effect; a small random sub-percent delta (at most 0.9%,
# so the command still means "open"/"closed") forces the server to apply the
# command and echo the state back (PC-19).
_LAMELLE_JITTER_MIN = 0.000000001
_LAMELLE_JITTER_MAX = 0.009


def _state_uuid(states, name):
    """
    The uuid the structure file registers for `states[name]`, or None.

    Several Loxone control types carry optional states (a Window without
    `targetPosition`, a Jalousie without `shadePosition`, ...); using `.get()`
    here is what keeps the event handlers from raising KeyError (PC-16).
    """
    return states.get(name)


def _lamelle_command(base_position):
    """
    Build a `manualLamelle/<position>` command with an explicit fixed
    decimal format (3 digits — the tint is coarse) plus the sub-percent
    jitter that keeps Loxone from discarding the command (PC-19).
    """
    position = base_position + random.uniform(_LAMELLE_JITTER_MIN, _LAMELLE_JITTER_MAX)
    return f"manualLamelle/{position:.3f}"


def gate_stop_command():
    """
    Command sent when a Gate `stop_cover` is requested (PC-07, VERIFY).

    The pre-fix code re-sent the *opposite* direction, which is certainly a
    direction reversal, not a stop. The intended semantics — and what
    `LoxoneJalousie.stop_cover` already does — is a real `stop`; Loxone gate
    controls expose it. A live-Miniserver check before merge is required to
    confirm the gate honours `stop`.
    """
    return "stop"


def gate_set_position_command(position):
    """
    Build the command for moving a Gate to a cover position (HA scale).

    The Gate is tracked on a 0..100 position scale in HA orientation
    (100 = open, 0 = closed — see :class:`LoxoneGate`'s event handler,
    which reads `position` * 100 directly), so the position is clamped
    into 0..100 and passed through without Loxone-style inversion, to
    `manualPosition/<position>` — the same CoverControl command Jalousies
    use for partial positioning.

    **VERIFY**: confirm on a live Miniserver that a Loxone gate control
    honours `manualPosition` and that its orientation matches the entity
    (`position` state * 100 == HA position). If the server expects a
    down-orientation for gates, the mapping flips in this one place only.
    """
    if position is None:
        clamped = 0.0
    else:
        clamped = max(0.0, min(100.0, float(position)))
    return f"manualPosition/{clamped}"


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
    _hass: HomeAssistant,
    _config: ConfigType,
    _async_add_entities: AddEntitiesCallback,
    _discovery_info: DiscoveryInfoType | None = None,
) -> bool:
    """
    Set up the Loxone covers.

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
                "config_entry": config_entry,
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

    # CORE-17: the old code subscribed to an ``async_signal_new_device``
    # signal that no code path ever sent and leaked the unsubscribe on
    # ``MiniServer.listeners`` (never iterated).  Covers are created
    # exclusively from the structure file.
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
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
        )

    @property
    def supported_features(self):
        """
        Flag supported features.

        SET_POSITION (WP-6.8) is advertised only when the structure file
        reports a `position` stream — a gate without one cannot follow a
        position command.
        """
        supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if self._position_uuid is not None:
            supported_features |= CoverEntityFeature.SET_POSITION
        return supported_features

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

    def open_cover(self, **_kwargs):
        """Open the cover."""
        if self._position == 100.0:
            return
        self._send("open")
        self.schedule_update_ha_state()

    def close_cover(self, **_kwargs):
        """Close the cover."""
        if self._position == 0:
            return
        self._send("close")
        self.schedule_update_ha_state()

    def stop_cover(self, **_kwargs):
        """Stop the cover (PC-07)."""
        self._send(gate_stop_command())
        self.schedule_update_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """
        Move the gate to the requested position (WP-6.8).

        Async (PC-14): HA entity services prefer the ``async_`` variant;
        a sync-only method would run in the executor thread and hit the
        wrong event loop in :meth:`LoxoneEntity._send`.
        """
        self._send(gate_set_position_command(kwargs.get(ATTR_POSITION)))
        self.async_write_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the optional position / active streams the handler reads.
        return frozenset(uuid for uuid in (self._position_uuid, self._state_uuid) if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, e):
        data = e
        position_seen = self._position_uuid is not None and self._position_uuid in data
        state_seen = self._state_uuid is not None and self._state_uuid in data
        if not (position_seen or state_seen):
            return

        if position_seen:
            self._position = float(data[self._position_uuid]) * 100.0
            self._closed = self._position == 0

        if state_seen:
            self._is_closing = False
            self._is_opening = False

            if data[self._state_uuid] == -1:
                self._is_closing = True
            elif data[self._state_uuid] == 1:
                self._is_opening = True
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """
        Return device specific state attributes.

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
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the optional position / direction / targetPosition streams.
        return frozenset(
            uuid
            for uuid in (self._position_uuid, self._direction_uuid, self._target_position_uuid)
            if isinstance(uuid, str) and uuid
        )

    @callback
    def event_handler(self, e):
        data = e
        position_seen = self._position_uuid is not None and self._position_uuid in data
        direction_seen = self._direction_uuid is not None and self._direction_uuid in data
        target_seen = self._target_position_uuid is not None and self._target_position_uuid in data
        if not (position_seen or direction_seen or target_seen):
            return

        if position_seen:
            self._position = float(data[self._position_uuid]) * 100.0
            self._closed = self._position == 0

        if direction_seen:
            self._direction = data[self._direction_uuid]

        if target_seen:
            self._target_position = float(data[self._target_position_uuid]) * 100.0

        self.async_write_ha_state()

    @property
    def current_cover_position(self):
        """
        Return current position of cover.

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

    def open_cover(self, **_kwargs: Any) -> None:
        self._send("fullopen")

    def close_cover(self, **_kwargs: Any) -> None:
        self._send("fullclose")

    def stop_cover(self, **_kwargs):
        """
        Stop the cover (PC-07): a real stop, regardless of direction.

        Previously a closing window was sent `fullopen` and an opening one
        `fullclose` — running the window to the opposite end instead of
        stopping it (JoDehli/PyLoxone#501).
        """
        self._send("stop")

    def set_cover_position(self, **kwargs):
        """Return the current tilt position of the cover."""
        position = kwargs.get(ATTR_POSITION)
        self._send(f"moveToPosition/{position}")


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
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
        )

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

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: position, shade position, up, down, auto-info, auto-state
        # plus the sun-automation target when present.
        uuids = (
            self._position_uuid,
            self._shade_position_uuid,
            self._up_uuid,
            self._down_uuid,
            self._auto_info_text_uuid,
            self._auto_state_uuid,
        )
        if self._is_automatic:
            uuids = (*uuids, self._target_position_uuid)
        return frozenset(uuid for uuid in uuids if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, e):
        data = e
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
            self._position_loxone = float(data[self._position_uuid]) * 100.0
            self._position = map_range(self._position_loxone, 0, 100, 100, 0)

            if self._position == 0:
                self._closed = True
            else:
                self._closed = False

        if self._shade_position_uuid is not None and self._shade_position_uuid in data:
            self._tilt_position_loxone = float(data[self._shade_position_uuid]) * 100.0
            self._tilt_position = map_range(self._tilt_position_loxone, 0, 100, 100, 0)
        if target_seen:
            target_position_loxone = float(data[self._target_position_uuid]) * 100.0
            self._target_position = map_range(target_position_loxone, 0, 100, 100, 0)

        if self._up_uuid is not None and self._up_uuid in data:
            self._is_opening = data[self._up_uuid]

        if self._down_uuid is not None and self._down_uuid in data:
            self._is_closing = data[self._down_uuid]

        if self._auto_info_text_uuid is not None and self._auto_info_text_uuid in data:
            self._auto_text = data[self._auto_info_text_uuid]

        if self._auto_state_uuid is not None and self._auto_state_uuid in data:
            self._auto_state = data[self._auto_state_uuid]

        self.async_write_ha_state()

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

    def close_cover(self, **_kwargs):
        """Close the cover."""
        if self._position == 0:
            return
        if self._position is None:
            self._closed = True
            self.schedule_update_ha_state()
            return

        self._send("FullDown")
        self.schedule_update_ha_state()

    def open_cover(self, **_kwargs):
        """Open the cover."""
        if self._position == 100.0:
            return
        if self._position is None:
            self._closed = False
            self.schedule_update_ha_state()
            return
        self._send("FullUp")
        self.schedule_update_ha_state()

    def stop_cover(self, **_kwargs):
        """Stop the cover."""
        self._send("stop")

    def set_cover_position(self, **kwargs):
        """Return the current tilt position of the cover."""
        position = kwargs.get(ATTR_POSITION)
        mapped_pos = map_range(position, 0, 100, 100, 0)
        self._send(f"manualPosition/{mapped_pos}")

    def open_cover_tilt(self, **_kwargs):
        """Open the cover slats (shade open)."""
        self._send(_lamelle_command(0.0))

    def stop_cover_tilt(self, **_kwargs):
        """Stop the cover."""
        self._send("stop")

    def close_cover_tilt(self, **_kwargs):
        """Close the cover slats (shade closed)."""
        self._send(_lamelle_command(100.0))

    def set_cover_tilt_position(self, **kwargs):
        """Move the cover tilt to a specific position."""
        tilt_position = kwargs.get(ATTR_TILT_POSITION)
        mapped_pos = map_range(tilt_position, 0, 100, 100, 0)
        self._send(_lamelle_command(mapped_pos))

    async def enable_sun_automation(self, **_kwargs: Any) -> None:
        """Enable the sun automation (PC-14: run on the event loop)."""
        self._send("auto")

    async def disable_sun_automation(self, **_kwargs: Any) -> None:
        """Disable the sun automation (PC-14: run on the event loop)."""
        self._send("NoAuto")

    async def quick_shade(self, **_kwargs: Any) -> None:
        """Move the slats to the Loxone-computed shade position."""
        self._send("shade")
