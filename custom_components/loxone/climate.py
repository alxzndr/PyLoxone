"""
Loxone climate

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

from dataclasses import dataclass
from enum import Enum
import json
import logging
from abc import ABC

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .const import CONF_HVAC_AUTO_MODE, PRESET_PAUSED_WINDOW, PRESET_SCHEDULE, loxone_climate_demand_signal

# Stable preset literals for the FIXED (14) / FIXED_DYNAMIC (112) active
# modes.  NOTE: not in const.py because that file is owned by other WPs
# (parallel-safe, WP-4.2/4.4); see the PR body follow-up.
PRESET_FIXED = "fixed"
PRESET_FIXED_DYNAMIC = "fixed_dynamic"
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure helpers (shared across the three climate classes)
# ---------------------------------------------------------------------------
# Canonical names for the Loxone state attributes the planner reads.
_COMFORT_C = "comfort_temperature"
_COMFORT_COOL = "comfort_temperature_cool"
_FROST_PROTECT = "frost_protect_temperature"
_HEAT_PROTECT = "heat_protect_temperature"
_ABSENT_MIN = "absent_min_offset"
_ABSENT_MAX = "absent_max_offset"


def _parse_mode_list(raw, what: str) -> list[dict]:
    """
    Parse Miniserver JSON lists (``fanspeeds``/``airflows``/``timerModes``)
    once, tolerant of malformed input (PC-16): anything that is not a list of
    dicts yields ``[]`` instead of raising.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError, TypeError:
            _LOGGER.debug("Could not parse %s: %r", what, raw)
            return []
    if not isinstance(raw, list):
        return []
    return [m for m in raw if isinstance(m, dict)]


def temperature_unit_from_format(fmt: str | None) -> str:
    """
    Map a Loxone format string to a temperature unit (PC-23).

    The designer can put any format string on the input sensor; we assume it
    mentions the unit.  Containment, not ``str.find`` (index 0 is falsy):
    ``"°C"`` at position 0 must read as Celsius.  Falls back to the legacy
    bare-letter heuristic, then to Celsius.
    """
    if fmt is None:
        return UnitOfTemperature.CELSIUS
    if "°F" in fmt or "F" in fmt:
        return UnitOfTemperature.FAHRENHEIT
    if "°C" in fmt or "C" in fmt:
        return UnitOfTemperature.CELSIUS
    return UnitOfTemperature.CELSIUS


# V2 (IRoomControllerV2) Loxone operating-mode codes.  -1 is the documented
# "off" command; 4/5 are manual heat/cool (this is the table the V2 writer
# always used, see OPMODETOLOXONE).
LOXONE_TO_HVAC_V2: dict[int, HVACMode] = {
    -1: HVACMode.OFF,
    0: HVACMode.AUTO,
    1: HVACMode.HEAT,
    2: HVACMode.COOL,
    3: HVACMode.HEAT_COOL,
    4: HVACMode.HEAT,
    5: HVACMode.COOL,
}

# Explicit write dispatch of the V2 table: one command code per HVAC mode.  It
# keeps the codes the legacy writer emitted (OFF -> -1, HEAT_COOL -> 3,
# HEAT -> 4, COOL -> 5); HVACMode.AUTO is not dispatched through this map
# (the writer uses the YAML-configured auto mode instead).
HVAC_TO_LOXONE_V2: dict[HVACMode, int] = {
    HVACMode.OFF: -1,
    HVACMode.HEAT_COOL: 3,
    HVACMode.HEAT: 4,
    HVACMode.COOL: 5,
}

# Legacy (IRoomController) table.  VERIFY: the pre-WP-4.1 code claimed
# 0=Auto,1=Heat,2=Cool,3=Heat/Cool,4=Off; keep that as the single shared
# source for both reading and writing until confirmed against a live V1
# structure (PC-26).
LEGACY_ROOM_CONTROLLER_MODES: dict[int, HVACMode] = {
    0: HVACMode.AUTO,
    1: HVACMode.HEAT,
    2: HVACMode.COOL,
    3: HVACMode.HEAT_COOL,
    4: HVACMode.OFF,
}


def legacy_mode_to_hvac(code: int | None) -> HVACMode:
    """
    Map a legacy IRoomController mode code to an HVAC mode (PC-26).

    Unknown codes fall back to OFF rather than raising so the read side
    never aborts a state update.
    """
    return LEGACY_ROOM_CONTROLLER_MODES.get(code, HVACMode.OFF)


def _hvac_to_legacy(hvac_mode: HVACMode) -> int | None:
    for lox, hvac in LEGACY_ROOM_CONTROLLER_MODES.items():
        if hvac == hvac_mode:
            return lox
    return None


