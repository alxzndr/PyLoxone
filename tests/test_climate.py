"""WP-4.1 regression tests for the climate platform.

Covered findings: PC-02, PC-10, PC-12, PC-13, PC-16 (climate lines), PC-20,
PC-23, PC-24, PC-25, PC-26 (VERIFY), PC-28, PC-32, PC-41 (climate lines).

Expected values are derived by hand from the LoxAPP3 fixture
(``tests/fixtures/LoxAPP3.json``, WP-0.2) and written as literals. Expected
offset arithmetic (e.g. ``24.0 - 23.0 = 1.0``) is hand-computed, never
produced by the code under test.
"""

from __future__ import annotations

import json
import logging
import pathlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature

from custom_components.loxone.climate import (
    HVAC_TO_LOXONE_V2,
    LEGACY_ROOM_CONTROLLER_MODES,
    LOXONE_TO_HVAC_V2,
    PRESET_FIXED_DYNAMIC,
    PRESET_FIXED,
    capabilities_to_hvac_modes,
    hvac_to_loxone,
    legacy_mode_to_hvac,
    plan_set_temperature,
    temperature_unit_from_format,
)
from custom_components.loxone.const import PRESET_PAUSED_WINDOW, PRESET_SCHEDULE
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FANSPEEDS_JSON = '[{"id": 0, "name": "Auto"}, {"id": 1, "name": "Low"}, {"id": 2, "name": "High"}]'
AIRFLOWS_JSON = '[{"id": 0, "name": "Auto"}, {"id": 1, "name": "Down"}, {"id": 2, "name": "Up"}]'

# ---------------------------------------------------------------------------
# Fixture literals (hand-transcribed from tests/fixtures/LoxAPP3.json)
# ---------------------------------------------------------------------------
V2_ACTION_UUID = "63746c3a-0126-9647-ffff-f6f6d20436f6000038"
AC_ACTION_UUID = "63746c3a-0145-9204-ffff-16c6c7761793000069"
AC_TARGET_UUID = "2d646464-7265-9e0aa-4cff-1c87409cd75d"


def _fresh_loxapp3() -> dict:
    """Load the LoxAPP3 fixture from disk (own copy per test)."""
    return json.loads((REPO_ROOT / "tests/fixtures/LoxAPP3.json").read_text())


def _fake_open_with(config):
    async def _fake_open(self, session=None):
        self.structure_file = config
        self.miniserver_version = config.get("softwareVersion")
        self.connected = True
        self.connection = Mock()
        self.connection.protocol.state.name = "OPEN"
        self._session_key = b"\x00" * 32
        return self

    return patch.object(LoxoneConnection, "open", _fake_open)


def _event(data: dict):
    """PS-13/CORE-27: handlers are now sync `@callback`s taking the plain
    ``{uuid: value}`` slice the dispatcher delivers — this is identity."""
    return dict(data)


def feed(entity, data: dict):
    """Run an entity's (now synchronous) event_handler from a sync test."""
    entity.event_handler(_event(data))


def _v2_make(states: dict, details: dict | None = None):
    """Build a V2 room controller against a fake bus that records SENDDOMAIN."""
    from custom_components.loxone.climate import LoxoneRoomControllerV2
    from custom_components.loxone.const import SENDDOMAIN

    out = []

    def _fire(domain, data=None):
        if domain == SENDDOMAIN:
            out.append(data.get("value"))

    bus = SimpleNamespace(fire=_fire, async_fire=_fire, async_listen=lambda *a, **k: lambda f: None)
    hass = SimpleNamespace(bus=bus)

    e = LoxoneRoomControllerV2(
        hass=hass,
        states=states,
        details=details
        if details is not None
        else {"timerModes": [], "possibleCapabilities": 3, "singleComfortTemperature": False},
        hvac_auto_mode=0,
        room="",
        name="Controller",
        uuid="uuid-v2-0000",
        uuidAction="uuid-v2-0000",
        cat="heating",
    )
    e.async_schedule_update_ha_state = lambda *a, **k: None
    e.schedule_update_ha_state = lambda *a, **k: None
    e.async_write_ha_state = lambda *a, **k: None
    return e, out


def _ac_make(states: dict, details: dict | None = None):
    """Build an AcControl against a fake bus that records SENDDOMAIN."""
    from custom_components.loxone.climate import LoxoneAcControl
    from custom_components.loxone.const import SENDDOMAIN

    out = []

    def _fire(domain, data=None):
        if domain == SENDDOMAIN:
            out.append(data.get("value"))

    bus = SimpleNamespace(fire=_fire, async_fire=_fire, async_listen=lambda *a, **k: lambda f: None)
    hass = SimpleNamespace(bus=bus)

    e = LoxoneAcControl(
        hass=hass,
        states=states,
        details=details or {},
        room="AC",
        name="AC",
        uuid="uuid-ac-0000",
        uuidAction="uuid-ac-0000",
        cat="cool",
    )
    e.async_schedule_update_ha_state = lambda *a, **k: None
    e.schedule_update_ha_state = lambda *a, **k: None
    e.async_write_ha_state = lambda *a, **k: None
    return e, out


