"""WP-7.3 entity-state correctness (upstream 6911e14, 6441290, 127f42a).

Three fixes, exercised through the real ``hass`` with the real service
handlers (fork rule: no ``__new__``/``SimpleNamespace`` harness):

* the writable ``text`` entity of a ``TextInput`` reads its value from the
  ``text`` *state* (PS-03; it listened on ``uuidAction``, a stream no
  Miniserver publishes on), and sends commands URI-encoded;
* a bidirectional ``Meter``'s ``Total Neg`` register is formatted with
  ``totalFormatNeg``, falling back to ``totalFormat`` (upstream 6441290);
* digital binary sensors read ``unknown`` until their first value and the
  strings ``"0"`` / ``"0.0"`` are off, not on (do-not-copy #5).

The bidirectional meter, the fallback meter and the stateless TextInput are
added by the module-level ``loxapp3`` override below (synthetic uuids; the
shared fixture file is owned by another work package).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from homeassistant.components.text import ATTR_MAX
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


# Hand-transcribed from tests/fixtures/LoxAPP3.json.
TEXT_INPUT_ACTION = "63746c3a-0116-9617-ffff-57320496e707000022"
TEXT_INPUT_TEXT_STATE = "36333734-0117-9336-ffff-d303131362d3000023"
MAIN_DOOR_ACTION = "63746c3a-0301-96d7-ffff-572617475726000098"
MAIN_DOOR_ACTIVE = "36333734-0302-9336-ffff-d303131322d3000099"
SMOKE_ACTION = "63746c3a-0152-9746-ffff-26f6f6d20536000082"
SMOKE_LEVEL = "36333734-0154-9336-ffff-d303135322d3000084"

# Synthetic controls added by the override below (invented uuids).
FALLBACK_TEXT_ACTION = "0f86a2b1-7300-0000-ffff-54455854000f"
BIDI_METER_ACTION = "0f86a2b1-7300-0000-ffff-4d4554450001"
BIDI_ACTUAL = "0f86a2b1-7300-0000-ffff-4d4554450101"
BIDI_TOTAL = "0f86a2b1-7300-0000-ffff-4d4554450102"
BIDI_TOTAL_NEG = "0f86a2b1-7300-0000-ffff-4d4554450103"
UNI_METER_ACTION = "0f86a2b1-7300-0000-ffff-4d4554450002"
UNI_ACTUAL = "0f86a2b1-7300-0000-ffff-4d4554450201"
UNI_TOTAL = "0f86a2b1-7300-0000-ffff-4d4554450202"
UNI_TOTAL_NEG = "0f86a2b1-7300-0000-ffff-4d4554450203"


@pytest.fixture
def loxapp3(loxapp3: dict) -> dict:
    """The session fixture plus the WP-7.3 blocks (module override).

    The conftest fixture is session-scoped — deep-copy, never mutate.
    Shapes follow structure file v16 (Meter p. 112: ``type:
    "bidirectional"``, ``totalFormatNeg`` "unavailable if unidirectional").
    """
    data = copy.deepcopy(loxapp3)
    data["controls"][FALLBACK_TEXT_ACTION] = {
        "name": "WP73 Legacy Text",
        "type": "TextInput",
        "uuidAction": FALLBACK_TEXT_ACTION,
        "room": "Garage",
        "cat": "Comfort",
        "details": {},
        # No "states" at all: a truncated structure file without the
        # "text" state is the *only* case that keeps uuidAction fallback.
    }
    data["controls"][BIDI_METER_ACTION] = {
        "name": "WP73 Bidirectional Meter",
        "type": "Meter",
        "uuidAction": BIDI_METER_ACTION,
        "room": "Garage",
        "cat": "Energy",
        "details": {
            "type": "bidirectional",
            "actualFormat": "%.1fW",
            "totalFormat": "%.2fkWh",
            "totalFormatNeg": "%.3fkWh",
        },
        "states": {"actual": BIDI_ACTUAL, "total": BIDI_TOTAL, "totalNeg": BIDI_TOTAL_NEG},
    }
    data["controls"][UNI_METER_ACTION] = {
        "name": "WP73 Total Meter",
        "type": "Meter",
        "uuidAction": UNI_METER_ACTION,
        "room": "Garage",
        "cat": "Energy",
        # A "totalNeg" register without the "totalFormatNeg" detail
        # (pre-13.01 structure) must fall back to "totalFormat".
        "details": {"actualFormat": "%.1fW", "totalFormat": "%.2fkWh"},
        "states": {"actual": UNI_ACTUAL, "total": UNI_TOTAL, "totalNeg": UNI_TOTAL_NEG},
    }
    return data


async def _setup(hass: HomeAssistant, mock_connection, mock_entry) -> er.EntityRegistry:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    return er.async_get(hass)


def _entity_id(registry: er.EntityRegistry, domain: str, unique_id: str) -> str:
    entity_id = registry.async_get_entity_id(domain, "loxone", unique_id)
    assert entity_id is not None, f"no {domain} entity for {unique_id}"
    return entity_id


# --------------------------------------------------------------------------- #
# TextInput — the value stream, the fallback, the command encoding
# --------------------------------------------------------------------------- #


async def test_text_entity_unknown_until_the_text_state_arrives(hass, mock_connection, mock_entry) -> None:
    """PS-03 / upstream 6911e14: the value arrives as the "text" state,
    which has its own uuid — the entity never showed anything because it
    listened on uuidAction instead."""
    registry = await _setup(hass, mock_connection, mock_entry)
    entity_id = _entity_id(registry, "text", TEXT_INPUT_ACTION)

    # After setup, before any value: unknown, not "".
    assert hass.states.get(entity_id).state == STATE_UNKNOWN
    # The maxLength detail of the block (20) becomes the entity's max.
    assert hass.states.get(entity_id).attributes[ATTR_MAX] == 20

    mock_connection.feed(TEXT_INPUT_TEXT_STATE, "Hallo Welt")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "Hallo Welt"

    # A value on the control's uuidAction is not the value stream any more.
    mock_connection.feed(TEXT_INPUT_ACTION, "echo must not land")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "Hallo Welt"


async def test_text_without_a_text_state_falls_back_to_uuid_action(hass, mock_connection, mock_entry) -> None:
    """Only a structure file without the "text" state keeps the fallback."""
    registry = await _setup(hass, mock_connection, mock_entry)
    entity_id = _entity_id(registry, "text", FALLBACK_TEXT_ACTION)
    assert hass.states.get(entity_id).state == STATE_UNKNOWN

    mock_connection.feed(FALLBACK_TEXT_ACTION, "legacy value")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "legacy value"


async def test_set_value_sends_the_uri_encoded_text(hass, mock_connection, mock_entry) -> None:
    """The command is the text itself on the control
    (jdev/sps/io/<uuid>/<text>) — characters that break that path must not
    go out raw."""
    registry = await _setup(hass, mock_connection, mock_entry)
    entity_id = _entity_id(registry, "text", TEXT_INPUT_ACTION)

    await hass.services.async_call("text", "set_value", {"entity_id": entity_id, "value": "a/b c"}, blocking=True)
    sent = [s for s in mock_connection.sent if s["uuid"] == TEXT_INPUT_ACTION]
    assert sent == [{"uuid": TEXT_INPUT_ACTION, "value": "a%2Fb%20c", "code": None}]


# --------------------------------------------------------------------------- #
# Meter — totalFormatNeg
# --------------------------------------------------------------------------- #


def _display_precision(registry: er.EntityRegistry, unique_id: str) -> Any:
    entity_id = _entity_id(registry, "sensor", unique_id)
    entry = registry.async_get(entity_id)
    return (entry.options.get("sensor") or {}).get("suggested_display_precision")


async def test_total_neg_uses_total_format_neg(hass, mock_connection, mock_entry) -> None:
    """Upstream 6441290, structure file v16 p. 112 ("totalFormatNeg",
    available since 13.01): the negative total has its own format.  The
    fork derives the display precision from the format, so the registry
    entry of "Total Neg" must show the %.3f precision, "Total" the %.2f."""
    registry = await _setup(hass, mock_connection, mock_entry)

    neg = registry.async_get(_entity_id(registry, "sensor", BIDI_TOTAL_NEG))
    assert neg.original_name == "Total Neg"
    assert _display_precision(registry, BIDI_TOTAL_NEG) == 3
    assert _display_precision(registry, BIDI_TOTAL) == 2


async def test_total_neg_falls_back_to_total_format(hass, mock_connection, mock_entry) -> None:
    """ "totalFormatNeg" is unavailable for unidirectional meters (and
    missing in pre-13.01 structures): fall back to "totalFormat"."""
    registry = await _setup(hass, mock_connection, mock_entry)
    assert _display_precision(registry, UNI_TOTAL_NEG) == 2