def hvac_to_legacy_mode(hvac_mode: HVACMode) -> int | None:
    """Map an HVAC mode to a legacy IRoomController command code (PC-26)."""
    return _hvac_to_legacy(hvac_mode)


def hvac_to_loxone(hvac_mode: HVACMode, table: str = "v2") -> int | None:
    """
    Map an HVAC mode back to a Loxone command code (PC-26).

    The V2 write side always had explicit codes (``set_hvac_mode(OFF)`` sent
    1 = manual heat by accident); both families now dispatch through one
    table so what we send is always what our own reader decodes.
    """
    if table == "legacy":
        return _hvac_to_legacy(hvac_mode)
    return HVAC_TO_LOXONE_V2.get(hvac_mode)


def capabilities_to_hvac_modes(bits, range_allowed: bool = True) -> list[HVACMode]:
    """
    Derive the offered operating modes from a possibleCapabilities
    bitmask (bit0 = heat, bit1 = cool).  parseInt-like inputs fall back to
    the full table (PC-26 / PC-16).
    """
    try:
        value = int(bits)
    except ValueError, TypeError:
        value = 3
    modes = [HVACMode.AUTO, HVACMode.OFF]
    if value & 1:
        modes.append(HVACMode.HEAT)
    if value & 2:
        modes.append(HVACMode.COOL)
    if range_allowed and (value & 3) == 3:
        modes.append(HVACMode.HEAT_COOL)
    return modes


def plan_set_temperature(op_mode: int, active: int, kwargs: dict, state: dict) -> list[str]:
    """
    Plan the SENDDOMAIN commands for one ``set_temperature`` call (PC-02).

    ``op_mode`` is the numeric V2 operating mode (see :data:`LOXONE_TO_HVAC_V2`),
    ``active`` the numeric active mode, ``kwargs`` the HA temperature kwargs,
    and ``state`` the currently known Loxone states (None where not yet
    seen); the dict also carries ``range_possible`` so a dual op mode can
    dispatch a single manual target when the controller cannot do ranges.
    Returns the command ``value`` strings in send order.  Pure: no state
    is mutated; an empty list means "nothing to send".

    PC-02: the heat (``target_temp_low``) branch in BUILDING_PROTECT compared
    against ``comfort_cool`` — a name only bound when the cool branch ran —
    which raised ``NameError`` for a lone ``target_temp_low``; it is now
    compared against ``frost_protect_temperature``.
    """
    commands: list[str] = []
    values = dict(state)
    is_fixed = active in (ActiveMode.FIXED_DYNAMIC.value, ActiveMode.FIXED.value)
    is_manual = (
        active == ActiveMode.MANUAL.value
        or op_mode
        in (
            OperatingMode.MANUAL_HEAT.value[0],
            OperatingMode.MANUAL_COOL.value[0],
        )
        or (op_mode == OperatingMode.MANUAL_HEAT_COOL.value[0] and not values.get("range_possible", False))
    )
    range_possible = values.get("range_possible", False)
    # Single manual/manual-comfort target.  FIXED_DYNAMIC keeps the
    # documented ``override/<temp*2560+112>/<temp>`` encoding (VERIFY live:
    # the ``//`` in the old f-string had no visible effect, so it was dropped).
    if is_fixed or is_manual:
        if "temperature" in kwargs:
            temp = kwargs["temperature"]
            if active == ActiveMode.FIXED_DYNAMIC.value:
                commands.append(f"override/{(temp * 2560) + 112}/{temp}")
            else:
                commands.append(f"setManualTemperature/{temp}")
        return commands
    # Range-capable dual operating modes: high (cool) and low (heat) targets.
    if range_possible and op_mode in (
        OperatingMode.AUTO_HEAT_COOL.value[0],
        OperatingMode.MANUAL_HEAT_COOL.value[0],
    ):
        comfort_cool = values.get(_COMFORT_COOL)
        comfort_heat = values.get(_COMFORT_C)
        if "target_temp_high" in kwargs and comfort_cool is not None:
            new_temp = kwargs["target_temp_high"]
            if active == ActiveMode.ECONOMY.value:
                desired = new_temp - comfort_cool
                desired = max(desired, 0.5)
                if desired != values.get(_ABSENT_MAX):
                    commands.append(f"setAbsentMaxTemperature/{desired}")
            elif active == ActiveMode.COMFORT.value:
                if new_temp != comfort_cool:
                    commands.append(f"setComfortTemperatureCool/{new_temp}")
            elif active == ActiveMode.BUILDING_PROTECT.value:
                heat_protect = values.get(_HEAT_PROTECT)
                if heat_protect is None or new_temp != heat_protect:
                    commands.append(f"setecoplusmaxtemperature/{new_temp}")
        if "target_temp_low" in kwargs and comfort_heat is not None:
            new_temp = kwargs["target_temp_low"]
            if active == ActiveMode.ECONOMY.value:
                desired = comfort_heat - new_temp
                desired = max(desired, 0.5)
                if desired != values.get(_ABSENT_MIN):
                    commands.append(f"setAbsentMinTemperature/{desired}")
            elif active == ActiveMode.COMFORT.value:
                if new_temp != comfort_heat:
                    commands.append(f"setComfortTemperature/{new_temp}")
            elif active == ActiveMode.BUILDING_PROTECT.value:
                # PC-02: frost-protect temperature, not comfort_cool
                frost_protect = values.get(_FROST_PROTECT)
                if frost_protect is None or new_temp != frost_protect:
                    commands.append(f"setecoplusmintemperature/{new_temp}")
        return commands
    # Everything else (auto single target, manual single heat/cool …) offsets
    # the comfort temperature.
    if "temperature" in kwargs:
        if active == ActiveMode.FIXED_DYNAMIC.value:
            commands.append(f"override/{(kwargs['temperature'] * 2560) + 112}/{kwargs['temperature']}")
        else:
            comfort = values.get(_COMFORT_C)
            if comfort is not None:
                offset = kwargs["temperature"] - comfort
                commands.append(f"setComfortModeTemp/{offset}")
    return commands