# ---------------------------------------------------------------------------
# PC-23: shared temperature unit helper (°C/°F contains, fallbacks, default)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("℃", UnitOfTemperature.CELSIUS),  # "°" at index 0: find("°") == 0 was falsy
        ("°C", UnitOfTemperature.CELSIUS),
        ("%.1f °C", UnitOfTemperature.CELSIUS),
        ("%.1f °F", UnitOfTemperature.FAHRENHEIT),
        ("°F", UnitOfTemperature.FAHRENHEIT),
        ("C", UnitOfTemperature.CELSIUS),  # legacy "C without degree symbol" fallback
        ("F", UnitOfTemperature.FAHRENHEIT),  # legacy "F without degree symbol" fallback
        ("", UnitOfTemperature.CELSIUS),
        (None, UnitOfTemperature.CELSIUS),  # no format at all -> Celsius
    ],
)
def test_temperature_unit_from_format(fmt, expected):
    assert temperature_unit_from_format(fmt) == expected


@pytest.mark.parametrize("cls_name", ["LoxoneRoomController", "LoxoneRoomControllerV2", "LoxoneAcControl"])
def test_all_platforms_use_the_shared_unit_helper(cls_name):
    """All three climate classes keep a temperature_unit property that routes
    through the single shared helper (two pre-WP-4.1 implementations
    disagreed: AcControl's window check skipped a bare 'C')."""
    import custom_components.loxone.climate as climate_mod

    prop = getattr(climate_mod.__dict__[cls_name], "temperature_unit", None)
    assert prop is not None, f"{cls_name} lost its temperature_unit property"


def test_accontrol_temperature_unit_celsius_not_found():
    """PC-23: '°C' at index 0 must read as Celsius (old find('°') == 0 -> F)."""
    e, _ = _ac_make({"temperature": "st1"}, details={"format": "°C"})
    assert e.temperature_unit == UnitOfTemperature.CELSIUS
    e2, _ = _ac_make({"temperature": "st1"}, details={"format": "°F"})
    assert e2.temperature_unit == UnitOfTemperature.FAHRENHEIT


def test_v2_temperature_unit_bare_c():
    e, _ = _v2_make({"activeMode": "st1"}, details={"timerModes": [], "format": "C"})
    assert e.temperature_unit == UnitOfTemperature.CELSIUS


# ---------------------------------------------------------------------------
# PC-26 (VERIFY): mode tables, single shared source
# ---------------------------------------------------------------------------
def test_v2_mode_table_roundtrip():
    """Whatever code the V2 writer dispatches must decode to the HVAC mode
    the user asked for (PC-26: read and write from ONE table pair)."""
    for hvac, lox in HVAC_TO_LOXONE_V2.items():
        assert HVACMode(hvac) is not None
        assert LOXONE_TO_HVAC_V2[lox] == hvac
    # The read side covers every numeric code, including -1 (off) and the
    # manual 4/5 variants.
    assert set(LOXONE_TO_HVAC_V2) == {-1, 0, 1, 2, 3, 4, 5}


def test_v2_off_code():
    assert hvac_to_loxone(HVACMode.OFF) == -1
    assert LOXONE_TO_HVAC_V2[-1] == HVACMode.OFF
    assert HVACMode.OFF not in HVAC_TO_LOXONE_V2 or HVAC_TO_LOXONE_V2[HVACMode.OFF] == -1


def test_legacy_mode_table_roundtrip():
    """PC-26: read and write sides of the legacy controller come from ONE
    table, so no dispatch can ever hand out a code the reader misdecodes
    (old: `set_hvac_mode(OFF)` sent code 1 = manual heat)."""
    for lox, hvac in LEGACY_ROOM_CONTROLLER_MODES.items():
        assert legacy_mode_to_hvac(lox) == hvac
        assert hvac_to_loxone(hvac, table="legacy") == lox
    # The legacy V1 table keeps the codes the legacy controller documented:
    # 0=Auto, 1=Manual/Heat, 2=Manual/Cool, 3=Auto/Heat-Cool, 4=Off.
    assert legacy_mode_to_hvac(0) == HVACMode.AUTO
    assert legacy_mode_to_hvac(1) == HVACMode.HEAT
    assert legacy_mode_to_hvac(2) == HVACMode.COOL
    assert legacy_mode_to_hvac(3) == HVACMode.HEAT_COOL
    assert legacy_mode_to_hvac(4) == HVACMode.OFF


