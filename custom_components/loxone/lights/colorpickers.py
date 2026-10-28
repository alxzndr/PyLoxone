import logging
from functools import cached_property

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import callback

from .. import LoxoneEntity
from ..helpers import device_info_for, hass_to_lox, literal_decoder, lox_to_hass

_LOGGER = logging.getLogger(__name__)

# Kelvin sent when a colour picker `turn_on` needs a temperature but none has
# ever been reported.  The value itself is arbitrary *inside the picker's own
# range*, so `plan_temp_turn_on` clamps it into that range (upstream
# 7cc7c77): a 2200-3000 K block must never be sent 4000.  The value is still
# VERIFY on a live Miniserver (PC-39; live check #13).
DEFAULT_TURN_ON_KELVIN = 4000

# Structure File v16/v17, ColorPickerV2 Details: `TWMin`/`TWMax` are "Lowest/
# Highest color-temperature supported" with defaults 2700/6500.  They are the
# fallback when a control's details carry no usable pair (PC-39).
DEFAULT_MIN_KELVIN = 2700
DEFAULT_MAX_KELVIN = 6500


def color_temp_range(details) -> tuple[int, int]:
    """
    The colour-temperature range a picker supports, from its ``details``
    (upstream ``7cc7c77``/``5867889``).

    ``TWMin``/``TWMax`` (Structure File v17, ColorPickerV2 Details; the real
    device sends them, e.g. 2200/6500 — v16 documents the same two details
    under the names `minKelvin`/`maxKelvin`).  A missing, ``null``,
    non-numeric or non-positive value falls back to the spec default for
    that end; a nonsensical pair (min >= max) falls back to the pair.
    Home Assistant would otherwise clamp every request into the hard-coded
    2700-6500 and make the warm end of a 2200 K block unreachable.
    """
    details = details if isinstance(details, dict) else {}

    def _kelvin(value, default):
        try:
            value = int(value)
        except (TypeError, ValueError):  # None, "", "abc", 2700.5.5, ...
            return default
        return value if value > 0 else default

    lo = _kelvin(details.get("TWMin"), DEFAULT_MIN_KELVIN)
    hi = _kelvin(details.get("TWMax"), DEFAULT_MAX_KELVIN)
    if lo >= hi:
        return DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN
    return lo, hi


def plan_turn_on(*, color_mode, hs_color, color_temp_kelvin, brightness, kwargs) -> str:
    """
    Build exactly one outgoing command for an RGB colour picker `turn_on`.

    The old inline branch logic could send *nothing* when only `brightness`
    was requested while the colour mode was still unknown (PC-03, upstream PR
    #512): neither the HS nor the colour-temp branch matched and the
    `setBrightness` fallthrough was unreachable.  The precedence is now:

    1. explicit ``hs_color``/``color_temp_kelvin`` kwargs,
    2. the current colour mode, but only when its companion value is known,
    3. ``setBrightness`` so that a brightness-only service call always emits
       *something*.

    The brightness used on the wire is the requested one, or the last known
    one, or 255 if nothing has ever been reported.
    """
    level = kwargs.get(ATTR_BRIGHTNESS, brightness) or 255
    if ATTR_HS_COLOR in kwargs:
        hue, sat = kwargs[ATTR_HS_COLOR]
        return f"hsv({hue}, {sat}, {hass_to_lox(level)})"
    if ATTR_COLOR_TEMP_KELVIN in kwargs:
        return f"temp({hass_to_lox(level)}, {kwargs[ATTR_COLOR_TEMP_KELVIN]})"
    if color_mode is ColorMode.HS and hs_color is not None:
        return f"hsv({hs_color[0]}, {hs_color[1]}, {hass_to_lox(level)})"
    if color_mode is ColorMode.COLOR_TEMP and color_temp_kelvin is not None:
        return f"temp({hass_to_lox(level)}, {color_temp_kelvin})"
    return f"setBrightness/{hass_to_lox(level)}"


