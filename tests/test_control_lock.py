"""A control locked in Loxone (``jLocked``) refuses commands from Home Assistant.

Structure file v16, "Locking controls": ``jLocked`` is a text state holding
JSON ``{"locked": 0|1|2, "reason": "..."}``; a locked control cannot be
controlled through the API. Every real control on the maintainer's
Miniserver publishes it (fw 17.2.8.28).
"""

from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.loxone.helpers import parse_control_lock

SWITCH = "63746c3a-0157-9766-ffff-e6720526f6f6000087"  # fixture "Living Room Light Switch"
SWITCH_ACTIVE = "36333734-0158-9336-ffff-d303135372d3000088"
SWITCH_LOCK = "36333734-0157-9336-ffff-d30313537ffff0000aa"  # added by the test


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", (0, "")),
        (None, (0, "")),
        ('{"locked": 0, "reason": ""}', (0, "")),
        ('{"locked": 1, "reason": "Cleaning"}', (1, "Cleaning")),
        ('{"locked": 2, "reason": "Holiday mode"}', (2, "Holiday mode")),
        (1.0, (1, "")),
        ("not json", (0, "")),
        ('{"locked": 7}', (0, "")),
    ],
)
def test_parse_control_lock(value, expected) -> None:
    assert parse_control_lock(value) == expected


@pytest.fixture
def locked_switch_fixture(loxapp3):
    states = loxapp3["controls"][SWITCH]["states"]
    states["jLocked"] = SWITCH_LOCK
    yield
    del states["jLocked"]


async def test_locked_control_refuses_commands(hass, locked_switch_fixture, mock_connection, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = "switch.parlour_living_room_light_switch"
    assert hass.states.get(entity_id) is not None, sorted(hass.states.async_entity_ids("switch"))

    mock_connection.feed(SWITCH_ACTIVE, 0)  # a state value first: an unavailable entity has no attributes
    mock_connection.feed(SWITCH_LOCK, '{"locked": 2, "reason": "Holiday mode"}')
    await hass.async_block_till_done()
    attributes = hass.states.get(entity_id).attributes
    assert attributes["locked"] is True
    assert attributes["locked_by"] == "logic"
    assert attributes["lock_reason"] == "Holiday mode"

    sent_before = len(mock_connection.sent)
    with pytest.raises(HomeAssistantError, match="locked by the Loxone logic: Holiday mode"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert len(mock_connection.sent) == sent_before

    # Unlocked again: the JSON with locked 0 is not a lock.
    mock_connection.feed(SWITCH_LOCK, '{"locked": 0, "reason": ""}')
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["locked"] is False
    assert "locked_by" not in hass.states.get(entity_id).attributes
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()
    assert len(mock_connection.sent) == sent_before + 1

    assert await hass.config_entries.async_unload(mock_entry.entry_id)


def test_radio_select_reads_an_unlocked_json_lock_as_unlocked(hass) -> None:
    """The Radio select used bool() of the text state, so the not-locked
    JSON {"locked": 0} read as locked and refused every selection."""
    from custom_components.loxone.select import LoxoneSelect

    select = LoxoneSelect(
        name="Radio",
        uuidAction="63746c3a-0157-9766-ffff-e6720526f6f6000099",
        type="Radio",
        room="Parlour",
        cat="Comfort",
        details={"outputs": {"1": "Low"}, "allOff": "Off"},
        states={"activeOutput": "r-o", "jLocked": "r-lock"},
    )
    select.hass = hass
    select.async_write_ha_state = lambda *a, **k: None
    select.event_handler({"r-lock": '{"locked": 0, "reason": ""}'})
    assert select._locked is False
    select.event_handler({"r-lock": '{"locked": 1, "reason": "x"}'})
    assert select._locked is True
