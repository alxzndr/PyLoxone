import logging
import math
from enum import StrEnum

from homeassistant.components.light import (
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .helpers import add_room_and_cat_to_value_values, device_info_for, get_all, hass_to_lox, iter_controls
from .lights.colorpickers import LumiTech, RGBColorPicker, TunableWhiteLight
from .lights.dimmer import EIBDimmer, LoxoneDimmer
from .lights.lightcontroller import LoxoneLightControllerV2
from .lights.switch import LoxoneLightSwitch
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)
DEFAULT_NAME = "Loxone Light Controller V2"
DEFAULT_FORCE_UPDATE = False


class LoxoneLights(StrEnum):
    """Possible loxone light types."""

    UNKNOWN = "unknown"
    SWITCH = "Switch"
    DIMMER = "Dimmer"
    COLORPICKERV2 = "ColorPickerV2"


class ColorPickerTypes(StrEnum):
    RGB = "Rgb"
    LUMITECH = "Lumitech"
    TUNABLEWHITE = "TunableWhite"


#: Mapping of ``details.pickerType`` → picker entity class (PC-43).
#:
#: Newer Miniservers report the picker type as an *integer* (0 = RGB,
#: 1 = kelvin, 2 = kelvin full-range), while older ones (and the
#: LumiTech special case) use the string names in :class:`ColorPickerTypes`.
#: The integer mapping is VERIFY — confirm it against a live Miniserver
#: before relying on it.
PICKER_TYPE_TO_CLASS = {
    0: RGBColorPicker,
    1: TunableWhiteLight,
    2: TunableWhiteLight,
    ColorPickerTypes.RGB: RGBColorPicker,
    ColorPickerTypes.LUMITECH: LumiTech,
    ColorPickerTypes.TUNABLEWHITE: TunableWhiteLight,
}


def picker_class_for(picker_type):
    """Return the light entity class for a ``details.pickerType`` value, or
    ``None`` when the type is unknown.  Handles both the integer and string
    representations; note in particular that ``0`` (RGB) is a valid, falsy
    value (the old truthiness check silently skipped every RGB picker)."""
    return PICKER_TYPE_TO_CLASS.get(picker_type)


# --------------------------------------------------------------------------- #
# WP-6.10 (PS-27): LightsceneRGB
#
# The ``red`` / ``green`` / ``blue`` channel states are authoritative for
# the entity's colour (verified against the live-block shape in
# ``docs/review/2026-09-findings.md``; the separate combined ``color``
# stream is **not** consumed — live check #22 records the assumption).
# --------------------------------------------------------------------------- #

LIGHTSCENE_CHANNELS = ("red", "green", "blue")