def test_legacy_set_hvac_mode_read_write_agreement():
    """Every hvac mode the legacy controller exposes must round-trip through
    the single table: what it sends is what its own `hvac_mode` reads back."""
    from custom_components.loxone.climate import LoxoneRoomController

    e = LoxoneRoomController(
        hass=SimpleNamespace(bus=SimpleNamespace(fire=lambda d, data=None: None)),
        states={},
        room="L",
        name="L",
        uuid="uuid-legacy-00",
        uuidAction="uuid-legacy-00",
        cat="heating",
    )
    out = []

    def _rec(domain, data=None):
        out.append(data.get("value"))

    e.hass.bus.fire = _rec
    e.hass.bus.async_fire = _rec
    e.schedule_update_ha_state = lambda *a, **k: None

    for hvac in e.hvac_modes:
        out.clear()
        e.set_hvac_mode(hvac)
        assert len(out) == 1
        code = int(out[0].rsplit("/", 1)[1])
        assert legacy_mode_to_hvac(code) == hvac

    # Explicit OFF regression: sends the table's OFF code, never manual heat.
    out.clear()
    e.set_hvac_mode(HVACMode.OFF)
    assert out == ["setMode/4"]
    assert legacy_mode_to_hvac(1) == HVACMode.HEAT  # code 1 stays "heat", not off


def test_capabilities_to_hvac_modes_table():
    assert capabilities_to_hvac_modes(3) == [
        HVACMode.AUTO,
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.HEAT_COOL,
    ]
    assert capabilities_to_hvac_modes(1) == [HVACMode.AUTO, HVACMode.OFF, HVACMode.HEAT]
    assert capabilities_to_hvac_modes(2) == [HVACMode.AUTO, HVACMode.OFF, HVACMode.COOL]
    assert capabilities_to_hvac_modes(None) == [
        HVACMode.AUTO,
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.HEAT_COOL,
    ]
    assert capabilities_to_hvac_modes(3, range_allowed=False) == [
        HVACMode.AUTO,
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
    ]


def test_v2_hvac_modes_property_from_details_bits():
    e, _ = _v2_make({"activeMode": "s1"}, details={"timerModes": [], "possibleCapabilities": 1})
    assert e.hvac_modes == [HVACMode.AUTO, HVACMode.OFF, HVACMode.HEAT]
    e2, _ = _v2_make({"activeMode": "s2"}, details={"timerModes": [], "possibleCapabilities": 2})
    assert e2.hvac_modes == [HVACMode.AUTO, HVACMode.OFF, HVACMode.COOL]
    e3, _ = _v2_make({"activeMode": "s3"}, details={"timerModes": [], "possibleCapabilities": 3})
    assert e3.hvac_modes == [HVACMode.AUTO, HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]


# ---------------------------------------------------------------------------
# plan_set_temperature — pure planner, all branches
# ---------------------------------------------------------------------------
_OFFSTATE = {
    "range_possible": True,
    "comfort_temperature": 21.0,
    "comfort_temperature_cool": 24.0,
    "frost_protect_temperature": 7.5,
    "heat_protect_temperature": 28.0,
    "absent_min_offset": 2.0,
    "absent_max_offset": 2.0,
}


