"""ValueSelector ("Up-Down Buttons") controls as ``number`` entities (upstream PR #522).

Per the Loxone Structure File (v16, "ValueSelector"): details ``increaseOnly``
and ``format``; states ``min``, ``max``, ``step`` and ``value``; command
``{value}`` (between min and max). Unlike a Slider, the range arrives as
state events, so the entity must follow them.

Uuids are copied from tests/fixtures/LoxAPP3.json ("Fan Level Selector").
"""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.const import DOMAIN

SELECTOR_UUID = "63746c3a-0230-9696-ffff-8746e6573732000230"
SELECTOR_MIN = "36333734-0231-9336-ffff-d303136612d3000231"
SELECTOR_MAX = "36333734-0232-9336-ffff-d303136612d3000232"
SELECTOR_STEP = "36333734-0233-9336-ffff-d303136612d3000233"
SELECTOR_VALUE = "36333734-0234-9336-ffff-d303136612d3000234"


async def _setup(hass, mock_entry) -> str:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("number", DOMAIN, SELECTOR_UUID)
    assert entity_id is not None, "no number entity for the ValueSelector"
    return entity_id


async def test_value_selector_follows_its_range_and_value_states(hass, mock_connection, mock_entry) -> None:
    entity_id = await _setup(hass, mock_entry)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE  # no value seen yet

    mock_connection.feed(SELECTOR_MIN, 1.0)
    mock_connection.feed(SELECTOR_MAX, 5.0)
    mock_connection.feed(SELECTOR_STEP, 1.0)
    mock_connection.feed(SELECTOR_VALUE, 3.0)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "3.0"
    assert state.attributes["min"] == 1.0
    assert state.attributes["max"] == 5.0
    assert state.attributes["step"] == 1.0
    assert state.attributes["increase_only"] is False
    assert state.attributes["device_type"] == "ValueSelector"

    # A later range change alone (no value event) still reaches the state.
    mock_connection.feed(SELECTOR_MAX, 10.0)
    mock_connection.feed(SELECTOR_STEP, 0.5)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.attributes["max"] == 10.0
    assert state.attributes["step"] == 0.5

    # A zero step would make the entity unsettable: ignored.
    mock_connection.feed(SELECTOR_STEP, 0)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["step"] == 0.5


async def test_value_selector_set_value_sends_the_bare_value(hass, mock_connection, mock_entry) -> None:
    entity_id = await _setup(hass, mock_entry)
    mock_connection.feed(SELECTOR_MIN, 0.0)
    mock_connection.feed(SELECTOR_MAX, 5.0)
    mock_connection.feed(SELECTOR_VALUE, 2.0)
    await hass.async_block_till_done()

    sent_before = len(mock_connection.sent)
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": 4}, blocking=True)
    await hass.async_block_till_done()

    assert mock_connection.sent[sent_before:] == [{"uuid": SELECTOR_UUID, "value": "4.0", "code": None}]


async def test_value_selector_gets_its_own_device(hass, mock_connection, mock_entry) -> None:
    entity_id = await _setup(hass, mock_entry)
    entry = er.async_get(hass).async_get(entity_id)
    device = dr.async_get(hass).async_get(entry.device_id)
    assert device.name == "Fan Level Selector"
    assert device.model == "ValueSelector"
    assert entry.has_entity_name is True
    assert entry.original_name is None  # the device name is the display name
