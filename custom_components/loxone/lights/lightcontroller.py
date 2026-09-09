from collections import OrderedDict
from functools import cached_property

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_EFFECT, ColorMode, LightEntity, LightEntityFeature
from homeassistant.core import callback

from .. import LoxoneEntity
from ..helpers import device_info_for, hass_to_lox, hass_to_lox_range, json_decoder, lox_to_hass, lox_to_hass_range

# LCV2 mood ids: `changeTo/0` is the all-off mood, `changeTo/99` the
# all-on "no mood" state, and an activeMoods payload of `[778]` means there
# is no mood active (off).  (PC-33: the magic numbers were inline.)
OFF_MOOD_ID = "0"
ON_MOOD_ID = "99"
ALL_OFF_ACTIVE_MOODS = [778]


class LoxoneLightControllerV2(LoxoneEntity, LightEntity):
    """Representation of a Light Controller V2."""

    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_is_on: bool | None = None
    _attr_available = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_is_on = None
        self._active_moods = []
        self._moodlist = []
        self._additional_moodlist = []
        self._attr_brightness = None
        self._master_value = None
        self._master_value_uuid = None
        self._master_position_uuid = None
        self._master_min_uuid = None
        self._master_max_uuid = None
        self._master_min = None
        self._master_max = None
        self._async_add_devices = kwargs["async_add_devices"]

        self._sub_controls = OrderedDict({})
        for uuid, control in kwargs.get("subControls", {}).items():
            self._sub_controls[uuid] = {
                "name": control["name"],
                "type": control["type"],
            }
            if "masterValue" in uuid and control.get("type") in ["Dimmer", "EIBDimmer"]:
                self._master_value = control

        if self._master_value:
            self._master_value_uuid = self._master_value.get("uuidAction")
            self._master_position_uuid = self._master_value.get("states", {}).get("position")
            self._master_min_uuid = self._master_value.get("states", {}).get("min")
            self._master_max_uuid = self._master_value.get("states", {}).get("max")
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

        self.type = "LightControllerV2"
        self._attr_device_info = device_info_for(kwargs.get("config_entry"), self.unique_id, self.name, self.type, self.room)

    @property
    def mood_list_uuid(self):
        return self.states["moodList"]

    def get_moodname_by_id(self, _id):
        for mood in self._moodlist:
            if "id" in mood and "name" in mood:
                if mood["id"] == _id:
                    return mood["name"]
        return _id

    def get_id_by_moodname(self, _name):
        for mood in self._moodlist:
            if "id" in mood and "name" in mood:
                if mood["name"] == _name:
                    return mood["id"]
        return _name

    @property
    def effect_list(self):
        """Return the moods of light controller."""
        moods = []
        for mood in self._moodlist:
            if "name" in mood:
                moods.append(mood["name"])
        return moods

    @property
    def effect(self):
        """Return the current effect."""
        if len(self._active_moods) == 1:
            return self.get_moodname_by_id(self._active_moods[0])
        return None

    async def got_effect(self, **kwargs):
        effects = kwargs["effect"].split(",")
        if len(effects) == 1:
            mood_id = self.get_id_by_moodname(kwargs["effect"])
            if mood_id != kwargs["effect"]:
                self._send(f"changeTo/{mood_id}")
            else:
                self._send("plus")
        else:
            effect_ids = []
            for _ in effects:
                mood_id = self.get_id_by_moodname(_.strip())
                if mood_id != _:
                    effect_ids.append(mood_id)

            self._send(f"changeTo/{effect_ids[0]}")

            for _ in effect_ids[1:]:
                self._send(f"addMood/{_}")

    @property
    def _master_min_max_known(self) -> bool:
        return self._master_min is not None and self._master_max is not None

    def _hass_to_master(self, hass_level) -> float:
        """HA brightness (1-255) → Loxone value, honouring the master
        dimmer's min/max (PC-17) and never rounding a non-zero request to
        0 (PC-18); shares its scaling with LoxoneDimmer."""
        if self._master_min_max_known:
            return hass_to_lox_range(hass_level, self._master_min, self._master_max)
        if not hass_level:
            return 0
        return max(1, round(hass_to_lox(hass_level)))

    async def async_turn_on(self, **kwargs) -> None:
        sent_something = False
        if ATTR_EFFECT in kwargs:
            await self.got_effect(**kwargs)
            sent_something = True
        if ATTR_BRIGHTNESS in kwargs and self._master_value_uuid:
            self._send(self._hass_to_master(kwargs[ATTR_BRIGHTNESS]), uuid=self._master_value_uuid)
            sent_something = True
        # PC-33: a bare `turn_on` while off turns on, and a non-empty
        # `turn_on` that touched no sub-control (e.g. brightness without a
        # master dimmer) still emits a command, so a service call never
        # silently does nothing.
        if not sent_something:
            if not kwargs and not self._attr_is_on:
                self._send(f"changeTo/{ON_MOOD_ID}")
            elif kwargs:
                self._send("on")
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._send(f"changeTo/{OFF_MOOD_ID}")
        self.async_schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the state/activeMoods/moodList/additionalMoods streams the
        # handler reads, plus the optional master dimmer streams.
        uuids = [
            self.uuidAction,
            self.states["activeMoods"],
            self.states["moodList"],
            self.states["additionalMoods"],
        ] + [
            self._master_min_uuid,
            self._master_max_uuid,
            self._master_position_uuid,
        ]
        return frozenset(uuid for uuid in uuids if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, event):
        request_update = False

        if self.uuidAction in event:
            self._attr_state = event[self.uuidAction]
            request_update = True

        if self._master_min_uuid and self._master_min_uuid in event:
            try:
                self._master_min = float(event[self._master_min_uuid])
            except TypeError, ValueError:
                pass
            request_update = True

        if self._master_max_uuid and self._master_max_uuid in event:
            try:
                self._master_max = float(event[self._master_max_uuid])
            except TypeError, ValueError:
                pass
            request_update = True

        if self._master_position_uuid and self._master_position_uuid in event:
            try:
                position = float(event[self._master_position_uuid])
            except TypeError, ValueError:
                position = None
            if position is not None:
                if self._master_min_max_known:
                    self._attr_brightness = lox_to_hass_range(position, self._master_min, self._master_max)
                else:
                    self._attr_brightness = round(lox_to_hass(position))
                request_update = True

        if self.states["activeMoods"] in event:
            self._active_moods = json_decoder(event[self.states["activeMoods"]])
            if self._active_moods is not None:
                if self._active_moods != ALL_OFF_ACTIVE_MOODS:
                    self._attr_is_on = True
                else:
                    self._attr_is_on = False
                request_update = True

        if self.states["moodList"] in event:
            self._moodlist = json_decoder(event[self.states["moodList"]])
            if self._moodlist is not None:
                request_update = True

        if self.states["additionalMoods"] in event:
            self._additional_moodlist = json_decoder(event[self.states["additionalMoods"]])
            request_update = True

        if request_update:
            if not self._attr_available:
                attr_is_on_is_not_unknown = self._attr_is_on is True or self._attr_is_on is False
                both_master_values_are_not_unknown = self._master_min_max_known
                if attr_is_on_is_not_unknown or both_master_values_are_not_unknown:
                    self._attr_available = True
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            **self._attr_extra_state_attributes,
            "selected_scene": self.effect,
            "selected_scenes": [self.get_moodname_by_id(id) for id in self._active_moods],
            "device_type": self.type,
            "subcontrols": self._sub_controls,
        }

    @cached_property
    def icon(self):
        """Return the sensor icon."""
        return "mdi:hubspot"