@pytest.mark.parametrize(
    ("op", "active", "kw", "state_patch", "expected"),
    [
        # FIXED_DYNAMIC: override/<temp*2560+112>/<temp> (hand: 7.5*2560+112 = 33552.0)
        pytest.param(-1, 112, {"temperature": 7.5}, {}, ["override/19312.0/7.5"], id="fixed_dynamic_user_temp"),
        # MANUAL mode: direct manual temperature
        pytest.param(0, 3, {"temperature": 20.0}, {}, ["setManualTemperature/20.0"], id="manual"),
        pytest.param(-1, 3, {}, {}, [], id="manual_no_temp"),
        # MANUAL_HEAT / MANUAL_COOL also route to the manual command
        pytest.param(4, 3, {"temperature": 19.0}, {}, ["setManualTemperature/19.0"], id="manual_heat"),
        pytest.param(5, 1, {"temperature": 25.0}, {}, ["setManualTemperature/25.0"], id="manual_cool"),
        # AUTOMATIC single target: offset relative to comfort (hand: 20.5-21.0 = -0.5)
        pytest.param(1, 1, {"temperature": 20.5}, {}, ["setComfortModeTemp/-0.5"], id="auto_comfort_user"),
        pytest.param(
            1, 1, {"temperature": 21.0}, {}, ["setComfortModeTemp/0.0"], id="auto_comfort_zero_offset_still_sent"
        ),
        pytest.param(1, 1, {"temperature": 20.5}, {"comfort_temperature": None}, [], id="auto_comfort_unknown_state"),
        pytest.param(1, 1, {}, {}, [], id="auto_no_temp_key"),
        # DUAL-comfort high target (cool side)
        pytest.param(3, 1, {"target_temp_high": 23.0}, {}, ["setComfortTemperatureCool/23.0"], id="dual_comfort_high"),
        pytest.param(3, 1, {"target_temp_high": 24.0}, {}, [], id="dual_comfort_high_equal_silenced"),
        pytest.param(
            3, 1, {"target_temp_low": 20.0}, {}, ["setComfortTemperature/20.0"], id="low_leg_without_high_fires_low"
        ),
        pytest.param(
            3, 1, {"target_temp_high": 23.0}, {"comfort_temperature_cool": None}, [], id="dual_high_cool_state_missing"
        ),
        # ECONOMY high: desired = new_temp - comfort_cool (hand: 25.0-24.0 = 1.0;
        # 25.5-24.0 = 1.5, equals the reported absent offset -> silenced;
        # target below comfort clamps at the 0.5 floor)
        pytest.param(3, 0, {"target_temp_high": 25.0}, {}, ["setAbsentMaxTemperature/1.0"], id="dual_economy_high"),
        pytest.param(
            3, 0, {"target_temp_high": 25.5}, {"absent_max_offset": 1.5}, [], id="dual_economy_high_equal_silenced"
        ),
        pytest.param(
            3, 0, {"target_temp_high": 23.5}, {}, ["setAbsentMaxTemperature/0.5"], id="dual_economy_high_clamp_floor"
        ),
        # BUILDING_PROTECT high: compared against heatProtectTemperature
        pytest.param(
            3, 2, {"target_temp_high": 27.0}, {}, ["setecoplusmaxtemperature/27.0"], id="dual_building_protect_high"
        ),
        pytest.param(3, 2, {"target_temp_high": 28.0}, {}, [], id="dual_building_protect_high_equal_silenced"),
        # DUAL-comfort low target (heat side)
        pytest.param(3, 1, {"target_temp_low": 20.0}, {}, ["setComfortTemperature/20.0"], id="dual_comfort_low"),
        pytest.param(3, 1, {"target_temp_low": 21.0}, {}, [], id="dual_comfort_low_equal_silenced"),
        pytest.param(
            3,
            1,
            {"target_temp_high": 24.5},
            {},
            ["setComfortTemperatureCool/24.5"],
            id="high_leg_without_low_fires_high",
        ),
        # ECONOMY low: desired = comfort - new_temp (hand: 21.0-19.0 = 2.0;
        # 21.0-20.5 = 0.5, equals reported absent offset -> silenced;
        # target above comfort clamps at the 0.5 floor)
        pytest.param(3, 0, {"target_temp_low": 18.0}, {}, ["setAbsentMinTemperature/3.0"], id="dual_economy_low"),
        pytest.param(
            3, 0, {"target_temp_low": 20.5}, {"absent_min_offset": 0.5}, [], id="dual_economy_low_equal_silenced"
        ),
        pytest.param(
            3, 0, {"target_temp_low": 21.4}, {}, ["setAbsentMinTemperature/0.5"], id="dual_economy_low_clamp_floor"
        ),
        pytest.param(
            3, 0, {"target_temp_low": 20.5}, {"absent_min_offset": 0.5}, [], id="dual_economy_low_equal_silenced"
        ),
        # BUILDING_PROTECT low: compared against frostProtectTemperature
        pytest.param(3, 2, {"target_temp_low": 7.5}, {}, [], id="dual_building_protect_low_equal_silenced"),
        pytest.param(
            3, 2, {"target_temp_low": 8.0}, {}, ["setecoplusmintemperature/8.0"], id="dual_building_protect_low"
        ),
        # PC-02 regression: in BUILDING_PROTECT, only target_temp_low provided
        # used to raise `NameError: comfort_cool`.
        pytest.param(
            3,
            2,
            {"target_temp_low": 8.0},
            {},
            ["setecoplusmintemperature/8.0"],
            id="pc02_nameerror_regression_low_alone",
        ),
        # Both targets are dispatched: high list first, then low list
        pytest.param(
            3,
            1,
            {"target_temp_high": 25.0, "target_temp_low": 19.0},
            {},
            ["setComfortTemperatureCool/25.0", "setComfortTemperature/19.0"],
            id="dual_both_targets",
        ),
        # ECONOMY clamps at 0.5 for both sides (hand: 23.9-24.0 = -0.1 -> 0.5;
        # 21.0-21.6 = -0.6 -> 0.5)
        pytest.param(
            3,
            0,
            {"target_temp_high": 23.5, "target_temp_low": 21.6},
            {},
            ["setAbsentMaxTemperature/0.5", "setAbsentMinTemperature/0.5"],
            id="economy_clamp_both_sides",
        ),
        # Without range the dual op mode dispatches a single manual target
        pytest.param(
            3,
            0,
            {"temperature": 19.0},
            {"range_possible": False},
            ["setManualTemperature/19.0"],
            id="dual_op_no_range_manual",
        ),
    ],
)
def test_plan_set_temperature(op, active, kw, state_patch, expected):
    state = dict(_OFFSTATE)
    state.update(state_patch)
    got = plan_set_temperature(op, active, kw, state)
    assert got == expected