def plan_temp_turn_on(
    *,
    color_temp_kelvin,
    brightness,
    kwargs,
    min_kelvin: int = DEFAULT_MIN_KELVIN,
    max_kelvin: int = DEFAULT_MAX_KELVIN,
) -> str:
    """
    Build the outgoing command for a TunableWhite picker `turn_on`.

    A *plain* `turn_on` (no service data) sends ``setBrightness/<last known
    level>`` — falling back to full when nothing has ever been reported —
    not ``On``: the ColorPickerV2 command list of the Structure File (v16
    and v17) documents `setBrightness`, `hsv()` and `temp()`, but no `on`
    (upstream ``7cc7c77``/``5867889``), and an unknown command is still
    answered with 200, so the old ``On`` may simply have been ignored.

    Defaults for the unreported state (brightness 255, and
    :data:`DEFAULT_TURN_ON_KELVIN` *clamped into the picker's own range*)
    are applied *before* formatting, so a `turn_on` before the first state
    cannot raise or emit ``None`` (PC-11) and a narrow-range picker is
    never sent a kelvin outside its range.
    """
    if not kwargs:
        return f"setBrightness/{hass_to_lox(brightness or 255)}"
    level = kwargs.get(ATTR_BRIGHTNESS, brightness) or 255
    kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN, color_temp_kelvin)
    if kelvin is None:
        kelvin = min(max(DEFAULT_TURN_ON_KELVIN, min_kelvin), max_kelvin)
    return f"temp({hass_to_lox(level)}, {kelvin})"


