"""Units from Loxone format strings, as Loxone Config actually writes them.

Loxone Config's own temperature formats are ``%.1f°`` and ``%d°``, without C
or F (found in LoxoneConfig.exe), and formats can use the ``<v.N>`` value
placeholders instead of printf. The temperature unit then comes from the
Miniserver itself: ``msInfo.tempUnit`` (0 = °C, 1 = °F).
"""

from __future__ import annotations

import copy
from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.loxone.helpers import clean_unit
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection

PARLOUR = "63746c3a-0109-96d7-ffff-572617475726000009"  # InfoOnlyAnalog "Temperature Parlour"


@pytest.mark.parametrize(
    ("fmt", "temperature_unit", "expected"),
    [
        ("%.1f°", "°C", "°C"),
        ("%d°", "°F", "°F"),
        ("%.1f °C", "°F", "°C"),  # an explicit unit wins
        ("<v.1>°C", "°C", "°C"),
        ("<v.1> kWh", "°C", "kWh"),
        ("%.3fkW", "°C", "kW"),
        ("%1.3fV", "°C", "V"),
        ("%fA", "°C", "A"),
        ("%.3f%%", "°C", "%"),
    ],
)
def test_clean_unit(fmt, temperature_unit, expected) -> None:
    assert clean_unit(fmt, temperature_unit) == expected


@pytest.mark.parametrize(("temp_unit", "expected"), [(0, "°C"), (1, "°F")])
async def test_bare_degree_sensor_uses_the_miniserver_unit(
    hass, mock_entry, loxapp3, enable_custom_integrations, monkeypatch, temp_unit, expected
) -> None:
    structure = copy.deepcopy(loxapp3)
    structure["controls"][PARLOUR]["details"]["format"] = "%.1f°"
    structure["msInfo"]["tempUnit"] = temp_unit

    async def fake_open(self, session=None, **_kwargs):
        self.structure_file = structure
        self.miniserver_version = structure.get("softwareVersion")
        self.connection = Mock()
        self.connection.protocol.state.name = "OPEN"
        self._session_key = b"\x00" * 32

    async def idle_run(self, *_args, **_kwargs):
        import asyncio

        await asyncio.Event().wait()

    monkeypatch.setattr(LoxoneConnection, "open", fake_open)
    monkeypatch.setattr(LoxoneConnection, "run", idle_run)
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    entity_id = er.async_get(hass).async_get_entity_id("sensor", "loxone", PARLOUR)
    entity = hass.data["entity_components"]["sensor"].get_entity(entity_id)
    assert entity.device_class == SensorDeviceClass.TEMPERATURE
    assert entity.native_unit_of_measurement == expected
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
