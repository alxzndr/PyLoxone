"""``LoxoneEntity._send`` must work from HA's executor thread.

Regression for the bug that broke every cover, climate, fan and button
command on the live install: those platforms implement the plain ``def``
service handlers (``stop_cover``, ``set_temperature``, ``press`` ...), which
Home Assistant runs in its executor. ``_send`` then created the outbound
task with ``async_create_background_task`` straight from that worker thread,
which the loop rejects ("loop ... is not the running loop"), so the service
failed with "Failed to perform action" and the command never left HA.

These tests go through the real service registry, so the handler really is
dispatched to a worker thread, and assert the command reached the (faked)
Miniserver connection. Expected uuids are copied from tests/fixtures/LoxAPP3.json.
"""

from __future__ import annotations

LIVING_JALOUSIE = "63746c3a-0172-9766-ffff-e67204a616c6000114"  # cover.parlour_living_jalousie (Jalousie)
GARDEN_GATE = "63746c3a-017f-9726-ffff-56e204761746000127"  # cover.garden_garden_gate (Gate)
INTRODUCE_BUTTON = "63746c3a-0161-9747-ffff-f64756365205000097"  # button.hall_introduce_pushbutton


async def _setup(hass, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_jalousie_stop_from_a_sync_handler_reaches_the_miniserver(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)

    await hass.services.async_call("cover", "stop_cover", {"entity_id": "cover.parlour_living_jalousie"}, blocking=True)
    await hass.async_block_till_done()

    assert {"uuid": LIVING_JALOUSIE, "value": "stop", "code": None} in mock_connection.sent


async def test_gate_open_and_close_from_sync_handlers(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)

    await hass.services.async_call("cover", "open_cover", {"entity_id": "cover.garden_garden_gate"}, blocking=True)
    await hass.services.async_call("cover", "close_cover", {"entity_id": "cover.garden_garden_gate"}, blocking=True)
    await hass.async_block_till_done()

    values = [s["value"] for s in mock_connection.sent if s["uuid"] == GARDEN_GATE]
    assert values == ["open", "close"]


async def test_button_press_from_a_sync_handler(hass, mock_connection, mock_entry) -> None:
    await _setup(hass, mock_entry)

    await hass.services.async_call("button", "press", {"entity_id": "button.hall_introduce_pushbutton"}, blocking=True)
    await hass.async_block_till_done()

    assert any(s["uuid"] == INTRODUCE_BUTTON for s in mock_connection.sent)


async def test_async_handler_still_sends_directly(hass, mock_connection, mock_entry) -> None:
    # The loop-thread path (async handlers such as the lights) must be unchanged.
    await _setup(hass, mock_entry)

    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": "cover.garden_garden_gate", "position": 50}, blocking=True
    )
    await hass.async_block_till_done()

    assert any(s["uuid"] == GARDEN_GATE for s in mock_connection.sent)


async def test_room_controller_v2_set_temperature_from_the_executor(hass, mock_connection, mock_entry) -> None:
    """The sync climate handler ends with a state write; from HA's executor
    that must be the thread-safe scheduler. ``async_write_ha_state`` there is
    an error for custom integrations (HA raises), which is what this pins."""
    import functools

    await _setup(hass, mock_entry)
    entity = hass.data["entity_components"]["climate"].get_entity("climate.bedroom_bedroom_controller")
    assert entity is not None

    # Exactly what HA's default async_set_temperature does with a plain def.
    await hass.async_add_executor_job(functools.partial(entity.set_temperature, temperature=21))
    await hass.async_block_till_done()
