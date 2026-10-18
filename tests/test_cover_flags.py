"""Gate and blind safety states appear as boolean attributes.

On the maintainer's Miniserver (fw 17.2.8.28) the Gate publishes
``preventOpen``/``preventClose`` and the automatic Jalousie ``safetyActive``
and ``locked``; none of them reached Home Assistant before.
"""

from __future__ import annotations

import pytest

GATE = "63746c3a-017f-9726-ffff-56e204761746000127"
JALOUSIE = "63746c3a-019b-9766-ffff-6a616c6f756e3000c0"

ADDED = {
    GATE: {
        "preventOpen": "50726576-0001-9336-ffff-000000000000aa01",
        "preventClose": "50726576-0002-9336-ffff-000000000000aa02",
    },
    JALOUSIE: {
        "safetyActive": "53616665-0001-9336-ffff-000000000000aa03",
        "locked": "4c6f636b-0001-9336-ffff-000000000000aa04",
    },
}


@pytest.fixture
def flag_states(loxapp3):
    for uuid, states in ADDED.items():
        loxapp3["controls"][uuid]["states"].update(states)
    yield
    for uuid, states in ADDED.items():
        for key in states:
            del loxapp3["controls"][uuid]["states"][key]


@pytest.mark.parametrize(
    ("control", "state", "attribute"),
    [
        (GATE, "preventOpen", "prevent_open"),
        (GATE, "preventClose", "prevent_close"),
        (JALOUSIE, "safetyActive", "safety_active"),
        (JALOUSIE, "locked", "movement_locked"),
    ],
)
async def test_flag_states_become_attributes(
    hass, loxapp3, flag_states, mock_connection, mock_entry, control, state, attribute
) -> None:
    from homeassistant.helpers import entity_registry as er

    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("cover", "loxone", control)

    # A position first: an unavailable entity carries no attributes.
    mock_connection.feed(loxapp3["controls"][control]["states"]["position"], 0.5)
    mock_connection.feed(ADDED[control][state], 1)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes[attribute] is True

    mock_connection.feed(ADDED[control][state], 0)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes[attribute] is False
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
