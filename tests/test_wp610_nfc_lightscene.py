"""WP-6.10 regression tests: ``NfcCodeTouch`` and ``LightsceneRGB`` (PS-27)
control types that produced no entities before this WP.

All expected values are hand-derived literals against the fixture
(``tests/fixtures/LoxAPP3.json`` — the three PS-27 controls added here
with the real uuids/state maps from a live Miniserver, firmware
17.2.8.28) and the documented protocol conventions (Loxone-epoch ms
counter per ``helpers.loxone_timestamp``, 0-100 % channel values, 0-255
HA brightness).

PS-27 credential rule: ``lastcode`` and ``lasttag`` identify *how*
someone authenticated (a credential) and must never appear as entity
state, as an attribute, or in the ``loxone_nfc_auth`` event payload.
"""

from __future__ import annotations

from datetime import datetime, UTC
from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.const import EVENT_NFC_AUTH
from custom_components.loxone.light import (
    LoxoneLightsceneRGB,
    lightscene_channel_command,
    lightscene_channel_value,
    lightscene_off_command,
    lightscene_on_command,
)
from custom_components.loxone.sensor import (
    LoxoneNfcCodeDateSensor,
    LoxoneNfcCodeTouchSensor,
    LoxoneNfcDeviceStateSensor,
    nfc_auth_event_payload,
    nfc_code_date,
)
from custom_components.loxone.select import (
    LoxoneLightsceneRGBScene,
    lightscene_active_scene_option,
    lightscene_scene_command,
    lightscene_scene_lookup,
)

# --------------------------------------------------------------------------- #
# Fixture control identities (read from tests/fixtures/LoxAPP3.json)
# --------------------------------------------------------------------------- #
NFC_ACTION = "13a9fb56-01bb-fda5-ffff47167370ed09"
NFC_LASTUSER = "1cf8a1de-01ff-c0ee-ffff47167370ed09"
NFC_CODEDATE = "1cf8a1de-01ff-c0e9-ffff47167370ed09"
NFC_DEVSTATE = "1cf8a1de-01ff-c0ea-ffff47167370ed09"
# credential states — present in the fixture, and must be used by nothing:
NFC_LASTCODE = "1cf8a1de-01ff-c0f0-ffff47167370ed09"
NFC_LASTTAG = "1cf8a1de-01ff-c0ef-ffff47167370ed09"

LS1_ACTION = "1cf643a3-0083-7c35-ffff47167370ed09"
LS1_RED = "1cf643a3-0083-7c2f-08ff494c2b0681a0"
LS1_GREEN = "1cf643a3-0083-7c30-09ff494c2b0681a0"
LS1_BLUE = "1cf643a3-0083-7c31-0aff494c2b0681a0"
LS2_ACTION = "1d6c95a2-00b6-e46f-ffff0d3a09848c7c"
LS2_BLUE = "1d6c95a2-00b6-e47a-ffff0d3a09848c7c"


def _stub_write(entity):
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    entity.schedule_update_ha_state = lambda *a, **k: None
    entity.async_write_ha_state = lambda *a, **k: None


def _nfc_kwargs(**overrides) -> dict:
    kwargs = dict(
        name="Entry NFC",
        uuidAction=NFC_ACTION,
        room="Hall",
        cat="Security",
        type="NfcCodeTouch",
        states={
            "lastuser": NFC_LASTUSER,
            "codeDate": NFC_CODEDATE,
            "deviceState": NFC_DEVSTATE,
            "lastcode": NFC_LASTCODE,  # a credential: must be ignored everywhere
            "lasttag": NFC_LASTTAG,  # a credential: must be ignored everywhere
        },
        details={"jLockable": True},
    )
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------- #
# nf_code_date (pure helper)
# --------------------------------------------------------------------------- #
def test_nfc_code_date_literals():
    # Loxone epoch: 1230768000 s = 2009-01-01T00:00:00Z (helper constant
    # ``LOXONE_EPOCH_SECONDS``).  0 ms therefore parses to the epoch date,
    # 86400000 ms (= 24 h) one day later — hand-derived, no arithmetic
    # from the code under test.
    assert nfc_code_date(0) == datetime(2009, 1, 1, tzinfo=UTC)
    assert nfc_code_date(86400000) == datetime(2009, 1, 2, tzinfo=UTC)
    # numeric-string form of the same counter
    assert nfc_code_date("0") == datetime(2009, 1, 1, tzinfo=UTC)
    # the second documented possibility: a datetime string, naive = UTC
    assert nfc_code_date("2026-09-10 12:34:56") == datetime(2026, 9, 10, 12, 34, 56, tzinfo=UTC)
    # an explicit offset is honoured (01:02:03+02:00 == 23:02:03Z the day before)
    assert nfc_code_date("2026-09-10T01:02:03+02:00") == datetime(2026, 9, 9, 23, 2, 3, tzinfo=UTC)
    # everything "nothing usable" maps to None
    assert nfc_code_date(None) is None
    assert nfc_code_date("") is None
    assert nfc_code_date(True) is None
    assert nfc_code_date("garbage") is None
    assert nfc_code_date(["2026-09-10"]) is None


