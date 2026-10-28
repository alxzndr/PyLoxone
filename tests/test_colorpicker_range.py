"""WP-7.2: ColorPickerV2 kelvin range from TWMin/TWMax; TunableWhite plain
turn-on (upstream ``7cc7c77`` / ``5867889``, light parts only).

Everything runs through the real ``hass`` harness: the entry sets up from
``tests/fixtures/LoxAPP3.json`` (the two standalone pickers added there —
"Parlour RGB Picker" in the shape a Miniserver really sends: string
``pickerType: "Rgb"``, ``TWMin`` 2200, ``TWMax`` 6500, states ``color`` /
``sequence`` / ``sequenceColorIdx``; "Parlour Tunable White" with the
narrow 2700-3500 range), states are fed through ``mock_connection.feed``
and service calls go through ``hass.services.async_call``.  Commands are
asserted on ``mock_connection.sent``.

Semantics and their sources:

* ``TWMin``/``TWMax`` are the ColorPickerV2 details with spec defaults
  2700/6500 — Structure File v16 (under the names ``minKelvin``/``maxKelvin``,
  which ``color_temp_range`` reads as a fallback since WP-7.8)
  and v17, confirmed in the spec text; the real device (firmware
  17.2.8.28) sends ``TWMin``/``TWMax`` and all three of the maintainer's
  pickers say 2200-6500, so a hard 2700 floor made Home Assistant clamp
  every warm request away (upstream ``7cc7c77``; PC-39);
* a plain TunableWhite turn-on sends ``setBrightness/<last level>`` — the
  ColorPickerV2 *Commands* list of the spec (v16 and v17) has
  ``setBrightness``, ``hsv()``, ``temp()`, ... but **no** ``on``, so the
  fork's ``On`` was at best undocumented, and a 200 reply never proves a
  command exists (upstream ``7cc7c77``/``5867889``; no TunableWhite picker
  in the house — live check #30 stays open for that half);
* ``DEFAULT_TURN_ON_KELVIN`` is clamped into the picker's own range when
  no temperature is known yet.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.lights.colorpickers import color_temp_range

# --------------------------------------------------------------------------- #
# Fixture control identities (read from tests/fixtures/LoxAPP3.json)
# --------------------------------------------------------------------------- #
RGB_ACTION = "63746c3a-0240-9766-ffff-7069636b6572000240"
RGB_COLOR = "37333735-0241-9336-ffff-636f6c6f72000241"

TW_ACTION = "63746c3a-0244-9766-ffff-7069636b6572000244"
TW_COLOR = "37333735-0245-9336-ffff-636f6c6f72000245"


async def _setup(hass, mock_connection, mock_entry):
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED
    reg = er.async_get(hass)
    rgb = reg.async_get_entity_id("light", "loxone", RGB_ACTION)
    tw = reg.async_get_entity_id("light", "loxone", TW_ACTION)
    assert rgb is not None and tw is not None, "the fixture pickers were not set up"
    return rgb, tw


# --------------------------------------------------------------------------- #
# The pure helper (unit level; the hass-level cases live below)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("details", "expected"),
    [
        # the real shape (all three of the maintainer's pickers)
        ({"pickerType": "Rgb", "TWMin": 2200, "TWMax": 6500}, (2200, 6500)),
        # Structure File v16 names the same two details
        # ``minKelvin``/``maxKelvin`` (WP-7.8: read as a fallback; the v16
        # spec examples are 2700 / 8500).
        ({"pickerType": "Rgb", "minKelvin": 2200, "maxKelvin": 8500}, (2200, 8500)),
        ({"minKelvin": 3000}, (3000, 6500)),
        # the v17 names win when a structure carries both
        ({"TWMin": 2200, "TWMax": 6500, "minKelvin": 2700, "maxKelvin": 8500}, (2200, 6500)),
        # missing details / missing or null values -> spec defaults (upstream
        # 5867889: `.get("TWMin", 2700)` alone still crashed on `int(None)`)
        (None, (2700, 6500)),
        ({}, (2700, 6500)),
        ({"TWMin": None, "TWMax": None}, (2700, 6500)),
        ({"TWMin": 2200}, (2200, 6500)),
        # non-numeric and non-positive fall back per end
        ({"TWMin": "warm", "TWMax": "cool"}, (2700, 6500)),
        ({"TWMin": 0, "TWMax": -5}, (2700, 6500)),
        # a nonsensical pair falls back as a pair
        ({"TWMin": 4000, "TWMax": 3000}, (2700, 6500)),
        ({"TWMin": 3000, "TWMax": 3000}, (2700, 6500)),
    ],
)
def test_color_temp_range_falls_back_to_the_spec_defaults(details, expected):
    assert color_temp_range(details) == expected


# --------------------------------------------------------------------------- #
# Through the real harness
# --------------------------------------------------------------------------- #
async def test_realistic_picker_advertises_the_range_from_details(hass, mock_connection, mock_entry) -> None:
    """Before the fix the class-level 2700-6500 was advertised, so Home
    Assistant clamped the whole 2200-2699 K band away."""
    rgb, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(RGB_COLOR, "temp(100,4000)")
    await hass.async_block_till_done()
    state = hass.states.get(rgb)
    assert state is not None and state.state == "on"
    assert state.attributes["min_color_temp_kelvin"] == 2200
    assert state.attributes["max_color_temp_kelvin"] == 6500


async def test_kelvin_at_the_reported_minimum_reaches_the_miniserver(hass, mock_connection, mock_entry) -> None:
    """`light.turn_on` with 2200 K must send `temp(...,2200)`; with the old
    hard 2700 floor Home Assistant clamped the request to 2700."""
    rgb, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(RGB_COLOR, "temp(100,4000)")
    await hass.async_block_till_done()
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": rgb, "color_temp_kelvin": 2200}, blocking=True)
    # brightness stays at the reported 100 % -> hass_to_lox(255) = 100.0
    assert mock_connection.sent[sent_before:] == [{"uuid": RGB_ACTION, "value": "temp(100.0, 2200)", "code": None}]


async def test_narrow_picker_default_kelvin_is_clamped_into_its_range(hass, mock_connection, mock_entry) -> None:
    """A turn-on without a known temperature must not send 4000 to a picker
    whose ``TWMax`` is 3500."""
    _, tw = await _setup(hass, mock_connection, mock_entry)
    # `hsv(0,0,0)` is the picker's off report: available, brightness 0, and
    # *no* temperature ever reported.
    mock_connection.feed(TW_COLOR, "hsv(0,0,0)")
    await hass.async_block_till_done()
    assert hass.states.get(tw).state == "off"
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": tw, "brightness": 255}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": TW_ACTION, "value": "temp(100.0, 3500)", "code": None}]


async def test_plain_turn_on_sends_set_brightness_not_on(hass, mock_connection, mock_entry) -> None:
    """The ColorPickerV2 command list (spec v16/v17) has no ``on``; the
    documented ``setBrightness`` carries the last known level (upstream
    5867889), full brightness when nothing has been reported (0 -> off ->
    the fallback)."""
    _, tw = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(TW_COLOR, "hsv(0,0,0)")
    await hass.async_block_till_done()
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": tw}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": TW_ACTION, "value": "setBrightness/100.0", "code": None}]


async def test_plain_turn_on_keeps_the_last_level(hass, mock_connection, mock_entry) -> None:
    """Not full brightness on every plain turn-on: the last reported level
    goes back out (80 % -> ``hass_to_lox(204)`` = 80.0)."""
    _, tw = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(TW_COLOR, "temp(80,3000)")
    await hass.async_block_till_done()
    assert hass.states.get(tw).state == "on"
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": tw}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": TW_ACTION, "value": "setBrightness/80.0", "code": None}]


async def test_known_temperature_survives_a_plain_brightness_call(hass, mock_connection, mock_entry) -> None:
    """A brightness-only call still sends the *reported* temperature with it
    (``temp()`` needs both parts; PC-11 unchanged)."""
    _, tw = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(TW_COLOR, "temp(80,3000)")
    await hass.async_block_till_done()
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": tw, "brightness": 128}, blocking=True)
    # hand: hass_to_lox(128) = 128 / 255 * 100
    assert mock_connection.sent[sent_before:] == [
        {"uuid": TW_ACTION, "value": "temp(50.19607843137255, 3000)", "code": None}
    ]
