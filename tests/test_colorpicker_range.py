"""WP-7.2: ColorPickerV2 kelvin range from TWMin/TWMax (upstream
``7cc7c77`` / ``5867889``).

Everything runs through the real ``hass`` harness: the entry sets up from
``tests/fixtures/LoxAPP3.json`` (the two standalone pickers added there —
"Parlour RGB Picker" in the shape a Miniserver really sends: string
``pickerType: "Rgb"``, ``TWMin`` 2200, ``TWMax`` 6500, states ``color`` /
``sequence`` / ``sequenceColorIdx``; "Parlour Tunable White" with the
narrow 2700-3500 range), states are fed through ``mock_connection.feed``
and service calls go through ``hass.services.async_call``.  Commands are
asserted on ``mock_connection.sent``.

Semantics and their source: ``TWMin``/``TWMax`` are the ColorPickerV2
details with spec defaults 2700/6500 — Structure File v16 (under the names
``minKelvin``/``maxKelvin``) and v17, confirmed in the spec text; the real
device (firmware 17.2.8.28) sends ``TWMin``/``TWMax`` and all three of the
maintainer's pickers say 2200-6500, so a hard 2700 floor made Home
Assistant advertise — and dashboard sliders honour — a range that cut the
warm end off (PC-39; live check #30).
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
    """Before the fix the class-level 2700-6500 was advertised, so the
    whole 2200-2699 K band was cut off — on the dashboard too."""
    rgb, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(RGB_COLOR, "temp(100,4000)")
    await hass.async_block_till_done()
    state = hass.states.get(rgb)
    assert state is not None and state.state == "on"
    assert state.attributes["min_color_temp_kelvin"] == 2200
    assert state.attributes["max_color_temp_kelvin"] == 6500


async def test_kelvin_at_the_reported_minimum_reaches_the_miniserver(hass, mock_connection, mock_entry) -> None:
    """`light.turn_on` with 2200 K must send `temp(...,2200)` through the
    real service path, end to end."""
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
