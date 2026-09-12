from functools import cached_property

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import callback

from .. import LoxoneEntity
from ..helpers import (
    device_info_for,
    hass_to_lox,
    hass_to_lox_range,
    lox_to_hass,
    lox_to_hass_range,
)


class LoxoneDimmer(LoxoneEntity, LightEntity):
    """Representation of a Loxone Dimmer."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_available = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the dimmer ."""
        self._attr_is_on = None
        self._attr_unique_id = self.uuidAction
        self._position = 0.0
        self._step = 1
        self._min_uuid = kwargs.get("states", {}).get("min", None)
        self._max_uuid = kwargs.get("states", {}).get("max", None)
        self._position_uuid = kwargs.get("states", {}).get("position", None)
        self._step_uuid = kwargs.get("states", {}).get("step", None)
        self._min = STATE_UNKNOWN
        self._max = STATE_UNKNOWN
        self._async_add_devices = kwargs["async_add_devices"]
        self._light_controller_id = kwargs.get("lightcontroller_id")
        self._light_controller_name = kwargs.get("lightcontroller_name")

        # WP-5.1: a LightControllerV2 sub-dimmer carries only its own
        # short name — its device is the controller's device, named after
        # the controller (CORE-26).  A standalone dimmer inherits the
        # device name and stores no entity name.
        if self._light_controller_id:
            self._attr_name = self._lox_name
        if self._light_controller_id:
            self.type = "LightControllerV2"
            self._attr_entity_registry_enabled_default = kwargs.get("enabled_default", True)
            # The device is the *controller's* device: name it after the
            # controller (CORE-20/PC-05 device identity).
            controller_name = self._light_controller_name or self._lox_name
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self._light_controller_id, controller_name, self.type, self.room, True
            )
        else:
            self.type = "Dimmer"
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
            )

        state_attributes = {
            "device_type": self.type,
        }
        if self._light_controller_name:
            state_attributes.update({"light_controller": self._light_controller_name})

        self._attr_extra_state_attributes.update(state_attributes)

    @property
    def _master_min_max_known(self) -> bool:
        return isinstance(self._min, (int, float)) and isinstance(self._max, (int, float))

    def _hass_to_master(self, hass_level) -> float:
        """
        HA brightness (1-255) → Loxone value, honouring the control's
        min/max (PC-17) and never rounding a non-zero request to 0 (PC-18).
        """
        if self._master_min_max_known:
            return hass_to_lox_range(hass_level, self._min, self._max)
        if not hass_level:
            return 0
        return max(1, round(hass_to_lox(hass_level)))

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._send(self._hass_to_master(kwargs[ATTR_BRIGHTNESS]))
        else:
            self._send("On")
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **_kwargs) -> None:
        self._send("Off")
        self.async_schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the min / max / step / position streams the handler reads.
        return frozenset(
            uuid
            for uuid in (self._min_uuid, self._max_uuid, self._step_uuid, self._position_uuid)
            if isinstance(uuid, str) and uuid
        )

    @callback
    def event_handler(self, e):
        request_update = False
        if self._min_uuid in e:
            try:
                self._min = float(e[self._min_uuid])
            except TypeError, ValueError:
                pass
            request_update = True

        if self._max_uuid in e:
            try:
                self._max = float(e[self._max_uuid])
            except TypeError, ValueError:
                pass
            request_update = True

        if self._step_uuid in e:
            self._step = e[self._step_uuid]
            request_update = True

        if self._position_uuid in e:
            position = e[self._position_uuid]
            try:
                position = float(position)
            except TypeError, ValueError:
                position = None
            if position is not None:
                if self._master_min_max_known:
                    self._attr_brightness = lox_to_hass_range(position, self._min, self._max)
                else:
                    self._attr_brightness = round(lox_to_hass(position))
                request_update = True

        self._attr_is_on = True if self._attr_brightness and self._attr_brightness > 0 else False

        if request_update:
            if not self._attr_available:
                if self._master_min_max_known or self._attr_is_on is not None:
                    self._attr_available = True
            self.async_write_ha_state()

    @cached_property
    def icon(self):
        """Return the sensor icon."""
        return "mdi:brightness-6"


class EIBDimmer(LoxoneDimmer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self._light_controller_id:
            self.type = "LightControllerV2"
            self._attr_entity_registry_enabled_default = kwargs.get("enabled_default", True)
            # The device is the *controller's* device: name it after the
            # controller (CORE-20/PC-05 device identity).
            controller_name = self._light_controller_name or self._lox_name
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self._light_controller_id, controller_name, self.type, self.room, True
            )
        else:
            self.type = "EIBDimmer"
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
            )

    @cached_property
    def icon(self):
        """Return the sensor icon."""
        return "mdi:brightness-4"