# --------------------------------------------------------------------------- #
# NfcCodeTouch entities (unit style + credential rule)
# --------------------------------------------------------------------------- #
def _nfc_hass(entity) -> list:
    """Wire the entity to a stub bus that records fired events."""
    fired: list[tuple] = []
    entity.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=lambda et, data=None: fired.append((et, data))))
    return fired


def test_nfc_last_user_sensor_updates_and_fires_event():
    e = LoxoneNfcCodeTouchSensor(**_nfc_kwargs())
    _stub_write(e)
    fired = _nfc_hass(e)
    assert e.native_value is None

    # who: follows the lastuser stream
    e.event_handler({NFC_LASTUSER: "Alice"})
    assert e.native_value == "Alice"
    assert fired == []

    # when: a codeDate change fires exactly one authentication event
    e.event_handler({NFC_CODEDATE: 0})
    assert len(fired) == 1
    event, payload = fired[0]
    assert event == EVENT_NFC_AUTH
    assert payload["uuid"] == NFC_ACTION
    assert payload["name"] == "Entry NFC"
    assert payload["user"] == "Alice"
    # 0 Loxone-epoch ms = 2009-01-01T00:00:00Z (hand-derived, see above)
    assert payload["code_date"] == "2009-01-01T00:00:00+00:00"

    # the same codeDate arriving again does not re-fire (a change, not a
    # message, is an authentication)
    e.event_handler({NFC_CODEDATE: 0})
    assert len(fired) == 1

    # a newer codeDate fires again with the current user
    e.event_handler({NFC_CODEDATE: 86400000})
    assert len(fired) == 2
    assert fired[1][1]["code_date"] == "2009-01-02T00:00:00+00:00"
    assert fired[1][1]["user"] == "Alice"


def test_nfc_last_user_empty_maps_to_unknown():
    e = LoxoneNfcCodeTouchSensor(**_nfc_kwargs())
    _stub_write(e)
    _nfc_hass(e)
    e.event_handler({NFC_LASTUSER: ""})
    assert e.native_value is None


def test_nfc_event_payload_never_contains_credentials():
    payload = nfc_auth_event_payload(
        control={"uuidAction": NFC_ACTION, "name": "Entry NFC"},
        user="Bob",
        code_date="2026-09-10 12:34:56",
        entry_id="entry-1",
    )
    assert payload == {
        "uuid": NFC_ACTION,
        "name": "Entry NFC",
        "user": "Bob",
        "code_date": "2026-09-10T12:34:56+00:00",
        "entry_id": "entry-1",
    }
    for forbidden in ("lastcode", "lasttag", "code", "tag", "keypadauth"):
        assert forbidden not in payload

    # unparsing codeDate falls back to the raw string; entry None drops the key
    payload = nfc_auth_event_payload(
        control={"uuidAction": NFC_ACTION, "name": "Entry NFC"},
        user=None,
        code_date="garbage",
    )
    assert payload["code_date"] == "garbage"
    assert payload["user"] is None
    assert "entry_id" not in payload


