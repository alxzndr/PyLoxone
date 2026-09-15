"""Entity service commands must reach the Miniserver, whatever thread they start on.

Regression for the bug that broke every cover, climate, fan and button
command on the live install: those platforms implemented the plain ``def``
service handlers (``stop_cover``, ``set_temperature``, ``press`` ...), which
Home Assistant runs in its executor. ``_send`` then created the outbound
task with ``async_create_background_task`` straight from that worker thread,
which the loop rejects ("loop ... is not the running loop"), so the service
failed with "Failed to perform action" and the command never left HA.

The handlers are coroutines now, so HA runs them inline on the loop. These
tests stay as the end-to-end guard: they go through the real service
registry and assert the command reached the (faked) Miniserver connection.
``_send``'s loop hop is kept as defence for any future sync caller, and
:func:`test_send_from_the_executor_still_reaches_the_miniserver` pins it
directly. Expected uuids are copied from tests/fixtures/LoxAPP3.json.
"""

from __future__ import annotations

LIVING_JALOUSIE = "63746c3a-0172-9766-ffff-e67204a616c6000114"  # cover.parlour_living_jalousie (Jalousie)
GARDEN_GATE = "63746c3a-017f-9726-ffff-56e204761746000127"  # cover.garden_garden_gate (Gate)
INTRODUCE_BUTTON = "63746c3a-0161-9747-ffff-f64756365205000097"  # button.hall_introduce_pushbutton
BEDROOM_CONTROLLER = "63746c3a-0126-9647-ffff-f6f6d20436f6000038"  # climate.bedroom_bedroom_controller (V2)
BEDROOM_OPERATING_MODE = "36333734-0134-9336-ffff-d303132362d3000052"  # its operatingMode state stream


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


async def test_room_controller_v2_set_temperature_through_the_service_registry(
    hass, mock_connection, mock_entry
) -> None:
    """The V2 climate handler is a coroutine, so HA runs it inline on the loop
    and its closing ``async_write_ha_state`` is legal. Only a real service call
    proves that: it is the dispatch path that used to hand the handler to a
    worker thread, where both the send and the state write blew up."""
    await _setup(hass, mock_entry)

    # An OFF controller advertises only TURN_ON; operating mode 4 (MANUAL_HEAT)
    # is what makes it accept a single ``temperature`` target.
    mock_connection.feed(BEDROOM_OPERATING_MODE, 4)
    await hass.async_block_till_done()

    sent_before = len(mock_connection.sent)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.bedroom_bedroom_controller", "temperature": 21},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert mock_connection.sent[sent_before:] == [
        {"uuid": BEDROOM_CONTROLLER, "value": "setManualTemperature/21.0", "code": None}
    ]


async def test_send_from_the_executor_still_reaches_the_miniserver(hass, mock_connection, mock_entry) -> None:
    """``LoxoneEntity._send``'s loop hop is kept as defence for any future sync
    caller, even though no service handler is one any more (v0.10.5)."""
    await _setup(hass, mock_entry)
    entity = hass.data["entity_components"]["climate"].get_entity("climate.bedroom_bedroom_controller")
    assert entity is not None

    sent_before = len(mock_connection.sent)
    await hass.async_add_executor_job(entity._send, "setManualTemperature/19.0")
    await hass.async_block_till_done()

    assert mock_connection.sent[sent_before:] == [
        {"uuid": BEDROOM_CONTROLLER, "value": "setManualTemperature/19.0", "code": None}
    ]
