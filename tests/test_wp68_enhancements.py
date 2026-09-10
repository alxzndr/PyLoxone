"""WP-6.8 regression tests:

  * alarm panel: ``ARM_NIGHT`` / ``ARM_VACATION`` and the arming-delay
    attributes (JoDehli/PyLoxone#323);
  * cover: Gate ``SET_POSITION``;
  * select: the Jalousie sun-automation select (off / auto / shade);
  * climate: AcControl ``hvac_action`` (JoDehli/PyLoxone#398 polish).

Expected command values and state strings are hand-derived literals for the
fixture controls in ``tests/fixtures/LoxAPP3.json`` and for the Loxone
CoverControl / AcControl / Alarm command tables documented by the protocol —
never computed by calling the code under test.

Items marked **VERIFY** (night/vacation arming semantics, the gate
`manualPosition` orientation, the `Shade` pin) are pinned here to their
*intended* semantics; a live-Miniserver check is required before relying on
them (see the PR body).
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components.climate.const import HVACAction
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.alarm_control_panel import (
    alarm_arm_delay_attributes,
    alarm_night_arm_value,
    alarm_vacation_arm_value,
)
from custom_components.loxone.climate import LoxoneAcControl
from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.cover import LoxoneGate, gate_set_position_command
from custom_components.loxone.select import (
    LoxoneJalousieAuto,
    jalousie_auto_command,
    jalousie_auto_option_from_state,
)

# --------------------------------------------------------------------------- #
# Fixture identities (hand-transcribed from tests/fixtures/LoxAPP3.json)
# --------------------------------------------------------------------------- #
ENTRANCE_ALARM_ACTION = "63746c3a-019c-96d6-ffff-0416c61726d30001c1"
ENTRANCE_ALARM_STATES = {
    "armed": "36333734-01cb-9336-ffff-d303161372d3000c11",
    "level": "36333734-01cb-9336-ffff-d303161372d3000c12",
    "disabledMove": "36333734-01cb-9336-ffff-d303161372d3000c13",
    "armedAt": "36333734-01cb-9336-ffff-d303161372d3000c14",
    "nextLevelAt": "36333734-01cb-9336-ffff-d303161372d3000c15",
    "armedDelay": "36333734-01cb-9336-ffff-d303161372d3000c16",
    "armedDelayTotal": "36333734-01cb-9336-ffff-d303161372d3000c17",
}
TERRACE_JALOUSIE_ACTION = "63746c3a-019b-9766-ffff-6a616c6f756e3000c0"
TERRACE_JALOUSIE_AUTO = "36333734-01c8-9336-ffff-d303137282d3000c05"
GARDEN_GATE_ACTION = "63746c3a-017f-9726-ffff-56e204761746000127"
GARDEN_GATE_POSITION = "36333734-0180-9336-ffff-d303137662d3000128"
AC_ACTION = "63746c3a-0145-9204-ffff-16c6c7761793000069"
AC_STATUS = "2d646464-7265-9d0d4-4cff-0cca85f0f54f"
AC_MODE = "2d646464-7265-9d030-4cff-445a3577e18a"
AC_TARGET = "2d646464-7265-9e0aa-4cff-1c87409cd75d"
AC_FAN = "2d646464-7265-9e0a6-4cff-b0b9658d7dbc"
AC_VENT = "2d646464-7265-9e009-4cff-6b35bc4986e1"


def _stub_write(entity) -> None:
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    entity.schedule_update_ha_state = lambda *a, **k: None
    entity.async_write_ha_state = lambda *a, **k: None


# --------------------------------------------------------------------------- #
# Pure helpers: hand-derived tables
# --------------------------------------------------------------------------- #
def test_jalousie_auto_command_table() -> None:
    """Option label -> Loxone CoverControl command (VERIFY: intended mapping)."""
    assert jalousie_auto_command("Off") == "NoAuto"
    assert jalousie_auto_command("Auto") == "auto"
    assert jalousie_auto_command("Shade") == "shade"
    assert jalousie_auto_command("something else") is None
    assert jalousie_auto_command("") is None


def test_jalousie_auto_option_from_state_table() -> None:
    """``autoState`` is a 0/1 stream: any non-zero value means the sun
    automation is active."""
    assert jalousie_auto_option_from_state(1) == "Auto"
    assert jalousie_auto_option_from_state(0) == "Off"
    assert jalousie_auto_option_from_state(True) == "Auto"
    assert jalousie_auto_option_from_state(False) == "Off"
    assert jalousie_auto_option_from_state(None) == "Off"
    assert jalousie_auto_option_from_state(2) == "Auto"


def test_gate_set_position_command_table() -> None:
    """HA position -> ``manualPosition/<clamped>`` (VERIFY: orientation)."""
    assert gate_set_position_command(0) == "manualPosition/0.0"
    assert gate_set_position_command(42) == "manualPosition/42.0"
    assert gate_set_position_command(55.5) == "manualPosition/55.5"
    assert gate_set_position_command(100) == "manualPosition/100.0"
    # off-band and missing values clamp into the 0..100 scale
    assert gate_set_position_command(-7) == "manualPosition/0.0"
    assert gate_set_position_command(250) == "manualPosition/100.0"
    assert gate_set_position_command(None) == "manualPosition/0.0"


def test_alarm_night_vacation_arm_values() -> None:
    """Night arming is the delayed (home) arm, vacation is the non-delayed
    (away) arm — the two arming commands a Loxone alarm knows (#323,
    VERIFY: confirm on a live Miniserver)."""
    assert alarm_night_arm_value() == "delayedon/1"
    assert alarm_vacation_arm_value() == "delayedon/0"


def test_alarm_arm_delay_attributes_table() -> None:
    """Raw stream values -> int seconds (or None), the surfaced type."""
    assert alarm_arm_delay_attributes(None, None) == {"armed_delay": None, "armed_delay_total_delay": None}
    assert alarm_arm_delay_attributes(10.0, 60.0) == {"armed_delay": 10, "armed_delay_total_delay": 60}
    assert alarm_arm_delay_attributes(10.9, "60") == {"armed_delay": 10, "armed_delay_total_delay": 60}
    assert alarm_arm_delay_attributes("junk", 30) == {"armed_delay": None, "armed_delay_total_delay": 30}
    assert alarm_arm_delay_attributes(0, 0) == {"armed_delay": 0, "armed_delay_total_delay": 0}


def test_ac_hvac_action_table() -> None:
    """AcControl (status, mode) -> HVACAction."""
    from custom_components.loxone.climate import ac_hvac_action

    assert ac_hvac_action(0, 2) is HVACAction.IDLE  # unit off, whatever the mode
    assert ac_hvac_action(None, 3) is HVACAction.IDLE
    assert ac_hvac_action(1, 2) is HVACAction.HEATING
    assert ac_hvac_action(1, 3) is HVACAction.COOLING
    assert ac_hvac_action(1, 4) is HVACAction.DRYING
    assert ac_hvac_action(1, 5) is HVACAction.FAN
    # auto (1) has no single action; unknown modes are idle, not a guess
    assert ac_hvac_action(1, 1) is HVACAction.IDLE
    assert ac_hvac_action(1, 9) is HVACAction.IDLE


# --------------------------------------------------------------------------- #
# Units: alarm night/vacation
# --------------------------------------------------------------------------- #
def _alarm(hass, **overrides) -> "object":
    kwargs = {
        "hass": hass,
        "name": "Test Alarm",
        "nameRu": "Test Alarm",
        "uuidAction": "U68KEE6-ALA-0001-0000-000000000002",
        "room": "",
        "type": "Alarm",
        "code": None,
        "details": {"modeList": ["Disarmed", "Armed Home", "Armed Away"]},
        "states": {
            "armed": "36333734-01a8-9336-ffff-d303161372d3011111",
            "level": "36333734-01a9-9336-ffff-d303161372d3066666",
            "armedDelay": "36333734-01a8-9336-ffff-d303161372d3011222",
            "armedDelayTotal": "36333734-01a8-9336-ffff-d303161372d3011333",
        },
        "isSecured": False,
    }
    kwargs.update(overrides)
    from custom_components.loxone.alarm_control_panel import LoxoneAlarm

    return LoxoneAlarm(**kwargs)


async def test_alarm_advertises_night_and_vacation_features(hass) -> None:
    from homeassistant.components.alarm_control_panel.const import AlarmControlPanelEntityFeature

    e = _alarm(hass)
    _stub_write(e)
    assert e.supported_features & AlarmControlPanelEntityFeature.ARM_NIGHT
    assert e.supported_features & AlarmControlPanelEntityFeature.ARM_VACATION
    # the home/away services remain
    assert e.supported_features & AlarmControlPanelEntityFeature.ARM_HOME
    assert e.supported_features & AlarmControlPanelEntityFeature.ARM_AWAY


async def test_alarm_arm_night_sends_delayedon_1(hass) -> None:
    """#323: the night icon must arm the way `arm_home` does."""
    e = _alarm(hass, isSecured=False)
    _stub_write(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_alarm_arm_night()
    await hass.async_block_till_done()

    assert fired == [{"uuid": "U68KEE6-ALA-0001-0000-000000000002", "value": "delayedon/1"}]


async def test_alarm_arm_vacation_sends_delayedon_0(hass) -> None:
    e = _alarm(hass, isSecured=False)
    _stub_write(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_alarm_arm_vacation()
    await hass.async_block_till_done()

    assert fired == [{"uuid": "U68KEE6-ALA-0001-0000-000000000002", "value": "delayedon/0"}]


async def test_alarm_arm_vacation_secured_goes_out_on_secured_channel(hass) -> None:
    from custom_components.loxone.const import SECUREDSENDDOMAIN

    e = _alarm(hass, isSecured=True)
    _stub_write(e)
    fired = []
    hass.bus.async_listen(SECUREDSENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_alarm_arm_vacation(code="0000")
    await hass.async_block_till_done()

    assert fired == [{"uuid": "U68KEE6-ALA-0001-0000-000000000002", "value": "delayedon/0", "code": "0000"}]


async def test_alarm_extras_surface_arming_delay_as_int_seconds(hass) -> None:
    """The delay attributes keep one stable type (int seconds / None)."""
    e = _alarm(hass, isSecured=False)
    _stub_write(e)
    assert e.extra_state_attributes["armed_delay"] is None
    assert e.extra_state_attributes["armed_delay_total_delay"] is None

    e.event_handler({"36333734-01a8-9336-ffff-d303161372d3011222": 10.0})
    assert e.extra_state_attributes["armed_delay"] == 10
    e.event_handler({"36333734-01a8-9336-ffff-d303161372d3011333": 60.0})
    assert e.extra_state_attributes["armed_delay_total_delay"] == 60
    # a pending delay counts as an arming state
    assert e.alarm_state == "arming"


# --------------------------------------------------------------------------- #
# Units: Gate SET_POSITION
# --------------------------------------------------------------------------- #
def _gate(hass, **overrides) -> LoxoneGate:
    kwargs = {
        "hass": hass,
        "name": "Test Gate",
        "nameRu": "Test Gate",
        "uuidAction": "U68KEE6-GAT-0001-0000-000000000004",
        "room": "",
        "type": "Gate",
        "details": {"animation": 0},
        "states": {"position": "g-pos", "active": "g-act"},
        "isSecured": False,
    }
    kwargs.update(overrides)
    return LoxoneGate(**kwargs)


def test_gate_advertises_set_position_only_with_position_stream() -> None:
    from homeassistant.components.cover import CoverEntityFeature

    e = _gate(None)
    _stub_write(e)
    assert e.supported_features & CoverEntityFeature.SET_POSITION
    assert e.supported_features & CoverEntityFeature.OPEN
    assert e.supported_features & CoverEntityFeature.CLOSE
    assert e.supported_features & CoverEntityFeature.STOP

    bare = _gate(None, states={"active": "g-act"})
    assert not (bare.supported_features & CoverEntityFeature.SET_POSITION)


async def test_gate_set_cover_position_sends_manual_position(hass) -> None:
    """WP-6.8: partial positioning for gates (VERIFY: orientation)."""
    e = _gate(hass)
    _stub_write(e)
    sent = []
    e._send = lambda value, *_a, **_k: sent.append(value)

    await e.async_set_cover_position(position=42)
    assert sent == ["manualPosition/42.0"]

    sent.clear()
    await e.async_set_cover_position(position=100)
    assert sent == ["manualPosition/100.0"]

    # a gate without a position stream still clamps (service never reaches
    # it, but the helper must not raise)
    bare = _gate(hass, states={"active": "g-act"})
    _stub_write(bare)
    sent2 = []
    bare._send = lambda value, *_a, **_k: sent2.append(value)
    await bare.async_set_cover_position(position=7)
    assert sent2 == ["manualPosition/7.0"]


# --------------------------------------------------------------------------- #
# Units: Jalousie sun-auto select
# --------------------------------------------------------------------------- #
def _jalousie_auto(**overrides) -> LoxoneJalousieAuto:
    kwargs = {
        "hass": None,
        "name": "Test Terrace",
        "nameRu": "Test Terrace",
        "uuidAction": "U68KEE6-JLS-0001-0000-000000000005",
        "room": "",
        "type": "Jalousie",
        "details": {"animation": 0, "isAutomatic": True},
        "states": {"autoState": "ta-auto", "position": "ta-pos"},
        "isSecured": False,
    }
    kwargs.update(overrides)
    return LoxoneJalousieAuto(**kwargs)


def test_jalousie_auto_identity() -> None:
    e = _jalousie_auto()
    _stub_write(e)
    # sub-entity of the Jalousie device: own unique id, short name
    assert e._attr_unique_id == "U68KEE6-JLS-0001-0000-000000000005/sun-auto"
    assert e._attr_name == "Sun auto"
    assert e._attr_options == ["Off", "Auto", "Shade"]
    assert e._state_uuids() == frozenset({"ta-auto"})


async def test_jalousie_auto_sends_its_commands() -> None:
    e = _jalousie_auto()
    _stub_write(e)
    sent = []
    e._send = lambda value, *_a, **_k: sent.append(value)

    await e.async_select_option("Off")
    assert sent == ["NoAuto"]
    assert e.current_option == "Off"

    e.event_handler({"ta-auto": 1})  # the server says: automation active
    assert e.current_option == "Auto"

    await e.async_select_option("Shade")
    assert sent == ["NoAuto", "shade"]
    # the one-shot shade pins until the next autoState value arrives
    assert e.current_option == "Shade"

    e.event_handler({"ta-auto": 0})
    assert e.current_option == "Off"


async def test_jalousie_auto_unknown_option_sends_nothing() -> None:
    e = _jalousie_auto()
    _stub_write(e)
    sent = []
    e._send = lambda value, *_a, **_k: sent.append(value)

    await e.async_select_option("sideways")
    assert sent == []
    assert e.current_option == "Off"


# --------------------------------------------------------------------------- #
# Units: AcControl hvac_action
# --------------------------------------------------------------------------- #
def _ac(states: dict) -> tuple[LoxoneAcControl, list]:
    from custom_components.loxone.const import SENDDOMAIN

    out: list = []

    def _fire(domain, data=None):
        if domain == SENDDOMAIN:
            out.append(data.get("value"))

    bus = SimpleNamespace(fire=_fire, async_fire=_fire, async_listen=lambda *a, **k: lambda f: None)
    hass = SimpleNamespace(bus=bus)
    e = LoxoneAcControl(
        hass=hass,
        states=states,
        details={},
        room="AC",
        name="AC",
        uuid="uuid-ac-0000",
        uuidAction="uuid-ac-0000",
        cat="cool",
    )
    _stub_write(e)
    return e, out


def test_ac_hvac_action_via_states() -> None:
    e, _ = _ac({"status": "st_status", "mode": "st_mode"})
    assert e.hvac_action == HVACAction.IDLE  # nothing streamed yet
    e.event_handler({"st_status": 1, "st_mode": 3})
    assert e.hvac_action == HVACAction.COOLING
    e.event_handler({"st_mode": 2})
    assert e.hvac_action == HVACAction.HEATING
    e.event_handler({"st_status": 0})
    assert e.hvac_action == HVACAction.IDLE


# --------------------------------------------------------------------------- #
# Full setup with the LoxAPP3 fixture: appear, update, service calls
# --------------------------------------------------------------------------- #
async def test_wp68_controls_appear_and_update(hass, mock_connection, mock_entry) -> None:
    """Acceptance: the WP-6.8 additions appear after setup from the
    fixture, update on fed state events, and their services reach the
    wire expectations (hand-derived literals)."""
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    reg = er.async_get(hass)

    # -- Alarm: appears, shows its delay, night/vacation arm -------------
    alarm = reg.async_get_entity_id("alarm_control_panel", "loxone", ENTRANCE_ALARM_ACTION)
    assert alarm is not None, "the fixture Entrance Alarm was not set up"
    assert hass.states.get(alarm).state == "disarmed"

    # a pending delay counts as arming; the attributes stay int seconds
    mock_connection.feed(ENTRANCE_ALARM_STATES["armedDelay"], 10.0)
    await hass.async_block_till_done()
    state = hass.states.get(alarm)
    assert state.state == "arming"
    assert state.attributes["armed_delay"] == 10
    assert state.attributes["armed_delay_total_delay"] is None

    mock_connection.feed(ENTRANCE_ALARM_STATES["armedDelayTotal"], 60.0)
    await hass.async_block_till_done()
    assert hass.states.get(alarm).attributes["armed_delay_total_delay"] == 60

    # fully armed with motion suppressed -> home
    mock_connection.feed(ENTRANCE_ALARM_STATES["armedDelay"], 0)
    mock_connection.feed(ENTRANCE_ALARM_STATES["armed"], 1)
    mock_connection.feed(ENTRANCE_ALARM_STATES["disabledMove"], 1)
    await hass.async_block_till_done()
    assert hass.states.get(alarm).state == "armed_home"

    sent_before = len(mock_connection.sent)
    await hass.services.async_call("alarm_control_panel", "alarm_arm_night", {"entity_id": alarm}, blocking=True)
    await hass.services.async_call("alarm_control_panel", "alarm_arm_vacation", {"entity_id": alarm}, blocking=True)
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [
        {"uuid": ENTRANCE_ALARM_ACTION, "value": "delayedon/1", "code": None},
        {"uuid": ENTRANCE_ALARM_ACTION, "value": "delayedon/0", "code": None},
    ]

    # -- Gate: SET_POSITION advertised, service reaches the wire --------
    gate = reg.async_get_entity_id("cover", "loxone", GARDEN_GATE_ACTION)
    assert gate is not None, "the fixture Garden Gate was not set up"
    from homeassistant.components.cover import CoverEntityFeature

    gate_flags = hass.states.get(gate).attributes["supported_features"]
    assert (gate_flags & CoverEntityFeature.SET_POSITION) == CoverEntityFeature.SET_POSITION

    mock_connection.feed(GARDEN_GATE_POSITION, 0.5)
    await hass.async_block_till_done()
    gate_state = hass.states.get(gate)
    assert gate_state.state == "open"
    assert gate_state.attributes["current_position"] == 50.0

    sent_before = len(mock_connection.sent)
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": gate, "position": 42}, blocking=True)
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [
        {"uuid": GARDEN_GATE_ACTION, "value": "manualPosition/42.0", "code": None}
    ]

    # -- Jalousie sun-auto select: appears, follows autoState -----------
    select = reg.async_get_entity_id("select", "loxone", f"{TERRACE_JALOUSIE_ACTION}/sun-auto")
    assert select is not None, "the fixture Terrace Jalousie sun-auto select was not set up"
    assert hass.states.get(select).state == "Off"

    mock_connection.feed(TERRACE_JALOUSIE_AUTO, 1)
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "Auto"

    sent_before = len(mock_connection.sent)
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Shade"}, blocking=True)
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [{"uuid": TERRACE_JALOUSIE_ACTION, "value": "shade", "code": None}]
    assert hass.states.get(select).state == "Shade"

    mock_connection.feed(TERRACE_JALOUSIE_AUTO, 0)
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "Off"

    # -- AcControl: modes, target, fan and the new hvac_action ----------
    ac = reg.async_get_entity_id("climate", "loxone", AC_ACTION)
    assert ac is not None, "the fixture AC Hallway (AcControl) was not set up"
    assert hass.states.get(ac).state == "off"

    mock_connection.feed(AC_STATUS, 1)
    mock_connection.feed(AC_MODE, 3)
    await hass.async_block_till_done()
    ac_state = hass.states.get(ac)
    assert ac_state.state == "cool"
    assert ac_state.attributes["hvac_action"] == "cooling"

    mock_connection.feed(AC_TARGET, 21.5)
    mock_connection.feed(AC_FAN, 1)
    mock_connection.feed(AC_VENT, 2)
    await hass.async_block_till_done()
    ac_state = hass.states.get(ac)
    assert ac_state.attributes["temperature"] == 21.5
    assert ac_state.attributes["fan_mode"] == "Low"
    assert ac_state.attributes["swing_mode"] == "Up"

    sent_before = len(mock_connection.sent)
    await hass.services.async_call("climate", "set_fan_mode", {"entity_id": ac, "fan_mode": "High"}, blocking=True)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": ac, "temperature": 22.5}, blocking=True)
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [
        {"uuid": AC_ACTION, "value": "setFan/2", "code": None},
        {"uuid": AC_ACTION, "value": "setTarget/22.5", "code": None},
    ]
