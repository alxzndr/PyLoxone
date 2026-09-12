from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import callback

from .. import LoxoneEntity
from ..helpers import device_info_for


class LoxoneLightSwitch(LoxoneEntity, LightEntity):
    """Representation of a light switch."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_is_on: bool | None = None
    _attr_state: None = None
    _attr_available = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_state = STATE_UNKNOWN
        self._attr_is_on = STATE_UNKNOWN
        self._attr_unique_id = self.uuidAction
        self._async_add_devices = kwargs["async_add_devices"]
        self._light_controller_id = kwargs.get("lightcontroller_id")
        self._light_controller_name = kwargs.get("lightcontroller_name")

        # WP-5.1: a LightControllerV2 sub-light carries only its own short
        # name — its device is the controller's device, named after the
        # controller (CORE-26).  A standalone light inherits the device
        # name and stores no entity name of its own.
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
            self.type = "Light"
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
            )

        state_attributes = {
            "device_type": self.type,
        }
        if self._light_controller_name:
            state_attributes.update({"light_controller": self._light_controller_name})

        self._attr_extra_state_attributes.update(state_attributes)

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self._send("on")
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self._send("off")
        self.async_schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the ``active`` stream the handler watches (if present).
        uuid = self.states.get("active")
        return frozenset({uuid}) if isinstance(uuid, str) and uuid else frozenset()

    @callback
    def event_handler(self, event):
        request_update = False
        if "active" in self.states:
            if self.states["active"] in event:
                active = event[self.states["active"]]
                new_state = True if active == 1.0 else False
                if new_state != self._attr_is_on:
                    self._attr_is_on = new_state
                    request_update = True

        if request_update:
            if not self._attr_available:
                self._attr_available = True
            self.async_write_ha_state()