def test_nfc_entities_expose_no_credentials_in_attributes():
    # The fixture control carries lastcode/lasttag states; none of the three
    # entities may echo them into state attributes.
    for cls in (LoxoneNfcCodeTouchSensor, LoxoneNfcCodeDateSensor, LoxoneNfcDeviceStateSensor):
        e = cls(**_nfc_kwargs())
        _stub_write(e)
        attrs = e.extra_state_attributes
        for forbidden in ("lastcode", "lasttag"):
            assert forbidden not in attrs, f"{cls.__name__} published {forbidden}"
        assert attrs["device_type"] == "NfcCodeTouch"


def test_nfc_code_date_sensor_updates():
    e = LoxoneNfcCodeDateSensor(**_nfc_kwargs())
    _stub_write(e)
    assert e.native_value is None
    e.event_handler({NFC_CODEDATE: 86400000})
    assert e.native_value == datetime(2009, 1, 2, tzinfo=UTC)
    # an unparseable value keeps the last known instant
    e.event_handler({NFC_CODEDATE: "garbage"})
    assert e.native_value == datetime(2009, 1, 2, tzinfo=UTC)
    # the credential streams are not subscribed to at all
    assert NFC_LASTCODE not in e._state_uuids()
    assert NFC_LASTTAG not in e._state_uuids()


def test_nfc_device_state_sensor_updates():
    e = LoxoneNfcDeviceStateSensor(**_nfc_kwargs())
    _stub_write(e)
    assert e.native_value is None
    e.event_handler({NFC_DEVSTATE: 3})
    assert e.native_value == "3"
    assert NFC_LASTCODE not in e._state_uuids()
    assert NFC_LASTTAG not in e._state_uuids()


# --------------------------------------------------------------------------- #
# LightsceneRGB light (pure helpers + unit style)
# --------------------------------------------------------------------------- #
def test_lightscene_channel_value_literals():
    assert lightscene_channel_value(0) == 0.0
    assert lightscene_channel_value(100) == 100.0
    assert lightscene_channel_value(75) == 75.0
    assert lightscene_channel_value("75") == 75.0
    assert lightscene_channel_value(" 33.5 ") == 33.5
    # clamped, never an out-of-band value
    assert lightscene_channel_value(150) == 100.0
    assert lightscene_channel_value(-2) == 0.0
    # malformed → None (keep the channel's last value)
    assert lightscene_channel_value("oops") is None
    assert lightscene_channel_value(None) is None
    assert lightscene_channel_value(True) is None


def test_lightscene_command_literals():
    # hand-derived: round(50.4)=50, round(100.0)=100
    assert lightscene_channel_command("red", 50.4) == "red/50"
    assert lightscene_channel_command("blue", 100.0) == "blue/100"
    assert lightscene_channel_command("green", 0.0) == "green/0"
    assert lightscene_on_command() == "On"
    assert lightscene_off_command() == "Off"


def _ls1_kwargs(**overrides) -> dict:
    kwargs = dict(
        name="Cinema Light 1",
        uuidAction=LS1_ACTION,
        room="Bedroom",
        cat="Comfort",
        type="LightsceneRGB",
        states={"red": LS1_RED, "green": LS1_GREEN, "blue": LS1_BLUE},
        details={"jLockable": True, "sceneList": {}},
    )
    kwargs.update(overrides)
    return kwargs


def test_lightscene_rgb_recomputes_color_from_channels():
    e = LoxoneLightsceneRGB(**_ls1_kwargs())
    _stub_write(e)
    assert e.is_on is False
    assert e._attr_rgb_color is None

    # 100 % → round(255*100/100)=255
    e.event_handler({LS1_RED: "100"})
    assert e._attr_rgb_color == (255, 0, 0)
    assert e.is_on is True

    # 50 % → round(255*50/100)=round(127.5)=128
    e.event_handler({LS1_GREEN: "50"})
    assert e._attr_rgb_color == (255, 128, 0)

    # 25 % → round(255*25/100)=round(63.75)=64
    e.event_handler({LS1_BLUE: "25"})
    assert e._attr_rgb_color == (255, 128, 64)

    # combined `color` stream is not consumed (the channels are
    # authoritative) — feeding it may not touch the state or raise
    e.event_handler({"some-color-uuid": "hsv(1,2,3)"})
    assert e._attr_rgb_color == (255, 128, 64)

    # a malformed channel value keeps the previous colour
    e.event_handler({LS1_RED: "oops"})
    assert e._attr_rgb_color == (255, 128, 64)

    # all channels back to 0 → off
    e.event_handler({LS1_RED: "0"})
    e.event_handler({LS1_GREEN: "0"})
    e.event_handler({LS1_BLUE: "0"})
    assert e._attr_rgb_color == (0, 0, 0)
    assert e.is_on is False


