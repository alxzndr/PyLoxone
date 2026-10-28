"""WP-7.1: LightControllerV2 on/off from moods, master and circuits; plain
turn-on (upstream ``e43301f`` / ``466ea0e`` / ``64ad11d`` / ``173d2ea``,
redesigned per the 2026-10 gaps doc "do not copy" items 1 and 2).

Everything runs through the real ``hass`` harness: the entry sets up from
``tests/fixtures/LoxAPP3.json`` (the two realistic controllers added there:
"Kitchen Light Controller" *with* a ``masterValue`` sub-control, "Bedroom
Light Controller" *without* one and without the optional ``moodList`` /
``additionalMoods`` states), states are fed through
``mock_connection.feed`` — the production coordinator path — and service
calls go through ``hass.services.async_call``.  Commands are asserted on
``mock_connection.sent``.

Expected values are hand-derived literals.  Semantics and their sources:

* mood id 0 is always off — Structure File v16/v17, LightControllerV2
  Commands (confirmed in the spec text);
* 778 — the static "Aus" mood of upstream's installation only
  (upstream ``466ea0e``; NOT in the structure file) —
  ``LIVE-MINISERVER-CHECKS.md`` #29;
* an empty ``activeMoods`` list is manual mode and follows the master,
  else the circuits — upstream's observation, unverified — live check #29;
* plain turn-on plays the first non-off mood of the mood list, else the
  spec word ``plus`` (the spec lists no other "on" command; the old
  ``changeTo/99`` is documented nowhere) — live check #29.
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.lights.lightcontroller import (
    OFF_MOOD_IDS,
    lcv2_circuit_level,
    lcv2_is_on,
    lcv2_mood_id,
    lcv2_mood_is_off,
    lcv2_plain_turn_on_command,
)

# --------------------------------------------------------------------------- #
# Fixture control identities (read from tests/fixtures/LoxAPP3.json)
# --------------------------------------------------------------------------- #
KIT_ACTION = "1d8f0a10-00a1-9766-ffff0d3a09848c7c"
KIT_ACTIVE_MOODS = "1d8f0a10-00a1-9767-ffff0d3a09848c7c"
KIT_MOOD_LIST = "1d8f0a10-00a1-976e-ffff0d3a09848c7c"
KIT_CEILING_POSITION = "1d8f0a10-00a1-9770-ffff0d3a09848c7c"
KIT_COUNTER_ACTIVE = "1d8f0a10-00a1-9774-ffff0d3a09848c7c"
KIT_MASTER_POSITION = "1d8f0a10-00a1-9777-ffff0d3a09848c7c"

BED_ACTION = "1d8f0a20-00b2-9766-ffff0d3a09848c7c"
BED_ACTIVE_MOODS = "1d8f0a20-00b3-9766-ffff0d3a09848c7c"
BED_LEFT_POSITION = "1d8f0a20-00b7-9766-ffff0d3a09848c7c"
BED_RIGHT_ACTIVE = "1d8f0a20-00bb-9766-ffff0d3a09848c7c"

MOODS = '[{"id": 0, "name": "Off"}, {"id": 3, "name": "Party"}, {"id": 5, "name": "Dinner"}]'
OFF_ONLY_MOODS = '[{"id": 0, "name": "Off"}, {"id": 778, "name": "Aus"}]'


async def _setup(hass, mock_connection, mock_entry):
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED
    reg = er.async_get(hass)
    kitchen = reg.async_get_entity_id("light", "loxone", KIT_ACTION)
    bedroom = reg.async_get_entity_id("light", "loxone", BED_ACTION)
    assert kitchen is not None and bedroom is not None, "the fixture controllers were not set up"
    return kitchen, bedroom


# --------------------------------------------------------------------------- #
# Pure helpers (parametrised literals)
# --------------------------------------------------------------------------- #
def test_off_mood_ids_documents_their_sources():
    # 0 = spec ("The moodID 0 is always 'off'"), 778 = upstream's observed
    # static "Aus" mood (not in the structure file; live check #29).
    assert OFF_MOOD_IDS == frozenset({0, 778})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (778, 778),
        ("0", 0),
        (" 3 ", 3),
        (-1, -1),
        ("ID4", None),
        ("", None),
        (True, None),
        (None, None),
        (1.5, None),
    ],
)
def test_lcv2_mood_id_normalises_ints_and_numeric_strings(value, expected):
    assert lcv2_mood_id(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(0, True), ("0", True), (778, True), ("778", True), (3, False), ("ID3", False)]
)
def test_lcv2_mood_is_off(value, expected):
    assert lcv2_mood_is_off(value) is expected


@pytest.mark.parametrize(
    ("active_moods", "master", "circuits", "expected"),
    [
        # activeMoods not yet received: unknown, whatever else is known.
        (None, 57.0, [40.0], None),
        (None, None, [], None),
        # any id outside the off set is on; ids arrive as ints or strings.
        ([3], None, [], True),
        ([0, 3], None, [], True),
        (["0", "778", "5"], None, [], True),
        # only off ids are off.
        ([0], None, [], False),
        ([778], None, [], False),
        ([0, 778], None, [], False),
        (["0", "778"], None, [], False),
        # Manual mode (empty list): the master decides when known...
        ([], 0.0, [90.0], False),
        ([], 57.0, [], True),
        # ... otherwise the circuits; nothing known at all stays unknown.
        ([], None, [], None),
        ([], None, [0.0, 0.0], False),
        ([], None, [0.0, 40.0], True),
        # Upstream's `master or 0` read every masterless manual-mode
        # controller as off while its circuits were lit (gaps doc, do-not-
        # copy 1): a missing master is None here, never 0.
        ([], None, [1.0], True),
        # Unknown circuit feeds (None entries) are skipped, not counted.
        ([], None, [None], None),
        ([], None, [None, 1.0], True),
    ],
)
def test_lcv2_is_on(active_moods, master, circuits, expected):
    assert lcv2_is_on(active_moods, master, circuits) is expected


@pytest.mark.parametrize(
    ("mood_list", "expected"),
    [
        # first mood whose id is not an off mood; off moods at the head are
        # skipped (upstream 466ea0e).
        ([{"id": 3, "name": "Party"}, {"id": 0, "name": "Off"}], "changeTo/3"),
        ([{"id": 0, "name": "Off"}, {"id": 1, "name": "On"}], "changeTo/1"),
        ([{"id": 0, "name": "Off"}, {"id": 778, "name": "Aus"}, {"id": 12, "name": "Cozy"}], "changeTo/12"),
        # no usable mood -> the spec word "plus" ("Changes to the next
        # mood"), never the undocumented changeTo/99.
        ([], "plus"),
        (None, "plus"),
        ([{"id": 0, "name": "Off"}, {"id": 778, "name": "Aus"}], "plus"),
        ([{"name": "no id"}, "junk"], "plus"),
        # non-numeric ids (the spec's "ID1" spelling) are not off ids.
        ([{"id": "ID4", "name": "Party"}], "changeTo/ID4"),
    ],
)
def test_lcv2_plain_turn_on_command(mood_list, expected):
    assert lcv2_plain_turn_on_command(mood_list) == expected


@pytest.mark.parametrize(
    ("circuit_type", "raw", "expected"),
    [
        ("Dimmer", 40, 40.0),
        ("Dimmer", "40.5", 40.5),
        ("Dimmer", 0, 0.0),
        ("Dimmer", "garbage", None),
        ("Switch", 1, 1.0),
        ("Switch", 1.0, 1.0),
        ("Switch", 0, 0.0),
        ("Switch", "1", 1.0),
        ("Switch", 0.5, 0.0),  # below the documented on level
        # The picker value uses the same parse as lights/colorpickers.py.
        ("ColorPickerV2", "hsv(120, 100, 45)", 45.0),
        ("ColorPickerV2", "hsv(0,0,0)", 0.0),
        ("ColorPickerV2", "temp[85, 2700]", 85.0),
        ("ColorPickerV2", "nonsense", None),
        ("ColorPickerV2", None, None),
        ("Unknown", 5, 5.0),  # plain passthrough for future shapes
    ],
)
def test_lcv2_circuit_level(circuit_type, raw, expected):
    assert lcv2_circuit_level(circuit_type, raw) == expected


# --------------------------------------------------------------------------- #
# The LCV2 `Switch` circuit (upstream 173d2ea, PS-02)
# --------------------------------------------------------------------------- #
def test_lcv2_switch_circuit_starts_none_not_unknown_string(loxapp3):
    """The circuit entity built from the real fixture control starts with
    ``is_on is None``.  The old ``_attr_is_on = STATE_UNKNOWN`` was the
    *string* ``"unknown"`` — truthy, so ``is_on`` claimed the light was
    on before any value ever arrived."""
    from custom_components.loxone.lights.switch import LoxoneLightSwitch

    circuit = dict(loxapp3["controls"][BED_ACTION]["subControls"][f"{BED_ACTION}/AI2"])
    entity = LoxoneLightSwitch(
        **{
            "hass": object(),
            "async_add_devices": lambda *_a, **_k: None,
            "config_entry": None,
            "lightcontroller_id": BED_ACTION,
            "lightcontroller_name": "Bedroom Light Controller",
            "enabled_default": False,
            "room": "Bedroom",
            "cat": "Comfort",
            **circuit,
        }
    )
    assert entity._attr_is_on is None
    assert entity.is_on is None


# --------------------------------------------------------------------------- #
# Real harness: state derivation through hass.states
# --------------------------------------------------------------------------- #
async def test_moods_decide_is_on(hass, mock_connection, mock_entry) -> None:
    """`[0]`, `[778]`, `[0, 778]` read off; `[3]`, `[0, 3]` read on.

    Old code (``activeMoods != [778]`` -> on): `[0]` and `[0, 778]` read
    *on*; see the non-vacuity note in the WP commit."""
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    for fed, expected in (("[0]", "off"), ("[778]", "off"), ("[0, 778]", "off"), ("[3]", "on"), ("[0, 3]", "on")):
        mock_connection.feed(KIT_ACTIVE_MOODS, fed)
        await hass.async_block_till_done()
        assert hass.states.get(kitchen).state == expected, f"activeMoods {fed}"


async def test_manual_mode_follows_master_with_master(hass, mock_connection, mock_entry) -> None:
    """Empty `activeMoods` with a master: the master position decides."""
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(KIT_ACTIVE_MOODS, "[]")
    mock_connection.feed(KIT_MASTER_POSITION, 0)
    await hass.async_block_till_done()
    assert hass.states.get(kitchen).state == "off"
    mock_connection.feed(KIT_MASTER_POSITION, 57)
    await hass.async_block_till_done()
    assert hass.states.get(kitchen).state == "on"


async def test_manual_mode_masterless_follows_circuits(hass, mock_connection, mock_entry) -> None:
    """Empty `activeMoods` on a controller *without* a master: the
    circuits decide (upstream's `master or 0` read this as off — do-not-
    copy 1)."""
    _, bedroom = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(BED_ACTIVE_MOODS, "[]")
    mock_connection.feed(BED_LEFT_POSITION, 0)
    mock_connection.feed(BED_RIGHT_ACTIVE, 0)
    await hass.async_block_till_done()
    assert hass.states.get(bedroom).state == "off"
    # one dimmer circuit above zero lights the controller...
    mock_connection.feed(BED_LEFT_POSITION, 40)
    await hass.async_block_till_done()
    assert hass.states.get(bedroom).state == "on"
    # ... and a lone switch circuit counts too.
    mock_connection.feed(BED_LEFT_POSITION, 0)
    mock_connection.feed(BED_RIGHT_ACTIVE, 1)
    await hass.async_block_till_done()
    assert hass.states.get(bedroom).state == "on"


async def test_unknown_before_first_active_moods(hass, mock_connection, mock_entry) -> None:
    """Known circuits but no `activeMoods` yet: unknown (PS-02), not a
    guess; the first `activeMoods` resolves it."""
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(KIT_CEILING_POSITION, 40)
    await hass.async_block_till_done()
    state = hass.states.get(kitchen)
    assert state.state == "unknown"
    mock_connection.feed(KIT_ACTIVE_MOODS, "[0]")
    await hass.async_block_till_done()
    assert hass.states.get(kitchen).state == "off"


async def test_plain_turn_on_sends_first_non_off_mood(hass, mock_connection, mock_entry) -> None:
    """The old code sent the undocumented `changeTo/99` (and upstream's
    first attempt did not skip off moods)."""
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(KIT_MOOD_LIST, MOODS)
    mock_connection.feed(KIT_ACTIVE_MOODS, "[0]")
    await hass.async_block_till_done()
    assert hass.states.get(kitchen).state == "off"
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": kitchen}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": KIT_ACTION, "value": "changeTo/3", "code": None}]


async def test_plain_turn_on_with_only_off_moods_sends_plus(hass, mock_connection, mock_entry) -> None:
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(KIT_MOOD_LIST, OFF_ONLY_MOODS)
    mock_connection.feed(KIT_ACTIVE_MOODS, "[0]")
    await hass.async_block_till_done()
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": kitchen}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": KIT_ACTION, "value": "plus", "code": None}]


async def test_plain_turn_on_while_on_sends_nothing(hass, mock_connection, mock_entry) -> None:
    """A plain turn-on must not steal the running mood (also true while
    the state is unknown — the *decision*, docstring of ``async_turn_on``:
    unknown counts as "not on", so an unknown controller does get the
    command)."""
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(KIT_MOOD_LIST, MOODS)
    mock_connection.feed(KIT_ACTIVE_MOODS, "[3]")
    await hass.async_block_till_done()
    assert hass.states.get(kitchen).state == "on"
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": kitchen}, blocking=True)
    assert mock_connection.sent[sent_before:] == []


async def test_plain_turn_on_while_unknown_sends_mood(hass, mock_connection, mock_entry) -> None:
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(KIT_MOOD_LIST, MOODS)
    mock_connection.feed(KIT_CEILING_POSITION, 0)  # available, state unknown
    await hass.async_block_till_done()
    assert hass.states.get(kitchen).state == "unknown"
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": kitchen}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": KIT_ACTION, "value": "changeTo/3", "code": None}]


async def test_turn_off_still_sends_change_to_zero(hass, mock_connection, mock_entry) -> None:
    kitchen, _ = await _setup(hass, mock_connection, mock_entry)
    mock_connection.feed(KIT_ACTIVE_MOODS, "[3]")
    await hass.async_block_till_done()
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_off", {"entity_id": kitchen}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": KIT_ACTION, "value": "changeTo/0", "code": None}]


async def test_controller_without_optional_states_sets_up_clean(hass, mock_connection, mock_entry, caplog) -> None:
    """The Bedroom controller has neither `moodList` nor `additionalMoods`
    (both optional per the structure file).  The old direct `states[...]`
    indexing raised a KeyError in `_state_uuids` — from `async_added_to_hass`
    — and the platform logged a skip (upstream 64ad11d's LightControllerV2
    part)."""
    with caplog.at_level(logging.WARNING):
        kitchen, bedroom = await _setup(hass, mock_connection, mock_entry)
    platform_errors = [
        record for record in caplog.records if record.levelno >= logging.WARNING and "loxone" in record.name.lower()
    ]
    assert not platform_errors, "loxone setup logged: " + "; ".join(r.getMessage() for r in platform_errors)
    # The controller without a mood list still switches (mood-less plain
    # turn-on falls back to `plus`).
    mock_connection.feed(BED_ACTIVE_MOODS, "[0]")
    await hass.async_block_till_done()
    assert hass.states.get(bedroom).state == "off"
    sent_before = len(mock_connection.sent)
    await hass.services.async_call("light", "turn_on", {"entity_id": bedroom}, blocking=True)
    assert mock_connection.sent[sent_before:] == [{"uuid": BED_ACTION, "value": "plus", "code": None}]
    # and the controller *with* the full state set is still there too
    # (scene generation and the effect list keep reading the mood list).
    assert hass.states.get(kitchen) is not None
