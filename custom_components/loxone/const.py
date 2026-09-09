"""
Loxone constants

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

# Loxone constants
from typing import Final

from homeassistant.const import Platform

LOXONE_PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.CLIMATE,
    Platform.ALARM_CONTROL_PANEL,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SCENE,
    Platform.SELECT,
    Platform.TEXT,
]

LOXONE_DEFAULT_PORT = 8080

ERROR_VALUE = -1
DEFAULT_PORT = 8080
DEFAULT_VERIFY_SSL = True
DEFAULT_DELAY_SCENE = 3
DEFAULT_IP = ""

EVENT = "loxone_event"
DOMAIN = "loxone"
LOX_CONFIG = "loxconfig"

SENDDOMAIN = "loxone_send"
SECUREDSENDDOMAIN = "loxone_send_secured"
DEFAULT = ""

ATTR_UUID = "uuid"

ATTR_VALUE = "value"
ATTR_CODE = "code"
ATTR_COMMAND = "command"
ATTR_DEVICE = "device"
ATTR_AREA_CREATE = "create_areas"
ATTR_ENTRY_ID = "entry_id"
DOMAIN_DEVICES = "devices"


def loxone_uuid_signal(config_entry_id: str, uuid: str) -> str:
    """CORE-27: the per-(entry, uuid) dispatcher signal for state fan-out.

    Namespaced by config entry id so state updates of one Miniserver can
    never reach entries of a *second* Miniserver on the same HA instance
    (the pre-fix single global ``loxone_event`` bus event did exactly
    that, and every listener of it -- one per entity, per entry).
    """
    return f"loxone_{config_entry_id}_{uuid}"


def loxone_climate_demand_signal(config_entry_id: str, room_uuid: str) -> str:
    """PS-18: entry-scoped control-list fan-out replacing the global
    ``CLIMATE_EVENT`` bus event.

    A ``LoxoneClimateController`` parses the room states and sends each
    room's ``demand`` (1 = heating, -1 = cooling, 0 = idle) to this signal
    addressed at the room controller's own uuid, instead of firing one
    bus event per linked room that every room-controller entity of every
    entry would receive.
    """
    return f"{loxone_uuid_signal(config_entry_id, room_uuid)}_demand"


# Climate preset names (translatable)
PRESET_SCHEDULE = "schedule"
PRESET_PAUSED_WINDOW = "paused_door_window"

CONF_ACTIONID = "uuidAction"
CONF_SCENE_GEN = "generate_scenes"
CONF_SCENE_GEN_DELAY = "generate_scenes_delay"
CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN = "generate_lightcontroller_subcontrols"
CONF_VERIFY_SSL = "verify_ssl"
# CORE-15: auto-group creation option. New config entries are stamped with
# this in the config flow (default OFF); installed entries never carry the
# key, and an absent key preserves the pre-option behaviour (groups ON).
CONF_GENERATE_GROUPS = "generate_groups"
DEFAULT_GENERATE_GROUPS = False
DEFAULT_FORCE_UPDATE = False

# PS-14: the control-type strings the auto-groups match on — the single
# source of truth used by the platforms (``self.type`` / the ``device_type``
# state attribute) *and* the group table in ``__init__.py``. The old code
# matched literals the platforms never produced (``"analog_sensor"``,
# ``"digital_sensor"``, ``"TimedSwitch"``), so those three auto-groups were
# always empty. Tests (test_contracts.py) cross-check that every table
# entry is emitted by at least one platform class.

# Loxone-only cover feature bits. These are NOT `CoverEntityFeature` values;
# they live far above HA's cover-feature range (highest bit is 128 as of
# 2026.8) so that a future HA release cannot silently claim them (PC-27).
# Kept out of `CoverEntityFeature` itself: platforms OR plain ints in.
SUPPORT_SUN_AUTOMATION = 1 << 12  # 4096
SUPPORT_QUICK_SHADE = 1 << 13  # 8192

SERVICE_ENABLE_SUN_AUTOMATION = "enable_sun_automation"
SERVICE_DISABLE_SUN_AUTOMATION = "disable_sun_automation"
SERVICE_QUICK_SHADE = "quick_shade"

CONF_HVAC_AUTO_MODE = "hvac_auto_mode"

STATE_ON = "on"
STATE_OFF = "off"

DEFAULT_AUDIO_ZONE_V2_PLAY_STATE = -1

THROTTLE_KEEP_ALIVE_TIME = 60

# Control-type strings (PS-14, see above).
DEVICE_TYPE_ANALOG = "Sensor analog"
DEVICE_TYPE_BINARY_SENSOR = "digital_sensor"
DEVICE_TYPE_SWITCH = "Switch"
# The switch platform's original value ("TimeSwitch") is the keeper: it is
# user-visible in the `device_type` attribute and the entities' history.
DEVICE_TYPE_TIMED_SWITCH = "TimeSwitch"
DEVICE_TYPE_PUSHBUTTON = "Pushbutton"
DEVICE_TYPE_JALOUSIE = "Jalousie"
DEVICE_TYPE_GATE = "Gate"
DEVICE_TYPE_WINDOW = "Window"
DEVICE_TYPE_LCV2 = "LightControllerV2"
DEVICE_TYPE_DIMMER = "Dimmer"
DEVICE_TYPE_IRC = "RoomControllerV2"
DEVICE_TYPE_AC = "AcControl"
DEVICE_TYPE_VENTILATION = "Ventilation"
DEVICE_TYPE_SLIDER = "Slider"
DEVICE_TYPE_TEXT_INPUT = "TextInput"

r"""\
cfmt description
(                                  # start of capture group 1
%                                  # literal "%"
(?:                                # first option
(?:[-+0 #]{0,5})                   # optional flags
(?:\d+|\*)?                        # width
(?:\.(?:\d+|\*))?                  # precision
(?:h|l|ll|w|I|I32|I64)?            # size
[cCdiouxXeEfgGaAnpsSZ]             # type
) |                                # OR
%%) 
"""

cfmt = r"(%(?:(?:[-+0 #]{0,5})(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:h|l|ll|w|I|I32|I64)?[cCdiouxXeEfgGaAnpsSZ])|%%)"