class ActiveMode(Enum):
    ECONOMY = 0
    COMFORT = 1
    BUILDING_PROTECT = 2
    MANUAL = 3
    OFF = 4
    FIXED = 14
    FIXED_DYNAMIC = 112


@dataclass
class ActiveState:
    mode: ActiveMode
    value: float

    @classmethod
    def from_raw(cls, raw_value: float) -> ActiveState:
        base_value = raw_value if raw_value < 112 else ActiveMode.FIXED_DYNAMIC.value
        dynamic_value = 0 if raw_value < 112 else (raw_value - 112) / 2560.0
        mode = ActiveMode(base_value)
        return cls(mode=mode, value=dynamic_value)


class OperatingMode(Enum):
    OFF = (-1, HVACMode.OFF)
    AUTO_HEAT_COOL = (0, HVACMode.AUTO)
    AUTO_HEAT = (1, HVACMode.HEAT)
    AUTO_COOL = (2, HVACMode.COOL)
    MANUAL_HEAT_COOL = (3, HVACMode.HEAT_COOL)
    MANUAL_HEAT = (4, HVACMode.HEAT)
    MANUAL_COOL = (5, HVACMode.COOL)

    @classmethod
    def from_mode(cls, mode: float):
        """Returns the OperatingMode matching the given integer value."""
        for member in cls:
            if member.value[0] == mode:
                return member
        raise ValueError(f"{mode} is not a valid number for {cls.__name__}")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LoxoneRoomControllerV2."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []

    for climate in get_all(loxconfig, "IRoomControllerV2"):
        climate = add_room_and_cat_to_value_values(loxconfig, climate)
        climate.update(
            {
                "hass": hass,
                "config_entry": config_entry,
                CONF_HVAC_AUTO_MODE: 0,
            }
        )
        entities.append(LoxoneRoomControllerV2(**climate))

    for climate in get_all(loxconfig, "IRoomController"):
        climate = add_room_and_cat_to_value_values(loxconfig, climate)
        climate.update(
            {
                "hass": hass,
                "config_entry": config_entry,
                CONF_HVAC_AUTO_MODE: 0,
            }
        )
        entities.append(LoxoneRoomController(**climate))

    for accontrol in get_all(loxconfig, "AcControl"):
        accontrol = add_room_and_cat_to_value_values(loxconfig, accontrol)
        accontrol.update(
            {
                "hass": hass,
                "config_entry": config_entry,
            }
        )
        entities.append(LoxoneAcControl(**accontrol))
    async_add_entities(entities)