def test_plan_set_temperature_is_pure():
    """The planner returns a fresh list and does not mutate its inputs."""
    kw = {"temperature": 20.5}
    state = dict(_OFFSTATE)
    a = plan_set_temperature(0, 1, kw, state)
    b = plan_set_temperature(0, 1, kw, state)
    assert a == b and a is not b
    assert kw == {"temperature": 20.5}
    assert state == _OFFSTATE


# ---------------------------------------------------------------------------
# V2 class-level behaviors
# ---------------------------------------------------------------------------
def _full_v2_states() -> dict:
    return {
        "activeMode": "st_active",
        "operatingMode": "st_op",
        "tempTarget": "st_target",
        "comfortTemperature": "st_c",
        "comfortTemperatureCool": "st_cc",
        "absentMinOffset": "st_amin",
        "absentMaxOffset": "st_amax",
        "frostProtectTemperature": "st_fp",
        "heatProtectTemperature": "st_hp",
    }


def test_v2_target_temperature_in_dual_building_protect():
    """PC-12: in dual mode the old code fell off the end of target_temperature
    -> None while TARGET_TEMPERATURE was advertised."""
    e, _ = _v2_make(_full_v2_states(), details={"timerModes": [], "possibleCapabilities": 3})
    feed(e, {"st_op": 3, "st_active": 2, "st_target": 21.5})
    assert e.target_temperature == 21.5
    # the feature advertisement is not TARGET + TARGET_RANGE at the same time
    feats = e.supported_features
    assert feats & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert not (feats & ClimateEntityFeature.TARGET_TEMPERATURE)


def test_v2_target_temperature_falls_back_to_comfort():
    """Until tempTarget is seen, the fallback to comfort temperature keeps
    the property a number while it is advertised (never a bare fall-off)."""
    e, _ = _v2_make(_full_v2_states(), details={"timerModes": []})
    assert e.target_temperature is None or isinstance(e.target_temperature, (int, float))
    feed(e, {"st_c": 20.5})
    assert e.target_temperature == 20.5
    feed(e, {"st_op": 0, "st_active": 0, "st_target": 22.25})
    assert e.target_temperature == 22.25
    feed(e, {"st_c": 19.75})  # tempTarget wins once present
    assert e.target_temperature == 22.25


def test_v2_single_target_still_advertised_in_dual_mode():
    """With heat-only capabilities there is no range: target_temperature
    must still deliver a number in dual COMFORT (old: fell off -> None)."""
    e, _ = _v2_make(_full_v2_states(), details={"timerModes": [], "possibleCapabilities": 1})
    feed(e, {"st_op": 3, "st_active": 1, "st_target": 21.0})
    feats = e.supported_features
    assert feats & ClimateEntityFeature.TARGET_TEMPERATURE
    assert not (feats & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)
    assert e.target_temperature == 21.0


@pytest.mark.asyncio
async def test_v2_unknown_mode_keeps_state_warns_only(caplog):
    """PC-13: unknown operatingMode/activeMode values must not raise and must
    keep the previous mode (old: ValueError interrupted the state event loop)."""
    e, out = _v2_make({"operatingMode": "st_op", "activeMode": "st_active"})
    e.event_handler(_event({"st_op": 0, "st_active": 0}))
    assert e.operating_mode.value[0] == 0
    with caplog.at_level(logging.WARNING):
        e.event_handler(_event({"st_op": 99, "st_active": 55}))
        e.event_handler(_event({"st_op": 64, "st_active": -3}))
    assert e.operating_mode.value[0] == 0  # previous state kept
    assert e.active_state.mode.value == 0  # ... and active mode too
    assert out == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_v2_hvac_action_fallback_from_own_valves():
    """PC-28: with no ClimateController demand event, hvac_action follows
    the controller's own valve/prepare states (old: permanently IDLE)."""
    states = dict(_full_v2_states())
    states.update(
        {
            "prepareState": "st_prep",
            "valveHeat": "st_vh",
            "valveCool": "st_vc",
            "openWindow": "st_ow",
            "overrideReason": "st_ov",
        }
    )
    e, _ = _v2_make(states)
    assert e.hvac_action == HVACAction.IDLE
    feed(e, {"st_vh": 0.5})
    assert e.hvac_action == HVACAction.HEATING
    feed(e, {"st_vh": 0.0, "st_vc": 1.0})
    assert e.hvac_action == HVACAction.COOLING
    feed(e, {"st_vh": 0.0, "st_vc": 0.0, "st_prep": 1})
    assert e.hvac_action == HVACAction.PREHEATING
    feed(e, {"st_prep": 0, "st_ow": 1})
    assert e.hvac_action == HVACAction.OFF