async def test_lightscene_rgb_sends_commands(hass):
    e = LoxoneLightsceneRGB(**_ls1_kwargs())
    _stub_write(e)
    e.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *a, **k: None))
    sent: list[str] = []
    e._send = lambda value, *_a, **_k: sent.append(value)

    await e.async_turn_off()
    assert sent == ["Off"]

    sent.clear()
    # 255 → round(255*100/255)=100, 0 → 0, 128 → round(128*100/255)=50
    # (hass_to_lox(128) = 50.196…)
    await e.async_turn_on(rgb_color=(255, 0, 128))
    assert sent == ["red/100", "green/0", "blue/50"]
    assert e._attr_rgb_color == (255, 0, 128)

    sent.clear()
    # a plain power-on (no rgb) sends the bare On word and keeps the
    # last known colour
    await e.async_turn_on(brightness=255)
    assert sent == ["On"]
    assert e._attr_rgb_color == (255, 0, 128)


# --------------------------------------------------------------------------- #
# LightsceneRGB scene select (pure helpers + unit style)
# --------------------------------------------------------------------------- #
def test_lightscene_scene_lookup_degrades_to_no_options():
    # the live block's sceneList is an empty dict — and every
    # missing/malformed shape degrades to "no options" (PS-19 rule),
    # which the setup translates into "no select entity".
    assert lightscene_scene_lookup(None) == ([], [])
    assert lightscene_scene_lookup({}) == ([], [])
    assert lightscene_scene_lookup({"sceneList": {}}) == ([], [])
    assert lightscene_scene_lookup({"sceneList": "oops"}) == ([], [])
    assert lightscene_scene_lookup({"sceneList": 5}) == ([], [])
    # but the two plausible populated shapes work (list of names, and the
    # Radio-style {number: name} dict with sorted keys)
    assert lightscene_scene_lookup({"sceneList": ["Relax", "Focus"]}) == (["Relax", "Focus"], ["Relax", "Focus"])
    assert lightscene_scene_lookup({"sceneList": {"0": "Relax", "1": "Focus"}}) == (
        ["Relax", "Focus"],
        ["Relax", "Focus"],
    )
    # duplicate labels are disambiguated the way Radio options are
    # (the dedupe is case-sensitive, mirroring build_option_maps)
    assert lightscene_scene_lookup({"sceneList": ["Dim", "Dim"]}) == (["Dim", "Dim (2)"], ["Dim", "Dim"])
    assert lightscene_scene_lookup({"sceneList": ["Dim", "dim"]}) == (["Dim", "dim"], ["Dim", "dim"])


def test_lightscene_active_scene_option_literals():
    options, raw = lightscene_scene_lookup({"sceneList": ["Relax", "Focus"]})
    # by scene name
    assert lightscene_active_scene_option("Relax", options, raw) == "Relax"
    assert lightscene_active_scene_option("Focus", options, raw) == "Focus"
    # by index (number or all-digit string)
    assert lightscene_active_scene_option(1, options, raw) == "Focus"
    assert lightscene_active_scene_option("1", options, raw) == "Focus"
    # unknown → None (keep the last known option)
    assert lightscene_active_scene_option("No Such Scene", options, raw) is None
    assert lightscene_active_scene_option(9, options, raw) is None
    assert lightscene_active_scene_option(-1, options, raw) is None
    assert lightscene_active_scene_option(None, options, raw) is None
    assert lightscene_active_scene_option(True, options, raw) is None

    assert lightscene_scene_command("Focus") == "scene/Focus"


