"""WP-4.5 regression tests for the "simple" platforms.

Platforms: sensor, binary_sensor, switch, select, number, button, scene and
the setup helpers (``get_all`` / ``map_range`` / ``iter_controls``).

Covered findings: PS-04, PS-05, PS-06, PS-07, PS-08, PS-09 (remainder),
PS-15 (remainder), PS-16, PS-17, PS-19, PS-21, PS-24, PS-25, CORE-22,
CORE-32 (``map_range`` / ``get_all`` hardening).

Expected values are derived by hand from the LoxAPP3 fixture
(``tests/fixtures/LoxAPP3.json``, WP-0.2) and written as literals. The
fixture uuids below were transcribed from the fixture file; the fixture
object itself is shared, so setup-level tests only deep-copy and modify it,
never write to it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCENE_DOMAIN = "scene"

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.binary_sensor import LoxoneDigitalSensor
from custom_components.loxone.button import LoxoneButton
from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.helpers import get_all, iter_controls, map_range
from custom_components.loxone.number import LoxoneNumber
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.scene import Loxonelightscene, parse_mood_list
from custom_components.loxone.select import LoxoneSelect, build_option_maps
from custom_components.loxone.sensor import (
    LoxoneCustomSensor,
    LoxoneMeterSensor,
    LoxoneRoomControllerOverrideSensor,
    LoxoneSensor,
    _analog_value,
    _is_numeric_format,
    _metering_indicated,
)
from custom_components.loxone.switch import LoxoneLightPresenceSwitch, LoxoneSwitch

# ``SCENE_DOMAIN`` was removed from HA 2026 const; the literal is the platform
# name used by the scene platform.
SCENE_DOMAIN = "scene"

# ---------------------------------------------------------------------------
# Fixture literals (hand-transcribed from tests/fixtures/LoxAPP3.json)
# ---------------------------------------------------------------------------
SMOKE_UUID_ACTION = "63746c3a-0152-9746-ffff-26f6f6d20536000082"
SMOKE_LEVEL_UUID = "36333734-0154-9336-ffff-d303135322d3000084"
SMOKE_MUTE_UUID = "36333734-0155-9336-ffff-d303135322d3000085"
DIGITAL_ACTIVE_UUID = "36333734-0113-9336-ffff-d303131322d3000019"
LCV2_ACTION_UUID = "63746c3a-0183-9766-ffff-e67204c69676000131"
LCV2_MOOD_LIST_UUID = "36333734-0188-9336-ffff-d303138332d3000136"
INTERCOM_ACTION_UUID = "63746c3a-0165-9696-ffff-0496e7465726000101"
INTERCOM_SUB_UUID = "7375623a-0167-9696-ffff-0496e7465726000103"
METER_ACTION_UUID = "63746c3a-011a-9657-ffff-779204d65746000026"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _event(data: dict):
    """CORE-27/PS-13: handlers now take the plain ``{uuid: value}`` dict the
    dispatcher delivers; identity for existing call sites."""
    return dict(data)


def _stub_write(entity):
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    entity.schedule_update_ha_state = lambda *a, **k: None
    # CORE-27: handlers (now sync @callbacks) write via async_write_ha_state;
    # raw (unattached) test instances would raise on the stock write.
    entity.async_write_ha_state = lambda *a, **k: None


def _fresh_loxapp3() -> dict:
    """Load the LoxAPP3 fixture from disk.

    The session-scoped ``loxapp3`` fixture returns a single dict object that
    earlier setup tests can mutate (some platforms still write runtime
    references into their control dict -- see the PR body), so each
    setup-level test works on its own fresh copy.
    """
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


# ---------------------------------------------------------------------------
# CORE-32: get_all / map_range hardening (Uni Ulm fuzzing PR #292)
# ---------------------------------------------------------------------------
def test_get_all_without_controls_key_returns_empty():
    # Acceptance: get_all({}, "Switch") == []
    assert get_all({}, "Switch") == []


def test_get_all_skips_controls_without_type():
    data = {"controls": {"a": {"name": "no-type"}, "b": {"type": "Switch", "name": "s"}}}
    assert [c["name"] for c in get_all(data, "Switch")] == ["s"]


def test_get_all_accepts_list_of_types():
    data = {"controls": {"a": {"type": "Switch"}, "b": {"type": "Dimmer"}}}
    assert len(get_all(data, ["Switch", "Dimmer"])) == 2


def test_map_range_equal_bounds_does_not_divide_by_zero():
    # Acceptance: equal bounds must not raise ZeroDivisionError.
    assert map_range(5, 2, 2, 0, 100) == 0
    assert map_range(2, 2, 2, 7, 9) == 7


def test_map_range_linear_identity():
    # 0..10 -> 0..100 (hand-derived).
    assert map_range(0, 0, 10, 0, 100) == 0
    assert map_range(5, 0, 10, 0, 100) == 50.0
    assert map_range(10, 0, 10, 0, 100) == 100.0


# ---------------------------------------------------------------------------
# PS-24: shared iter_controls helper
# ---------------------------------------------------------------------------
def test_iter_controls_resolves_room_and_cat():
    loxconfig = {
        "rooms": {"r1": {"name": "Parlour"}},
        "cats": {"c1": {"name": "Energy"}},
        "controls": {
            "a": {"type": "InfoOnlyAnalog", "name": "T", "room": "r1", "cat": "c1"},
            "b": {"type": "Switch", "name": "S", "room": "zz", "cat": "yy"},
        },
    }
    miniserver = SimpleNamespace(lox_config=SimpleNamespace(json=loxconfig))
    with patch("custom_components.loxone.miniserver.get_miniserver_from_hass", return_value=miniserver):
        got = list(iter_controls(Mock(), Mock(), "InfoOnlyAnalog"))
    assert len(got) == 1
    assert got[0]["room"] == "Parlour"
    assert got[0]["cat"] == "Energy"
    # Unknown room/cat uuids degrade to "" instead of raising.
    with patch("custom_components.loxone.miniserver.get_miniserver_from_hass", return_value=miniserver):
        got2 = list(iter_controls(Mock(), Mock(), "Switch"))
    assert got2[0]["room"] == ""
    assert got2[0]["cat"] == ""


# ---------------------------------------------------------------------------
# PS-04: smoke alarm reads the level, not "alarm signals off"
# ---------------------------------------------------------------------------
def _smoke_sensor():
    states = {
        "active": "36333734-0153-9336-ffff-d303135322d3000083",
        "level": SMOKE_LEVEL_UUID,
        "areAlarmSignalsOff": SMOKE_MUTE_UUID,
    }
    return LoxoneDigitalSensor(
        uuidAction=SMOKE_UUID_ACTION,
        name="Bathroom Smoke",
        room="Bathroom",
        cat="Security",
        states=states,
        type="smoke",
        details={},
    )


async def test_smoke_alarm_reports_on_when_level_positive(hass):
    # Acceptance: a SmokeAlarm reports "on" when level > 0.
    e = _smoke_sensor()
    e.hass = hass
    _stub_write(e)
    # The state uuid must be the *level*, not areAlarmSignalsOff (PS-04).
    assert e._state_uuid == SMOKE_LEVEL_UUID

    e.event_handler(_event({SMOKE_LEVEL_UUID: 1.0}))
    assert e.is_on is True
    assert e.state == STATE_ON
    assert e._attr_available is True

    e.event_handler(_event({SMOKE_LEVEL_UUID: 2.0}))
    assert e.is_on is True

    # Level 0 clears the event.
    e.event_handler(_event({SMOKE_LEVEL_UUID: 0.0}))
    assert e.is_on is False
    assert e.state == STATE_OFF


async def test_smoke_alarm_ignores_areAlarmSignalsOff(hass):
    # Acceptance: the "areAlarmSignalsOff" (mute) stream is ignored.
    e = _smoke_sensor()
    e.hass = hass
    _stub_write(e)
    e.event_handler(_event({SMOKE_LEVEL_UUID: 1.0}))
    assert e.is_on is True

    e.event_handler(_event({SMOKE_MUTE_UUID: 1.0}))
    assert e.is_on is True


async def test_smoke_alarm_missing_level_falls_back_to_uuid_action(hass):
    # A smoke alarm without a level stream must not abort setup with a
    # KeyError (PS-04).
    e = LoxoneDigitalSensor(
        uuidAction=SMOKE_UUID_ACTION,
        name="Bathroom Smoke",
        room="Bathroom",
        cat="Security",
        states={"active": "a1", "areAlarmSignalsOff": SMOKE_MUTE_UUID},
        type="smoke",
        details={},
    )
    e.hass = hass
    _stub_write(e)
    assert e._state_uuid == SMOKE_UUID_ACTION


def test_digital_sensor_listens_on_active_not_uuid_action():
    # PS-04: InfoOnlyDigital uses the explicit `active` state, not the
    # echoed action uuid.
    e = LoxoneDigitalSensor(
        uuidAction="dig-0001",
        name="Breaker 1",
        room="Bedroom",
        cat="Energy",
        states={"active": DIGITAL_ACTIVE_UUID, "values": "dig-0002"},
        type="digital",
        details={"text": {"on": "ON", "off": "OFF"}},
    )
    assert e._state_uuid == DIGITAL_ACTIVE_UUID


# ---------------------------------------------------------------------------
# PS-21 / PS-09 / PS-25: sensor value handling and state classes
# ---------------------------------------------------------------------------
def _analog_sensor(name, *, unit_format, cat="") -> LoxoneSensor:
    return LoxoneSensor(
        uuidAction=f"an-{name.replace(' ', '-')}-01",
        name=name,
        room="Parlour",
        cat=cat,
        states={"active": "an-active-01", "hints": "an-hints-01"},
        details={"format": unit_format},
        type="analog",
    )


def test_analog_value_maps_error_sentinel_to_none():
    # PS-09: the Miniserver error sentinel -1 and None mean "no reading".
    assert _analog_value(None) is None
    assert _analog_value(-1) is None
    assert _analog_value(-1.0) is None
    # Real readings pass through, including exactly 0 and -0.5.
    assert _analog_value(0) == 0
    assert _analog_value(-0.5) == -0.5
    assert _analog_value(21.5) == 21.5


async def test_analog_sensor_error_value_publishes_unknown(hass):
    e = _analog_sensor("Temperature", unit_format="%.1f °C")
    e.hass = hass
    _stub_write(e)
    e.event_handler(_event({e.uuidAction: 21.5}))
    assert e.native_value == 21.5
    e.event_handler(_event({e.uuidAction: -1}))
    assert e.native_value is None
    e.event_handler(_event({e.uuidAction: None}))
    assert e.native_value is None


def test_kwh_without_metering_name_gets_measurement():
    # PS-21: "Consumption today" is a resetting value, not a running meter.
    e = _analog_sensor("Consumption today", unit_format="%d kWh")
    assert e.state_class == "measurement"


def test_kwh_with_metering_name_gets_total_increasing():
    e = _analog_sensor("Meter Total", unit_format="%d kWh", cat="Energy")
    assert e.state_class == "total_increasing"


def test_metering_keyword_helper_literals():
    assert _metering_indicated("Main meter total", "")
    assert _metering_indicated("", "Zähler")
    assert not _metering_indicated("Consumption today", "Energy")


def test_state_class_only_for_numeric_formats():
    assert _is_numeric_format("%.1f °C") is True
    assert _is_numeric_format("%d kWh") is True
    assert _is_numeric_format("%%") is True
    # A string-typed format must not advertise a numeric state_class (PS-09):
    # HA raises a ValueError when a text string is published under a
    # numeric class.
    assert _is_numeric_format("%s") is False
    assert _is_numeric_format("no format at all") is False
    assert _is_numeric_format(None) is False


def test_precision_zero_is_really_zero():
    # PS-25: `if precision:` treated 0 digits as "no precision".
    e = _analog_sensor("Round Number", unit_format="%.0f W")
    assert e._attr_suggested_display_precision == 0


def test_meter_register_classes():
    # PS-21: Meter registers have explicit per-register classes:
    # actual -> POWER/MEASUREMENT, total/totalNeg -> ENERGY/TOTAL_INCREASING,
    # storage -> ENERGY/MEASUREMENT (no spikes for resetting/sign-flipping
    # values in the energy dashboard).
    actual = LoxoneMeterSensor(
        uuidAction="m-actual-01",
        name="Energy Meter Actual",
        room="Hall",
        cat="Energy",
        parent_id="meter-01",
        states={},
        details={"format": "%d W"},
        device_class="power",
        state_class="measurement",
        config_entry=Mock(),
    )
    assert actual.device_class == "power"
    assert actual.state_class == "measurement"

    total = LoxoneMeterSensor(
        uuidAction="m-total-01",
        name="Energy Meter Total",
        room="Hall",
        cat="Energy",
        parent_id="meter-01",
        states={},
        details={"format": "%d kWh"},
        device_class="energy",
        state_class="total_increasing",
        config_entry=Mock(),
    )
    assert total.device_class == "energy"
    assert total.state_class == "total_increasing"

    storage = LoxoneMeterSensor(
        uuidAction="m-storage-01",
        name="Energy Meter Level",
        room="Hall",
        cat="Energy",
        parent_id="meter-01",
        states={},
        details={"format": "%d kWh"},
        device_class="energy",
        state_class="measurement",
        config_entry=Mock(),
    )
    assert storage.device_class == "energy"
    assert storage.state_class == "measurement"


# ---------------------------------------------------------------------------
# PS-08: one Meter without a format key must not abort the sensor platform
# ---------------------------------------------------------------------------
async def test_meter_without_storage_format_still_yields_other_sub_sensors(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    config = _fresh_loxapp3()
    meter = config["controls"][METER_ACTION_UUID]
    del meter["details"]["storageFormat"]

    mock_entry.add_to_hass(hass)
    with _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    # The registers with a format are still created (the fixture Meter has
    # total/totalNeg/storage states; there is no `actual` register) ... (entity
    # ids may carry a room prefix from the area registry, so match on name).
    entity_ids = hass.states.async_entity_ids("sensor")
    assert any("energy_meter_total_neg" in i for i in entity_ids)
    assert any("energy_meter_total" in i and "total_neg" not in i for i in entity_ids)
    # The register without a format (Level, storageFormat was deleted) is
    # also created -- with a neutral format fallback -- instead of
    # aborting the platform (acceptance: a Meter without storageFormat
    # still yields the other sub-sensors).
    assert any("energy_meter_level" in i for i in entity_ids)
    # ... and the platform did not abort: other sensors made it in too.
    assert any("temperature_parlour" in i for i in entity_ids)
    assert any("energy_kitchenv2" in i for i in entity_ids)

    # The agenda device/state classes land on the register entities (PS-21).
    entity_reg = er.async_get(hass)
    total = entity_reg.async_get_entity_id("sensor", "loxone", "36333734-011c-9336-ffff-d303131612d3000028")
    attrs = hass.states.get(total).attributes
    assert attrs["device_class"] == "energy"
    assert attrs["state_class"] == "total_increasing"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# PS-07: YAML sensor without a name still gets a unique id
# ---------------------------------------------------------------------------
def test_yaml_sensor_unique_id_without_name():
    e = LoxoneCustomSensor(uuidAction="abc-123")
    assert e.unique_id == "abc-123"


def test_yaml_sensor_unique_id_with_name():
    # The name is only a suffix, so a given (uuidAction, name) pair stays
    # stable.
    e = LoxoneCustomSensor(uuidAction="abc-123", name="My Sensor")
    assert e.unique_id == "abc-123-My Sensor"


# ---------------------------------------------------------------------------
# CORE-22: override-reason sensor states are slugs that translate
# ---------------------------------------------------------------------------
async def test_override_reason_states_are_slugs(hass):
    e = LoxoneRoomControllerOverrideSensor(
        name="Kitchen Override Reason",
        uuid="reason-uuid",
        device_info={"identifiers": set(), "name": "Kitchen"},
        parent_uuid="irc-01",
    )
    e.hass = hass
    _stub_write(e)
    assert e._attr_translation_key == "override_reason"
    assert e.native_value == "none"

    # 4 = eco override in the IRoomControllerV2 override-reason table.
    e.event_handler(_event({"reason-uuid": 4.0}))
    assert e.native_value == "eco_override"

    # 8 = overridden by source.
    e.event_handler(_event({"reason-uuid": 8.0}))
    assert e.native_value == "overridden_by_source"

    # Unknown codes collapse to the single "unknown" slug instead of a
    # runtime-minted "Unknown (n)" display string.
    e.event_handler(_event({"reason-uuid": 19.0}))
    assert e.native_value == "unknown"
    assert "unknown" in e.options
    assert "Unknown (19)" not in e.options

    # Non-numeric values must not crash the handler.
    e.event_handler(_event({"reason-uuid": "garbage"}))
    assert e.native_value == "unknown"


def test_override_reason_translations_carry_the_slug_path():
    # The entity resolves states through
    # ``entity.sensor.override_reason.state.<slug>`` (HA core layout,
    # WP-5.1) -- verify the translation files actually carry that path.
    for lang in ("en", "de", "cs"):
        data = json.loads((REPO_ROOT / "custom_components/loxone/translations" / f"{lang}.json").read_text())
        states = data["entity"]["sensor"]["override_reason"]["state"]
        for slug in ("none", "eco_override", "fixed", "unknown"):
            assert slug in states


# ---------------------------------------------------------------------------
# PS-05 / PS-06: switch platform hardening
# ---------------------------------------------------------------------------
async def test_switch_without_active_state_does_not_raise(hass):
    # PS-05: the unguarded self.states["active"] raised on every event.
    e = LoxoneSwitch(uuidAction="sw-0001", name="Mystery", room="Office", states={}, type="Switch")
    e.hass = hass
    _stub_write(e)
    e.event_handler(_event({"sw-0001": 1.0}))  # must not raise
    assert e._attr_is_on is None


def test_presence_switch_constructs_from_states():
    # PS-06: the presence state uuid lives in `states`; the guard and the
    # constructor read the same key.
    e = LoxoneLightPresenceSwitch(
        uuidAction=LCV2_ACTION_UUID,
        name="Living Light Controller",
        room="Parlour",
        cat="Comfort",
        states={"presence": "presence-uuid-01", "active": "l-active-01"},
        details={"icon": True},
    )
    _stub_write(e)
    assert e._presence_id == "presence-uuid-01"
    assert e.unique_id == "presence-uuid-01"
    # WP-5.1: the presence switch is a *sub-entity* of the light
    # controller.  It carries the short name "Presence Detection";
    # the parent control device contributes "Living Light Controller"
    # (the UI shows "Living Light Controller Presence Detection").
    assert e._attr_name == "Presence Detection"
    assert e.name == "Presence Detection"


# ---------------------------------------------------------------------------
# PS-19: select platform
# ---------------------------------------------------------------------------
def _radio(**overrides):
    base = {
        "uuidAction": "radio-0001",
        "name": "Fan Speed",
        "room": "Parlour",
        "cat": "Comfort",
        "states": {"value": "r-value-01", "active": "r-active-01", "activeOutput": "r-output-01"},
        "details": {
            "allOff": "Off",
            "outputs": {"1": "Low", "2": "Med", "3": "High"},
        },
    }
    base.update(overrides)
    return base


def test_radio_without_outputs_has_empty_options():
    # Acceptance precondition: a Radio with neither `outputs` nor `allOff`
    # has no selectable options (options == [] is rejected by HA).
    options, _, _, _ = build_option_maps({})
    assert options == []


async def test_setup_empty_radio_skipped_with_log(
    hass, mock_connection, mock_entry, enable_custom_integrations, caplog
):
    # Acceptance: a Radio without outputs is skipped with a log instead of
    # crashing the whole select platform (PS-19).
    config = _fresh_loxapp3()
    radio = config["controls"]["63746c3a-016e-96e7-ffff-96c6174696f6000110"]
    radio["details"] = {"jLockable": True}  # no outputs, no allOff

    mock_entry.add_to_hass(hass)
    with caplog.at_level(logging.WARNING), _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert not any("ventilation_radio" in i for i in hass.states.async_entity_ids("select"))
    # The platform ran through (no KeyError/abort) and the user is told why.
    assert any("no outputs" in r.message for r in caplog.records if r.levelno == logging.WARNING)

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_select_current_option_from_output_event(hass):
    e = LoxoneSelect(**_radio())
    e.hass = hass
    _stub_write(e)
    assert e.current_option is None
    # Output 3 is the "High" option of the sample Radio block.
    e.event_handler(_event({"r-output-01": 3.0}))
    assert e.current_option == "High"
    assert e.options == ["Off", "Low", "Med", "High"]


async def test_select_locked_raises(hass):
    # PS-19: while the Loxone block is locked, selection must fail with a
    # user-visible error instead of silently sending.
    e = LoxoneSelect(**_radio(states={"value": "r-v", "active": "r-a", "activeOutput": "r-o", "jLocked": "r-lock"}))
    e.hass = hass
    _stub_write(e)
    e.event_handler(_event({"r-lock": 1.0}))
    assert e._locked is True
    with pytest.raises(HomeAssistantError):
        await e.async_select_option("Low")


async def test_select_single_state_write_per_event(hass):
    # PS-19: one state write per event, even when output and lock both
    # update in the same event.
    e = LoxoneSelect(**_radio(states={"value": "r-v", "active": "r-a", "activeOutput": "r-o", "jLocked": "r-lock"}))
    e.hass = hass
    calls = []
    # CORE-27/PS-19: the handler is now a sync @callback; it writes once per
    # event through async_write_ha_state.
    e.async_write_ha_state = lambda *a, **k: calls.append(1)
    e.event_handler(_event({"r-o": 2.0, "r-lock": 0.0}))
    assert calls == [1]


# ---------------------------------------------------------------------------
# PS-15: number platform
# ---------------------------------------------------------------------------
def _slider(**overrides):
    base = {
        "uuidAction": "slider-0001",
        "name": "Brightness Slider",
        "room": "Parlour",
        "cat": "Comfort",
        "states": {"value": "slv-01", "active": "sla-01"},
        "details": {"min": 0.0, "max": 100.0, "step": 10.0},
    }
    base.update(overrides)
    return base


async def test_number_starts_unknown_and_listens_on_value(hass):
    e = LoxoneNumber(**_slider())
    e.hass = hass
    _stub_write(e)
    assert e.native_value is None
    assert e.state is None  # unknown, not the STATE_UNKNOWN string

    # The feed lands on the `value` state uuid, not the action uuid.
    e.event_handler(_event({"slv-01": 40.0}))
    assert e.native_value == 40.0
    assert e._attr_available is True

    # A value-less event on the action uuid must not change the number.
    e.event_handler(_event({"slider-0001": "ignored"}))
    assert e.native_value == 40.0


def test_number_without_max_rejected():
    kwargs = _slider(details={"min": 0.0, "step": 1.0})
    with pytest.raises(ValueError):
        LoxoneNumber(**kwargs)


def test_number_unit_and_range_from_details():
    e = LoxoneNumber(**_slider(details={"min": 0.0, "max": 60.0, "step": 0.5, "format": "%.1f min"}))
    assert e.native_max_value == 60.0
    assert e.native_min_value == 0.0
    assert e.native_step == 0.5
    assert e.native_unit_of_measurement == "min"


# ---------------------------------------------------------------------------
# PS-16: button platform
# ---------------------------------------------------------------------------
def _pushbutton():
    return {
        "uuidAction": "push-0001",
        "name": "Introduce Pushbutton",
        "room": "Hall",
        "cat": "Security",
        "states": {"active": "pb-active-01", "direction": "pb-dir-01"},
        "details": {},
    }


def test_button_does_not_override_final_state():
    from homeassistant.components.button import ButtonEntity

    # PS-16: ButtonEntity.state is @final; the pushbutton must not
    # redefine it.
    assert "state" not in vars(LoxoneButton)
    assert LoxoneButton.state is ButtonEntity.state


async def test_button_press_echo_becomes_attribute(hass):
    e = LoxoneButton(**_pushbutton())
    e.hass = hass
    _stub_write(e)
    assert e.state is None
    assert e.extra_state_attributes["last_pressed"] is None

    # The echo of an actual press (active == 1).
    e.event_handler(_event({"pb-active-01": 1.0}))
    attr = e.extra_state_attributes
    assert attr["new_state"] is True
    assert attr["last_pressed"] is not None

    # A release (active == 0) is indexed but does not move the
    # last-press timestamp.
    pressed = attr["last_pressed"]
    e.event_handler(_event({"pb-active-01": 0.0}))
    assert e.extra_state_attributes["new_state"] is False
    assert e.extra_state_attributes["last_pressed"] == pressed


async def test_button_press_sends_pulse(hass):
    e = LoxoneButton(**_pushbutton())
    e.hass = hass
    _stub_write(e)
    fired = []
    e.hass = SimpleNamespace(
        bus=SimpleNamespace(async_fire=lambda et, event_data=None, **kw: fired.append((et, event_data)))
    )
    e.press()
    assert (SENDDOMAIN, {"uuid": "push-0001", "value": "pulse"}) in fired


# ---------------------------------------------------------------------------
# PS-17: scene platform
# ---------------------------------------------------------------------------
def test_parse_mood_list():
    assert parse_mood_list('[{"id": "107", "name": "Cozy"}, {"id": "79", "name": "Bright"}]') == [
        {"id": "107", "name": "Cozy"},
        {"id": "79", "name": "Bright"},
    ]
    # Malformed JSON and a bare state uuid are not mood lists.
    assert parse_mood_list("{1") is None
    assert parse_mood_list("36333734-0188-9336-ffff-d303138332d3000136") is None
    # Entries without both an id and a name are dropped.
    assert parse_mood_list('[{"name": "NoId"}, {"id": "1"}]') == []


async def test_scenes_created_from_mood_list_event(hass, mock_connection, mock_entry, enable_custom_integrations):
    # Enable scene generation (the fixture entry keeps it off by default,
    # which is also what the old 3-second-delay path tested against).
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={**mock_entry.options, "generate_scenes": True})
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    # No scenes before the mood list has arrived; nothing is scheduled on
    # a timer anymore (PS-17).
    assert len(hass.states.async_entity_ids(SCENE_DOMAIN)) == 0

    moods_json = json.dumps([{"id": "107", "name": "Relax"}, {"id": "108", "name": "Party"}])
    mock_connection.feed(LCV2_MOOD_LIST_UUID, moods_json)
    await hass.async_block_till_done()

    scene_ids = hass.states.async_entity_ids(SCENE_DOMAIN)
    # HA 2026.x prefixes the device's area to the scene object id because the
    # scene name starts with the device name and the LCV2 device carries the
    # room as suggested_area (WP-3.3: room resolution is now idempotent, so
    # the light platform finally sees the room).
    assert "scene.parlour_living_light_controller_relax" in scene_ids
    assert "scene.parlour_living_light_controller_party" in scene_ids

    # The scenes carry the LCV2 device (device link): the device holds the
    # (DOMAIN, uuidAction) identifier used by get_or_create_device.
    reg_device = dr.async_get(hass).async_get_device(identifiers={("loxone", LCV2_ACTION_UUID)})
    assert reg_device is not None
    entity_reg = er.async_get(hass)
    scene_entry = entity_reg.async_get("scene.parlour_living_light_controller_relax")
    assert scene_entry.device_id == reg_device.id

    # A second delivery of the same list must not duplicate the scenes.
    mock_connection.feed(LCV2_MOOD_LIST_UUID, moods_json)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SCENE_DOMAIN)) == 2

    # The scene subscription is tracked and cleaned up with the entry: the
    # loxone_event listener count drops by one on unload (scene.py adds
    # exactly one listener per LCV2).
    listener_count = hass.bus.async_listeners().get("loxone_event", 0)
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.bus.async_listeners().get("loxone_event", 0) <= listener_count - 1


async def test_scene_activate_sends_change_to_command(hass):
    ok_fired = []

    def fake_fire(event_type, event_data=None, **kwargs):
        ok_fired.append((event_type, event_data))

    scene = Loxonelightscene(
        name="Living Light Controller - Relax",
        mood_id="107",
        uuid=LCV2_ACTION_UUID,
        light_controller_id=LCV2_ACTION_UUID,
    )
    scene.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=fake_fire))
    await scene.async_activate()
    assert (SENDDOMAIN, {"uuid": LCV2_ACTION_UUID, "value": "changeTo/107"}) in ok_fired


# ---------------------------------------------------------------------------
# PS-06 / PS-05 setup level: presence switch created; intercom sub without
# `active` skipped with a log
# ---------------------------------------------------------------------------
async def test_setup_presence_switch_from_states(hass, mock_connection, mock_entry, enable_custom_integrations):
    # PS-06: the guard and the constructor now read states["presence"]; a
    # LCV2 with a presence state gets its switch entity.
    config = _fresh_loxapp3()
    config["controls"][LCV2_ACTION_UUID]["states"]["presence"] = "lcv2-presence-01"

    mock_entry.add_to_hass(hass)
    with _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    # The presence switch entity exists (entity id may carry a room prefix).
    assert any("presence_detection" in i for i in hass.states.async_entity_ids("switch"))

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_intercom_sub_control_without_active_skipped(
    hass, mock_connection, mock_entry, enable_custom_integrations, caplog
):
    # PS-05: an intercom sub-control without an `active` state is skipped
    # with a warning instead of raising on every event.
    config = _fresh_loxapp3()
    intercom = config["controls"][INTERCOM_ACTION_UUID]
    sub = intercom["subControls"][INTERCOM_SUB_UUID]
    sub["states"] = {"on": "37333735-0168-9336-ffff-d303136372d3000104"}  # no `active`

    mock_entry.add_to_hass(hass)
    with caplog.at_level(logging.WARNING), _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    switch_ids = hass.states.async_entity_ids("switch")
    # The entity is skipped ...
    assert not any("main_intercom_micro" in i for i in switch_ids)
    # ... the platform continues (the plain Switch set up fine) ...
    assert any("living_room_light_switch" in i for i in switch_ids)
    # ... and the user is told why.
    assert any("no 'active' state" in r.message for r in caplog.records if r.levelno == logging.WARNING)

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