def test_v2_demand_event_still_wins_when_it_arrives():
    """The ClimateController demand keeps priority once it has been seen."""
    states = dict(_full_v2_states())
    states["valveHeat"] = "st_vh"
    e, _ = _v2_make(states)
    feed(e, {"st_vh": 0.0})
    # PS-18: the demand arrives via the per-(entry, room) dispatcher signal;
    # the entity-side hook is the bound method the signal subscribes to.
    e.on_climate_demand(-1)
    assert e.hvac_action == HVACAction.COOLING
    e.on_climate_demand(0)
    assert e.hvac_action == HVACAction.IDLE


def test_v2_preset_mode_fixed_literals_and_commands():
    """PC-32: FIXED (14) / FIXED_DYNAMIC (112) read back as stable presets;
    the schedule preset is always offered; `stop` sends `setOperatingMode/0`
    (PC-20), never `setOperationMode/0`."""
    modes = [{"id": 0, "name": "Away"}, {"id": 1, "name": "Here"}]
    states = {"activeMode": "st_active", "operatingMode": "st_op", "openWindow": "st_ow", "overrideReason": "st_ov"}
    e, out = _v2_make(states, details={"timerModes": modes, "possibleCapabilities": 3})
    feed(e, {"st_active": 14})
    assert e.preset_mode == PRESET_FIXED
    feed(e, {"st_active": 112})
    assert e.preset_mode == PRESET_FIXED_DYNAMIC
    feed(e, {"st_active": 1})
    assert e.preset_mode == "Here"
    feed(e, {"st_ow": 1})
    assert e.preset_mode == PRESET_PAUSED_WINDOW
    # preset_modes is a constant: schedule and both fixed presets are always there
    pm = e.preset_modes
    assert PRESET_SCHEDULE in pm
    assert PRESET_FIXED in pm
    assert PRESET_FIXED_DYNAMIC in pm
    assert PRESET_PAUSED_WINDOW in pm
    feed(e, {"st_ow": 0})
    # fixed presets are informational: selecting one sends nothing
    e.set_preset_mode(PRESET_FIXED)
    assert out == []
    e.set_preset_mode(PRESET_FIXED_DYNAMIC)
    assert out == []
    # schedule preset -> correctly spelled command
    e.set_preset_mode(PRESET_SCHEDULE)
    assert out == ["setOperatingMode/0"]
    e.set_preset_mode("Here")
    assert out[-1] == "override/1"
    e.set_preset_mode("Bogus")
    assert out[-1] == "override/1"  # unchanged


def test_v2_mode_list_does_not_mutate_shared_details():
    """PC-16: constructing the entity must not modify the structure's
    details (old: 'stop' was injected into the shared timerModes list)."""
    details = {"timerModes": [{"name": "Here", "id": 1}], "possibleCapabilities": 3}
    e, _ = _v2_make({"activeMode": "s"}, details=details)
    assert details["timerModes"] == [{"name": "Here", "id": 1}]
    assert [m["name"] for m in e._modeList] == ["Here", PRESET_SCHEDULE]
    # a second entity on the same details sees the same pristine list
    e2, _ = _v2_make({"activeMode": "s2"}, details=details)
    assert [m["name"] for m in e2._modeList] == ["Here", PRESET_SCHEDULE]


def test_v2_missing_timer_modes_in_details_does_not_crash():
    """PC-16: `details["timerModes"]` used to be directly wired; a structure
    entry without it must not attach like the platform."""
    e, _ = _v2_make({"activeMode": "s", "operatingMode": "o"}, details={"possibleCapabilities": 3})
    assert [m["name"] for m in e._modeList] == [PRESET_SCHEDULE]
    assert PRESET_SCHEDULE in e.preset_modes
    # every preset the entity lists stays selectable
    e.set_preset_mode(PRESET_SCHEDULE)


def test_v2_set_temperature_end_to_end():
    """The planner's commands reach the bus through the entity (PC-02 etc.)."""
    e, out = _v2_make(_full_v2_states(), details={"timerModes": [], "possibleCapabilities": 3})
    # comfort states must be known for the range branch to fire
    feed(e, {"st_op": 3, "st_active": 2, "st_c": 21.0, "st_cc": 24.0, "st_fp": 7.5, "st_hp": 28.0})
    e.set_temperature(target_temp_high=27.0)
    assert out == ["setecoplusmaxtemperature/27.0"]
    e.set_temperature(target_temp_low=7.5)
    assert out == ["setecoplusmaxtemperature/27.0"]  # equal frost -> no-op
    e.set_temperature(target_temp_low=8.0)
    assert out[-1] == "setecoplusmintemperature/8.0"