def _scene_kwargs(**overrides) -> dict:
    kwargs = dict(
        name="Cinema Light 1",
        uuidAction=LS1_ACTION,
        room="Bedroom",
        cat="Comfort",
        type="LightsceneRGB",
        details={"jLockable": True, "sceneList": ["Relax", "Focus"]},
        states={"activeScene": LS1_ACTION + "/active", "red": LS1_RED, "green": LS1_GREEN, "blue": LS1_BLUE},
    )
    kwargs.update(overrides)
    return kwargs


def test_lightscene_scene_select_tracks_active_scene():
    e = LoxoneLightsceneRGBScene(**_scene_kwargs())
    _stub_write(e)
    assert e._attr_options == ["Relax", "Focus"]
    assert e._attr_current_option is None

    e.event_handler({LS1_ACTION + "/active": "Focus"})
    assert e._attr_current_option == "Focus"
    e.event_handler({LS1_ACTION + "/active": 0})
    assert e._attr_current_option == "Relax"
    # an unknown scene name keeps the last known option
    e.event_handler({LS1_ACTION + "/active": "Renamed"})
    assert e._attr_current_option == "Relax"


async def test_lightscene_scene_select_sends_command(hass):
    e = LoxoneLightsceneRGBScene(**_scene_kwargs())
    _stub_write(e)
    e.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *a, **k: None))
    sent: list[str] = []
    e._send = lambda value, *_a, **_k: sent.append(value)

    await e.select_option("Focus")
    assert sent == ["scene/Focus"]
    assert e._attr_current_option == "Focus"

    try:
        await e.select_option("No Such Scene")
        raise AssertionError("selecting an unknown scene must raise")
    except HomeAssistantError:
        pass
    assert sent == ["scene/Focus"]


