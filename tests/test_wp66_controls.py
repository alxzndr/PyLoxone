"""WP-6.6 regression tests: ``InfoOnlyText``, ``UpDownDigital``, ``Tracker``
controls and the recursive ``get_all`` over ``subControls`` (PS-26).

All expected values are hand-derived literals against the fixture
(``tests/fixtures/LoxAPP3.json``) and the LoxApp protocol shapes as
documented by the openHAB Loxone binding (which ports the same
Miniserver API): ``InfoOnlyText`` reports a ``text`` state,
``UpDownDigital`` has no states and accepts ``UpOn``/``UpOff``/
``DownOn``/``DownOff`` commands, and ``Tracker`` reports its entries as a
JSON list on the ``entries`` state.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.button import LoxoneUpDownDigitalButton
from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.helpers import get_all
from custom_components.loxone.sensor import LoxoneTextSensor, LoxoneTrackerSensor, tracker_entries

# --------------------------------------------------------------------------- #
# Fixture control identities (read from tests/fixtures/LoxAPP3.json)
# --------------------------------------------------------------------------- #
DEV_STATUS_ACTION = "63746c3a-01c0-96d7-ffff-f696e726f756000190"
DEV_STATUS_TEXT = "36333734-01c1-9336-ffff-d303204b72d3000191"
STAIRWELL_ACTION = "63746c3a-01c2-96d7-ffff-f5726172626c000192"
TRACKER_ACTION = "63746c3a-01c4-96d7-ffff-f726636b6572000194"
TRACKER_ENTRIES = "36333734-01c5-9336-ffff-d303131372d3000195"
ALARM_SENSORS = "63746c3a-01a7-96d6-ffff-0416c61726d3000167/sensors"
ALARM_SENSORS_ENTRIES = "36333734-01ab-9336-ffff-d303161372d3000196"


def _event(data: dict):
    return dict(data)


def _stub_write(entity):
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    entity.schedule_update_ha_state = lambda *a, **k: None
    entity.async_write_ha_state = lambda *a, **k: None


# --------------------------------------------------------------------------- #
# Recursive get_all over subControls
# --------------------------------------------------------------------------- #
def _nested_structure() -> dict:
    return {
        "controls": {
            "c1": {"name": "Top Tracker", "type": "Tracker", "uuidAction": "act-1", "states": {"entries": "st-1"}},
            "c2": {
                "name": "Some Alarm",
                "type": "Alarm",
                "uuidAction": "act-2",
                "subControls": {
                    "act-2/sensors": {
                        "name": "Nested Tracker",
                        "type": "Tracker",
                        "uuidAction": "act-2/sensors",
                        "states": {"entries": "st-2"},
                    },
                },
            },
            # a wanted control one level *inside* a non-wanted control
            "c3": {
                "name": "Analog Parent",
                "type": "InfoOnlyAnalog",
                "uuidAction": "act-3",
                "subControls": {
                    "act-3/x": {"name": "Inner InfoOnlyText", "type": "InfoOnlyText", "uuidAction": "act-3/x"},
                },
            },
            # the same uuidAction referenced from two places: only the
            # first occurrence may be returned
            "c4": {"name": "Dup A", "type": "Tracker", "uuidAction": "act-dup", "states": {"entries": "st-dup"}},
            "c5": {
                "name": "Dup Host",
                "type": "Other",
                "uuidAction": "act-5",
                "subControls": {"act-dup": {"name": "Dup B", "type": "Tracker", "uuidAction": "act-dup"}},
            },
            # a Tracker without a uuidAction: no way to de-duplicate, kept
            "c6": {"name": "Bogus Tracker", "type": "Tracker"},
            "c7": {"name": "No Type", "uuidAction": "act-7"},
            # malformed entries that must not raise
            "c8": "junk",
            "c9": {"name": "Bad Subs", "type": "Tracker", "uuidAction": "act-9", "subControls": "oops"},
        }
    }


def test_get_all_top_level_unchanged():
    # The default (non-recursive) scan returns the same top-level results
    # as before WP-6.6, in structure order, and skips typeless/malformed.
    names = [c["name"] for c in get_all(_nested_structure(), "Tracker")]
    assert names == ["Top Tracker", "Dup A", "Bogus Tracker", "Bad Subs"]
    assert get_all(_nested_structure(), "InfoOnlyText") == []
    assert [c["name"] for c in get_all(_nested_structure(), "Alarm")] == ["Some Alarm"]


def test_get_all_recursive_finds_nested_controls():
    names = [c["name"] for c in get_all(_nested_structure(), "Tracker", recursive=True)]
    # hand-derived depth-first order; Dup B is eaten by the act-dup dedupe
    assert names == ["Top Tracker", "Nested Tracker", "Dup A", "Bogus Tracker", "Bad Subs"]
    assert "Dup B" not in names


def test_get_all_recursive_other_types():
    names = [c["name"] for c in get_all(_nested_structure(), "InfoOnlyText", recursive=True)]
    assert names == ["Inner InfoOnlyText"]


def test_get_all_returned_controls_are_copies():
    structure = _nested_structure()
    results = get_all(structure, "Tracker", recursive=True)
    nested = next(c for c in results if c["name"] == "Nested Tracker")
    nested["states"]["entries"] = "mutated"
    nested["mutated"] = True
    original = structure["controls"]["c2"]["subControls"]["act-2/sensors"]
    assert original["states"]["entries"] == "st-2"
    assert "mutated" not in original


# --------------------------------------------------------------------------- #
# Tracker entries parsing (pure helper)
# --------------------------------------------------------------------------- #
def test_tracker_entries_literal_cases():
    assert tracker_entries('["Front Door", "Hall Motion"]') == ["Front Door", "Hall Motion"]
    assert tracker_entries('  ["Gate", 7, 2.5]  ') == ["Gate", "7", "2.5"]
    assert tracker_entries(["OK", 3]) == ["OK", "3"]
    assert tracker_entries("[]") == []
    # everything "nothing usable" maps to None (keep the previous list)
    assert tracker_entries("") is None
    assert tracker_entries(None) is None
    assert tracker_entries("not a list") is None
    assert tracker_entries('{"entries": []}') is None
    assert tracker_entries("36333734-01c5-9336-ffff-d303131372d3000195") is None
    # non-scalar entries are dropped, scalars coerced
    assert tracker_entries('[1, "ok", {"x": 1}, [2]]') == ["1", "ok"]


TRACKER_KWARGS = dict(
    name="Device Tracker",
    uuidAction="trk-0001",
    room="Garden",
    cat="Security",
    type="Tracker",
    states={"entries": "trk-0001-entries"},
)


def test_tracker_sensor_updates_from_entries_stream():
    e = LoxoneTrackerSensor(**TRACKER_KWARGS)
    _stub_write(e)
    assert e.native_value is None
    assert e.extra_state_attributes["entries"] == []

    e.event_handler(_event({"trk-0001-entries": '["Front Door", "Hall Motion"]'}))
    assert e.native_value == "Front Door, Hall Motion"
    assert e.extra_state_attributes["entries"] == ["Front Door", "Hall Motion"]
    assert e.extra_state_attributes["count"] == 2

    # a malformed payload keeps the previous list
    e.event_handler(_event({"trk-0001-entries": "not json"}))
    assert e.native_value == "Front Door, Hall Motion"

    # an empty list of entries reads as unknown (HA rejects an empty
    # sensor state string)
    e.event_handler(_event({"trk-0001-entries": "[]"}))
    assert e.native_value is None
    assert e.extra_state_attributes["count"] == 0


def test_tracker_sensor_falls_back_to_action_uuid():
    e = LoxoneTrackerSensor(**{**TRACKER_KWARGS, "states": {}})
    _stub_write(e)
    e.event_handler(_event({"trk-0001": '["X"]'}))
    assert e.native_value == "X"


# --------------------------------------------------------------------------- #
# InfoOnlyText: read-only guard on writes
# --------------------------------------------------------------------------- #
async def test_info_only_text_refuses_writes(hass):
    sent: list[tuple] = []

    # A read-only InfoOnlyText must not emit any outbound command...
    info = LoxoneTextSensor(
        name="Dev Status",
        uuidAction="iot-io-0001",
        room="",
        cat="",
        type="InfoOnlyText",
        states={"text": "iot-io-0001-text"},
    )
    _stub_write(info)
    info.hass = hass
    info._send = lambda value, *_a, **_k: sent.append((value, "io"))  # record outbound
    assert info.type == "InfoOnlyText"
    await info.async_set_value("hello")
    assert sent == []

    # ...while the writable TextInput sends its value.
    text = LoxoneTextSensor(
        name="Dev Status",
        uuidAction="iot-0002",
        room="",
        cat="",
        type="TextInput",
        states={"text": "iot-0002-text"},
    )
    _stub_write(text)
    text.hass = hass
    text._send = lambda value, *_a, **_k: sent.append((value, "input"))
    assert text.type == "TextInput"
    await text.async_set_value("hello")
    assert sent == [("hello", "input")]


# --------------------------------------------------------------------------- #
# UpDownDigital buttons: unit style (bus fallback, like LoxoneButton tests)
# --------------------------------------------------------------------------- #
def _updown(**overrides):
    kwargs = dict(name="Stairwell", uuidAction="ud-0001", room="Hall", cat="Comfort", type="UpDownDigital")
    kwargs.update(overrides)
    return kwargs


async def test_updown_digital_buttons_send_on_commands(hass):
    for direction, expected_name, expected_command in (("up", "Up", "UpOn"), ("down", "Down", "DownOn")):
        e = LoxoneUpDownDigitalButton(**_updown(direction=direction))
        _stub_write(e)
        fired: list[tuple] = []
        e.hass = SimpleNamespace(
            bus=SimpleNamespace(async_fire=lambda et, event_data=None, _f=fired, **kw: _f.append((et, event_data)))
        )
        assert e._attr_unique_id == f"ud-0001/{direction}"
        assert e._attr_name == expected_name
        # the control has no state streams at all
        assert e._state_uuids() == frozenset()
        await e.async_press()
        assert (SENDDOMAIN, {"uuid": "ud-0001", "value": expected_command}) in fired


# --------------------------------------------------------------------------- #
# Full setup with the LoxAPP3 fixture: appear, update, press
# --------------------------------------------------------------------------- #
async def test_wp66_controls_appear_and_update(hass, mock_connection, mock_entry) -> None:
    """Acceptance: the WP-6.6 fixture controls appear after setup, update
    on fed state events, and the UpDownDigital buttons send their On
    commands."""
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    by_unique_id = {entry.unique_id: entry for entry in registry.entities.values()}

    # -- InfoOnlyText: appears, then follows its text stream -------------
    info_text = by_unique_id.get(DEV_STATUS_ACTION)
    assert info_text is not None, "the fixture Dev Status (InfoOnlyText) was not set up"
    assert info_text.domain == "sensor"
    # the entity exists before the first state message
    assert hass.states.get(info_text.entity_id) is not None

    mock_connection.feed(DEV_STATUS_TEXT, "OK")
    await hass.async_block_till_done()
    assert hass.states.get(info_text.entity_id).state == "OK"

    mock_connection.feed(DEV_STATUS_TEXT, "Pump fault: high pressure")
    await hass.async_block_till_done()
    assert hass.states.get(info_text.entity_id).state == "Pump fault: high pressure"

    # -- Tracker: top-level and the Alarm-nested one (recursive get_all) --
    tracker = by_unique_id.get(TRACKER_ACTION)
    assert tracker is not None, "the fixture Device Tracker was not set up"
    assert tracker.domain == "sensor"

    mock_connection.feed(TRACKER_ENTRIES, '["Front Door", "Hall Motion"]')
    await hass.async_block_till_done()
    state = hass.states.get(tracker.entity_id)
    assert state.state == "Front Door, Hall Motion"
    assert state.attributes["entries"] == ["Front Door", "Hall Motion"]
    assert state.attributes["count"] == 2

    # The nested Alarm/Sensors Tracker only exists via the recursive
    # subControls walk — and updates without disturbing the other one.
    alarm_tracker = by_unique_id.get(ALARM_SENSORS)
    assert alarm_tracker is not None, "the fixture Home Alarm/Sensors (nested Tracker) was not set up"
    assert alarm_tracker.domain == "sensor"
    mock_connection.feed(ALARM_SENSORS_ENTRIES, '["Kitchen Smoke"]')
    await hass.async_block_till_done()
    assert hass.states.get(alarm_tracker.entity_id).state == "Kitchen Smoke"
    assert hass.states.get(tracker.entity_id).state == "Front Door, Hall Motion"

    # -- UpDownDigital: one button per rocker side ------------------------
    up = by_unique_id.get(f"{STAIRWELL_ACTION}/up")
    down = by_unique_id.get(f"{STAIRWELL_ACTION}/down")
    assert up is not None, "the fixture Stairwell Up (UpDownDigital) button was not set up"
    assert down is not None, "the fixture Stairwell Down (UpDownDigital) button was not set up"
    assert up.domain == "button"
    assert down.domain == "button"
    assert up.original_name == "Up"
    assert down.original_name == "Down"

    sent_before = len(mock_connection.sent)
    await hass.services.async_call("button", "press", {"entity_id": up.entity_id}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": down.entity_id}, blocking=True)
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [
        {"uuid": STAIRWELL_ACTION, "value": "UpOn", "code": None},
        {"uuid": STAIRWELL_ACTION, "value": "DownOn", "code": None},
    ]
