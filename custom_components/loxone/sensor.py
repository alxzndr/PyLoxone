"""
Loxone Sensors

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import json
import logging
import re
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import (
    CONF_STATE_CLASS,
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_UNIT_OF_MEASUREMENT,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfRatio,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import LoxoneEntity
from .const import (
    ATTR_ENTRY_ID,
    ATTR_UUID,
    CONF_ACTIONID,
    DEVICE_TYPE_ANALOG,
    DOMAIN,
    ERROR_VALUE,
    EVENT,
    EVENT_NFC_AUTH,
    THROTTLE_KEEP_ALIVE_TIME,
    loxone_climate_demand_signal,
    loxone_message_signal,
)
from .helpers import (
    add_room_and_cat_to_value_values,
    clean_unit,
    device_info_for,
    get_miniserver_type,
    iter_controls,
    loxone_timestamp,
    software_version_string,
)
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Loxone Sensor"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ACTIONID): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
        vol.Optional(CONF_DEVICE_CLASS): cv.string,
        vol.Optional(CONF_STATE_CLASS): cv.string,
    }
)

# IRoomControllerV2 override-reason codes, as slugs for translation
# (``entity.sensor.loxone.override_reason.state.<slug>``). A single ``unknown``
# slug covers every other/odd code instead of minting a ``"Unknown (n)"``
# display string per code (CORE-22).
OVERRIDE_REASON_SLUGS = {
    0: "none",
    1: "presence",
    2: "window_open",
    3: "comfort_override",
    4: "eco_override",
    5: "eco_plus_override",
    6: "prepare_heat_up",
    7: "prepare_cool_down",
    8: "overridden_by_source",
    14: "fixed",
}
OVERRIDE_REASON_UNKNOWN = "unknown"

# Meter sub-sensor classification (PS-21). A blanket TOTAL_INCREASING on every
# kWh/L-formatted value put "Consumption today" counters and the `totalNeg`
# register in the energy dashboard, where resets show up as spikes.
METER_STATE_CLASSES = {
    "actual": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "total": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    "totalNeg": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    "storage": (SensorDeviceClass.ENERGY, SensorStateClass.MEASUREMENT),
}
METER_FORMAT_KEYS = {
    "actual": "actualFormat",
    "total": "totalFormat",
    "totalNeg": "totalFormat",
    "storage": "storageFormat",
}
METER_NAME_SUFFIX = {
    "actual": "Actual",
    "total": "Total",
    "totalNeg": "Total Neg",
    "storage": "Level",
}

# WP-6.5: the newer metering controls expose the same register set as the
# legacy ``Meter`` (actual power, running totals, storage level), so they
# run the same sub-state loop with the classification table above.  Only
# the registers actually present in a control's ``states`` yield entities,
# so a control with fewer registers simply yields fewer sub-sensors.
METER_FAMILY_TYPES = ("Meter", "EnergyManager", "EnergyManager2", "PowerUnit", "Wallbox")

# #461: the analog sub-readings a PresenceDetector control publishes
# alongside its presence signal, as ``state key -> (entity name, Loxone
# format)``.  Presence detectors without light/sound hardware advertise
# no such state, so only the present states yield a sub-sensor; the
# format fixes the unit (lux / dB), which drives the device class via
# the unit table below (illuminance matches ``lx``, noise gets a plain
# numeric measurement).  WP-6.1 keeps the *presence* device linkage in
# the binary_sensor platform: the sub-sensors here carry the parent
# control's device identifiers, so HA merges them into the same device.
PRESENCE_SUB_SENSOR_SPECS: dict[str, tuple[str, str]] = {
    "illuminance": ("Illuminance", "%.0f lx"),
    "noise": ("Noise", "%.0f dB"),
}
# The model name the binary_sensor platform stamps on the presence
# device (`self.type` = "presence"), so a structure file emits one
# merged device instead of two.
PRESENCE_DEVICE_MODEL = "presence"

# A plain InfoOnlyAnalog that counts total energy/water deserves
# ``TOTAL_INCREASING`` only when its name/category actually says it is a
# meter. Anything else (e.g. "Consumption today") is a resetting value and
# gets MEASUREMENT (PS-21).
METERING_KEYWORDS = ("total", "meter", "zähler", "zaehler", "compteur", "counter")

# WP-6.2 (#515): the command that syncs the Miniserver's Message Center
# entries, addressed at the Message Center control's ``uuidAction``.
# The ``/2`` suffix is the upstream's literal (VERIFY against a live
# Miniserver whether it is a server-side version counter).
MESSAGE_CENTER_GET_ENTRIES_COMMAND = "getEntries/2"
MESSAGE_CENTER_GET_ENTRIES_PREFIX = "getEntries"
# WP-6.2: every repair issue the Message Center mirror creates is
# namespaced per config entry (two Miniservers on one HA instance must
# never share one) and per Message Center entry.
MESSAGE_CENTER_ISSUE_PREFIX = "message_center_"


class LoxoneEntityDescription(SensorEntityDescription, frozen_or_thawed=True):
    """
    Describes a Loxone sensor entity.

    Acts as a classification object: carries matching criteria (which Loxone
    units/keywords trigger this description) and the resulting classification
    (device_class, state_class). Presentation details (actual unit, precision)
    come from the Loxone format string via _attr_* in __init__.
    """

    loxone_format_strings: tuple[str, ...]
    category_keywords: tuple[str, ...] = ()
    name_keywords: tuple[str, ...] = ()


SENSOR_TYPES: tuple[LoxoneEntityDescription, ...] = (
    LoxoneEntityDescription(
        key="temperature",
        loxone_format_strings=(UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    LoxoneEntityDescription(
        key="wind_speed",
        loxone_format_strings=(UnitOfSpeed.KILOMETERS_PER_HOUR,),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.WIND_SPEED,
    ),
    LoxoneEntityDescription(
        key="energy",
        loxone_format_strings=(
            UnitOfEnergy.KILO_WATT_HOUR,
            UnitOfEnergy.WATT_HOUR,
            UnitOfEnergy.MEGA_WATT_HOUR,
        ),
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    LoxoneEntityDescription(
        key="power",
        loxone_format_strings=(UnitOfPower.WATT, UnitOfPower.KILO_WATT),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    LoxoneEntityDescription(
        key="volume_flow_rate",
        loxone_format_strings=(
            UnitOfVolumeFlowRate.LITERS_PER_HOUR,
            UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        ),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
    ),
    LoxoneEntityDescription(
        key="water",
        loxone_format_strings=(UnitOfVolume.LITERS,),
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.WATER,
    ),
    LoxoneEntityDescription(
        key="illuminance",
        loxone_format_strings=(LIGHT_LUX, "Lx", "lx", "lux"),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ILLUMINANCE,
    ),
    LoxoneEntityDescription(
        key="carbon_dioxide",
        loxone_format_strings=(UnitOfRatio.PARTS_PER_MILLION,),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CO2,
    ),
    LoxoneEntityDescription(
        key="humidity",
        loxone_format_strings=(PERCENTAGE,),
        category_keywords=("vlhkost", "humidity", "feucht", "humidité"),
        name_keywords=("vlhkost", "humidity", "feucht", "humidité"),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.HUMIDITY,
    ),
    LoxoneEntityDescription(
        key="battery",
        loxone_format_strings=(PERCENTAGE,),
        name_keywords=("batt", "akku", "battery"),
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    ),
)

UNAMBIGUOUS_UNITS: frozenset[str] = frozenset(
    u
    for desc in SENSOR_TYPES
    if not desc.category_keywords and not desc.name_keywords
    for u in desc.loxone_format_strings
)
"""Units that map to exactly one device class without needing keyword disambiguation."""

# The Loxone format spec types that carry a *numeric* reading. ``s`` is the
# only string-typed format; state_class may only be advertised for numeric
# values, or HA raises a ValueError when a text string arrives (PS-09).
_NUMERIC_EXCLUDED_TYPES = frozenset({"s", ""})


def _is_numeric_format(lox_format: Any) -> bool:
    """True when the Loxone format string carries a numeric value type."""
    if not isinstance(lox_format, str):
        return False
    match = re.search(r"%[-+0 #]*\d*(?:\.\d*)?[a-zA-Z%]+", lox_format)
    if not match:
        return False
    return match.group(0)[-1] not in _NUMERIC_EXCLUDED_TYPES


def _analog_value(value: Any) -> Any:
    """
    Normalise a raw InfoOnlyAnalog stream value for ``native_value``.

    ``None`` and the Miniserver's error sentinel (``ERROR_VALUE`` == -1)
    mean "no reading" and map to ``None`` (HA shows ``unknown``);
    everything else is passed through unchanged (PS-09).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) == ERROR_VALUE:
        return None
    return value