def test_v2_legacy_rgc_still_builds_from_legacy_states():
    """The legacy (non-V2) room controller keeps its documented property
    surface and never throws for the common missing-state case."""
    from custom_components.loxone.climate import LoxoneRoomController

    states = {"tempActual": "sA", "tempTarget": "sT", "mode": "sM"}
    e = LoxoneRoomController(
        hass=SimpleNamespace(bus=SimpleNamespace(fire=lambda d, data=None: None)),
        states=states,
        room="Foyer",
        name="Foyer",
        uuid="uuid-v1-0000",
        uuidAction="uuid-v1-0000",
        cat="heating",
    )
    e.async_write_ha_state = lambda *a, **k: None
    e.schedule_update_ha_state = lambda *a, **k: None
    assert e.name == "Foyer Climate"
    assert e.target_temperature is None
    assert e.current_temperature is None
    assert e.hvac_mode == HVACMode.OFF
    feed(e, {"sM": 1, "sT": 21.0, "sA": 19.5})
    assert e.hvac_mode == HVACMode.HEAT
    assert e.target_temperature == 21.0
    assert e.current_temperature == 19.5
    # unknown mode code: falls back to OFF, not an exception
    feed(e, {"sM": 47})
    assert e.hvac_mode == HVACMode.OFF


# ---------------------------------------------------------------------------
# AcControl (PC-10 / PC-24 / PC-25)
# ---------------------------------------------------------------------------
def test_accontrol_missing_states_never_raise():
    """PC-10: properties that read unknown states default, no KeyError."""
    e, _ = _ac_make({})
    assert e.get_state_value("none") is None
    assert e.hvac_mode == HVACMode.OFF
    assert e.fan_modes == []
    assert e.swing_modes == []
    assert e.fan_mode is None
    assert e.swing_mode is None
    assert e.target_temperature is None
    assert e.current_temperature is None


def test_accontrol_features_from_present_states():
    """PC-24: FAN/SWING advertised only when the control HAS those states;
    fan_modes/swing_modes yield [] (not None) when the data is missing."""
    e0, _ = _ac_make({}, details={})
    assert e0.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert e0.supported_features & ClimateEntityFeature.TURN_ON
    assert not (e0.supported_features & ClimateEntityFeature.FAN_MODE)
    assert not (e0.supported_features & ClimateEntityFeature.SWING_MODE)

    e1, _ = _ac_make({"fan": "st_fan", "fanspeeds": FANSPEEDS_JSON}, details={})
    assert e1.supported_features & ClimateEntityFeature.FAN_MODE
    assert not (e1.supported_features & ClimateEntityFeature.SWING_MODE)
    assert e1.fan_modes == ["Auto", "Low", "High"]
    feed(e1, {"st_fan": 2})
    assert e1.fan_mode == "High"

    e2, _ = _ac_make(
        {"fan": "st_fan", "fanspeeds": FANSPEEDS_JSON, "ventMode": "st_v", "airflows": AIRFLOWS_JSON}, details={}
    )
    assert e2.supported_features & ClimateEntityFeature.SWING_MODE
    assert e2.swing_modes == ["Auto", "Down", "Up"]
    assert e2.swing_mode is None  # ventMode not yet reported
    feed(e2, {"st_v": 2})
    assert e2.swing_mode == "Up"


def test_accontrol_set_fan_and_swing_no_none_commands():
    """set_fan_mode/set_swing_mode never send `setFan/None`/`setAirDir/None`
    for unknown names (old: json.loads on a missing state raised or None)."""
    e, out = _ac_make({"fan": "st_fan", "fanspeeds": FANSPEEDS_JSON, "ventMode": "st_v", "airflows": AIRFLOWS_JSON})
    e.set_fan_mode("Low")
    assert out == ["setFan/1"]
    e.set_fan_mode("Turbo")
    assert out == ["setFan/1"]  # unchanged
    e.set_swing_mode("Down")
    assert out == ["setFan/1", "setAirDir/1"]
    e.set_swing_mode("Sideways")
    assert out == ["setFan/1", "setAirDir/1"]  # unchanged
    # no fanspeeds state at all: no-op, no exception
    e0, out0 = _ac_make({})
    e0.set_fan_mode("Low")
    e0.set_swing_mode("Down")
    assert out0 == []


def test_accontrol_set_hvac_off_single_off_command():
    """PC-25: OFF sends just `off` (old: `off` followed by `setMode/1`)."""
    e, out = _ac_make({"status": "st_status"})
    e.set_hvac_mode(HVACMode.OFF)
    assert out == ["off"]
    e.set_hvac_mode(HVACMode.HEAT)
    assert out == ["off", "on", "setMode/2"]
    e.set_hvac_mode(HVACMode.DRY)
    assert out == ["off", "on", "setMode/2", "on", "setMode/4"]
    e.set_hvac_mode(HVACMode.AUTO)
    assert out[-1] == "setMode/1"


