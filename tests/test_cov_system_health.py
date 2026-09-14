"""Coverage for ``custom_components.loxone.system_health`` (was 0%).

Pins what the System Health card reports (CORE-31 / WP-5.2):

* a loaded entry's coordinator is read from ``config_entry.runtime_data``,
* fields the structure file does not carry stay at the literal
  ``"Unavailable"`` instead of raising or reporting ``None``,
* an entry whose coordinator is not connected yet is skipped in favour of
  the next entry (and never raises ``KeyError``),
* with no entries at all every field is ``"Unavailable"``,
* ``async_register`` hands ``system_health_info`` to HA.
"""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loxone.const import DOMAIN
from custom_components.loxone.miniserver import MiniServer
from custom_components.loxone.system_health import async_register, system_health_info

# Hand-written structure file for the synthetic entries below: it carries the
# three msInfo fields the real tests/fixtures/LoxAPP3.json deliberately omits.
_STRUCTURE: dict[str, Any] = {
    "msInfo": {
        "serialNr": "504F94A0B1C2",
        "msName": "Villa",
        "projectName": "Villa Nova",
        "localUrl": "villa.local",
        "remoteUrl": "dns.loxonecloud.com/504F94A0B1C2",
        "miniserverType": 2,
    },
    "softwareVersion": ["14", "5", "12", "7"],
}

_ALL_UNAVAILABLE = {
    "Loxone Miniserver Serial": "Unavailable",
    "Project Name": "Unavailable",
    "Local Url": "Unavailable",
    "Remote Url": "Unavailable",
    "Loxone Software Version": "Unavailable",
}


class _Coordinator:
    """The only part of ``LoxoneCoordinator`` system health looks at."""

    def __init__(self, miniserver) -> None:
        self.miniserver = miniserver


def _add_entry(hass, unique_id: str, structure: dict | None) -> MockConfigEntry:
    """Register a loxone entry; ``structure=None`` means 'not connected yet'."""
    entry = MockConfigEntry(domain=DOMAIN, version=4, data={}, options={}, unique_id=unique_id)
    entry.add_to_hass(hass)
    if structure is not None:
        entry.runtime_data = _Coordinator(MiniServer(hass, structure, entry))
    return entry


async def test_system_health_info_with_no_entries(hass) -> None:
    """Nothing installed: every field degrades to 'Unavailable', no KeyError."""
    assert await system_health_info(hass) == _ALL_UNAVAILABLE


async def test_system_health_info_reports_the_structure_file(hass) -> None:
    """A connected entry reports serial, version and the three msInfo urls."""
    _add_entry(hass, "504F94A0B1C2", _STRUCTURE)

    assert await system_health_info(hass) == {
        "Loxone Miniserver Serial": "504F94A0B1C2",
        "Project Name": "Villa Nova",
        "Local Url": "villa.local",
        "Remote Url": "dns.loxonecloud.com/504F94A0B1C2",
        # ["14", "5", "12", "7"] dot-joined by hand (CORE-16).
        "Loxone Software Version": "14.5.12.7",
    }


async def test_system_health_info_skips_an_unconnected_entry(hass) -> None:
    """An entry without runtime_data is stepped over, not reported on."""
    _add_entry(hass, "NOT-CONNECTED", None)
    _add_entry(hass, "504F94A0B1C2", _STRUCTURE)

    info = await system_health_info(hass)

    assert info["Loxone Miniserver Serial"] == "504F94A0B1C2"
    assert info["Project Name"] == "Villa Nova"


async def test_system_health_info_skips_an_entry_without_msinfo(hass) -> None:
    """A structure file that has no msInfo dict is treated as 'not ready'."""
    _add_entry(hass, "NO-MSINFO", {"softwareVersion": ["1", "0"]})

    assert await system_health_info(hass) == _ALL_UNAVAILABLE


async def test_system_health_info_of_the_real_fixture_entry(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """End to end against a genuinely set-up entry and tests/fixtures/LoxAPP3.json.

    That fixture carries serialNr + softwareVersion but no projectName /
    localUrl / remoteUrl, so exactly those three stay 'Unavailable'.
    """
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert await system_health_info(hass) == {
        "Loxone Miniserver Serial": "TEST-SERIAL-0001",
        "Project Name": "Unavailable",
        "Local Url": "Unavailable",
        "Remote Url": "Unavailable",
        # fixture softwareVersion is ["7", "1", "0", "28"].
        "Loxone Software Version": "7.1.0.28",
    }


def test_async_register_registers_the_info_callback(hass) -> None:
    """HA's registration hook is handed the info coroutine itself."""
    registered = []

    class _Registration:
        def async_register_info(self, info_callback) -> None:
            registered.append(info_callback)

    async_register(hass, _Registration())

    assert registered == [system_health_info]
