"""Climate commands per the Loxone Structure File (v16), ported from upstream PR #530.

* IRoomControllerV2 ``setComfortModeTemp/{temp}``: "Temporary change the
  comfort mode temperature ... states.comfortTemperature +
  states.comfortTemperatureOffset ± step(0.5)" -- an absolute temperature.
* IRoomController (legacy): ``mode`` 0-6 (0 automatic, 1/2 automatic
  currently heating/cooling, 3/4 automatic heating/cooling, 5/6 manual),
  ``serviceMode`` 1 = heating and cooling off, commands ``mode``,
  ``service`` and ``settemp``.

Driven through the real service registry against the fixture; uuids are
copied from tests/fixtures/LoxAPP3.json.
"""

from __future__ import annotations

from homeassistant.components.climate import HVACMode

from custom_components.loxone.const import PRESET_SCHEDULE

BEDROOM_V2 = "63746c3a-0126-9647-ffff-f6f6d20436f6000038"
BEDROOM_ENTITY = "climate.bedroom_bedroom_controller"
BEDROOM_COMFORT = "36333734-0129-9336-ffff-d303132362d3000041"
BEDROOM_ACTIVE_MODE = "36333734-012f-9336-ffff-d303132362d3000047"
BEDROOM_OPERATING_MODE = "36333734-0134-9336-ffff-d303132362d3000052"

OFFICE = "63746c3a-013e-9666-ffff-36520436f6e7000062"
OFFICE_ENTITY = "climate.office_office_climate"
OFFICE_MODE = "36333734-0235-9336-ffff-d303133652d3000235"
OFFICE_SERVICE_MODE = "36333734-0236-9336-ffff-d303133652d3000236"
OFFICE_HEAT_IX = "36333734-0237-9336-ffff-d303133652d3000237"
OFFICE_COOL_IX = "36333734-0238-9336-ffff-d303133652d3000238"