# --------------------------------------------------------------------------- #
# Full setup with the LoxAPP3 fixture: appear, update, command, event
# --------------------------------------------------------------------------- #
async def test_wp610_controls_appear_and_update(hass, mock_connection, mock_entry) -> None:
    """Acceptance: the PS-27 fixture controls appear after setup, update on
    fed state events, send their write commands, and the NFC reader fires
    its authentication event — with no credential in sight."""
    await hass.config.async_set_time_zone("UTC")
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    by_unique_id = {entry.unique_id: entry for entry in registry.entities.values()}

    # -- NfcCodeTouch: three sensors on the control device ---------------
    # the primary sensor's registry identity is the control's uuidAction
    nfc_user = by_unique_id.get(NFC_ACTION)
    assert nfc_user is not None, "the fixture Entry NFC (lastuser) sensor was not set up"
    assert nfc_user.domain == "sensor"
    nfc_date = by_unique_id.get(f"{NFC_ACTION}-code_date")
    assert nfc_date is not None, "the fixture Entry NFC code date sensor was not set up"
    assert nfc_date.domain == "sensor"
    nfc_state = by_unique_id.get(f"{NFC_ACTION}-device_state")
    assert nfc_state is not None, "the fixture Entry NFC device state sensor was not set up"
    assert nfc_state.domain == "sensor"

    # an entity must not exist per credential state
    assert NFC_LASTCODE not in by_unique_id
    assert NFC_LASTTAG not in by_unique_id

    # capture the authentication event
    auth_events: list[dict] = []
    unsub = hass.bus.async_listen(EVENT_NFC_AUTH, lambda e: auth_events.append(e.data))

    # who: the lastuser sensor follows its stream
    mock_connection.feed(NFC_LASTUSER, "Alice")
    await hass.async_block_till_done()
    assert hass.states.get(nfc_user.entity_id).state == "Alice"
    assert auth_events == []

    # when: a codeDate change updates the timestamp sensor AND fires the
    # event (86400000 Loxone-epoch ms = 2009-01-02T00:00:00Z, hand-derived)
    mock_connection.feed(NFC_CODEDATE, "86400000")
    await hass.async_block_till_done()
    assert hass.states.get(nfc_date.entity_id).state == "2009-01-02T00:00:00+00:00"
    assert len(auth_events) == 1
    assert auth_events[0]["user"] == "Alice"
    assert auth_events[0]["code_date"] == "2009-01-02T00:00:00+00:00"
    assert auth_events[0]["uuid"] == NFC_ACTION
    for forbidden in ("lastcode", "lasttag", "code", "tag"):
        assert forbidden not in auth_events[0], f"credential {forbidden!r} leaked into the event"

    # the repeated identical value fires nothing
    mock_connection.feed(NFC_CODEDATE, "86400000")
    await hass.async_block_till_done()
    assert len(auth_events) == 1

    # deviceState: the raw register read
    mock_connection.feed(NFC_DEVSTATE, 3)
    await hass.async_block_till_done()
    assert hass.states.get(nfc_state.entity_id).state == "3"

    # the credential *streams* exist in the structure file; feeding them
    # must not surface anywhere
    mock_connection.feed(NFC_LASTCODE, "deadbeef")
    await hass.async_block_till_done()
    assert "deadbeef" not in hass.states.get(nfc_user.entity_id).state
    assert hass.states.get(nfc_date.entity_id).state == "2009-01-02T00:00:00+00:00"
    assert len(auth_events) == 1
    unsub()

    # -- LightsceneRGB: one RGB light per control -------------------------
    ls1 = by_unique_id.get(LS1_ACTION)
    assert ls1 is not None, "the fixture Cinema Light 1 was not set up"
    assert ls1.domain == "light"
    ls2 = by_unique_id.get(LS2_ACTION)
    assert ls2 is not None, "the fixture Cinema Light 2 was not set up"
    assert ls2.domain == "light"

    # an entity (light or select) must not be created per channel stream or
    # for the combined color state (only the channels are consumed)
    assert "light" not in {e.domain for e in by_unique_id.values() if e.unique_id in (LS1_RED, LS1_BLUE)}
    for entry in by_unique_id.values():
        if entry.domain == "light":
            assert entry.unique_id not in (LS1_RED, LS1_GREEN, LS1_BLUE)

    # feed channel 1: 100 % red reads (255, 0, 0) → on (tuple(): the
    # attribute serialises as a 3-tuple; the values are the assertion)
    mock_connection.feed(LS1_RED, "100")
    await hass.async_block_till_done()
    state = hass.states.get(ls1.entity_id)
    assert state.state == "on"
    assert tuple(state.attributes["rgb_color"]) == (255, 0, 0)

    # the second light is independent (half blue → (0, 0, 128))
    mock_connection.feed(LS2_BLUE, "50")
    await hass.async_block_till_done()
    assert hass.states.get(ls2.entity_id).state == "on"
    assert tuple(hass.states.get(ls2.entity_id).attributes["rgb_color"]) == (0, 0, 128)
    # …and light 1 is untouched by light 2's feed
    assert tuple(hass.states.get(ls1.entity_id).attributes["rgb_color"]) == (255, 0, 0)

    # empty sceneList (the live block shape) → *no* scene select at all
    assert f"{LS1_ACTION}/scene" not in by_unique_id
    assert f"{LS2_ACTION}/scene" not in by_unique_id
    assert "LightsceneRGB" not in [e.original_name for e in registry.entities.values() if e.domain == "select"]

    # turn off sends the Off word, addressed at the control's uuidAction
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_off", {"entity_id": ls1.entity_id}, blocking=True)
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [
        {"uuid": LS1_ACTION, "value": "Off", "code": None},
    ]

    # turn on with an rgb: 255→100, 64→25 (round(64*100/255)=round(25.1)=25),
    # 8→3 (round(8*100/255)=round(3.14)=3)
    sent_before = len(mock_connection.sent)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ls1.entity_id, "rgb_color": (255, 64, 8)}, blocking=True
    )
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [
        {"uuid": LS1_ACTION, "value": "red/100", "code": None},
        {"uuid": LS1_ACTION, "value": "green/25", "code": None},
        {"uuid": LS1_ACTION, "value": "blue/3", "code": None},
    ]

    # a plain power-on sends the bare On word
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": ls2.entity_id}, blocking=True)
    await hass.async_block_till_done()
    assert mock_connection.sent[sent_before:] == [
        {"uuid": LS2_ACTION, "value": "On", "code": None},
    ]
