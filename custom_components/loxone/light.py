import logging
from enum import StrEnum

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .helpers import add_room_and_cat_to_value_values, get_all
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

    async_add_entities(entities)