async def _setup(hass, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()


def _sent_to(mock_connection, uuid: str, since: int) -> list[str]:
    return [s["value"] for s in mock_connection.sent[since:] if s["uuid"] == uuid]


async def _call(hass, service: str, data: dict) -> None:
    await hass.services.async_call("climate", service, data, blocking=True)
    await hass.async_block_till_done()


async def test_v2_auto_target_sends_the_absolute_comfort_temperature(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)
    mock_connection.feed(BEDROOM_OPERATING_MODE, 1)  # automatic, heating only
    mock_connection.feed(BEDROOM_ACTIVE_MODE, 1)  # comfort
    mock_connection.feed(BEDROOM_COMFORT, 21.0)
    await hass.async_block_till_done()

    before = len(mock_connection.sent)
    await _call(hass, "set_temperature", {"entity_id": BEDROOM_ENTITY, "temperature": 22.5})

    # Not "setComfortModeTemp/1.5" (22.5 - 21.0), which asked for 1.5 °C.
    assert _sent_to(mock_connection, BEDROOM_V2, before) == ["setComfortModeTemp/22.5"]


async def test_legacy_controller_reads_mode_and_service_mode(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)

    for mode, expected in ((0, "auto"), (2, "auto"), (3, "heat"), (4, "cool"), (5, "heat"), (6, "cool")):
        mock_connection.feed(OFFICE_MODE, mode)
        await hass.async_block_till_done()
        assert hass.states.get(OFFICE_ENTITY).state == expected, mode

    mock_connection.feed(OFFICE_SERVICE_MODE, 1)  # heating and cooling off
    await hass.async_block_till_done()
    assert hass.states.get(OFFICE_ENTITY).state == HVACMode.OFF

    # No HEAT_COOL: the legacy controller has no such mode.
    assert hass.states.get(OFFICE_ENTITY).attributes["hvac_modes"] == ["off", "auto", "heat", "cool"]


async def test_legacy_controller_hvac_mode_commands(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)
    mock_connection.feed(OFFICE_MODE, 0)
    mock_connection.feed(OFFICE_SERVICE_MODE, 0)
    await hass.async_block_till_done()

    before = len(mock_connection.sent)
    await _call(hass, "set_hvac_mode", {"entity_id": OFFICE_ENTITY, "hvac_mode": "off"})
    assert _sent_to(mock_connection, OFFICE, before) == ["service/1"]

    # Leaving OFF first resets the service mode, then selects the mode.
    mock_connection.feed(OFFICE_SERVICE_MODE, 1)
    await hass.async_block_till_done()
    before = len(mock_connection.sent)
    await _call(hass, "set_hvac_mode", {"entity_id": OFFICE_ENTITY, "hvac_mode": "heat"})
    assert _sent_to(mock_connection, OFFICE, before) == ["service/0", "mode/3"]

    mock_connection.feed(OFFICE_SERVICE_MODE, 0)
    await hass.async_block_till_done()
    before = len(mock_connection.sent)
    await _call(hass, "set_hvac_mode", {"entity_id": OFFICE_ENTITY, "hvac_mode": "cool"})
    assert _sent_to(mock_connection, OFFICE, before) == ["mode/4"]


async def test_legacy_controller_settemp_uses_the_active_index(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)
    mock_connection.feed(OFFICE_HEAT_IX, 1)  # comfort heating
    mock_connection.feed(OFFICE_COOL_IX, 2)  # comfort cooling
    mock_connection.feed(OFFICE_MODE, 3)  # automatic heating
    await hass.async_block_till_done()

    before = len(mock_connection.sent)
    await _call(hass, "set_temperature", {"entity_id": OFFICE_ENTITY, "temperature": 21})
    assert _sent_to(mock_connection, OFFICE, before) == ["settemp/1/21.0"]

    mock_connection.feed(OFFICE_MODE, 4)  # automatic cooling: the cooling index
    await hass.async_block_till_done()
    before = len(mock_connection.sent)
    await _call(hass, "set_temperature", {"entity_id": OFFICE_ENTITY, "temperature": 24})
    assert _sent_to(mock_connection, OFFICE, before) == ["settemp/2/24.0"]


BEDROOM_OVERRIDE_REASON = "36333734-0135-9336-ffff-d303132362d3000053"
OVERRIDE_REASON_ENTITY = "sensor.bedroom_bedroom_controller_override_reason"


async def test_v2_schedule_preset_during_an_override_ends_the_override(hass, mock_connection, mock_entry) -> None:
    """WP-7.4 fork regression (lost in c7ad8c9; 0.9.23 had it, ``7561247``):
    selecting "Schedule" while overridden must send ``stopOverride`` —
    Structure File v16, IRoomControllerV2: "Stops a currently running
    override timer" — and nothing that changes the operating mode.
    ``setOperatingMode/0`` (what the fork sent since WP-4.1) does not end
    the override and moves this automatic, heating-only controller (1) to
    heating and cooling (0)."""
    await _setup(hass, mock_entry)
    mock_connection.feed(BEDROOM_OPERATING_MODE, 1)  # automatic, only heating allowed
    mock_connection.feed(BEDROOM_OVERRIDE_REASON, 3)  # comfort override
    await hass.async_block_till_done()
    assert hass.states.get(BEDROOM_ENTITY).attributes["is_overridden"] is True

    before = len(mock_connection.sent)
    await _call(hass, "set_preset_mode", {"entity_id": BEDROOM_ENTITY, "preset_mode": PRESET_SCHEDULE})
    assert _sent_to(mock_connection, BEDROOM_V2, before) == ["stopOverride"]


async def test_v2_schedule_preset_without_override_switches_to_automatic(hass, mock_connection, mock_entry) -> None:
    """Not overridden: ``setOperatingMode/0`` (Structure File v16: 0 =
    "Automatic, heating and cooling allowed") is the documented way back to
    the schedule from a manual operating mode or off; upstream 0.10.2
    (``cf51951``) and the fork agree on this case — kept deliberately, see
    ``schedule_preset_commands``."""
    await _setup(hass, mock_entry)
    mock_connection.feed(BEDROOM_OPERATING_MODE, 1)
    mock_connection.feed(BEDROOM_OVERRIDE_REASON, 0)
    await hass.async_block_till_done()
    assert hass.states.get(BEDROOM_ENTITY).attributes["is_overridden"] is False

    before = len(mock_connection.sent)
    await _call(hass, "set_preset_mode", {"entity_id": BEDROOM_ENTITY, "preset_mode": PRESET_SCHEDULE})
    assert _sent_to(mock_connection, BEDROOM_V2, before) == ["setOperatingMode/0"]


async def test_override_reason_codes_beyond_eight(hass, mock_connection, mock_entry) -> None:
    """9-20 (upstream PR #530) are named; 14 is "low request", not "fixed"."""
    await _setup(hass, mock_entry)
    for code, slug in ((2, "window_open"), (14, "low_request"), (17, "load_shedding"), (21, "unknown")):
        mock_connection.feed(BEDROOM_OVERRIDE_REASON, code)
        await hass.async_block_till_done()
        state = hass.states.get(OVERRIDE_REASON_ENTITY)
        assert state.state == slug, code
    assert "fixed" not in state.attributes["options"]
