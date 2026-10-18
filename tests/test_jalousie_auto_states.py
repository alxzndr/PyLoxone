"""Jalousie sun-automation states per the Structure File (upstream PR #530).

The spec names them ``autoActive`` and ``infoText``; the integration used to
read ``autoState`` / ``autoInfoText`` only, so on a structure file with the
spec names the sun automation always showed "off" and no ``Sun auto`` select
was created. The old names stay as fallback (tests/test_cover.py builds its
Jalousie with them). The fixture's "Terrace Jalousie" carries the spec names.
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.loxone.const import DOMAIN
from custom_components.loxone.helpers import jalousie_auto_state_uuid, jalousie_auto_text_uuid

TERRACE = "63746c3a-019b-9766-ffff-6a616c6f756e3000c0"
TERRACE_AUTO_ACTIVE = "36333734-01c8-9336-ffff-d303137282d3000c05"
TERRACE_INFO_TEXT = "36333734-01c8-9336-ffff-d303137282d3000c06"


async def test_spec_named_auto_states_reach_cover_and_select(hass, mock_connection, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    cover = registry.async_get_entity_id("cover", DOMAIN, TERRACE)
    select = registry.async_get_entity_id("select", DOMAIN, f"{TERRACE}/sun-auto")
    assert cover is not None
    assert select is not None, "a Jalousie with autoActive gets the Sun auto select"

    mock_connection.feed(TERRACE_AUTO_ACTIVE, 1)
    mock_connection.feed(TERRACE_INFO_TEXT, "Sun position")
    await hass.async_block_till_done()

    attrs = hass.states.get(cover).attributes
    assert attrs["auto_state"] == "on"  # "off" for every blind before
    assert attrs["automatic_text"] == "Sun position"
    assert hass.states.get(select).state == "Auto"


def test_spec_names_win_and_old_names_are_the_fallback() -> None:
    both = {"autoActive": "a", "autoState": "b", "infoText": "c", "autoInfoText": "d"}
    assert jalousie_auto_state_uuid(both) == "a"
    assert jalousie_auto_text_uuid(both) == "c"
    old = {"autoState": "b", "autoInfoText": "d"}
    assert jalousie_auto_state_uuid(old) == "b"
    assert jalousie_auto_text_uuid(old) == "d"
    assert jalousie_auto_state_uuid({}) is None
    assert jalousie_auto_text_uuid(None) is None