class LoxoneRoomController(LoxoneEntity, ClimateEntity, ABC):
    """Loxone room controller (legacy, non-V2)"""

    def __init__(self, **kwargs):
        # Add room name to entity name for better identification in HomeKit
        if kwargs.get("room"):
            kwargs["name"] = f"{kwargs['room']} Climate"

        super().__init__(**kwargs)
        self.hass = kwargs["hass"]
        self._autoMode = kwargs.get(CONF_HVAC_AUTO_MODE, 0)
        self._stateAttribUuids = kwargs.get("states") or {}
        self._stateAttribValues = {}
        self.type = "RoomController"

        # Set supported features
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        )

        # Flatten UUID values - some might be lists (e.g., "temperatures")
        self._all_uuids = set()
        for value in self._stateAttribUuids.values():
            if isinstance(value, list):
                self._all_uuids.update(value)
            else:
                self._all_uuids.add(value)

        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: every state stream the handler consumes (values may be
        # single uuids, or lists for e.g. ``temperatures``).
        return frozenset(self._all_uuids)

    @callback
    def event_handler(self, event):
        update = False

        for key in self._all_uuids & event.keys():
            self._stateAttribValues[key] = event[key]
            update = True

        if update:
            self.async_write_ha_state()

    def get_state_value(self, name):
        uuid = self._stateAttribUuids.get(name)
        if isinstance(uuid, list):
            # For "temperatures" which is a list of UUIDs
            return [self._stateAttribValues.get(u) for u in uuid if u in self._stateAttribValues]
        return self._stateAttribValues[uuid] if uuid and uuid in self._stateAttribValues else None

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "mode": self.get_state_value("mode"),
            "override": self.get_state_value("override"),
            "open_window": self.get_state_value("openWindow"),
            "curr_heat_temp_ix": self.get_state_value("currHeatTempIx"),
            "curr_cool_temp_ix": self.get_state_value("currCoolTempIx"),
        }

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self.get_state_value("tempActual")

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self.get_state_value("tempTarget")

    def set_temperature(self, **kwargs):
        """Set new target temperature"""
        temp = kwargs.get("temperature")
        if temp is None:
            return

        # IRoomController uses setTemp with current temperature index
        # Get the current active temperature index based on mode
        mode = self.get_state_value("mode")

        # Determine which temperature index to use
        temp_idx = self.get_state_value("currHeatTempIx")
        if mode == 2:  # Cooling mode
            cool_idx = self.get_state_value("currCoolTempIx")
            if cool_idx is not None:
                temp_idx = cool_idx

        if temp_idx is not None:
            # Command format: setTemp/<index>/<value>
            self._send(f"setTemp/{int(temp_idx)}/{temp}")
            self.async_write_ha_state()

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action (heating, cooling)."""
        valve_heat = self.get_state_value("valveHeat")
        valve_cool = self.get_state_value("valveCool")

        if valve_heat and valve_heat > 0:
            return HVACAction.HEATING
        if valve_cool and valve_cool > 0:
            return HVACAction.COOLING

        if self.get_state_value("isPreparing") == 1:
            return HVACAction.PREHEATING

        return HVACAction.IDLE

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac operation mode (PC-26: single shared legacy table)."""
        return legacy_mode_to_hvac(self.get_state_value("mode"))

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available hvac operation modes."""
        return [
            HVACMode.OFF,
            HVACMode.AUTO,
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.HEAT_COOL,
        ]

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement used by the platform (PC-23)."""
        return temperature_unit_from_format(self.details.get("format") if isinstance(self.details, dict) else None)

    @property
    def target_temperature_step(self) -> float | None:
        """Return the supported step of target temperature."""
        return 0.5

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return 7.0

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return 35.0

    def set_hvac_mode(self, hvac_mode: str):
        """Set new target hvac mode (PC-26: dispatch through the shared table)."""
        target_mode = hvac_to_loxone(hvac_mode, table="legacy")
        if target_mode is None:
            _LOGGER.debug("No legacy IRoomController mode for hvac mode %r", hvac_mode)
            return

        self._send(f"setMode/{target_mode}")

        self.schedule_update_ha_state()