def lightscene_channel_value(raw) -> float | None:
    """Normalise a raw LightsceneRGB ``red``/``green``/``blue`` stream value
    (a 0-100 % channel level) to a clamped ``[0.0, 100.0]`` float.

    Accepts numbers and numeric strings (Loxone streams do both); bools,
    non-numbers and non-finite values return ``None`` so one malformed
    feed keeps the entity's last known colour instead of wiping it.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw.strip())
        except ValueError:
            return None
    if not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return None
    return min(100.0, max(0.0, float(raw)))


def lightscene_channel_command(channel: str, value: float) -> str:
    """The outbound command for one LightsceneRGB channel (VERIFY —
    live check #22).

    No structure file, upstream issue or wire capture documents the write
    path of a ``LightsceneRGB`` control; this assumes the plain
    ``<state>/<value>`` command shape the other Loxone controls use, with
    the channel name as state and the 0-100 level as value (e.g.
    ``red/50``).  Flip the whole write path here in one place.
    """
    return f"{channel}/{int(round(value))}"


def lightscene_on_command() -> str:
    """Turn-on command for a LightsceneRGB that was asked to come on with
    no explicit colour (VERIFY — live check #22).

    Assumes the bare ``On`` word, the same shape :class:`TunableWhiteLight`
    sends for a stateless turn-on.
    """
    return "On"


def lightscene_off_command() -> str:
    """Turn-off command for a LightsceneRGB (VERIFY — live check #22).

    Assumes the ``Off`` word, the same shape :class:`LoxoneSwitch` uses
    for its ``turn_off``.
    """
    return "Off"


class LoxoneLightsceneRGB(LoxoneEntity, LightEntity):
    """LightsceneRGB control (WP-6.10, PS-27) as a RGB light.

    The three channel states report the colour (0-100 % each); the
    combined ``color`` stream is deliberately not consumed (the channels
    are authoritative — see live check #22).  A scene over the control's
    ``sceneList``, when populated, is exposed by the select platform.
    """

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_available = False
    _attr_icon = "mdi:lightbulb"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_unique_id = self.uuidAction
        self.type = "LightsceneRGB"
        # HA refuses to render a light that reports no colour mode at all;
        # UNKNOWN until the first channel feed establishes RGB (the
        # ColorPickerV2 lights use the same initial value).
        self._attr_color_mode = ColorMode.UNKNOWN
        self._attr_rgb_color: tuple[int, int, int] | None = None
        states = kwargs.get("states")
        states = states if isinstance(states, dict) else {}
        self._channel_uuids = {name: states.get(name) for name in LIGHTSCENE_CHANNELS}
        self._channel_values: dict[str, float] = {}
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the three channel streams (the `color` stream is not consumed).
        return frozenset(uuid for uuid in self._channel_uuids.values() if isinstance(uuid, str) and uuid)

    def _rgb_color(self) -> tuple[int, int, int]:
        """The channel values recomputed to the 0-255 RGB triplet HA stores."""
        return tuple(int(round(255.0 * self._channel_values.get(name, 0.0) / 100.0)) for name in LIGHTSCENE_CHANNELS)

    @property
    def is_on(self) -> bool:
        return any(value > 0 for value in self._channel_values.values())

    @callback
    def event_handler(self, e: dict) -> None:
        update = False
        for name, uuid in self._channel_uuids.items():
            if isinstance(uuid, str) and uuid in e:
                value = lightscene_channel_value(e[uuid])
                if value is None:
                    # A malformed feed part keeps the channel's last value.
                    continue
                self._channel_values[name] = value
                update = True
        if not update:
            return
        self._attr_rgb_color = self._rgb_color()
        self._attr_color_mode = ColorMode.RGB
        if not self._attr_available:
            self._attr_available = True
        self.async_write_ha_state()

    async def async_turn_off(self, **_kwargs) -> None:
        self._send(lightscene_off_command())
        self.async_schedule_update_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_RGB_COLOR in kwargs and isinstance(kwargs[ATTR_RGB_COLOR], (list, tuple)):
            # Optimistic local model: mirror the requested 0-255 levels into
            # the channel values before the server echoes them back.
            for name, level in zip(LIGHTSCENE_CHANNELS, (int(c) for c in kwargs[ATTR_RGB_COLOR]), strict=True):
                value = lightscene_channel_value(hass_to_lox(max(0, min(255, level))))
                if value is not None:
                    self._channel_values[name] = value
                self._send(lightscene_channel_command(name, value if value is not None else 0.0))
            self._attr_rgb_color = self._rgb_color()
            self._attr_color_mode = ColorMode.RGB
        else:
            # A plain power-on (brightness excluded: the control has no
            # brightness state) asks the Miniserver to restore its scene.
            self._send(lightscene_on_command())
        if not self._attr_available:
            self._attr_available = True
        self.async_schedule_update_ha_state()


class DimmerTypes(StrEnum):
    DIMMER = "Dimmer"
    EIBDIMMER = "EIBDimmer"


async def async_setup_platform(
    _hass: HomeAssistant,
    _config: ConfigType,
    _async_add_entities: AddEntitiesCallback,
    _discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Light Controller."""
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Loxone Light Controller."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    generate_subcontrols = config_entry.options.get("generate_lightcontroller_subcontrols", False)
    loxconfig = miniserver.lox_config.json
    entities = []
    dimmers_without_light_controller = get_all(loxconfig, ["Dimmer", "EIBDimmer"])
    # PC-43: standalone ColorPickerV2 controls (not part of a LightControllerV2)
    # were never created — this platform only walked subControls.
    color_pickers_without_light_controller = get_all(loxconfig, "ColorPickerV2")

    switches = []
    dimmers = []
    color_pickers = []

    for light_controller in get_all(loxconfig, "LightControllerV2"):
        light_controller = add_room_and_cat_to_value_values(loxconfig, light_controller)
        light_controller.update(
            {
                "config_entry": config_entry,
                "async_add_devices": async_add_entities,
            }
        )
        new_light_controller = LoxoneLightControllerV2(**light_controller)
        entities.append(new_light_controller)

        if "subControls" in light_controller:
            for sub_control_uuid in light_controller["subControls"]:
                if sub_control_uuid.find("masterValue") > -1 or sub_control_uuid.find("masterColor") > -1:
                    continue
                sub_control = light_controller["subControls"][sub_control_uuid]
                # Update for all entities
                sub_control = add_room_and_cat_to_value_values(loxconfig, sub_control)
                sub_control.update(
                    {
                        "lightcontroller_id": light_controller.get("uuidAction", None),
                        # PC-04 (extension): share the parent controller's
                        # fixed device payload, so the controller's name and
                        # suggested_area survive no-matter which sub-control
                        # is registered first (sub-controls carry no room).
                        "device_info": new_light_controller._attr_device_info,
                        "lightcontroller_name": light_controller.get("name", None),
                        "config_entry": config_entry,
                        "async_add_devices": async_add_entities,
                        "enabled_default": generate_subcontrols,
                    }
                )

                if sub_control["type"] == LoxoneLights.SWITCH:
                    switches.append(sub_control)

                elif sub_control["type"] == LoxoneLights.DIMMER:
                    dimmers.append(sub_control)

                elif sub_control["type"] == LoxoneLights.COLORPICKERV2:
                    color_pickers.append(sub_control)

                else:
                    _LOGGER.debug("Not supported type found in light controller: %s", sub_control["type"])

    for switch in switches:
        new_switch = LoxoneLightSwitch(**switch)
        entities.append(new_switch)

    for dimmer in dimmers + dimmers_without_light_controller:
        if "async_add_devices" not in dimmer:
            dimmer = add_room_and_cat_to_value_values(loxconfig, dimmer)
            dimmer.update(
                {
                    "config_entry": config_entry,
                    "async_add_devices": async_add_entities,
                }
            )

        if dimmer["type"] == DimmerTypes.DIMMER:
            new_dimmer = LoxoneDimmer(**dimmer)
            entities.append(new_dimmer)
        elif dimmer["type"] == DimmerTypes.EIBDIMMER:
            new_eib_dimmer = EIBDimmer(**dimmer)
            entities.append(new_eib_dimmer)
        else:
            _LOGGER.error("Not implemented Dimmer Type %s", dimmer["type"])

    for color_picker in color_pickers + color_pickers_without_light_controller:
        if "async_add_devices" not in color_picker:
            color_picker = add_room_and_cat_to_value_values(loxconfig, color_picker)
            color_picker.update(
                {
                    "config_entry": config_entry,
                    "async_add_devices": async_add_entities,
                }
            )

        picker_class = picker_class_for(color_picker.get("details", {}).get("pickerType", None))
        if picker_class is None:
            _LOGGER.error(
                "Not implemented Colorpicker Type %s for %s",
                color_picker.get("details", {}).get("pickerType"),
                color_picker.get("name"),
            )
            continue

        entities.append(picker_class(**color_picker))

    # WP-6.10 / PS-27: the LightsceneRGB scene lights are standalone
    # controls (no LightControllerV2 parent).  The red/green/blue channel
    # streams drive the RGB light; a `select` over the control's
    # `sceneList` is created by the select platform when it is populated.
    for scene_light in iter_controls(hass, config_entry, "LightsceneRGB"):
        try:
            entities.append(LoxoneLightsceneRGB(**{**scene_light, "config_entry": config_entry}))
        except Exception:
            _LOGGER.exception("Skipping LightsceneRGB control %s", scene_light.get("name", "?"))

    async_add_entities(entities)
