"""Entities and devices of blocks deleted in Loxone Config are removed."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.loxone import DOMAIN, async_remove_config_entry_device

# Not in tests/fixtures/LoxAPP3.json (a removed heat-pump meter, as on the
# maintainer's installation).
GONE = "1279d200-019e-4360-ffff248e4fbf4562"
# The fixture's "Switch" control.
PRESENT = "63746c3a-0157-9766-ffff-e6720526f6f6000087"


def _seed(hass, mock_entry, unique_id: str, *, with_device: bool = True) -> str:
    dev_reg = dr.async_get(hass)
    device_id = None
    if with_device:
        device_id = dev_reg.async_get_or_create(
            config_entry_id=mock_entry.entry_id, identifiers={(DOMAIN, unique_id)}, name=f"dev {unique_id}"
        ).id
    return (
        er.async_get(hass)
        .async_get_or_create("sensor", DOMAIN, unique_id, config_entry=mock_entry, device_id=device_id)
        .entity_id
    )


async def _setup(hass, mock_entry) -> None:
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.LOADED


async def test_entities_of_removed_blocks_are_removed(hass, mock_connection, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    stale = _seed(hass, mock_entry, GONE)
    stale_suffixed = _seed(hass, mock_entry, f"{GONE}-777", with_device=False)
    not_judged = _seed(hass, mock_entry, "traffic_rx_sensor", with_device=False)

    await _setup(hass, mock_entry)

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(stale) is None
    assert ent_reg.async_get(stale_suffixed) is None
    # Unique ids without a Loxone id are never judged.
    assert ent_reg.async_get(not_judged) is not None
    # The fixture's own entities stay.
    assert ent_reg.async_get_entity_id("switch", DOMAIN, PRESENT) is not None
    # The emptied device of the removed block goes too.
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GONE)}) is None


async def test_nothing_is_removed_when_most_entities_look_stale(hass, mock_connection, mock_entry, loxapp3) -> None:
    """A structure file that lost most of its controls (a Miniserver
    mid-upload) must not wipe the registry."""
    mock_entry.add_to_hass(hass)
    stale = [_seed(hass, mock_entry, f"{GONE[:-4]}{i:04x}", with_device=False) for i in range(400)]

    await _setup(hass, mock_entry)

    ent_reg = er.async_get(hass)
    assert all(ent_reg.async_get(e) is not None for e in stale)


async def test_a_device_whose_block_exists_cannot_be_deleted(hass, mock_connection, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    await _setup(hass, mock_entry)
    dev_reg = dr.async_get(hass)
    present = dev_reg.async_get_device(identifiers={(DOMAIN, PRESENT)})
    assert present is not None
    assert await async_remove_config_entry_device(hass, mock_entry, present) is False

    gone = dev_reg.async_get_or_create(config_entry_id=mock_entry.entry_id, identifiers={(DOMAIN, GONE)}, name="gone")
    assert await async_remove_config_entry_device(hass, mock_entry, gone) is True