def test_accontrol_hvac_mode_from_status_and_mode():
    e, _ = _ac_make({"status": "st_status", "mode": "st_mode"})
    feed(e, {"st_status": 0})
    assert e.hvac_mode == HVACMode.OFF
    feed(e, {"st_status": 1, "st_mode": 1})
    assert e.hvac_mode == HVACMode.AUTO
    feed(e, {"st_mode": 2})
    assert e.hvac_mode == HVACMode.HEAT
    feed(e, {"st_mode": 3})
    assert e.hvac_mode == HVACMode.COOL
    feed(e, {"st_mode": 5})
    assert e.hvac_mode == HVACMode.FAN_ONLY


def test_accontrol_set_temperature_sends_target():
    e, out = _ac_make({"setTemperature": "st_set"})
    e.set_temperature(temperature=23.5)
    assert out == ["setTarget/23.5"]
    e.set_temperature()  # no key -> no command
    assert out == ["setTarget/23.5"]


# ---------------------------------------------------------------------------
# Full-entry setup (WP-0.2 fixtures)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_accontrol_from_fixture_without_fanspeeds(hass, mock_connection, mock_entry, enable_custom_integrations):
    """Acceptance: an AcControl from the fixture WITHOUT fanspeeds/airflows
    still sets up, stays present, and writes state on a temperature event."""
    config = _fresh_loxapp3()
    for key in ("fanspeeds", "airflows", "fan", "ventMode"):
        config["controls"][AC_ACTION_UUID]["states"].pop(key, None)

    mock_entry.add_to_hass(hass)
    with _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    ac_entity_id = reg.async_get_entity_id("climate", "loxone", AC_ACTION_UUID)
    if ac_entity_id is None:
        # unique id may be carried bare; fall back to a lax scan
        for entry in er.async_get(hass).entities:
            if entry.unique_id and AC_ACTION_UUID in entry.unique_id and entry.domain == "climate":
                ac_entity_id = entry.entity_id
                break
    assert ac_entity_id is not None, "AcControl not registered (PC-24/PC-10)"
    assert hass.states.get(ac_entity_id) is not None

    mock_connection.feed(AC_TARGET_UUID, 23.5)
    await hass.async_block_till_done()
    state = hass.states.get(ac_entity_id)
    assert state is not None
    # fan/swing are not offered for this control, so no bogus fan_mode state
    attrs = state.attributes or {}
    assert attrs.get("fan_mode") in (None, "unknown") or state.state in ("unknown", "off")
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_fixture_structure_not_mutated_by_setup(hass, mock_connection, mock_entry, enable_custom_integrations):
    """PC-16 end-to-end: after setup the structure's timerModes must still
    equal the fixture (no 'stop' injection, no details rewrites)."""
    config = _fresh_loxapp3()
    timer_before = [dict(m) for m in config["controls"][V2_ACTION_UUID]["details"]["timerModes"]]
    mock_entry.add_to_hass(hass)
    with _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert config["controls"][V2_ACTION_UUID]["details"]["timerModes"] == timer_before
    assert not any(m.get("id") == "stop" for m in config["controls"][V2_ACTION_UUID]["details"]["timerModes"])
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_v2_hvac_action_after_setup_valve_feed(hass, mock_connection, mock_entry, enable_custom_integrations):
    """PC-28 end-to-end: feeding the room controller's own valve states
    updates `hvac_action` without any ClimateController event."""
    config = _fresh_loxapp3()
    v2_states = config["controls"][V2_ACTION_UUID]["states"]
    if "valveHeat" not in v2_states or "valveCool" not in v2_states:
        # Give the controller the valve states a real V2 structure exports
        # (unique per-test fake uuids); the fixture may or may not carry them.
        v2_states["valveHeat"] = "36333734-0127-9336-ffff-0b11e6000000000001"
        v2_states["valveCool"] = "36333734-0127-9336-ffff-0b11e6000000000002"

    mock_entry.add_to_hass(hass)
    with _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    v2_entity_id = reg.async_get_entity_id("climate", "loxone", V2_ACTION_UUID)
    if v2_entity_id is None:
        for entry in er.async_get(hass).entities:
            if entry.unique_id and V2_ACTION_UUID in entry.unique_id and entry.domain == "climate":
                v2_entity_id = entry.entity_id
                break
    assert v2_entity_id is not None, "RoomControllerV2 not registered"

    state = hass.states.get(v2_entity_id)
    assert state is not None
    assert state.attributes.get("hvac_action") in (None, "idle")

    mock_connection.feed(v2_states["valveHeat"], 0.33)
    await hass.async_block_till_done()
    assert hass.states.get(v2_entity_id).attributes.get("hvac_action") == "heating"

    mock_connection.feed(v2_states["valveHeat"], 0.0)
    mock_connection.feed(v2_states["valveCool"], 0.2)
    await hass.async_block_till_done()
    assert hass.states.get(v2_entity_id).attributes.get("hvac_action") == "cooling"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