class LoxoneRoomControllerV2(LoxoneEntity, ClimateEntity, ABC):
    """Loxone room controller V2 with demand tracking and dynamic capabilities."""

    _attr_translation_key = "room_controller_v2"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]
        self._autoMode = kwargs.get(CONF_HVAC_AUTO_MODE, 0)
        self._states = kwargs.get("states") or {}
        self._states_reversed = {}
        for key, value in self._states.items():
            if isinstance(value, str):
                self._states_reversed[value] = key
        self._state_attr_values = {}
        self._attr_min_temp = 5
        self._attr_max_temp = 40
        self.operating_mode = OperatingMode.OFF
        self.active_state = ActiveState.from_raw(ActiveMode.OFF.value)
        self.type = "RoomControllerV2"
        details = kwargs.get("details") or {}
        self._single_comfort_temp = details.get("singleComfortTemperature", False)
        # No demand event has arrived yet (None), so hvac_action falls back to
        # the controller's own states until a ClimateController speaks up (PC-28)
        self._demand: int | None = None

        # Copy timer modes (PC-16): every entry becomes a fresh dict, because
        # the structure file is shared and must never be modified in place.
        self._modeList = [dict(m) for m in _parse_mode_list(details.get("timerModes"), "timerModes")]
        if not any(m.get("id") == "stop" for m in self._modeList):
            self._modeList.append({"id": "stop", "name": PRESET_SCHEDULE})

        # PC-13: unknown mode codes are warned about once per distinct value
        self._mode_warned: set = set()

        # Determine heating/cooling capabilities from the details bitmask
        # (default: both possible, as before)
        possible_capabilities = details.get("possibleCapabilities", 3)
        heat_possible = possible_capabilities & 1
        cool_possible = possible_capabilities & 2
        self._range_possible = bool(heat_possible and cool_possible)

        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    async def async_added_to_hass(self):
        """
        Register listeners once the entity is added to HA.

        PS-18: the heat/cool demand is scoped to *this* room controller's
        uuid via the per-uuid dispatcher signal — a ClimateController feeding
        another entry no longer reaches into this entity.
        """
        await super().async_added_to_hass()
        coordinator = self._connection_coordinator()
        if coordinator is None:
            return  # no entry to scope the demand signal with (unit tests)
        entry_id = coordinator.config_entry.entry_id
        unsub = async_dispatcher_connect(
            self.hass, loxone_climate_demand_signal(entry_id, self.uuidAction), self.on_climate_demand
        )
        self.async_on_remove(unsub)

    @callback
    def on_climate_demand(self, demand: int) -> None:
        """PS-18: demand update for *this* room (per-uuid dispatcher signal)."""
        self._demand = demand
        self.async_write_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: every state stream the handler consumes.
        return frozenset(uuid for uuid in self._states.values() if isinstance(uuid, str) and uuid)

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """
        Return supported features based on device capabilities.

        PC-12: TARGET_TEMPERATURE_RANGE is advertised only in the
        dual-target case and announces TARGET_TEMPERATURE *instead* of the
        plain target — both were set at once before, and bare
        ``target_temperature`` would return None in that case.
        """
        op_mode = self.operating_mode
        if op_mode is OperatingMode.OFF:
            return ClimateEntityFeature.TURN_ON

        features = ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON

        active_mode = self.active_mode
        is_dual = op_mode in (OperatingMode.AUTO_HEAT_COOL, OperatingMode.MANUAL_HEAT_COOL)
        is_fixed = active_mode in (ActiveMode.FIXED_DYNAMIC, ActiveMode.FIXED)
        if not is_fixed and self._range_possible and not self._single_comfort_temp and is_dual:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE

        return features

    def get_mode_from_id(self, mode_id):
        for mode in self._modeList:
            if mode.get("id") == mode_id:
                return mode.get("name")

    @callback
    def event_handler(self, event):
        update = False

        for key in set(self._states.values()) & event.keys():
            if not isinstance(key, str):
                continue
            val = event[key]
            self._state_attr_values[key] = val
            if self._states_reversed.get(key) == "operatingMode":
                try:
                    self.operating_mode = OperatingMode.from_mode(val)
                except ValueError:
                    # PC-13: unknown code keeps the previous mode, warns once
                    if val not in self._mode_warned:
                        self._mode_warned.add(val)
                        _LOGGER.warning("LoxoneRoomControllerV2: unknown operating mode %r", val)
                    else:
                        _LOGGER.debug("LoxoneRoomControllerV2: unknown operating mode %r", val)
            elif self._states_reversed.get(key) == "activeMode":
                try:
                    self.active_state = ActiveState.from_raw(val)
                except ValueError:
                    # PC-13: unknown code keeps the previous active state
                    if val not in self._mode_warned:
                        self._mode_warned.add(val)
                        _LOGGER.warning("LoxoneRoomControllerV2: unknown active mode %r", val)
                    else:
                        _LOGGER.debug("LoxoneRoomControllerV2: unknown active mode %r", val)
            update = True

        if update:
            self.async_write_ha_state()

    def get_state_value(self, name, default=None):
        uuid = self._states.get(name)
        if uuid is None or not isinstance(uuid, str):
            return default
        return self._state_attr_values.get(uuid, default)

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "is_overridden": self.is_overridden,
            "demand": self._demand,
            "operating_mode": self.get_state_value("operatingMode"),
            "active_mode": self.get_state_value("activeMode"),
            "current_mode": self.get_state_value("currentMode"),
            "op_mode": self.operating_mode,
            "active_state": self.active_state,
        }

    @property
    def is_overridden(self) -> bool:
        return (self.get_state_value("overrideReason") or 0) > 0

    @property
    def active_mode(self) -> ActiveMode:
        return self.active_state.mode

    @property
    def temperature_unit(self) -> str:
        """
        Return the unit of measurement used by the platform (PC-23).

        The Loxone Config app allows the designer to set an arbitrary
        format string for the room controller's input temperature sensor.
        We assume that the format string contains the unit of temperature,
        and default to Celsius if not.
        """
        return temperature_unit_from_format(self.details.get("format") if isinstance(self.details, dict) else None)

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self.get_state_value("tempActual")

    def set_temperature(self, **kwargs):
        """Set new target temperature (routes through :func:`plan_set_temperature`)."""
        state = {
            _COMFORT_C: self.get_state_value("comfortTemperature"),
            _COMFORT_COOL: self.get_state_value("comfortTemperatureCool"),
            _FROST_PROTECT: self.get_state_value("frostProtectTemperature"),
            _HEAT_PROTECT: self.get_state_value("heatProtectTemperature"),
            _ABSENT_MIN: self.get_state_value("absentMinOffset"),
            _ABSENT_MAX: self.get_state_value("absentMaxOffset"),
            "range_possible": self._range_possible,
        }
        for value in plan_set_temperature(self.operating_mode.value[0], self.active_mode.value, kwargs, state):
            self._send(value)

        self.async_write_ha_state()

    @property
    def target_temperature(self) -> float | None:
        """
        Return the temperature we try to reach.

        PC-12: never falls off the end — before tempTarget is seen the
        comfort temperature is the sensible single target, so a non-None
        value is returned while TARGET_TEMPERATURE is advertised.
        """
        if self.active_state.mode is ActiveMode.FIXED_DYNAMIC:
            return self.active_state.value
        return self.get_state_value("tempTarget") or self.get_state_value("comfortTemperature")

    @property
    def target_temperature_step(self) -> float | None:
        """Return the supported step of target temperature."""
        return 0.5

    @property
    def target_temperature_high(self) -> float | None:
        """Return the highbound target temperature we try to reach."""
        mode = self.operating_mode
        active = self.active_mode
        if mode in (OperatingMode.AUTO_HEAT_COOL, OperatingMode.MANUAL_HEAT_COOL):
            if active == ActiveMode.COMFORT:
                return self.get_state_value("comfortTemperatureCool")
            if active == ActiveMode.ECONOMY:
                temp = self.get_state_value("comfortTemperatureCool")
                offset = self.get_state_value("absentMaxOffset")
                if temp is None or offset is None:
                    return None
                return temp + offset
            if active == ActiveMode.BUILDING_PROTECT:
                return self.get_state_value("heatProtectTemperature")
            if active == ActiveMode.OFF:
                return None

    @property
    def target_temperature_low(self) -> float | None:
        """Return the lowbound target temperature we try to reach."""
        mode = self.operating_mode
        active = self.active_mode
        if mode in (OperatingMode.AUTO_HEAT_COOL, OperatingMode.MANUAL_HEAT_COOL):
            if active == ActiveMode.COMFORT:
                return self.get_state_value("comfortTemperature")
            if active == ActiveMode.ECONOMY:
                temp = self.get_state_value("comfortTemperature")
                offset = self.get_state_value("absentMinOffset")
                if temp is None or offset is None:
                    return None
                return temp - offset
            if active == ActiveMode.BUILDING_PROTECT:
                return self.get_state_value("frostProtectTemperature")
            if active == ActiveMode.OFF:
                return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """
        Return the current HVAC action (heating, cooling, idle).

        PC-28: without a ClimateController demand event (``_demand is None``)
        the action is derived from the controller's own valve/prepare
        states instead of staying IDLE forever.
        """
        if self.get_state_value("openWindow"):
            return HVACAction.OFF
        if self.get_state_value("prepareState") == 1:
            return HVACAction.PREHEATING
        if self._demand is not None:
            if self._demand == -1:
                return HVACAction.COOLING
            if self._demand == 1:
                return HVACAction.HEATING
            return HVACAction.IDLE
        valve_heat = self.get_state_value("valveHeat")
        if valve_heat is not None and valve_heat > 0:
            return HVACAction.HEATING
        valve_cool = self.get_state_value("valveCool")
        if valve_cool is not None and valve_cool > 0:
            return HVACAction.COOLING
        return HVACAction.IDLE

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac operation ie. heat, cool mode."""
        is_auto = self.operating_mode in (
            OperatingMode.AUTO_HEAT_COOL,
            OperatingMode.AUTO_COOL,
            OperatingMode.AUTO_HEAT,
        )
        if is_auto and not self.is_overridden:
            return HVACMode.AUTO
        if self.operating_mode is OperatingMode.AUTO_HEAT_COOL and self.is_overridden:
            return HVACMode.HEAT_COOL
        return self.operating_mode.value[1]

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available hvac operation modes."""
        return capabilities_to_hvac_modes(
            (self.details or {}).get("possibleCapabilities", 3) if isinstance(self.details, dict) else 3,
            range_allowed=self._range_possible,
        )

    def set_hvac_mode(self, hvac_mode: HVACMode):
        """Set new target hvac mode (PC-26: dispatch through the shared V2 table)."""
        target_mode = self._autoMode if hvac_mode == HVACMode.AUTO else hvac_to_loxone(hvac_mode, table="v2")
        if target_mode is None:
            _LOGGER.debug("No V2 Loxone mode for hvac mode %r", hvac_mode)
            return

        is_auto = self.operating_mode in (
            OperatingMode.AUTO_HEAT_COOL,
            OperatingMode.AUTO_HEAT,
            OperatingMode.AUTO_COOL,
            OperatingMode.MANUAL_HEAT_COOL,
        )
        if is_auto and self.is_overridden:
            self._send("stopOverride")

        self._send(f"setOperatingMode/{target_mode}")

        self.schedule_update_ha_state()

    @property
    def preset_mode(self):
        """
        Return the current preset mode.

        PC-32: FIXED (14) / FIXED_DYNAMIC (112) are stable preset literals,
        not a ``None`` hole in ``timerModes``.
        """
        if self.get_state_value("openWindow"):
            return PRESET_PAUSED_WINDOW
        if self.active_mode is ActiveMode.FIXED:
            return PRESET_FIXED
        if self.active_mode is ActiveMode.FIXED_DYNAMIC:
            return PRESET_FIXED_DYNAMIC
        return self.get_mode_from_id(self.active_mode.value) or PRESET_FIXED

    @property
    def preset_modes(self):
        """
        Return a (constant) list of available preset modes.

        PC-32: static list — schedule and the fixed presets are always
        offered, no more dynamic hiding that made the *current* value
        disappear from its own options.
        """
        modes = [mode["name"] for mode in self._modeList if mode.get("id") not in ("stop",)]
        modes.append(PRESET_SCHEDULE)
        modes.append(PRESET_FIXED)
        modes.append(PRESET_FIXED_DYNAMIC)
        if self.get_state_value("openWindow"):
            modes.append(PRESET_PAUSED_WINDOW)
        return modes

    def set_preset_mode(self, preset_mode: str):
        """Set new preset mode."""
        if preset_mode in (PRESET_PAUSED_WINDOW, PRESET_FIXED, PRESET_FIXED_DYNAMIC):
            return  # informational only — controlled by timer / fix-frozen value
        mode_id = next((mode["id"] for mode in self._modeList if mode["name"] == preset_mode), None)
        if mode_id is None:
            _LOGGER.debug("Unknown preset mode %r for %s (%s)", preset_mode, self._lox_name, self.type)
            return
        if mode_id == "stop":
            # PC-20: the correct command is setOperatingMode/0, not setOperationMode/0
            self._send("setOperatingMode/0")
        else:
            self._send(f"override/{mode_id}")
        self.schedule_update_ha_state()


