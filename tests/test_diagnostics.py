"""WP-1.4 diagnostics redaction (CORE-08).

The diagnostics export must carry only the *requested* entry and redact the
serial number + MAC (and any token/salt) before returning.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.loxone.diagnostics import async_get_config_entry_diagnostics

FIXTURE = Path(__file__).parent / "fixtures" / "LoxAPP3.json"

RAW_SERIAL = "TEST-SERIAL-0001"
RAW_MAC = "40:2B:0C:11:22:33"


async def test_diagnostics_redacts_serial_and_mac(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    raw = FIXTURE.read_text()
    assert RAW_SERIAL in raw and RAW_MAC in raw  # sanity: the fixture carries both

    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    lux = diag["LoxAPP3.json"]
    msinfo = lux["msInfo"]
    # The leading-3 secret fields must be masked to the HA sentinel.
    assert msinfo["serialNr"] == "**REDACTED**"
    assert msinfo["mac"] == "**REDACTED**"

    # The raw secret survives nowhere in the msInfo payload.
    serial_dump = json.dumps(lux, default=str)
    assert RAW_SERIAL not in serial_dump
    assert RAW_MAC not in serial_dump

    # Non-sensitive fields survive redaction.
    assert msinfo.get("firmware") == "7.1.0"
    assert "entry" in diag  # request-scoped payload is still present


async def test_diagnostics_returns_empty_dict_when_no_entry(hass, mock_entry, enable_custom_integrations) -> None:
    """An entry that never ran async_setup_entry: no miniserver -> {} not None."""
    result = await async_get_config_entry_diagnostics(hass, mock_entry)
    assert result == {}
