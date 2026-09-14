"""Coverage-gap regression tests for ``custom_components/loxone/__init__.py``.

Four code paths that the rest of the suite never entered are pinned here,
all against the *real* ``hass`` harness (the observable effect is asserted in
the real area registry / on the real mocked connection, never on a MagicMock):

* ``sync_areas_with_loxone`` — the ``loxone.sync_areas`` service: create a
  missing HA area from an entity's Loxone ``room`` attribute, reuse an area
  that already exists, honour the ``create_areas`` flag (and its coercion of
  anything that is not a real bool to ``False``), never touch an entity that
  already has an area, an entity without a ``room`` attribute, or an entity
  belonging to another integration.
* ``handle_event_websocket_command`` / ``handle_secured_event_websocket_command``
  plus their shared ``_resolve_outbound_target`` / ``_loxone_coordinator_by_uuid``
  resolution — which entry receives the command, and every rejection.
* ``loxone_send`` — the ``loxone_send`` / ``loxone_send_secured`` bus events
  documented for user automations (CORE-27: only the entry whose structure
  file knows the uuid may execute them).
* the "neither 401 nor 503" setup-failure branch, which until now was only
  ever entered by accident during another test's teardown.

All expected values are hand-written literals: room names and control
``uuidAction``s are copied out of ``tests/fixtures/LoxAPP3.json`` by hand, and
the entity ids are resolved through the entity registry by those uuids (a
Loxone entity's registry ``unique_id`` is its control ``uuidAction``).
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.const import DOMAIN, SECUREDSENDDOMAIN, SENDDOMAIN
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection

# --------------------------------------------------------------------------- #
# Hand-copied out of tests/fixtures/LoxAPP3.json
# --------------------------------------------------------------------------- #
# ``rooms``: the seven room names the fixture defines.
FIXTURE_ROOMS = ("Parlour", "Office", "Kitchen", "Bedroom", "Hall", "Garden", "Bathroom")

# ``controls`` -> uuidAction / room of the controls used below.
JALOUSIE_UUID = "63746c3a-0172-9766-ffff-e67204a616c6000114"  # "Living Jalousie", room Parlour
WINDOW_UUID = "63746c3a-017b-9746-ffff-8656e2057696000123"  # "Kitchen Window", room Kitchen
SWITCH_UUID = "63746c3a-0157-9766-ffff-e6720526f6f6000087"  # "Living Room Light Switch", room Parlour
# "Dev Status" (InfoOnlyText) carries a room uuid that is not in ``rooms``,
# so its resolved room name is "" and it never gets a ``room`` attribute.
DEV_STATUS_UUID = "63746c3a-01c0-96d7-ffff-f696e726f756000190"

# A uuid no Miniserver in the fixture owns.
FOREIGN_UUID = "00000000-dead-beef-ffff-000000000000"


async def _setup_entry(hass, mock_entry) -> None:
    """Load the mock config entry through the real config-entry machinery."""
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.LOADED


def _entity_id(hass, domain: str, unique_id: str) -> str:
    """Resolve the entity id of the Loxone entity whose unique_id is ``unique_id``."""
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, f"no {domain} entity for {unique_id}"
    return entity_id


def _area_id_of(hass, entity_id: str) -> str | None:
    return er.async_get(hass).async_get(entity_id).area_id


def _register_foreign_entity(hass) -> str:
    """A registry entity of *another* integration that also reports a Loxone room."""
    entry = er.async_get(hass).async_get_or_create(
        "sensor", "demo", "foreign-unique-1", suggested_object_id="foreign_room_sensor"
    )
    hass.states.async_set(entry.entity_id, "on", {"room": "Kitchen"})
    return entry.entity_id


# =========================================================================== #
# sync_areas_with_loxone
# =========================================================================== #
async def test_sync_areas_creates_the_missing_area_and_assigns_entities(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """``create_areas: true`` creates the area named after the Loxone room and assigns the entity."""
    await _setup_entry(hass, mock_entry)
    area_registry = ar.async_get(hass)

    # Device registration (`suggested_area`) already created all seven rooms;
    # drop Parlour so the "area does not exist yet" branch is a real one.
    parlour = area_registry.async_get_area_by_name("Parlour")
    assert parlour is not None
    area_registry.async_delete(parlour.id)
    await hass.async_block_till_done()
    assert area_registry.async_get_area_by_name("Parlour") is None

    jalousie = _entity_id(hass, "cover", JALOUSIE_UUID)
    window = _entity_id(hass, "cover", WINDOW_UUID)
    dev_status = _entity_id(hass, "sensor", DEV_STATUS_UUID)
    foreign = _register_foreign_entity(hass)
    assert _area_id_of(hass, jalousie) is None
    assert _area_id_of(hass, window) is None

    await hass.services.async_call(DOMAIN, "sync_areas", {"create_areas": True}, blocking=True)
    await hass.async_block_till_done()

    # Created: an area literally named after the fixture's room.
    recreated = area_registry.async_get_area_by_name("Parlour")
    assert recreated is not None
    assert _area_id_of(hass, jalousie) == recreated.id
    # Reused: Kitchen still existed, so the entity joins it (no duplicate area).
    kitchen = area_registry.async_get_area_by_name("Kitchen")
    assert kitchen is not None
    assert _area_id_of(hass, window) == kitchen.id
    assert len([a for a in area_registry.async_list_areas() if a.name == "Kitchen"]) == 1
    # Untouched: no ``room`` attribute at all, and another integration's entity.
    assert _area_id_of(hass, dev_status) is None
    assert _area_id_of(hass, foreign) is None
    # Every room the fixture defines is an area again.
    assert {a.name for a in area_registry.async_list_areas()} >= set(FIXTURE_ROOMS)


@pytest.mark.parametrize(
    "service_data",
    [
        {},  # ATTR_AREA_CREATE absent -> DEFAULT ("") -> coerced to False
        {"create_areas": "yes"},  # a non-bool truthy value is coerced to False too
        {"create_areas": False},  # an explicit bool passes through unchanged
    ],
)
async def test_sync_areas_without_create_never_invents_an_area(
    hass, mock_connection, mock_entry, enable_custom_integrations, service_data
) -> None:
    """Without a real ``create_areas: true`` a missing area stays missing; existing ones are still used."""
    await _setup_entry(hass, mock_entry)
    area_registry = ar.async_get(hass)
    area_registry.async_delete(area_registry.async_get_area_by_name("Parlour").id)
    await hass.async_block_till_done()

    jalousie = _entity_id(hass, "cover", JALOUSIE_UUID)
    window = _entity_id(hass, "cover", WINDOW_UUID)

    await hass.services.async_call(DOMAIN, "sync_areas", service_data, blocking=True)
    await hass.async_block_till_done()

    assert area_registry.async_get_area_by_name("Parlour") is None
    assert _area_id_of(hass, jalousie) is None
    # The Kitchen area was never deleted, so that assignment still happens.
    assert _area_id_of(hass, window) == area_registry.async_get_area_by_name("Kitchen").id


async def test_sync_areas_keeps_an_entity_that_already_has_an_area(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """A user-assigned area wins: sync_areas only fills in entities whose area_id is None."""
    await _setup_entry(hass, mock_entry)
    area_registry = ar.async_get(hass)
    entity_registry = er.async_get(hass)

    studio = area_registry.async_create("Studio")
    jalousie = _entity_id(hass, "cover", JALOUSIE_UUID)
    entity_registry.async_update_entity(jalousie, area_id=studio.id)

    await hass.services.async_call(DOMAIN, "sync_areas", {"create_areas": True}, blocking=True)
    await hass.async_block_till_done()

    assert _area_id_of(hass, jalousie) == studio.id
    assert area_registry.async_get_area_by_name("Parlour").id != studio.id


# =========================================================================== #
# loxone.event_websocket_command / loxone.event_secured_websocket_command
# =========================================================================== #
async def test_event_websocket_command_service_sends_to_the_owning_entry(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """A raw-uuid service call reaches the entry whose structure file knows the uuid."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    await hass.services.async_call(
        DOMAIN, "event_websocket_command", {"uuid": SWITCH_UUID, "value": "On"}, blocking=True
    )
    await hass.async_block_till_done()

    assert mock_connection.sent == [{"uuid": SWITCH_UUID, "value": "On", "code": None}]