# ------------------ AC CONTROL --------------------------------------------------------
def ac_hvac_action(status, mode):
    """
    Map the AcControl `status`/`mode` states to an HVACAction (WP-6.8).

    Intended semantics (hand-derived from the Loxone AcControl state
    table the properties above already encode): unit off (``status``
    falsy) → IDLE; mode 2 → HEATING, 3 → COOLING, 4 → DRYING, 5 → FAN;
    the auto mode (1) — and any unknown value — has no single action,
    so it reports IDLE while ``hvac_mode`` still shows AUTO.
    """
    if not status:
        return HVACAction.IDLE
    if mode == 2:
        return HVACAction.HEATING
    if mode == 3:
        return HVACAction.COOLING
    if mode == 4:
        return HVACAction.DRYING
    if mode == 5:
        return HVACAction.FAN
    return HVACAction.IDLE


class LoxoneAcControl(LoxoneEntity, ClimateEntity, ABC):
    """Representation of a ACControl Loxone device."""

    def __init__(self, **kwargs):
        _LOGGER.debug("Input AcControl: %s", kwargs)
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]

        self._stateAttribUuids = kwargs.get("states") or {}
        self._stateAttribValues = {}
        self.type = "AcControl"

        # PC-24: fan/airflow tables are parsed ONCE here, from the structure
        # definition, so the properties never re-parse (or json.loads(None)).
        self._fan_modes = _parse_mode_list(self._stateAttribUuids.get("fanspeeds"), "fanspeeds")
        self._airflow_modes = _parse_mode_list(self._stateAttribUuids.get("airflows"), "airflows")

        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: every state stream the handler consumes.
        return frozenset(uuid for uuid in self._stateAttribUuids.values() if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, event):
        update = False

        for key in set(self._stateAttribUuids.values()) & event.keys():
            self._stateAttribValues[key] = event[key]
            update = True

        if update:
            self.async_write_ha_state()

    def get_state_value(self, name, default=None):
        """
        Return the latest value for a state key, or ``default``.

        PC-10: ``.get`` with a default mirrors the V2 signature — a control
        without that state can no longer raise KeyError.
        """
        uuid = self._stateAttribUuids.get(name)
        if uuid is None or not isinstance(uuid, str):
            return default
        return self._stateAttribValues.get(uuid, default)

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

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """
        Return supported features based on which states exist (PC-24):
        FAN_MODE / SWING_MODE are advertised only for controls that
        actually carry fan/airflow states.
        """
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        )
        if self._fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self._airflow_modes:
            features |= ClimateEntityFeature.SWING_MODE
        return features

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self.get_state_value("temperature")

    def set_temperature(self, **kwargs):
        """Set new target temperature"""
        temp = kwargs.get("temperature")
        if temp is None:
            return
        self._send(f"setTarget/{temp}")

    async def async_set_temperature(self, **kwargs):
        """
        Event-loop entry point (PC-14): HA's generic `async_set_temperature`
        runs the sync version in the executor, which would send from the
        wrong loop.  #398 polish: make the service actually work.
        """
        self.set_temperature(**kwargs)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """
        Return hvac operation ie. heat, cool mode.

        Need to be one of HVAC_MODE_*.  PC-10: unknown/missing states read
        safely via ``get_state_value`` instead of raising.
        """
        if self.get_state_value("status"):
            mode = self.get_state_value("mode")
            if mode == 2:
                return HVACMode.HEAT
            if mode == 3:
                return HVACMode.COOL
            if mode == 4:
                return HVACMode.DRY
            if mode == 5:
                return HVACMode.FAN_ONLY
            return HVACMode.AUTO
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """What the unit is doing right now (WP-6.8, #398 polish)."""
        return ac_hvac_action(self.get_state_value("status"), self.get_state_value("mode"))

    def set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode (PC-25: OFF sends only ``off``)."""
        if hvac_mode == HVACMode.OFF:
            self._send("off")
            return

        mode = 1
        match hvac_mode:
            case HVACMode.HEAT:
                mode = 2
            case HVACMode.COOL:
                mode = 3
            case HVACMode.DRY:
                mode = 4
            case HVACMode.FAN_ONLY:
                mode = 5

        self._send("on")
        self._send(f"setMode/{mode}")

    async def async_set_hvac_mode(self, hvac_mode):
        """
        Event-loop entry point (PC-14): HA would otherwise run the sync
        version in the executor.  #398 polish.
        """
        self.set_hvac_mode(hvac_mode)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """
        Return the list of available hvac operation modes.

        Need to be a subset of HVAC_MODES.
        """
        return [
            HVACMode.OFF,
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
            HVACMode.AUTO,
        ]

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement used by the platform (PC-23)."""
        return temperature_unit_from_format(self.details.get("format") if isinstance(self.details, dict) else None)

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self.get_state_value("targetTemperature")

    @property
    def target_temperature_step(self) -> float | None:
        """Return the supported step of target temperature."""
        return 0.5

    @property
    def fan_mode(self) -> str | None:
        """Return current fan mode (None when the control has no fan state)."""
        if not self._fan_modes:
            return None
        for mode in self._fan_modes:
            if self.get_state_value("fan") == mode["id"]:
                return mode["name"]
        return None

    def set_fan_mode(self, fan_mode):
        """Set new target fan mode (PC-24: never sends ``setFan/None``)."""
        fan_id = next((o["id"] for o in self._fan_modes if o["name"] == fan_mode), None)
        if fan_id is None:
            _LOGGER.debug("Unknown fan mode %r for %s (%s)", fan_mode, self._lox_name, self.type)
            return
        self._send(f"setFan/{fan_id}")

    async def async_set_fan_mode(self, fan_mode):
        """Event-loop entry point (PC-14).  #398 polish."""
        self.set_fan_mode(fan_mode)

    @property
    def fan_modes(self) -> list[str]:
        """Return the list of available fan modes ([] when not offered)."""
        return [o["name"] for o in self._fan_modes]

    @property
    def swing_mode(self) -> str | None:
        """Return current swing mode (None when the control has no airflow state)."""
        if not self._airflow_modes:
            return None
        for mode in self._airflow_modes:
            if self.get_state_value("ventMode") == mode["id"]:
                return mode["name"]
        return None

    def set_swing_mode(self, swing_mode):
        """Set new target swing mode (PC-24: never sends ``setAirDir/None``)."""
        airflow_id = next((o["id"] for o in self._airflow_modes if o["name"] == swing_mode), None)
        if airflow_id is None:
            _LOGGER.debug("Unknown swing mode %r for %s (%s)", swing_mode, self._lox_name, self.type)
            return
        self._send(f"setAirDir/{airflow_id}")

    async def async_set_swing_mode(self, swing_mode):
        """Event-loop entry point (PC-14).  #398 polish."""
        self.set_swing_mode(swing_mode)

    @property
    def swing_modes(self) -> list[str]:
        """Return the list of available swing modes ([] when not offered)."""
        return [o["name"] for o in self._airflow_modes]
