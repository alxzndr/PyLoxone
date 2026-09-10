"""WP-6.4 regression tests: ``InfoOnlyDigital`` device-class inference (#402).

An ``InfoOnlyDigital`` control carries no control-type hint, so its binary
sensor device class is *inferred* from the LoxConfig author's own labels:
first the control's on/off display text (``details.text``), then the
control's category name.  Everything expected below is a hand-derived
literal read against the HA ``BinarySensorDeviceClass`` values and the
fixture (``tests/fixtures/LoxAPP3.json``, WP-0.2):

* "Main Door" (new fixture control, category "Doors", generic ON/OFF text)
  → ``door`` (category fallback, substring "door");
* "Breaker 1" (fixture, category "Energy") → ``plug`` (the category says
  the control powers something).

The keyword tables are a heuristic — a live-Miniserver check of the
inferred labels/categories is required before the mapping is assumed
right (flagged in the PR).
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.binary_sensor import LoxoneDigitalSensor, infer_digital_device_class

MAIN_DOOR_ACTION_UUID = "63746c3a-0301-96d7-ffff-572617475726000098"
MAIN_DOOR_ACTIVE_UUID = "36333734-0302-9336-ffff-d303131322d3000099"
BREAKER_ACTION_UUID = "63746c3a-0112-9656-ffff-b657220313a4000018"

ON_OFF_TEXT = {"text": {"on": "ON", "off": "OFF"}}


def _digital(**overrides):
    kwargs = {
        "uuidAction": "63746c3a-d101-96d7-ffff-572617475726000010",
        "name": "Test Digital",
        "room": "Hall",
        "cat": "Comfort",
        "type": "digital",
        "states": {"active": "36333734-d102-9336-ffff-d303131322d300011"},
        "details": ON_OFF_TEXT,
    }
    kwargs.update(overrides)
    return LoxoneDigitalSensor(**kwargs)


# --------------------------------------------------------------------------- #
# The pure helper: details.text wins over the category
# --------------------------------------------------------------------------- #
def test_text_phrase_wins_over_category():
    # "bewegung erkannt" / "frei" (a typical LoxConfig motion label pair)
    # class the control `motion` even though the category (Energy →
    # plug, by the category table) would say otherwise.
    assert infer_digital_device_class({"text": {"on": "Bewegung erkannt", "off": "Frei"}}, "Energy") == "motion"
    # on/off labels are matched case-insensitively.
    assert infer_digital_device_class({"text": {"on": "OPEN", "off": "CLOSED"}}, "Doors") == "opening"


def test_category_fallback_from_generic_text():
    # Generic ON/OFF text carries no meaning → the category decides:
    # "Doors" contains "door" → `door`.
    assert infer_digital_device_class(ON_OFF_TEXT, "Doors") == "door"
    assert infer_digital_device_class(ON_OFF_TEXT, "Energy") == "plug"
    assert infer_digital_device_class(ON_OFF_TEXT, "Türen") == "door"
    assert infer_digital_device_class(ON_OFF_TEXT, "Fenster") == "window"


def test_no_match_yields_none():
    # Neither a known text label nor a known category → classless, as
    # before WP-6.4.
    assert infer_digital_device_class(ON_OFF_TEXT, "Comfort") is None
    assert infer_digital_device_class(ON_OFF_TEXT, "") is None
    assert infer_digital_device_class(ON_OFF_TEXT, None) is None
    # "No motion" is a *negation* label, not a table phrase — the exact
    # match is deliberate: a substring hunt would class it `motion`.
    assert infer_digital_device_class({"text": {"on": "No motion", "off": "Free"}}, "Comfort") is None
    assert infer_digital_device_class(None, "") is None
    assert infer_digital_device_class({}, "Comfort") is None


def test_malformed_details_never_raise():
    # User data may miss, be typed wrong, or be a non-mapping: every
    # lookup is .get()-guarded (PS-04/WP-4.5 principle).
    assert infer_digital_device_class(None, None) is None
    assert infer_digital_device_class({"text": None}, "gas") == "gas"
    assert infer_digital_device_class({"text": {"on": 5}}, "") is None
    assert infer_digital_device_class({"text": {"off": None}}, "") is None
    assert infer_digital_device_class({"text": {"on": "  wasserschaden  "}}, "") == "moisture"
    assert infer_digital_device_class("nope", "water") == "moisture"


# --------------------------------------------------------------------------- #
# Entity level: the inferred class lands on `_attr_device_class`
# --------------------------------------------------------------------------- #
def test_digital_control_gets_category_class():
    e = _digital(cat="Doors")
    assert e._attr_device_class == BinarySensorDeviceClass.DOOR


def test_digital_control_generic_stays_classless():
    e = _digital(cat="Comfort")
    assert e._attr_device_class is None


def test_digital_subsensor_is_not_inferred():
    # A sub-sensor of another control (like the Ventilation presence)
    # keeps its dedicated class logic — the parent's category must not
    # re-class it.
    e = _digital(type="presence", parent_id="fan-01", cat="Doors")
    assert e._attr_device_class == BinarySensorDeviceClass.PRESENCE

    e = _digital(parent_id="fan-01", cat="Doors")
    assert e._attr_device_class is None


# --------------------------------------------------------------------------- #
# Full setup with the LoxAPP3 fixture: appear, classified, updates on feed
# --------------------------------------------------------------------------- #
async def test_fixture_info_only_digital_appear_classified_and_update(hass, mock_connection, mock_entry) -> None:
    """Acceptance: the fixture's InfoOnlyDigital entities appear after
    setup with their inferred class, and update on a fed state event."""
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    by_unique_id = {entry.unique_id: entry for entry in registry.entities.values() if entry.domain == "binary_sensor"}
    door = by_unique_id.get(MAIN_DOOR_ACTION_UUID)
    assert door is not None, "the fixture Main Door (InfoOnlyDigital) was not set up"
    # Category "Doors", generic ON/OFF text → `door` (hand-derived).
    assert door.original_device_class == "door"

    breaker = by_unique_id.get(BREAKER_ACTION_UUID)
    assert breaker is not None, "the fixture Breaker 1 (InfoOnlyDigital) was not set up"
    # Category "Energy" → `plug` (hand-derived).
    assert breaker.original_device_class == "plug"

    # The entity exists before the first state message.  (The pre-feed
    # value is PS-11 territory -- a different WP -- so nothing about it
    # is asserted here.)
    assert hass.states.get(door.entity_id) is not None

    mock_connection.feed(MAIN_DOOR_ACTIVE_UUID, 1.0)
    await hass.async_block_till_done()
    assert hass.states.get(door.entity_id).state == "on"

    mock_connection.feed(MAIN_DOOR_ACTIVE_UUID, 0.0)
    await hass.async_block_till_done()
    assert hass.states.get(door.entity_id).state == "off"