async def test_event_websocket_command_service_defaults_the_value(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """``value`` is optional and defaults to the empty DEFAULT string."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    await hass.services.async_call(DOMAIN, "event_websocket_command", {"uuid": SWITCH_UUID}, blocking=True)
    await hass.async_block_till_done()

    assert mock_connection.sent == [{"uuid": SWITCH_UUID, "value": "", "code": None}]


async def test_event_websocket_command_service_resolves_an_entity_target(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """``device:`` addresses the control by the entity's registry unique_id (its uuidAction)."""
    await _setup_entry(hass, mock_entry)
    jalousie = _entity_id(hass, "cover", JALOUSIE_UUID)
    mock_connection.sent.clear()

    await hass.services.async_call(
        DOMAIN, "event_websocket_command", {"device": jalousie, "value": "FullUp"}, blocking=True
    )
    await hass.async_block_till_done()

    assert mock_connection.sent == [{"uuid": JALOUSIE_UUID, "value": "FullUp", "code": None}]


async def test_secured_event_websocket_command_service_forwards_the_code(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """The secured variant forwards (uuid, value, code) to the secured send method."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    await hass.services.async_call(
        DOMAIN,
        "event_secured_websocket_command",
        {"uuid": SWITCH_UUID, "value": "On", "code": "1234"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert mock_connection.sent == [{"uuid": SWITCH_UUID, "value": "On", "code": "1234", "secured": True}]


async def test_secured_event_websocket_command_service_defaults_value_and_code(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """Both optional fields default to the empty DEFAULT string."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    await hass.services.async_call(DOMAIN, "event_secured_websocket_command", {"uuid": SWITCH_UUID}, blocking=True)
    await hass.async_block_till_done()

    assert mock_connection.sent == [{"uuid": SWITCH_UUID, "value": "", "code": "", "secured": True}]


@pytest.mark.parametrize("service", ["event_websocket_command", "event_secured_websocket_command"])
async def test_command_services_reject_a_uuid_no_entry_owns(
    hass, mock_connection, mock_entry, enable_custom_integrations, service
) -> None:
    """CORE-27: a uuid outside every loaded structure file is refused, and nothing is sent."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    with pytest.raises(ServiceValidationError, match=FOREIGN_UUID):
        await hass.services.async_call(DOMAIN, service, {"uuid": FOREIGN_UUID}, blocking=True)
    await hass.async_block_till_done()

    assert mock_connection.sent == []


@pytest.mark.parametrize("service", ["event_websocket_command", "event_secured_websocket_command"])
async def test_command_services_require_exactly_one_target(
    hass, mock_connection, mock_entry, enable_custom_integrations, service
) -> None:
    """CORE-11: neither ``uuid`` nor ``device`` is a validation error, not a send of ""."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    with pytest.raises(ServiceValidationError, match="Exactly one"):
        await hass.services.async_call(DOMAIN, service, {"value": "On"}, blocking=True)
    await hass.async_block_till_done()

    assert mock_connection.sent == []


async def test_command_service_rejects_an_unknown_entity(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """An entity id that is not in the registry is refused (the old code dereferenced None)."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    with pytest.raises(ServiceValidationError, match="Unknown entity"):
        await hass.services.async_call(
            DOMAIN, "event_websocket_command", {"device": "sensor.nothing_here"}, blocking=True
        )
    assert mock_connection.sent == []


async def test_command_service_rejects_an_entity_of_another_integration(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """A registry entity owned by another platform is refused."""
    await _setup_entry(hass, mock_entry)
    foreign = _register_foreign_entity(hass)
    mock_connection.sent.clear()

    with pytest.raises(ServiceValidationError, match="does not belong to the loxone integration"):
        await hass.services.async_call(DOMAIN, "event_websocket_command", {"device": foreign}, blocking=True)
    assert mock_connection.sent == []


@pytest.mark.parametrize("service", ["event_websocket_command", "event_secured_websocket_command"])
async def test_command_services_refuse_while_the_connection_is_gone(
    hass, mock_connection, mock_entry, enable_custom_integrations, service
) -> None:
    """With ``coordinator.api`` cleared the call is a validation error, not an AttributeError."""
    await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    api = coordinator.api
    coordinator.api = None
    mock_connection.sent.clear()
    try:
        with pytest.raises(ServiceValidationError, match="not ready"):
            await hass.services.async_call(DOMAIN, service, {"uuid": SWITCH_UUID}, blocking=True)
    finally:
        coordinator.api = api
    assert mock_connection.sent == []


# =========================================================================== #
# the loxone_send / loxone_send_secured bus events
# =========================================================================== #
async def test_send_bus_event_forwards_to_the_owning_entry(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """The documented ``loxone_send`` automation event reaches the Miniserver that owns the uuid."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    hass.bus.async_fire(SENDDOMAIN, {"uuid": SWITCH_UUID, "value": "On"})
    await hass.async_block_till_done()

    assert mock_connection.sent == [{"uuid": SWITCH_UUID, "value": "On", "code": None}]


async def test_secured_send_bus_event_forwards_the_code(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """``loxone_send_secured`` routes through the secured send method with the code."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    hass.bus.async_fire(SECUREDSENDDOMAIN, {"uuid": SWITCH_UUID, "value": "On", "code": "9876"})
    await hass.async_block_till_done()

    assert mock_connection.sent == [{"uuid": SWITCH_UUID, "value": "On", "code": "9876", "secured": True}]


async def test_send_bus_events_coerce_none_value_and_code_to_the_default(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """An explicit ``None`` value/code becomes the empty DEFAULT string, never ``None``."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    hass.bus.async_fire(SENDDOMAIN, {"uuid": SWITCH_UUID, "value": None})
    hass.bus.async_fire(SECUREDSENDDOMAIN, {"uuid": SWITCH_UUID, "value": None, "code": None})
    await hass.async_block_till_done()

    assert mock_connection.sent == [
        {"uuid": SWITCH_UUID, "value": "", "code": None},
        {"uuid": SWITCH_UUID, "value": "", "code": "", "secured": True},
    ]


@pytest.mark.parametrize("event_type", [SENDDOMAIN, SECUREDSENDDOMAIN])
async def test_send_bus_events_ignore_a_uuid_this_miniserver_does_not_know(
    hass, mock_connection, mock_entry, enable_custom_integrations, event_type
) -> None:
    """CORE-27: before the fix every entry executed every fired command."""
    await _setup_entry(hass, mock_entry)
    mock_connection.sent.clear()

    hass.bus.async_fire(event_type, {"uuid": FOREIGN_UUID, "value": "On"})
    hass.bus.async_fire(event_type, {"uuid": None, "value": "On"})
    hass.bus.async_fire(event_type, {"uuid": 42, "value": "On"})
    hass.bus.async_fire(event_type, {"value": "On"})
    await hass.async_block_till_done()

    assert mock_connection.sent == []


async def test_send_bus_event_is_dropped_while_the_connection_is_gone(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """No live api: the listener logs and returns instead of raising into the bus."""
    await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    api = coordinator.api
    coordinator.api = None
    mock_connection.sent.clear()
    try:
        hass.bus.async_fire(SENDDOMAIN, {"uuid": SWITCH_UUID, "value": "On"})
        await hass.async_block_till_done()
    finally:
        coordinator.api = api

    assert mock_connection.sent == []


# =========================================================================== #
# setup failure classification
# =========================================================================== #
async def test_a_plain_transport_failure_on_open_is_retried_and_logged(
    hass, mock_connection, mock_entry, enable_custom_integrations, caplog
) -> None:
    """Neither 401 nor 503: the generic branch warns with the cause and still lands in setup_retry.

    Before this test the branch was only ever entered incidentally, during another
    test's teardown.
    """

    async def _failing_open(self, session=None):
        raise OSError("no route to host")

    mock_entry.add_to_hass(hass)
    with caplog.at_level(logging.WARNING), patch.object(LoxoneConnection, "open", new=_failing_open):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    assert any(
        "Could not connect to Loxone Miniserver at loxberry.local" in record.message
        and "no route to host" in record.message
        for record in caplog.records
        if record.levelno == logging.WARNING
    ), [r.message for r in caplog.records if r.levelno == logging.WARNING]