class TunableWhiteLight(LoxoneEntity, LightEntity):
    # Defaults; replaced per control from `details.TWMin`/`TWMax` in
    # `__init__` (upstream 7cc7c77).
    _attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN
    _attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN

    _attr_supported_color_modes: set[ColorMode] = {ColorMode.COLOR_TEMP}
    _attr_available = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the Tunable White Light."""
        self._attr_is_on = None
        self._attr_unique_id = self.uuidAction
        self._attr_color_mode = ColorMode.UNKNOWN
        self._color_uuid = kwargs.get("states", {}).get("color", None)
        self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin = color_temp_range(kwargs.get("details"))

        self._async_add_devices = kwargs["async_add_devices"]
        self._light_controller_id = kwargs.get("lightcontroller_id")
        self._light_controller_name = kwargs.get("lightcontroller_name")

        # WP-5.1: LCV2 sub-lights keep their own (short) names; the
        # device is the controller's, named after the controller (CORE-26).
        if self._light_controller_id:
            self._attr_name = self._lox_name
        if self._light_controller_id:
            self.type = "LightControllerV2"
            self._attr_entity_registry_enabled_default = kwargs.get("enabled_default", True)
            # The device is the *controller's* device: name it after the
            # controller (the entity's own name carries the "- Sub"
            # suffix, which must not overwrite the shared device name in
            # the registry (CORE-20/PC-05 device identity)).
            controller_name = self._light_controller_name or self._lox_name
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self._light_controller_id, controller_name, self.type, self.room
            )
        else:
            self.type = "ColorPickerV2"
            # Standalone picker: the device identifier must be a string
            # (PC-05 — `self._light_controller_id` is `None` here).
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
            )

    @property
    def is_on(self) -> bool:
        return True if self._attr_brightness and self._attr_brightness > 0 else False

    async def async_turn_off(self, **_kwargs) -> None:
        self._send("setBrightness/0")
        self.async_schedule_update_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._attr_color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        self._send(
            plan_temp_turn_on(
                color_temp_kelvin=self._attr_color_temp_kelvin,
                brightness=self._attr_brightness,
                kwargs=kwargs,
                min_kelvin=self._attr_min_color_temp_kelvin,
                max_kelvin=self._attr_max_color_temp_kelvin,
            )
        )
        self.async_schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the color stream the handler parses.
        return frozenset({self._color_uuid}) if isinstance(self._color_uuid, str) and self._color_uuid else frozenset()

    @callback
    def event_handler(self, e):
        request_update = False
        if self._color_uuid in e:
            _color = e[self._color_uuid]

            if _color.startswith("temp"):
                _color = _color.replace("temp", "")
                _color = literal_decoder(_color)
                if _color is not None:
                    self._attr_color_mode = ColorMode.COLOR_TEMP
                    self._attr_color_temp_kelvin = _color[1]
                    self._attr_brightness = round(255 * _color[0] / 100)
                    request_update = True
            elif _color.startswith("hsv"):
                if _color == "hsv(0,0,0)":
                    # "hsv(0,0,0)" is how the Miniserver reports a tunable-white
                    # picker that was switched off: it must publish like any other
                    # state report. It used to zero the brightness silently, so the
                    # entity went stale in HA (or stayed unavailable forever when
                    # this was its first report).
                    self._attr_brightness = 0
                    request_update = True
                else:
                    _LOGGER.warning("hsv not supported for TunableWhiteLight")
            else:
                _LOGGER.error("Not handled command -> %s", _color)

        if request_update:
            if not self._attr_available:
                self._attr_available = True
            self.async_write_ha_state()

    @cached_property
    def icon(self):
        """Return the sensor icon."""
        return "mdi:lightbulb"


class RGBColorPicker(LoxoneEntity, LightEntity):
    # Defaults; replaced per control from `details.TWMin`/`TWMax` in
    # `__init__` (upstream 7cc7c77).
    _attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN
    _attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN
    _attr_available = False

    _attr_supported_color_modes: set[ColorMode] = {
        ColorMode.COLOR_TEMP,
        ColorMode.HS,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the RGB color picker."""
        self._attr_unique_id = self.uuidAction
        self._attr_color_mode = ColorMode.UNKNOWN
        self._color_uuid = kwargs.get("states", {}).get("color", None)
        self._sequence_uuid = kwargs.get("states", {}).get("sequence", None)
        self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin = color_temp_range(kwargs.get("details"))

        self._async_add_devices = kwargs["async_add_devices"]
        self._light_controller_id = kwargs.get("lightcontroller_id")
        self._light_controller_name = kwargs.get("lightcontroller_name")

        # WP-5.1: LCV2 sub-lights keep their own (short) names; the
        # device is the controller's, named after the controller (CORE-26).
        if self._light_controller_id:
            self._attr_name = self._lox_name
        if self._light_controller_id:
            self.type = "LightControllerV2"
            self._attr_entity_registry_enabled_default = kwargs.get("enabled_default", True)
            # The device is the *controller's* device: name it after the
            # controller (the entity's own name carries the "- Sub"
            # suffix, which must not overwrite the shared device name in
            # the registry (CORE-20/PC-05 device identity)).
            controller_name = self._light_controller_name or self._lox_name
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self._light_controller_id, controller_name, self.type, self.room
            )
        else:
            self.type = "ColorPickerV2"
            # Standalone picker: the device identifier must be a string
            # (PC-05 — `self._light_controller_id` is `None` here).
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
            )

    @property
    def is_on(self) -> bool:
        return True if self._attr_brightness and self._attr_brightness > 0 else False

    async def async_turn_off(self, **_kwargs) -> None:
        self._send("setBrightness/0")
        self.async_schedule_update_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._attr_color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            self._attr_color_mode = ColorMode.COLOR_TEMP
        if ATTR_HS_COLOR in kwargs:
            hue, sat = kwargs[ATTR_HS_COLOR]
            # Keep the model in the coordinate system we're told to set.
            self._attr_hs_color = (hue, sat)
            self._attr_color_mode = ColorMode.HS
        self._send(
            plan_turn_on(
                color_mode=self._attr_color_mode,
                hs_color=self._attr_hs_color,
                color_temp_kelvin=self._attr_color_temp_kelvin,
                brightness=self._attr_brightness,
                kwargs=kwargs,
            )
        )
        self.async_schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the color stream the handler parses.
        return frozenset({self._color_uuid}) if isinstance(self._color_uuid, str) and self._color_uuid else frozenset()

    @callback
    def event_handler(self, e):
        request_update = False
        if self._color_uuid in e:
            _color = e[self._color_uuid]

            if _color.startswith("hsv"):
                _color = _color.replace("hsv", "")
                _color = literal_decoder(_color)
                if _color is not None:
                    self._attr_color_mode = ColorMode.HS
                    self._attr_hs_color = (_color[0], _color[1])
                    # HA brightness is an int 0-255 (the temp branch rounds too).
                    self._attr_brightness = round(lox_to_hass(_color[2]))
                    request_update = True
            elif _color.startswith("temp"):
                _color = _color.replace("temp", "")
                _color = literal_decoder(_color)
                if _color is not None:
                    self._attr_color_mode = ColorMode.COLOR_TEMP
                    self._attr_color_temp_kelvin = _color[1]
                    self._attr_hs_color = None
                    self._attr_brightness = round(255 * _color[0] / 100)
                    request_update = True
            else:
                _LOGGER.error("Not handled command -> %s", _color)

        if request_update:
            if not self._attr_available:
                self._attr_available = True
            self.async_write_ha_state()

    @cached_property
    def icon(self):
        """Return the sensor icon."""
        return "mdi:eyedropper-variant"


class LumiTech(RGBColorPicker):
    """Representation of a Loxone LumiTech Dimmer."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the LumiTech."""
        if self._light_controller_id:
            self.type = "LightControllerV2"
            self._attr_entity_registry_enabled_default = kwargs.get("enabled_default", True)
            # The device is the *controller's* device: name it after the
            # controller (the entity's own name carries the "- Sub"
            # suffix, which must not overwrite the shared device name in
            # the registry (CORE-20/PC-05 device identity)).
            controller_name = self._light_controller_name or self._lox_name
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self._light_controller_id, controller_name, self.type, self.room
            )
        else:
            self.type = "LumiTech"
            self._attr_device_info = device_info_for(
                kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
            )
