"""TextState ("Status" block, #529): a read-only text sensor.

Shape per the Loxone structure file v16 (p. 145): ``textAndIcon`` is a text
event with the text, ``iconAndColor`` (since 13.1) a text state holding
``{"icon": "Icons/xxxx.svg", "color": "#45864A"}``.  The fixture control
"Heating Status" (tests/fixtures/LoxAPP3.json) follows that shape; the
expected values below are literals, not derived from the code under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.sensor import text_state_icon_and_color

REPO_ROOT = Path(__file__).resolve().parent.parent

STATUS_ACTION = "63746c3a-0200-96d7-ffff-f746578747374000200"
STATUS_TEXT = "36333734-0201-9336-ffff-d303230302d3000201"
STATUS_ICON_COLOR = "36333734-0202-9336-ffff-d303230302d3000202"
STATUS_ENTITY = "sensor.office_heating_status"


async def _setup(hass, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()


async def _unload(hass, mock_entry) -> None:
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_text_state_creates_read_only_sensor(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup(hass, mock_entry)

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", "loxone", STATUS_ACTION) == STATUS_ENTITY
    device = dr.async_get(hass).async_get(registry.async_get(STATUS_ENTITY).device_id)
    assert device.name == "Heating Status"
    assert device.model == "TextState"
    # Read-only: no text entity for the Status block.
    assert registry.async_get_entity_id("text", "loxone", STATUS_ACTION) is None

    mock_connection.feed(STATUS_TEXT, "Heizen aktiv")
    await hass.async_block_till_done()
    state = hass.states.get(STATUS_ENTITY)
    assert state.state == "Heizen aktiv"
    assert state.attributes["device_type"] == "TextState"
    assert state.attributes["state_uuid"] == STATUS_TEXT

    await _unload(hass, mock_entry)


async def test_text_state_icon_and_color_attributes(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup(hass, mock_entry)

    mock_connection.feed(STATUS_TEXT, "Lüften")
    mock_connection.feed(STATUS_ICON_COLOR, '{"icon": "Icons/Ventilation.svg", "color": "#45864A"}')
    await hass.async_block_till_done()
    state = hass.states.get(STATUS_ENTITY)
    assert state.state == "Lüften"
    assert state.attributes["loxone_icon"] == "Icons/Ventilation.svg"
    assert state.attributes["color"] == "#45864A"
    # A Loxone SVG path is not an mdi icon; the frontend must not try it.
    assert "icon" not in state.attributes

    # Malformed payloads clear the attributes instead of raising.
    mock_connection.feed(STATUS_ICON_COLOR, "{not json")
    await hass.async_block_till_done()
    state = hass.states.get(STATUS_ENTITY)
    assert state.state == "Lüften"
    assert "loxone_icon" not in state.attributes
    assert "color" not in state.attributes

    await _unload(hass, mock_entry)


async def test_text_state_long_text_is_capped(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup(hass, mock_entry)

    text = "x" * 300
    mock_connection.feed(STATUS_TEXT, text)
    await hass.async_block_till_done()
    state = hass.states.get(STATUS_ENTITY)
    assert len(state.state) == 255
    assert state.state == "x" * 254 + "…"
    assert state.attributes["text"] == text

    # Back under the limit: the attribute goes away.
    mock_connection.feed(STATUS_TEXT, "kurz")
    await hass.async_block_till_done()
    state = hass.states.get(STATUS_ENTITY)
    assert state.state == "kurz"
    assert "text" not in state.attributes

    await _unload(hass, mock_entry)


async def test_text_state_without_icon_and_color_state(hass, mock_entry, enable_custom_integrations):
    """Firmware before 13.1 has no ``iconAndColor`` state."""
    config = json.loads((REPO_ROOT / "tests/fixtures/LoxAPP3.json").read_text())
    del config["controls"][STATUS_ACTION]["states"]["iconAndColor"]

    async def _fake_open(self, session=None):
        self.structure_file = config
        self.miniserver_version = config.get("softwareVersion")
        self.connected = True
        self.connection = Mock()
        self.connection.protocol.state.name = "OPEN"
        self._session_key = b"\x00" * 32
        return self

    mock_entry.add_to_hass(hass)
    with patch.object(LoxoneConnection, "open", _fake_open):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(STATUS_ENTITY)
    assert state is not None
    assert "loxone_icon" not in state.attributes

    await _unload(hass, mock_entry)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"icon": "Icons/a.svg", "color": "#FF0000"}', {"icon": "Icons/a.svg", "color": "#FF0000"}),
        ({"icon": "Icons/a.svg"}, {"icon": "Icons/a.svg"}),
        ('{"icon": "", "color": 5}', {}),
        ("[1, 2]", {}),
        ("", {}),
        (None, {}),
        (3.0, {}),
    ],
)
def test_icon_and_color_parser(raw, expected):
    assert text_state_icon_and_color(raw) == expected
