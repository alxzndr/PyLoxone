"""Coverage-gap regression tests for the climate and cover target/command paths.

Pinned here:

* ``LoxoneRoomControllerV2.target_temperature_low`` / ``target_temperature_high``
  — the dual-setpoint bounds HA reads while ``TARGET_TEMPERATURE_RANGE`` is
  advertised.  Only the two dual operating modes have bounds at all; within
  them each active mode reads different state keys (comfort, comfort +/- the
  absent offsets, the protection temperatures) and OFF / MANUAL have none.
* ``LoxoneGate.open_cover`` / ``close_cover`` and
  ``LoxoneJalousie.open_cover`` / ``close_cover`` — the command actually put on
  the wire, and the "already at that end stop" short-circuits that send
  nothing at all.

Both cover classes are built without a coordinator, so
``LoxoneEntity._send`` takes its documented bus fallback and the exact
``{uuid, value}`` payload is recorded off a stub ``hass.bus.async_fire``.
Every expected value is a hand-written literal derived from the code's
documented mapping, never from running the code under test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.loxone.climate import LoxoneRoomControllerV2
from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.cover import LoxoneGate, LoxoneJalousie

# --------------------------------------------------------------------------- #
# climate: LoxoneRoomControllerV2 target_temperature_low / _high
# --------------------------------------------------------------------------- #
OPERATING_MODE = "st-operating-mode"
ACTIVE_MODE = "st-active-mode"
COMFORT = "st-comfort-temperature"
COMFORT_COOL = "st-comfort-temperature-cool"
ABSENT_MIN = "st-absent-min-offset"
ABSENT_MAX = "st-absent-max-offset"
FROST_PROTECT = "st-frost-protect"
HEAT_PROTECT = "st-heat-protect"

FULL_STATES = {
    "operatingMode": OPERATING_MODE,
    "activeMode": ACTIVE_MODE,
    "comfortTemperature": COMFORT,
    "comfortTemperatureCool": COMFORT_COOL,
    "absentMinOffset": ABSENT_MIN,
    "absentMaxOffset": ABSENT_MAX,
    "frostProtectTemperature": FROST_PROTECT,
    "heatProtectTemperature": HEAT_PROTECT,
}

# OperatingMode ids (climate.py): 0 = AUTO_HEAT_COOL, 1 = AUTO_HEAT,
# 2 = AUTO_COOL, 3 = MANUAL_HEAT_COOL, 4 = MANUAL_HEAT, 5 = MANUAL_COOL.
OP_AUTO_HEAT_COOL = 0
OP_AUTO_HEAT = 1
OP_AUTO_COOL = 2
OP_MANUAL_HEAT_COOL = 3
OP_MANUAL_HEAT = 4

# ActiveMode ids: 0 = ECONOMY, 1 = COMFORT, 2 = BUILDING_PROTECT,
# 3 = MANUAL, 4 = OFF.
ACT_ECONOMY = 0
ACT_COMFORT = 1
ACT_BUILDING_PROTECT = 2
ACT_MANUAL = 3
ACT_OFF = 4

# The temperatures fed in below.
T_COMFORT = 21.0
T_COMFORT_COOL = 25.0
T_ABSENT_MIN = 3.0
T_ABSENT_MAX = 2.0
T_FROST_PROTECT = 7.0
T_HEAT_PROTECT = 30.0


def _controller(states: dict[str, str] | None = None) -> LoxoneRoomControllerV2:
    """A bare V2 room controller; no hass is needed to read the target bounds."""
    return LoxoneRoomControllerV2(
        hass=None,
        states=dict(FULL_STATES if states is None else states),
        details={"timerModes": [], "possibleCapabilities": 3, "singleComfortTemperature": False},
        hvac_auto_mode=0,
        room="",
        name="Controller",
        uuid="uuid-cov-climate-0001",
        uuidAction="uuid-cov-climate-0001",
        cat="heating",
    )


def _feed_temperatures(entity: LoxoneRoomControllerV2) -> None:
    entity.event_handler(
        {
            COMFORT: T_COMFORT,
            COMFORT_COOL: T_COMFORT_COOL,
            ABSENT_MIN: T_ABSENT_MIN,
            ABSENT_MAX: T_ABSENT_MAX,
            FROST_PROTECT: T_FROST_PROTECT,
            HEAT_PROTECT: T_HEAT_PROTECT,
        }
    )


@pytest.mark.parametrize("operating_mode", [OP_AUTO_HEAT_COOL, OP_MANUAL_HEAT_COOL])
@pytest.mark.parametrize(
    ("active_mode", "expected_low", "expected_high"),
    [
        # COMFORT: the two comfort setpoints, verbatim.
        (ACT_COMFORT, T_COMFORT, T_COMFORT_COOL),
        # ECONOMY: comfort shifted by the absent offsets (21.0 - 3.0 / 25.0 + 2.0).
        (ACT_ECONOMY, 18.0, 27.0),
        # BUILDING_PROTECT: the frost / heat protection temperatures.
        (ACT_BUILDING_PROTECT, T_FROST_PROTECT, T_HEAT_PROTECT),
        # OFF: no bounds at all.
        (ACT_OFF, None, None),
        # MANUAL: no branch of its own -> no bounds either.
        (ACT_MANUAL, None, None),
    ],
)
def test_v2_target_bounds_per_active_mode(operating_mode, active_mode, expected_low, expected_high) -> None:
    """In the two dual operating modes each active mode has its own pair of bounds."""
    entity = _controller()
    _feed_temperatures(entity)
    entity.event_handler({OPERATING_MODE: operating_mode, ACTIVE_MODE: active_mode})

    assert entity.target_temperature_low == expected_low
    assert entity.target_temperature_high == expected_high


@pytest.mark.parametrize("operating_mode", [OP_AUTO_HEAT, OP_AUTO_COOL, OP_MANUAL_HEAT])
def test_v2_single_setpoint_modes_have_no_range_bounds(operating_mode) -> None:
    """Outside AUTO_HEAT_COOL / MANUAL_HEAT_COOL there is no low/high pair, only a single target."""
    entity = _controller()
    _feed_temperatures(entity)
    entity.event_handler({OPERATING_MODE: operating_mode, ACTIVE_MODE: ACT_COMFORT})

    assert entity.target_temperature_low is None
    assert entity.target_temperature_high is None
    # The single-target property still answers (PC-12).
    assert entity.target_temperature == T_COMFORT


def test_v2_economy_bounds_are_none_without_the_absent_offsets() -> None:
    """A controller whose structure file has no absent-offset states reports no ECONOMY bounds."""
    states = {k: v for k, v in FULL_STATES.items() if k not in ("absentMinOffset", "absentMaxOffset")}
    entity = _controller(states)
    entity.event_handler({COMFORT: T_COMFORT, COMFORT_COOL: T_COMFORT_COOL})
    entity.event_handler({OPERATING_MODE: OP_AUTO_HEAT_COOL, ACTIVE_MODE: ACT_ECONOMY})

    assert entity.target_temperature_low is None
    assert entity.target_temperature_high is None


def test_v2_economy_bounds_are_none_before_the_comfort_values_arrive() -> None:
    """The offsets alone are not enough: no comfort value seen yet means no bounds."""
    entity = _controller()
    entity.event_handler({ABSENT_MIN: T_ABSENT_MIN, ABSENT_MAX: T_ABSENT_MAX})
    entity.event_handler({OPERATING_MODE: OP_AUTO_HEAT_COOL, ACTIVE_MODE: ACT_ECONOMY})

    assert entity.target_temperature_low is None
    assert entity.target_temperature_high is None


# --------------------------------------------------------------------------- #
# cover: the open/close commands of Gate and Jalousie
# --------------------------------------------------------------------------- #
GATE_UUID = "U68KEE6-COV-0002-0000-000000000002"
JALOUSIE_UUID = "U68KEE6-COV-0002-0000-000000000001"
POS_UUID = "36333734-01c1-9336-ffff-d30316c676c3000201"
ACTIVE_UUID = "36333734-01c1-9336-ffff-d30316c676c3000202"


def _recording_hass() -> tuple[SimpleNamespace, list]:
    """A stub hass whose bus records every fired event (the ``_send`` fallback)."""
    fired: list[tuple[str, dict]] = []

    def _fire(event_type, event_data=None):
        fired.append((event_type, event_data))

    bus = SimpleNamespace(fire=_fire, async_fire=_fire, async_listen=lambda *a, **k: lambda: None)
    return SimpleNamespace(bus=bus), fired


def _gate() -> tuple[LoxoneGate, list]:
    hass, fired = _recording_hass()
    entity = LoxoneGate(
        hass=hass,
        name="Cov Gate",
        uuidAction=GATE_UUID,
        room="",
        type="gate",
        details={"animation": 1},
        states={"position": POS_UUID, "active": ACTIVE_UUID},
        isSecured=False,
    )
    entity.schedule_update_ha_state = lambda *a, **k: None
    entity.async_write_ha_state = lambda *a, **k: None
    return entity, fired


def _jalousie() -> tuple[LoxoneJalousie, list]:
    hass, fired = _recording_hass()
    entity = LoxoneJalousie(
        hass=hass,
        name="Cov Jalousie",
        uuidAction=JALOUSIE_UUID,
        room="",
        type="jalousie",
        details={"animation": 0},
        states={"position": POS_UUID, "active": ACTIVE_UUID},
        isSecured=False,
    )
    entity.schedule_update_ha_state = lambda *a, **k: None
    entity.async_write_ha_state = lambda *a, **k: None
    return entity, fired


def test_gate_open_and_close_put_the_documented_commands_on_the_bus() -> None:
    """A gate at an unknown position sends the plain ``open`` / ``close`` commands."""
    entity, fired = _gate()
    assert entity.current_cover_position is None

    entity.close_cover()
    entity.open_cover()

    assert fired == [
        (SENDDOMAIN, {"uuid": GATE_UUID, "value": "close"}),
        (SENDDOMAIN, {"uuid": GATE_UUID, "value": "open"}),
    ]


def test_gate_does_not_resend_at_its_end_stops() -> None:
    """Closing a closed gate (0) or opening an open one (100.0) sends nothing."""
    entity, fired = _gate()

    entity._position = 0
    entity.close_cover()
    entity._position = 100.0
    entity.open_cover()

    assert fired == []

    # ... and the opposite command is still sent from those positions.
    entity._position = 0
    entity.open_cover()
    entity._position = 100.0
    entity.close_cover()

    assert fired == [
        (SENDDOMAIN, {"uuid": GATE_UUID, "value": "open"}),
        (SENDDOMAIN, {"uuid": GATE_UUID, "value": "close"}),
    ]


def test_jalousie_open_and_close_send_fullup_and_fulldown() -> None:
    """A jalousie at a partial position sends the Loxone FullDown / FullUp commands."""
    entity, fired = _jalousie()

    entity._position = 50.0
    entity.close_cover()
    entity._position = 50.0
    entity.open_cover()

    assert fired == [
        (SENDDOMAIN, {"uuid": JALOUSIE_UUID, "value": "FullDown"}),
        (SENDDOMAIN, {"uuid": JALOUSIE_UUID, "value": "FullUp"}),
    ]


def test_jalousie_does_not_resend_at_its_end_stops() -> None:
    """A jalousie already fully down (0, its construction default) or fully up (100.0) sends nothing."""
    entity, fired = _jalousie()
    assert entity.current_cover_position == 0

    entity.close_cover()
    entity._position = 100.0
    entity.open_cover()

    assert fired == []


def test_jalousie_without_a_known_position_only_flips_its_closed_flag() -> None:
    """No position stream value yet: the entity records the end stop locally and sends nothing."""
    entity, fired = _jalousie()

    entity._position = None
    entity.close_cover()
    assert entity.is_closed is True
    assert fired == []

    entity._position = None
    entity.open_cover()
    assert entity.is_closed is False
    assert fired == []