def _metering_indicated(name: str, category: str) -> bool:
    """True when the name/category names a running meter (PS-21)."""
    lowered = f"{name} {category}".lower()
    return any(kw in lowered for kw in METERING_KEYWORDS)


# --------------------------------------------------------------------------- #
# WP-6.2 (#515): Message Center -> repair issues
# --------------------------------------------------------------------------- #


def _as_int_severity(value) -> int:
    """
    Normalise a Message Center entry ``severity`` (int, integral float,
    numeric string) to the int the severity classes are defined on.
    Non-numeric junk maps to 0 (reported below any real severity), so
    a corrupt entry can never escalate the summary.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


def message_center_summary(entries) -> tuple[dict[str, int], int]:
    """
    The (severity -> count) tallies and the maximum severity of the
    *active* Message Center entries.

    Historic entries are already resolved — the upstream port deletes
    their repair issue instead of counting them.  Corrupt entries
    (non-dicts, non-numeric severity) are skipped rather than aborting
    the whole sync.  Returns ``({}, 0)`` for an empty/absent list.
    """
    if not isinstance(entries, list):
        entries = []
    counts: dict[str, int] = {}
    max_severity = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("isHistoric"):
            continue
        severity = _as_int_severity(entry.get("severity"))
        counts[str(severity)] = counts.get(str(severity), 0) + 1
        max_severity = max(max_severity, severity)
    return counts, max_severity


def message_center_issue_severity(severity: int) -> ir.IssueSeverity:
    """
    The HA issue severity for a Message Center severity class.

    Exact port of the upstream mapping (#515): ``> 3`` is CRITICAL,
    ``> 2`` is ERROR, everything else (1 = warning, 0/absent) is
    WARNING.  (0 = informational is not a repair.)
    """
    severities = _as_int_severity(severity)
    if severities > 3:
        return ir.IssueSeverity.CRITICAL
    if severities > 2:
        return ir.IssueSeverity.ERROR
    return ir.IssueSeverity.WARNING


def message_center_entry_timestamp(timestamps) -> datetime | None:
    """
    The *occurred at* moment of a Message Center entry, or None.

    The entry ``timestamps`` field is a list of Unix epoch seconds
    (hand-derived expected values in ``tests/test_message_center.py``);
    the first element is the original occurrence.  Guards: non-list
    values, empty lists and non-numeric heads return None instead of
    crashing the sync.
    """
    if not isinstance(timestamps, (list, tuple)) or not timestamps:
        return None
    value = timestamps[0]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return dt_util.utc_from_timestamp(value)
    except OverflowError, OSError, ValueError:
        return None


def _is_get_entries_response(control) -> bool:
    """
    True when a state message's ``control`` field acknowledges a
    Message Center ``getEntries`` request addressed at this sensor.

    Tolerant of the two shapes the websocket layer can produce for a
    command response (a dict of uuid -> command list, or the raw command
    string).  The command value must contain the ``getEntries`` token so
    unrelated control echoes do not replay another Miniserver's state.
    """
    if isinstance(control, str):
        return MESSAGE_CENTER_GET_ENTRIES_PREFIX in control
    if not isinstance(control, dict):
        return False
    for address in control.values():
        try:
            addresses = [address] if isinstance(address, str) else list(address)
        except TypeError:
            continue
        for item in addresses:
            if isinstance(item, str) and MESSAGE_CENTER_GET_ENTRIES_PREFIX in item:
                return True
    return False


def message_center_issue_id(config_entry_id: str, entry_uuid: str) -> str:
    """
    The per-(entry, message) repair issue id (WP-6.2).

    Namespaced by config entry id: two Miniservers on one HA instance
    must never share an issue (same convention as the CORE-30
    per-entry ids in ``__init__.py``).  The Message Center entry UUID
    is stable for the lifetime of the message on the Miniserver.
    """
    return f"{MESSAGE_CENTER_ISSUE_PREFIX}{config_entry_id}_{entry_uuid}"


def presence_sub_sensor_kwargs(control: dict, config_entry) -> list[dict]:
    """
    ``LoxoneSensor`` kwargs for the illuminance/noise sub-states of a
    PresenceDetector control (#461).

    One sub-sensor dict per advertised sub-state: short entity name
    (the device is named after the parent control), the *parent*
    control's uuid as ``parent_id``, and device info built from the
    parent's identifiers so the sub-sensors land on the same device as
    the presence binary sensor (same house pattern as the Meter and
    IRoomControllerV2 sub-sensors).  Every ``states``/``details`` lookup
    is guarded with ``.get()``: a structure file without the states
    yields *no* sub-sensors instead of aborting the platform.  The
    return order is the insertion order of ``PRESENCE_SUB_SENSOR_SPECS``.
    """
    states = control.get("states")
    if not isinstance(states, dict):
        return []
    uuid_action = control.get("uuidAction")
    if not isinstance(uuid_action, str) or not uuid_action:
        # PC-05: device_info_for needs the parent uuid for the shared
        # device identifiers; without it there is no device to attach to.
        return []
    room = control.get("room", "")
    kwargs_list: list[dict] = []
    for state_name, (name, default_format) in PRESENCE_SUB_SENSOR_SPECS.items():
        uuid = states.get(state_name)
        if not isinstance(uuid, str) or not uuid:
            continue
        kwargs_list.append(
            {
                "parent_id": uuid_action,
                "uuidAction": uuid,
                "type": "analog",
                "room": room,
                "cat": control.get("cat", ""),
                "name": name,
                "details": {"format": default_format},
                "device_info": device_info_for(
                    config_entry, uuid_action, control.get("name", ""), PRESENCE_DEVICE_MODEL, room
                ),
                "config_entry": config_entry,
            }
        )
    return kwargs_list


def meter_device_model(control: dict) -> str:
    """
    Device model string for a Meter-family control (WP-6.5).

    A legacy ``Meter`` may carry a free-form ``details.type`` (e.g.
    ``"Module Meter"``) which historically produced ``"<Type> Meter"``;
    the other family members are modelled by their control type name.
    """
    control_type = control.get("type")
    if control_type == "Meter":
        details = control.get("details")
        legacy = details.get("type") if isinstance(details, dict) else None
        if isinstance(legacy, str) and legacy:
            return legacy.capitalize() + " Meter"
        return "Meter"
    if isinstance(control_type, str) and control_type:
        return control_type
    return "Meter"


def meter_device_info(control: dict, config_entry) -> dict | None:
    """
    Shared device info for the registers of one Meter-family control
    (WP-6.5).

    All sub-registers of one control carry the parent control's own
    ``(DOMAIN, uuidAction)`` identifier and name/model, so the device
    registry merges them into a single device (PS-20, same house
    pattern as the IRoomControllerV2 and presence sub-sensors).  Without
    a usable ``uuidAction`` this returns ``None`` and the register falls
    back to its own device.
    """
    uuid_action = control.get("uuidAction")
    if not isinstance(uuid_action, str) or not uuid_action:
        return None
    return device_info_for(
        config_entry,
        uuid_action,
        control.get("name", ""),
        meter_device_model(control),
        control.get("room", ""),
    )


def meter_sub_sensor_kwargs(control: dict, config_entry) -> list[dict]:
    """
    ``LoxoneMeterSensor`` kwargs for the registers of a Meter-family
    control (WP-6.5): ``Meter``, ``EnergyManager``, ``EnergyManager2``,
    ``PowerUnit`` and ``Wallbox``.

    One sub-sensor dict per *advertised* register: only the registers
    present (as a state uuid) in the control's ``states`` yield kwargs.
    Every ``states``/``details`` lookup is guarded with ``.get()``: a
    truncated structure file yields fewer (or no) sub-sensors instead of
    aborting the platform (PS-08).  The return order is the insertion
    order of ``METER_STATE_CLASSES``.
    """
    states = control.get("states")
    if not isinstance(states, dict):
        return []
    details = control.get("details")
    details = details if isinstance(details, dict) else {}
    device_info = meter_device_info(control, config_entry)
    kwargs_list: list[dict] = []
    for state_key, (device_class, state_class) in METER_STATE_CLASSES.items():
        uuid = states.get(state_key)
        if not isinstance(uuid, str) or not uuid:
            continue
        kwargs_list.append(
            {
                "device_info": device_info,
                "parent_id": control.get("uuidAction", ""),
                "uuidAction": uuid,
                "type": "analog",
                "room": control.get("room", ""),
                "cat": control.get("cat", ""),
                # WP-5.1: short sub-entity name — the device is named
                # after the parent control, so no control-name prefix
                # (CORE-26).
                "name": METER_NAME_SUFFIX[state_key],
                "details": {"format": details.get(METER_FORMAT_KEYS[state_key], "%.1f")},
                "device_class": device_class,
                "state_class": state_class,
                "config_entry": config_entry,
            }
        )
    return kwargs_list


# --------------------------------------------------------------------------- #
# WP-6.10 (PS-27): NfcCodeTouch
#
# A Loxone NFC Code Touch is an access-control reader.  The ``lastcode``
# and ``lasttag`` states identify *how* someone authenticated (a
# credential) and are never exposed — not as a sensor state, not as an
# attribute, not in the event payload (the same treatment the
# Miniserver serial gets from CORE-08).  Only *who* (``lastuser``) and
# *when* (``codeDate``) are surfaced; the per-authentication
# ``loxone_nfc_auth`` bus event is the primary exposure (an access
# device is interesting at the moment it authenticates, not as a
# polled "last user" string).
# --------------------------------------------------------------------------- #

NFC_DEVICE_MODEL = "NfcCodeTouch"


def nfc_code_date(value: Any) -> datetime | None:
    """
    Normalise a raw NfcCodeTouch ``codeDate`` stream value to an aware UTC datetime.

    Intended semantics (**VERIFY** — live check #21 in
    ``docs/review/LIVE-MINISERVER-CHECKS.md``): ``codeDate`` carries the
    moment the last code was entered.  The wire format is not documented
    in anything we hold, so the parse tries, in order:

    1. a Loxone-epoch millisecond counter (the same epoch family the
       Message Center's ``changed`` stream uses —
       :func:`loxone_timestamp`), also as an all-digit string;
    2. a ``2026-09-10 12:34:56`` / ISO-8601 string, read as UTC when
       it carries no offset.

    Anything unparseable returns ``None`` so the caller keeps its last
    known value instead of going silent on a corrupt feed.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return loxone_timestamp(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.lstrip("-").isdigit():
            return loxone_timestamp(int(text))
        parsed = dt_util.parse_datetime(text)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.UTC)
        return dt_util.as_utc(parsed)
    return None


def nfc_auth_event_payload(*, control: dict, user: Any, code_date: Any, entry_id: str | None = None) -> dict:
    """
    The bus-event data of one NfcCodeTouch authentication (PS-27).

    The automation receives *who* (``user`` = the ``lastuser`` state)
    and *when* (``code_date``: the parsed ISO-8601 UTC instant, falling
    back to the raw string when the value is not parseable) plus the
    control's identity and the owning config entry.  ``lastcode`` /
    ``lasttag`` feed in nowhere: the credential of an authentication is
    never part of this payload (PS-27 credential rule).
    """
    parsed = nfc_code_date(code_date)
    payload = {
        ATTR_UUID: control.get("uuidAction", ""),
        "name": control.get("name", ""),
        "user": None if user in (None, "") else str(user),
        # ISO-8601 (aware UTC) for automation use, raw value when the
        # format is not one this integration can parse.
        "code_date": parsed.isoformat() if parsed is not None else (None if code_date is None else str(code_date)),
    }
    if entry_id is not None:
        payload[ATTR_ENTRY_ID] = entry_id
    return payload


class LoxoneNfcCodeTouchSensor(LoxoneEntity, SensorEntity):
    """
    NfcCodeTouch (PS-27): who last authenticated, and the per-authentication
    ``loxone_nfc_auth`` bus event.

    The state is the ``lastuser`` value (a credential-free "who").  The
    entity also subscribes to ``codeDate``: every *change* of it fires
    the authentication event with the current ``lastuser``.  The
    ``lastcode`` / ``lasttag`` states are never consumed.
    """

    _attr_icon = "mdi:identifier"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = NFC_DEVICE_MODEL
        states = kwargs.get("states")
        states = states if isinstance(states, dict) else {}
        self._lastuser_uuid = states.get("lastuser") if isinstance(states.get("lastuser"), str) else None
        self._code_date_uuid = states.get("codeDate") if isinstance(states.get("codeDate"), str) else None
        self._attr_native_value: str | None = None
        self._last_code_date_raw = None
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the lastuser stream (the state) plus codeDate (the event trigger).
        return frozenset(uuid for uuid in (self._lastuser_uuid, self._code_date_uuid) if uuid)

    def control_identity(self) -> dict:
        """The ``uuidAction``/``name`` of the parent control, for the event payload."""
        return {"uuidAction": self.uuidAction, "name": self._lox_name}

    @callback
    def event_handler(self, e: dict) -> None:
        if self._lastuser_uuid and self._lastuser_uuid in e:
            user = e[self._lastuser_uuid]
            self._attr_native_value = None if user in (None, "") else str(user)
            self.async_write_ha_state()
        if self._code_date_uuid and self._code_date_uuid in e and e[self._code_date_uuid] != self._last_code_date_raw:
            self._last_code_date_raw = e[self._code_date_uuid]
            self._fire_auth_event(e[self._code_date_uuid])

    def _fire_auth_event(self, code_date_raw: Any) -> None:
        if self.hass is None:
            return
        coordinator = self._connection_coordinator()
        entry_id = coordinator.config_entry.entry_id if coordinator is not None else None
        self.hass.bus.async_fire(
            EVENT_NFC_AUTH,
            nfc_auth_event_payload(
                control=self.control_identity(),
                user=self._attr_native_value,
                code_date=code_date_raw,
                entry_id=entry_id,
            ),
        )

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


class LoxoneNfcCodeDateSensor(LoxoneEntity, SensorEntity):
    """
    NfcCodeTouch (PS-27): when the last code was entered (diagnostic).

    A TIMESTAMP sensor fed by the ``codeDate`` stream through
    :func:`nfc_code_date`; unparseable values keep the last known instant.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = NFC_DEVICE_MODEL
        # WP-5.1: short sub-entity name; the device is named after the
        # parent control (CORE-26).
        self._attr_name = "Code Date"
        self._attr_unique_id = f"{self.uuidAction}-code_date"
        states = kwargs.get("states")
        states = states if isinstance(states, dict) else {}
        self._code_date_uuid = (
            states.get("codeDate") if isinstance(states.get("codeDate"), str) and states.get("codeDate") else None
        ) or self.uuidAction
        self._attr_native_value: datetime | None = None
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the codeDate stream (falls back to the control's action uuid).
        return frozenset({self._code_date_uuid, self.uuidAction})

    @callback
    def event_handler(self, e: dict) -> None:
        parsed = None
        if self._code_date_uuid in e:
            parsed = nfc_code_date(e[self._code_date_uuid])
        if self.uuidAction in e and self.uuidAction != self._code_date_uuid:
            # Fallback wiring (no codeDate stream in the structure file).
            parsed = nfc_code_date(e[self.uuidAction])
        if parsed is None:
            # A malformed value keeps the last known instant.
            return
        self._attr_native_value = parsed
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


class LoxoneNfcDeviceStateSensor(LoxoneEntity, SensorEntity):
    """NfcCodeTouch (PS-27): the raw ``deviceState`` register (diagnostic)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = NFC_DEVICE_MODEL
        self._attr_name = "Device State"
        self._attr_unique_id = f"{self.uuidAction}-device_state"
        states = kwargs.get("states")
        states = states if isinstance(states, dict) else {}
        self._device_state_uuid = (
            states.get("deviceState")
            if isinstance(states.get("deviceState"), str) and states.get("deviceState")
            else None
        ) or self.uuidAction
        self._attr_native_value: str | None = None
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the deviceState stream (falls back to the control's action uuid).
        return frozenset({self._device_state_uuid, self.uuidAction})

    @callback
    def event_handler(self, e: dict) -> None:
        for key in (self._device_state_uuid, self.uuidAction):
            if key in e:
                value = e[key]
                self._attr_native_value = None if value is None else str(value)
                self.async_write_ha_state()
                break

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


def match_sensor_description(
    unit: str,
    name: str = "",
    category: str = "",
) -> LoxoneEntityDescription | None:
    """
    Find the first matching sensor description for a Loxone sensor.

    Unambiguous units (°C, kWh, ppm, …) match immediately.
    Ambiguous units (%) require a keyword hit in name or category.
    Returns None if no description matches.
    """
    name_lower = name.lower()
    cat_lower = category.lower()
    for desc in SENSOR_TYPES:
        if unit not in desc.loxone_format_strings:
            continue
        if not desc.category_keywords and not desc.name_keywords:
            return desc
        cat_match = any(kw in cat_lower for kw in desc.category_keywords)
        name_match = any(kw in name_lower for kw in desc.name_keywords)
        if cat_match or name_match:
            return desc
    return None


async def async_setup_platform(
    _hass: HomeAssistant,
    config: ConfigType,
    async_add_devices: AddEntitiesCallback,
    _discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Sensor from yaml"""
    # Devices from yaml
    if config:
        # Setup all Sensors in Yaml-File
        new_sensor = LoxoneCustomSensor(**config)
        async_add_devices([new_sensor], update_before_add=True)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    miniserver = get_miniserver_from_hass(hass, config_entry)

    loxconfig = miniserver.lox_config.json
    if not isinstance(loxconfig, dict):
        # A structure file that failed to parse/degrade to None: the
        # keep-alive sensor still comes up, every structure-driven
        # sensor is skipped (the old code crashed on `in loxconfig`).
        _LOGGER.error("No LoxAPP3 structure file for %s; only the keep-alive sensor is set up", config_entry.entry_id)
        loxconfig = {}

    # PS-20: the keep-alive and version sensors belong to the Miniserver
    # host device (identifiers = (DOMAIN, serial)), not to no device at
    # all.  Skipped (as before) when the structure file has no serial /
    # software version.
    ms_device_info: DeviceInfo | None = None
    if miniserver.serial:
        ms_device_info = device_info_for(
            config_entry,
            miniserver.serial,
            miniserver.name,
            get_miniserver_type(miniserver.miniserver_type),
        )
    entities: list[Any] = [LoxoneKeepAliveSensor(miniserver.serial, ms_device_info)]

    if "softwareVersion" in loxconfig:
        entities.append(LoxoneVersionSensor(miniserver.serial, loxconfig["softwareVersion"], ms_device_info))

    for sensor in iter_controls(hass, config_entry, "InfoOnlyAnalog"):
        try:
            sensor.update({"type": "analog", "config_entry": config_entry})
            entities.append(LoxoneSensor(**sensor))
        except Exception:
            # One bad control must not abort the whole sensor platform
            # (PS-08).
            _LOGGER.exception("Skipping InfoOnlyAnalog control %s", sensor.get("name", "?"))

    for sensor in iter_controls(hass, config_entry, ["TextInput", "InfoOnlyText"]):
        try:
            sensor.update({"config_entry": config_entry})
            entities.append(LoxoneTextSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping %s control %s", sensor.get("type", "TextInput"), sensor.get("name", "?"))

    # WP-6.5: the Meter family (Meter + EnergyManager/EnergyManager2/
    # PowerUnit/Wallbox) all expose the same register set, so one loop
    # over ``METER_FAMILY_TYPES`` and the pure ``meter_sub_sensor_kwargs``
    # helper creates every register.
    for sensor in iter_controls(hass, config_entry, list(METER_FAMILY_TYPES)):
        _LOGGER.debug("Found Meter-family control: %s", sensor.get("name"))
        try:
            for subsensor in meter_sub_sensor_kwargs(sensor, config_entry):
                entities.append(LoxoneMeterSensor(**subsensor))
        except Exception:
            # One bad control must not abort the whole sensor platform
            # (PS-08).
            _LOGGER.exception("Skipping %s control %s", sensor.get("type", "Meter"), sensor.get("name", "?"))

    # #461: PresenceDetector illuminance/noise sub-sensors.  The analog
    # sub-readings live on the sensor platform (a LoxoneSensor added via
    # the binary_sensor platform would be pinned to the *binary_sensor*
    # domain); they share the parent control's device identifiers, so
    # the device registry keeps them on the presence device.
    for sensor in iter_controls(hass, config_entry, "PresenceDetector"):
        try:
            for sub in presence_sub_sensor_kwargs(sensor, config_entry):
                entities.append(LoxoneSensor(**sub))
        except Exception:
            # One bad control must not abort the whole sensor platform
            # (PS-08).
            _LOGGER.exception("Skipping PresenceDetector control %s", sensor.get("name", "?"))

    # WP-6.6 / PS-26: Tracker controls report their entries as a JSON
    # list on the ``entries`` state (pattern: LoxoneClimateController).
    # recursive=True — real structure files also nest a Tracker under an
    # Alarm's subControls (its ``sensors`` state), not just top level.
    for sensor in iter_controls(hass, config_entry, "Tracker", recursive=True):
        try:
            sensor.update({"config_entry": config_entry})
            entities.append(LoxoneTrackerSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping Tracker control %s", sensor.get("name", "?"))

    # WP-6.10 / PS-27: the NfcCodeTouch access reader — a lastuser sensor
    # plus diagnostic codeDate (timestamp) and deviceState sensors, all on
    # the control's device.  The lastuser sensor fires the
    # ``loxone_nfc_auth`` bus event on every codeDate change; ``lastcode``
    # / ``lasttag`` are credentials and are never exposed (PS-27).
    for sensor in iter_controls(hass, config_entry, "NfcCodeTouch"):
        try:
            sensor.update({"config_entry": config_entry})
            entities.append(LoxoneNfcCodeTouchSensor(**sensor))
            entities.append(LoxoneNfcCodeDateSensor(**sensor))
            entities.append(LoxoneNfcDeviceStateSensor(**sensor))
        except Exception:
            _LOGGER.exception("Skipping NfcCodeTouch control %s", sensor.get("name", "?"))

    # Climate controller demand sensors
    for ctrl_type in ("ClimateController", "ClimateControllerUS"):
        for ctrl in iter_controls(hass, config_entry, ctrl_type):
            try:
                ctrl_kwargs = {**ctrl, "type": "climate_controller", "hass": hass, "config_entry": config_entry}
                entities.append(LoxoneClimateController(**ctrl_kwargs))
            except Exception:
                _LOGGER.exception("Skipping %s control %s", ctrl_type, ctrl.get("name", "?"))

    # IRoomControllerV2 sub-sensors: override reason + comfort temperatures
    for irc in iter_controls(hass, config_entry, "IRoomControllerV2"):
        try:
            states = irc.get("states", {})
            device_info = device_info_for(
                config_entry, irc["uuidAction"], irc["name"], "RoomControllerV2", irc.get("room", "")
            )

            if "overrideReason" in states:
                entities.append(
                    LoxoneRoomControllerOverrideSensor(
                        # WP-5.1: short sub-entity name — the device is named
                        # after the room controller (CORE-26).
                        name="Override Reason",
                        uuid=states["overrideReason"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )

            if "comfortTemperature" in states:
                entities.append(
                    LoxoneRoomControllerTemperatureSensor(
                        name="Comfort Temperature",
                        uuid=states["comfortTemperature"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )

            if "comfortTemperatureCool" in states:
                entities.append(
                    LoxoneRoomControllerTemperatureSensor(
                        name="Comfort Temperature (Cool)",
                        uuid=states["comfortTemperatureCool"],
                        device_info=device_info,
                        parent_uuid=irc["uuidAction"],
                    )
                )
        except Exception:
            _LOGGER.exception("Skipping IRoomControllerV2 control %s", irc.get("name", "?"))

    # WP-6.2 (#515): the Miniserver's Message Center.  The structure file
    # carries it as a top-level ``messageCenter`` block (one control per
    # key, consumed by LoxoneMessageCenterSensor) instead of under
    # ``controls`` (it is command-address state, not a polled entity).  One
    # severity-summary diagnostic sensor per control; the Miniserver's
    # active entries are mirrored as repair issues.
    message_center = loxconfig.get("messageCenter")
    if isinstance(message_center, dict):
        for key, control in message_center.items():
            if not isinstance(control, dict):
                continue
            try:
                # A copy: LoxoneEntity setattr's every kwarg onto the entity,
                # and room/cat are resolved in place — the shared structure
                # file must not be mutated on platform construction.
                ctrl_kwargs = dict(control)
                add_room_and_cat_to_value_values(loxconfig, ctrl_kwargs)
                ctrl_kwargs["config_entry"] = config_entry
                entities.append(LoxoneMessageCenterSensor(**ctrl_kwargs))
            except Exception:
                _LOGGER.exception("Skipping Message Center control %s", key)

    # WP-6.2 (#515): the Miniserver's global notification text stream.
    global_states = loxconfig.get("globalStates")
    notifications_uuid = global_states.get("notifications") if isinstance(global_states, dict) else None
    if isinstance(notifications_uuid, str) and notifications_uuid:
        entities.append(LoxoneNotificationsSensor(notifications_uuid, ms_device_info))

    # CORE-17: the old code subscribed to an ``async_signal_new_device``
    # signal that no code path ever sent and leaked the unsubscribe on
    # ``MiniServer.listeners`` (never iterated).  Sensors are created
    # exclusively from the structure file.
    async_add_entities(entities, update_before_add=True)


class LoxoneCustomSensor(LoxoneEntity, SensorEntity):
    def __init__(self, **kwargs):
        # Device-less (YAML) entry: the entity carries the control name
        # itself (there is no device to inherit it from); an unnamed
        # sensor falls back to the default name.
        name = kwargs.pop("name", None)
        self._attr_state_class = kwargs.pop("state_class", None)
        self._attr_device_class = kwargs.pop("device_class", None)
        self._attr_native_unit_of_measurement = kwargs.pop("unit_of_measurement", None)
        self._attr_native_value = None  # Initialize state
        # Must be after the kwargs.pop functions!
        super().__init__(**kwargs)
        self._attr_name = name or DEFAULT_NAME
        # CORE-26: the unique id is set as an attribute (not via the
        # deleted ``cached_property`` override).  A YAML sensor without a
        # name still gets a usable unique id (PS-07): the uuidAction
        # alone, or "uuidAction-name" when a name is given.  Done after
        # ``super()`` because the base constructor sets ``_attr_unique_id``
        # from the bare uuidAction.
        uuid = kwargs.get("uuidAction")
        self._attr_unique_id = f"{uuid}-{name}" if (uuid is not None and name) else uuid

    @callback
    def event_handler(self, e):
        if self.uuidAction in e:
            data = e[self.uuidAction]
            if isinstance(data, (list, dict)):
                data = str(data)
                if len(data) >= 255:
                    self._attr_native_value = data[:255]
                else:
                    self._attr_native_value = data
            else:
                self._attr_native_value = data

            self.async_write_ha_state()

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        if self._attr_native_unit_of_measurement in ["None", "none", "-"]:
            return None
        return self._attr_native_unit_of_measurement

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {**self._attr_extra_state_attributes}


class LoxoneKeepAliveSensor(LoxoneEntity, SensorEntity):
    _attr_name = "Loxone Last Keep Alive Message"
    _attr_icon = "mdi:information-outline"
    _attr_unique_id = "loxone_keep_alive_sensor_uuid"
    _attr_device_class = SensorDeviceClass.TIMESTAMP  # tell HA this is a timestamp
    # PS-20: diagnostic on the Miniserver device, hidden in the UI by
    # default (it is a connection heartbeat, not a measurement).
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, miniserver_serial, device_info: DeviceInfo | None = None, **kwargs):
        super().__init__(**kwargs)
        self._miniserver_serial = miniserver_serial
        # CORE-26: the per-instance unique id replaces the deleted
        # ``cached_property`` override (same string, stored as an
        # ``_attr_unique_id`` attribute).
        self._attr_unique_id = f"{self._miniserver_serial}-loxone_keep_alive_sensor_uuid"
        # PS-20: attach to the Miniserver host device (identifiers
        # (DOMAIN, serial)); a structure file without a serial yields
        # ``device_info is None`` and a device-less entity (no
        # ``(DOMAIN, None)`` identifier).
        if device_info is not None:
            self._attr_device_info = device_info
        self._attr_native_value = None

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the special keep-alive stream injected by the coordinator.
        return frozenset({"keep_alive"})

    @callback
    def event_handler(self, e):
        if e.get("keep_alive") == "received":
            now = dt_util.utcnow()
            # only update if at least 60 seconds passed since last update
            if self._attr_native_value is not None:
                time_since_last = (now - self._attr_native_value).total_seconds()
                if time_since_last < THROTTLE_KEEP_ALIVE_TIME:
                    # too soon, skip this update
                    return

            # update the timestamp
            self._attr_native_value = now
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {**self._attr_extra_state_attributes}


class LoxoneVersionSensor(LoxoneEntity, SensorEntity):
    _attr_name = "Loxone Software Version"
    _attr_icon = "mdi:information-outline"
    _attr_unique_id = "loxone_software_version_uuid"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, miniserver_serial, version, device_info: DeviceInfo | None = None, **kwargs):
        super().__init__(**kwargs)
        self._miniserver_serial = miniserver_serial
        # CORE-26: the per-instance unique id replaces the deleted
        # ``cached_property`` override (same string, stored as an
        # ``_attr_unique_id`` attribute).
        self._attr_unique_id = f"{self._miniserver_serial}-loxone_software_version_uuid"
        # PS-20: ``software_version_string`` handles list-form *and*
        # string-form versions (the old join split the string into
        # characters); an unusable value stays ``None`` (HA renders
        # unknown) instead of the literal string "unknown".
        parsed = software_version_string(version)
        self._attr_native_value = parsed or None
        if device_info is not None:
            self._attr_device_info = device_info


class LoxoneTextSensor(LoxoneEntity, SensorEntity):
    """Representation of a Text Sensor."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # WP-6.6: writable TextInput vs read-only InfoOnlyText — both
        # report the ``text`` state, but an InfoOnlyText must accept no
        # write command (the control is an output).
        self.type = "InfoOnlyText" if kwargs.get("type") == "InfoOnlyText" else "TextInput"
        self._state = None
        self._state_uuid = self.states.get("text") or self.uuidAction
        # CORE-20 / device link: a fresh device built from the control's
        # own identity (previously no device at all).
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, kwargs.get("room", "")
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the ``text`` state stream (falls back to uuidAction).
        return frozenset({self._state_uuid, self.uuidAction})

    @callback
    def event_handler(self, e):
        if self._state_uuid in e:
            self._state = _analog_value(e[self._state_uuid])
            if self._state is not None:
                self._state = str(self._state)
            self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    async def async_set_value(self, value):
        """Set new value."""
        if self.type == "InfoOnlyText":
            # Read-only control: a write would raise on the Miniserver;
            # refuse it here instead.
            _LOGGER.warning("Ignoring write to read-only InfoOnlyText '%s'", self._lox_name)
            return
        self._send(f"{value}")
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


def tracker_entries(raw: Any) -> list[str] | None:
    """
    Normalise a Tracker control's raw ``entries`` stream value (WP-6.6).

    Intended semantics (VERIFY against a live Miniserver before this is
    assumed right): the stream pushes a JSON array of names/ids — e.g.
    the sensor names an Alarm's ``sensors`` tracker currently holds —
    delivered as an already-parsed list or as the JSON string of it.
    Returns the entries coerced to strings in order.  Returns ``None``
    for a missing, empty or unparseable payload so the caller keeps its
    last known list.  Non-scalar entries (lists/dicts) are dropped.
    """
    value: Any = raw
    if isinstance(value, str):
        text = value.strip()
        if not text.startswith("["):
            return None
        try:
            value = json.loads(text)
        except ValueError:
            return None
    if not isinstance(value, (list, tuple)):
        return None
    return [str(item) for item in value if isinstance(item, (str, int, float, bool))]


class LoxoneTrackerSensor(LoxoneEntity, SensorEntity):
    """
    Tracker control (WP-6.6, PS-26): its ``entries`` JSON list rendered
    as a comma-joined name summary, the entry list as extra attributes
    (pattern: ``LoxoneClimateController``'s JSON-list handling).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = "Tracker"
        states = kwargs.get("states")
        self._entries_uuid = (
            states.get("entries") if isinstance(states, dict) and isinstance(states.get("entries"), str) else None
        ) or self.uuidAction
        self._entries: list[str] = []
        self._attr_native_value: str | None = None
        # CORE-20: a fresh device built from the control's own identity.
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, kwargs.get("room", "")
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the ``entries`` stream (falls back to uuidAction).
        return frozenset({self._entries_uuid, self.uuidAction})

    @callback
    def event_handler(self, e):
        if self._entries_uuid in e:
            parsed = tracker_entries(e[self._entries_uuid])
            if parsed is None:
                # A malformed / absent payload keeps the last list.
                return
            self._entries = parsed
            # An empty tracker reads as unknown (HA rejects an empty
            # sensor state string).
            self._attr_native_value = ", ".join(self._entries) if self._entries else None
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "entries": list(self._entries),
            "count": len(self._entries),
            "state_uuid": self._entries_uuid,
            "device_type": self.type,
        }


class LoxoneSensor(LoxoneEntity, SensorEntity):
    """Representation of a Loxone Sensor."""

    def __init__(self, **kwargs):
        # Register-level classification from the Meter setup (PS-21); popped
        # here so the generic kwarg loop in LoxoneEntity does not try to
        # setattr them as plain attributes.
        forced_device_info = kwargs.pop("device_info", None)
        forced_device_class = kwargs.pop("device_class", None)
        forced_state_class = kwargs.pop("state_class", None)
        super().__init__(**kwargs)
        # WP-5.1: sub-sensors (Meter registers, Ventilation fan readings)
        # keep their short name; the device is named after the parent
        # control (CORE-26).
        if kwargs.get("parent_id"):
            self._attr_name = self._lox_name
        # CORE-20: a forced device (fan sub-sensors pass the parent's,
        # Meter sub-sensors pass the meter's own) wins over the default.
        self._forced_device_info = forced_device_info
        details = getattr(self, "details", None)
        details = details if isinstance(details, dict) else {}
        lox_format = details.get("format", "")
        self._attr_should_poll = False
        self._attr_native_unit_of_measurement = clean_unit(lox_format) if isinstance(lox_format, str) else None
        self._parent_id = kwargs.get("parent_id")

        # PS-25: a format with an explicit ``.0`` still has a real precision
        # of 0 digits (``if precision:`` treated 0 as "none").
        precision = self._parse_digits_after_decimal(lox_format)
        if precision is not None:
            self._attr_suggested_display_precision = precision

        # Device class is detected from unit/category/name;
        # per-entity overrides remain possible via HA's entry-specific
        # customization (customizing the entity's device_class/type in
        # configuration.yaml still wins over the automatic match).
        desc = match_sensor_description(
            unit=self._attr_native_unit_of_measurement,
            name=self._lox_name,
            category=kwargs.get("cat", ""),
        )

        # Per-register classifications from the Meter setup (PS-21) win over
        # the unit-based match.
        if desc is not None and (forced_device_class is not None or forced_state_class is not None):
            desc = replace(
                desc,
                device_class=forced_device_class or desc.device_class,
                state_class=forced_state_class or desc.state_class,
            )

        # A plain (non-Meter) kWh/L value only becomes TOTAL_INCREASING when
        # the name/category indicates a real meter; resetting values like
        # "Consumption today" would otherwise show spikes in the energy
        # dashboard (PS-21).
        if (
            desc is not None
            and forced_state_class is None
            and desc.state_class == SensorStateClass.TOTAL_INCREASING
            and not _metering_indicated(self._lox_name, kwargs.get("cat", ""))
        ):
            desc = replace(desc, state_class=SensorStateClass.MEASUREMENT)

        numeric = _is_numeric_format(lox_format)
        if desc is not None:
            if not numeric and desc.state_class is not None:
                # Text values must not advertise a numeric state_class,
                # or HA raises when a text string is published (PS-09).
                desc = replace(desc, state_class=None)
            self.entity_description = desc
        elif numeric:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        # PS-14: the group table matches the "Sensor analog" constant
        # (the old extras value appended "_sensor" to it, and the old
        # device identifier used the parent id for sub-sensors, so the
        # shared dict was seeded by whichever sibling was built first).
        self.type = DEVICE_TYPE_ANALOG
        self._attr_device_info = self._forced_device_info or device_info_for(
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
        )

    def _parse_digits_after_decimal(self, format_string: Any):
        """Parse digits after the decimal point from the format string."""
        if not isinstance(format_string, str):
            return None
        pattern = r"\.(\d+)"
        match = re.search(pattern, format_string)
        if match:
            digits = int(match.group(1))
            return digits
        return None

    @callback
    def event_handler(self, e):
        if self.uuidAction in e:
            self._attr_native_value = _analog_value(e[self.uuidAction])
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


class LoxoneMeterSensor(LoxoneSensor, SensorEntity):
    """
    A register (Actual/Total/Total Neg/Level) of a Meter-family
    control (WP-6.5).  Register construction and the shared device link
    live in the pure ``meter_sub_sensor_kwargs`` / ``meter_device_info``
    helpers; this class keeps the ``LoxoneSensor`` behaviour (per-register
    class overrides from the setup, analog state updates).
    """


class LoxoneRoomControllerTemperatureSensor(SensorEntity):
    """Sensor for IRoomControllerV2 comfort temperature states."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, name: str, uuid: str, device_info: DeviceInfo, parent_uuid: str):
        self._attr_name = name
        self._uuid = uuid
        self._attr_unique_id = uuid
        self._attr_device_info = device_info
        self._attr_native_value = None
        self._parent_uuid = parent_uuid

    async def async_added_to_hass(self):
        """Subscribe to Loxone events."""

        @callback
        def _on_bus_message(event) -> None:
            # CORE-27: handlers take the plain {uuid: value} dict on every
            # path (the dispatcher slice or the bus event's data).
            self.event_handler(event.data)

        self.async_on_remove(self.hass.bus.async_listen(EVENT, _on_bus_message))

    @callback
    def event_handler(self, e):
        if self._uuid in e:
            self._attr_native_value = _analog_value(e[self._uuid])
            self.async_write_ha_state()


class LoxoneRoomControllerOverrideSensor(SensorEntity):
    """
    Sensor for IRoomControllerV2 override reason.

    The values are slugs that resolve through
    ``entity.sensor.loxone.override_reason.state.<slug>`` (CORE-22).
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "override_reason"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, name: str, uuid: str, device_info: DeviceInfo, parent_uuid: str):
        self._attr_name = name
        self._uuid = uuid
        self._attr_unique_id = uuid
        self._attr_device_info = device_info
        self._attr_native_value = OVERRIDE_REASON_SLUGS[0]
        self._attr_options = [*OVERRIDE_REASON_SLUGS.values(), OVERRIDE_REASON_UNKNOWN]
        self._parent_uuid = parent_uuid

    async def async_added_to_hass(self):
        """Subscribe to Loxone events."""

        @callback
        def _on_bus_message(event) -> None:
            # CORE-27: handlers take the plain {uuid: value} dict on every
            # path (the dispatcher slice or the bus event's data).
            self.event_handler(event.data)

        self.async_on_remove(self.hass.bus.async_listen(EVENT, _on_bus_message))

    @callback
    def event_handler(self, e):
        if self._uuid in e:
            try:
                code = int(float(e[self._uuid]))
            except TypeError, ValueError:
                return
            slug = OVERRIDE_REASON_SLUGS.get(code, OVERRIDE_REASON_UNKNOWN)
            if slug not in self._attr_options:
                self._attr_options = [*self._attr_options, slug]
            self._attr_native_value = slug
            self.async_write_ha_state()


class LoxoneClimateController(LoxoneEntity, SensorEntity):
    """
    Climate controller sensor that fires the per-room demand signals
    for IRoomControllerV2 (PS-18: entry-scoped dispatcher signal replacing
    the global CLIMATE_EVENT bus event).

    Reads the control list from the ClimateController's state and publishes
    each room's demand (1 = heating, -1 = cooling, 0 = idle) to that
    room's own per-uuid signal.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._stateAttribUuids = kwargs.get("states", {})
        self._stateAttribValues = {}
        self._heat_demand = 0
        self._cool_demand = 0
        self.type = "ClimateController"

        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: every monitored state stream of the climate controller.
        return frozenset(uuid for uuid in self._stateAttribUuids.values() if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, e):
        update = False
        coordinator = self._connection_coordinator()
        entry_id = coordinator.config_entry.entry_id if coordinator is not None else None

        for key in set(self._stateAttribUuids.values()) & e.keys():
            raw = e[key]
            # Parse JSON control lists from the Miniserver
            if isinstance(raw, str) and raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    self._stateAttribValues[key] = parsed
                    # PS-18: fan out heat/cool demand per room via that
                    # room's own per-uuid dispatcher signal — entry B's
                    # ClimateController can no longer flip entry A's rooms.
                    heat_count = 0
                    cool_count = 0
                    for control in parsed:
                        demand = control.get("demand", 0)
                        if demand == 1:
                            heat_count += 1
                        elif demand == -1:
                            cool_count += 1
                        room_uuid = control.get("uuid")
                        if entry_id is not None and isinstance(room_uuid, str) and room_uuid:
                            async_dispatcher_send(self.hass, loxone_climate_demand_signal(entry_id, room_uuid), demand)
                    self._heat_demand = heat_count
                    self._cool_demand = cool_count
                except (json.JSONDecodeError, TypeError, KeyError) as err:
                    _LOGGER.debug("ClimateController JSON parse error: %s", err)
            else:
                self._stateAttribValues[key] = raw
            update = True

        if update:
            self.schedule_update_ha_state()

    @property
    def native_value(self):
        """Return summary state."""
        if self._heat_demand > 0:
            return f"Heating ({self._heat_demand})"
        if self._cool_demand > 0:
            return f"Cooling ({self._cool_demand})"
        return "Idle"

    @property
    def extra_state_attributes(self):
        """Return detailed demand attributes."""
        return {
            **self._attr_extra_state_attributes,
            "heat_demand": self._heat_demand,
            "cool_demand": self._cool_demand,
            "device_type": self.type,
        }


class LoxoneNotificationsSensor(LoxoneEntity, SensorEntity):
    """
    The Miniserver's global notification text (WP-6.2, #515).

    ``globalStates.notifications`` in the structure file addresses a free
    text stream (e.g. "Maintenance mode active").  The sensor mirrors it
    on the Miniserver host device, matching the upstream PR's intent.
    """

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:notifications"

    def __init__(self, notifications_uuid: str, device_info: DeviceInfo | None = None, **kwargs):
        # No ``uuidAction``: the global-state stream is command-less, so
        # the unique id and the stream uuid are the state uuid itself.
        super().__init__(**kwargs)
        self._attr_name = "Notifications"
        self._state_uuid = notifications_uuid
        # CORE-26: stored as attribute; stable per entry.
        self._attr_unique_id = notifications_uuid
        self._attr_native_value = None
        if device_info is not None:
            self._attr_device_info = device_info

    def _state_uuids(self) -> frozenset[str]:
        return frozenset({self._state_uuid})

    @callback
    def event_handler(self, e):
        value = e.get(self._state_uuid)
        if value is None:
            return
        self._attr_native_value = value if isinstance(value, str) else str(value)
        self.async_write_ha_state()


class LoxoneMessageCenterSensor(LoxoneEntity, SensorEntity):
    """
    Message Center severity summary + HA repair issues (WP-6.2, #515).

    One per top-level ``messageCenter`` control of the structure file.  The
    sensor state is the max severity class (0 = no active entries) and the
    ``status`` attribute carries the per-severity counts.  Re-sync is
    event-driven: a newer value on the control's ``states.changed`` stream
    sends the ``getEntries/2`` command through this entry's own coordinator
    (``LoxoneEntity._send``), and the Miniserver's response arrives as a
    full-message fan-out on the entry-scoped
    ``loxone_message_signal`` (CORE-27 decoded: the response carries no
    stream uuid to dispatch on).  Each active entry is mirrored as a
    persistent, non-fixable repair issue, translated per its affected
    entities; resolved (historic) entries delete their issue.
    """

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:dashboard"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # The change counter stream that triggers a re-fetch of the entries
        # (guarded: absent/corrupt structures fall back to a manual PM
        # re-read of the structure).  ``value`` / ``control`` message fields
        # are not stream names, so they are never dispatched here.
        states = getattr(self, "states", None)
        self._changed_uuid = states.get("changed") if isinstance(states, dict) else None
        if not isinstance(self._changed_uuid, str) or not self._changed_uuid:
            self._changed_uuid = None
        self._last_changed: datetime | None = None
        self._status: dict[str, int] = {}
        self._attr_native_value = 0
        # CORE-20: fresh device built from the control's own identity,
        # linked to the Miniserver host device.
        room = self.room if isinstance(self.room, str) else None
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"),
            self._attr_unique_id,
            self._lox_name,
            kwargs.get("type") or "MessageCenter",
            room,
        )

    def _state_uuids(self) -> frozenset[str]:
        uuids = {self.uuidAction}
        if self._changed_uuid is not None:
            uuids.add(self._changed_uuid)
        return frozenset(uuids)

    def _owning_entry(self):
        """
        The live platform config entry (prefers ``platform.config_entry``
        over the construction-time reference, same resolution as
        ``LoxoneEntity._connection_coordinator``).
        """
        platform = getattr(self, "platform", None)
        entry = getattr(platform, "config_entry", None)
        if entry is None:
            entry = getattr(self, "config_entry", None)
        return entry

    def _issue_id(self, entry_uuid: str) -> str:
        entry = self._owning_entry()
        return message_center_issue_id(entry.entry_id, entry_uuid)

    async def async_added_to_hass(self):
        """
        Subscribe to the entry's FULL message fan-out in addition to the
        per-uuid streams (only the Message Center sensor needs it — the
        getEntries response has no stream uuid to dispatch on).
        """
        await super().async_added_to_hass()
        config_entry = self._owning_entry()
        if config_entry is None:
            return
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, loxone_message_signal(config_entry.entry_id), partial(self.event_handler)
            )
        )

    @callback
    def event_handler(self, e):
        """
        Handles per-uuid stream slices *and* full-message fanouts alike.

        The base ``LoxoneEntity`` callback wrapper routes the per-uuid fan-in
        to this dict; the extra full-message subscription feeds the same
        handler.  Only the fields used below are read.
        """
        if not isinstance(e, dict):
            return
        # 1) A newer change counter -> one re-sync of the Message Center.
        if self._changed_uuid is not None and self._changed_uuid in e:
            self._maybe_request_entries(e.get(self._changed_uuid))
        # 2) The getEntries response (control + value fields).
        if _is_get_entries_response(e.get("control")):
            self._schedule_entry_processing(e.get("value"))

    @callback
    def _maybe_request_entries(self, changed_value):
        """Send ``getEntries/2`` when the change counter moved forward."""
        changed = loxone_timestamp(changed_value)
        if changed is None:
            return
        if self._last_changed is not None and changed <= self._last_changed:
            return
        self._last_changed = changed
        self._send(MESSAGE_CENTER_GET_ENTRIES_COMMAND)

    @callback
    def _schedule_entry_processing(self, value):
        if not isinstance(value, str):
            return
        config_entry = self._owning_entry()
        if config_entry is None:
            # No entry to scope the task to (and no path to re-sync); skip.
            return
        config_entry.async_create_background_task(
            self.hass, self._process_entries(value), name="message-center-entries"
        )

    async def _process_entries(self, value):
        """Parse the ``getEntries`` response and reconcile the issues."""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError, TypeError:
            _LOGGER.exception("Failed to parse the Message Center getEntries response")
            return
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return

        active_ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_uuid = entry.get("entryUuid")
            if not isinstance(entry_uuid, str) or not entry_uuid:
                continue
            if entry.get("isHistoric"):
                self._delete_issue(entry_uuid)
            else:
                self._upsert_issue(entry)
                active_ids.add(self._issue_id(entry_uuid))

        counts, max_severity = message_center_summary(entries)
        self._status = counts
        self._attr_native_value = max_severity
        self._remove_stale_issues(active_ids)
        self.async_write_ha_state()

    # -- repair issue helpers -------------------------------------------------

    def _delete_issue(self, entry_uuid: str):
        entry = self._owning_entry()
        if entry is None:
            return
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(entry_uuid))

    def _upsert_issue(self, entry: dict):
        if self._owning_entry() is None:
            _LOGGER.debug("Message Center entry without an owning entry; no repair issue")
            return
        entry_uuid = entry["entryUuid"]
        severity = _as_int_severity(entry.get("severity"))
        title = entry.get("title")
        name = entry.get("affectedName") or "Unknown"
        message = entry.get("desc") or ""
        if not isinstance(message, str):
            message = str(message)
        message = message.replace("<br><br>Further details can be found under the following link.", "\n")
        occurred = message_center_entry_timestamp(entry.get("timestamps"))
        if occurred is not None:
            message += f"\n\nOccurred at: {occurred.isoformat()}"

        placeholders = {"name": name, "message_name": name, "title": title, "description": message}
        linked_entities: list[str] = []
        linked = self._link_affected_entities(entry, placeholders, linked_entities)
        translation_key = "loxone_device_status" if linked else "loxone_status"
        help_link = entry.get("helpLink")

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(entry_uuid),
            is_fixable=False,
            is_persistent=True,
            severity=message_center_issue_severity(severity),
            translation_key=translation_key,
            translation_placeholders=placeholders,
            learn_more_url=help_link if isinstance(help_link, str) and help_link else None,
            data={"entry": entry, "devices": linked_entities},
        )

    def _link_affected_entities(self, entry: dict, placeholders: dict, linked_entities: list[str]) -> bool:
        """
        Attach the affected controls' HA entities to the issue.

        Returns True when at least one affected uuid resolved to an entity
        of this entry (the translation switches to the {title}: {name} form
        in that case, per the upstream keys).
        """
        affected = entry.get("affectedUuids")
        if not isinstance(affected, list) or not affected:
            return False
        entry_obj = self._owning_entry()
        if entry_obj is None:
            return False
        registry = er.async_get(self.hass)
        entities = registry.entities.get_entries_for_config_entry_id(entry_obj.entry_id)
        source = entry.get("sourceUuid")
        linked = False
        by_uuid = {e.unique_id: e for e in entities if isinstance(e.unique_id, str)}
        for uuid in affected:
            if not isinstance(uuid, str) or not uuid:
                continue
            entity = by_uuid.get(uuid)
            if entity is None:
                continue
            linked = True
            linked_entities.append(entity.entity_id)
            # WP-5.1: primary entities store no registry name (None / None)
            # — the *device* carries the display name, so fall back to it.
            display_name = entity.name or entity.original_name
            if not display_name and entity.device_id:
                device = dr.async_get(self.hass).async_get(entity.device_id)
                display_name = device.name if device else None
            if isinstance(display_name, str) and display_name:
                placeholders["description"] += f"\n[{display_name}](/?more-info-entity-id={entity.entity_id})"
                if source == uuid:
                    placeholders["name"] = display_name
        return linked

    def _remove_stale_issues(self, active_ids: set[str]):
        """Drop this entry's message-center issues no longer in ``active_ids``"""
        entry = self._owning_entry()
        if entry is None:
            return
        prefix = f"{MESSAGE_CENTER_ISSUE_PREFIX}{entry.entry_id}_"
        registry = ir.async_get(self.hass)
        stale = [
            issue.issue_id
            for issue in registry.issues.values()
            if issue.domain == DOMAIN and issue.issue_id.startswith(prefix) and issue.issue_id not in active_ids
        ]
        for issue_id in stale:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "status": self._status,
        }
