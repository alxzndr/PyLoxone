"""Intercom blocks (``Intercom`` / ``IntercomV2``): the outputs are pushbuttons.

Structure file v16 (pp. 90-93): an ``Intercom`` has the states ``bell``
(0/1), ``lastBellEvents``, ``lastBellTimestamp``, ``version``; an
``IntercomV2`` has ``bell``, ``address``, ``answers``, ``muted``,
``deviceState``.  Both take ``answer`` (deactivates the bell); V2 also
takes ``mute/{0/1}``.  Their sub-controls are "0-3" / "0-N" *outputs of
type Pushbutton*: state ``active``, commands ``pulse`` / ``on`` / ``off``.

Up to 1.0.0 the outputs were switches whose turn-on sent ``on`` -- that held
a door strike until a turn-off.  They are buttons now (a press sends
``pulse``), and the obsolete switch registry entries are removed on setup
with a repair issue.  WP-6.3 (#466) put ``IntercomV2`` next to ``Intercom``.

The fixture models both blocks to the spec: "Main Intercom" (``Intercom``,
output "Door Opener") and "Entrance Intercom" (``IntercomV2``, output
"Door Lock"), both in room Hall.  Expected values are hand-derived from
``tests/fixtures/LoxAPP3.json`` and written as literals.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import Mock, patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.loxone import DOMAIN
from custom_components.loxone.intercom import intercom_output_kwargs
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hand-transcribed from tests/fixtures/LoxAPP3.json ("Entrance Intercom").
V2_ACTION_UUID = "63746c3a-9c7d-96f2-ffff-e301696e743276000024"
V2_SUB_UUID = "7375623a-9c9d-9696-ffff-c74c6f6c6b3276000026"
V2_SUB_ACTIVE_UUID = "37333735-9cad-9336-ffff-d303136b2c42d300027"
V2_CONTROL_NAME = "Entrance Intercom"
V2_SUB_NAME = "Door Lock"
# ... and "Main Intercom" (legacy ``Intercom``).
V1_ACTION_UUID = "63746c3a-0165-9696-ffff-0496e7465726000101"
V1_SUB_UUID = "7375623a-0167-9696-ffff-0496e7465726000103"
# The fixture's one plain "Switch" (not an intercom output).
PLAIN_SWITCH_UUID = "63746c3a-0157-9766-ffff-e6720526f6f6000087"

# Hand-derived entity ids: area (Hall) + device name + short entity name.
V2_DOOR_BUTTON = "button.hall_entrance_intercom_door_lock"
V1_DOOR_BUTTON = "button.hall_main_intercom_door_opener"

# Hand-transcribed from the fixture's rooms/cats tables (uuid -> name).
HALL_ROOM_UUID = "726f6f6d-0105-9616-ffff-c726f6f6d3a4000005"  # "Hall"
SECURITY_CAT_UUID = "6361743a-0164-9637-ffff-269747963617000100"  # "Security"

TRANSLATION_LANGUAGES = ("en", "de", "cs")


def _fresh_loxapp3() -> dict:
    return json.loads((REPO_ROOT / "tests/fixtures/LoxAPP3.json").read_text())


def _fake_open_with(config):
    async def _fake_open(self, session=None, **_kwargs):
        self.structure_file = config
        self.miniserver_version = config.get("softwareVersion")
        self.connected = True
        self.connection = Mock()
        self.connection.protocol.state.name = "OPEN"
        self._session_key = b"\x00" * 32
        return self

    return patch.object(LoxoneConnection, "open", _fake_open)


def _v2(sub_states) -> dict:
    """A hand-built IntercomV2 control (test-only; not read from the fixture)."""
    return {
        "name": "Test Intercom",
        "type": "IntercomV2",
        "uuidAction": "ic-v2-action",
        "room": "Hall",
        "cat": "Security",
        "details": {},
        "states": {"bell": "ic-v2-bell"},
        "subControls": {
            "ic-v2-sub": {
                "name": "Test Sub",
                "type": "Pushbutton",
                "uuidAction": "ic-v2-sub",
                "room": HALL_ROOM_UUID,
                "cat": SECURITY_CAT_UUID,
                "details": {},
                "states": sub_states,
            }
        },
    }


async def _setup(hass, entry, config=None):
    entry.add_to_hass(hass)
    if config is None:
        assert await hass.config_entries.async_setup(entry.entry_id)
    else:
        with _fake_open_with(config):
            assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _unload(hass, entry):
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _sent_to(mock_connection, uuid):
    return [msg["value"] for msg in mock_connection.sent if msg["uuid"] == uuid]


# --------------------------------------------------------------------------- #
# Pure helper: intercom_output_kwargs
# --------------------------------------------------------------------------- #
def test_output_kwargs_carry_parent_device():
    kwargs_list = intercom_output_kwargs(_v2({"active": "ic-v2-sub-active"}), None, _fresh_loxapp3())
    assert len(kwargs_list) == 1
    kwargs = kwargs_list[0]
    # The output keeps its *own* name (the device carries the intercom's),
    # the parent's uuid wires in as ``parent_id``.
    assert kwargs["name"] == "Test Sub"
    assert kwargs["uuidAction"] == "ic-v2-sub"
    assert kwargs["parent_id"] == "ic-v2-action"
    # Room and category uuids resolve to names via the structure file.
    assert kwargs["room"] == "Hall"
    assert kwargs["cat"] == "Security"
    info = kwargs["device_info"]
    assert info["identifiers"] == {(DOMAIN, "ic-v2-action")}
    assert info["name"] == "Test Intercom"
    assert info["suggested_area"] == "Hall"
    # The device model is the *block* type that carried the output.
    assert info["model"] == "IntercomV2"


def test_legacy_intercom_has_legacy_model():
    control = _v2({"active": "a"})
    control["type"] = "Intercom"
    assert intercom_output_kwargs(control, None)[0]["device_info"]["model"] == "Intercom"


def test_output_without_active_state_is_kept():
    """A button needs no state: an output without ``active`` is still an
    output (it just has no press echo)."""
    kwargs_list = intercom_output_kwargs(_v2({"on": "only-on"}), None)
    assert [k["uuidAction"] for k in kwargs_list] == ["ic-v2-sub"]


def test_output_with_missing_or_malformed_states_gets_empty_states():
    control = _v2({})
    del control["subControls"]["ic-v2-sub"]["states"]
    assert intercom_output_kwargs(control, None)[0]["states"] == {}
    assert intercom_output_kwargs(_v2(None), None)[0]["states"] == {}
    assert intercom_output_kwargs(_v2("garbage"), None)[0]["states"] == {}


def test_control_without_subcontrols_yields_none():
    for shape in ({}, {"subControls": None}, {"subControls": []}):
        control = _v2({"active": "a"})
        if "subControls" in shape:
            control["subControls"] = shape["subControls"]
        else:
            del control["subControls"]
        assert intercom_output_kwargs(control, None) == []


def test_control_without_uuid_action_yields_none():
    control = _v2({"active": "a"})
    control["uuidAction"] = ""
    assert intercom_output_kwargs(control, None) == []
    control["uuidAction"] = None
    assert intercom_output_kwargs(control, None) == []
    assert intercom_output_kwargs(None, None) == []


def test_helper_does_not_mutate_the_structure_file():
    control = _v2({"active": "a"})
    intercom_output_kwargs(control, None)
    sub = control["subControls"]["ic-v2-sub"]
    assert "parent_id" not in sub
    assert "device_info" not in sub


def test_only_outputs_with_a_uuid_survive():
    control = _v2({"active": "a"})
    control["subControls"]["no-uuid"] = {"name": "No Uuid", "type": "Pushbutton", "states": {}}
    control["subControls"]["not-a-dict"] = "junk"
    kwargs_list = intercom_output_kwargs(control, None)
    assert [k["uuidAction"] for k in kwargs_list] == ["ic-v2-sub"]
    # CORE-20: every output gets its own device payload object.
    control["subControls"]["second"] = {"name": "Light", "uuidAction": "ic-v2-sub2", "states": {}}
    first, second = intercom_output_kwargs(control, None)
    assert first["device_info"] == second["device_info"]
    assert first["device_info"] is not second["device_info"]


# --------------------------------------------------------------------------- #
# Setup from the fixture: the outputs are buttons on the intercom's device
# --------------------------------------------------------------------------- #
async def test_setup_creates_output_buttons_not_switches(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup(hass, mock_entry)
    registry = er.async_get(hass)

    assert registry.async_get_entity_id("button", DOMAIN, V2_SUB_UUID) == V2_DOOR_BUTTON
    assert registry.async_get_entity_id("button", DOMAIN, V1_SUB_UUID) == V1_DOOR_BUTTON
    # No switch for any output (the 1.0.0 shape is gone).
    assert registry.async_get_entity_id("switch", DOMAIN, V2_SUB_UUID) is None
    assert registry.async_get_entity_id("switch", DOMAIN, V1_SUB_UUID) is None
    assert not any("door_lock" in i or "door_opener" in i for i in hass.states.async_entity_ids("switch"))

    devices = dr.async_get(hass)
    device = devices.async_get(registry.async_get(V2_DOOR_BUTTON).device_id)
    assert device.name == V2_CONTROL_NAME
    assert device.model == "IntercomV2"
    assert registry.async_get(V2_DOOR_BUTTON).original_name == V2_SUB_NAME
    assert devices.async_get(registry.async_get(V1_DOOR_BUTTON).device_id).model == "Intercom"

    await _unload(hass, mock_entry)


async def test_output_press_sends_pulse_never_on(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup(hass, mock_entry)

    await hass.services.async_call("button", "press", {"entity_id": V2_DOOR_BUTTON}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {"entity_id": V1_DOOR_BUTTON}, blocking=True)
    await hass.async_block_till_done()

    # One tap each: ``pulse`` to the output's own uuid, nothing that holds it.
    assert _sent_to(mock_connection, V2_SUB_UUID) == ["pulse"]
    assert _sent_to(mock_connection, V1_SUB_UUID) == ["pulse"]

    await _unload(hass, mock_entry)


async def test_output_press_echo_is_recorded(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup(hass, mock_entry)
    assert hass.states.get(V2_DOOR_BUTTON).attributes["last_pressed"] is None

    mock_connection.feed(V2_SUB_ACTIVE_UUID, 1.0)
    await hass.async_block_till_done()
    attrs = hass.states.get(V2_DOOR_BUTTON).attributes
    assert attrs["last_pressed"] is not None
    assert attrs["state_uuid"] == V2_SUB_ACTIVE_UUID

    await _unload(hass, mock_entry)


async def test_output_without_active_state_is_a_button(hass, mock_connection, mock_entry, enable_custom_integrations):
    config = _fresh_loxapp3()
    config["controls"][V1_ACTION_UUID]["subControls"][V1_SUB_UUID]["states"] = {}
    await _setup(hass, mock_entry, config)

    assert hass.states.get(V1_DOOR_BUTTON) is not None
    await hass.services.async_call("button", "press", {"entity_id": V1_DOOR_BUTTON}, blocking=True)
    await hass.async_block_till_done()
    assert _sent_to(mock_connection, V1_SUB_UUID) == ["pulse"]

    await _unload(hass, mock_entry)


async def test_intercom_without_subcontrols_is_inert(hass, mock_connection, mock_entry, enable_custom_integrations):
    config = _fresh_loxapp3()
    del config["controls"][V2_ACTION_UUID]["subControls"]
    await _setup(hass, mock_entry, config)

    assert hass.states.get(V2_DOOR_BUTTON) is None
    # The other intercom (and the platform) still came up.
    assert hass.states.get(V1_DOOR_BUTTON) is not None

    await _unload(hass, mock_entry)


# --------------------------------------------------------------------------- #
# Migration: the 1.0.0 switch entries of the outputs are removed
# --------------------------------------------------------------------------- #
async def test_obsolete_output_switches_are_removed_with_a_repair_issue(
    hass, mock_connection, mock_entry, enable_custom_integrations, caplog
):
    mock_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    # The registry of a 1.0.0 install: the V2 output was a switch.
    old = registry.async_get_or_create(
        "switch",
        DOMAIN,
        V2_SUB_UUID,
        config_entry=mock_entry,
        suggested_object_id="hall_entrance_intercom_door_lock",
    )
    assert old.entity_id == "switch.hall_entrance_intercom_door_lock"

    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get(old.entity_id) is None
    assert registry.async_get_entity_id("switch", DOMAIN, V2_SUB_UUID) is None
    assert registry.async_get_entity_id("button", DOMAIN, V2_SUB_UUID) == V2_DOOR_BUTTON
    assert hass.states.get(old.entity_id) is None
    # A plain switch is not an intercom output and stays.
    assert registry.async_get_entity_id("switch", DOMAIN, PLAIN_SWITCH_UUID) is not None

    warnings = [
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "intercom output" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "switch.hall_entrance_intercom_door_lock" in warnings[0]
    assert V2_DOOR_BUTTON in warnings[0]

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"intercom_switches_replaced_{mock_entry.entry_id}")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.is_persistent is True
    assert issue.translation_key == "intercom_switches_replaced"
    assert issue.translation_placeholders == {
        "entities": f"switch.hall_entrance_intercom_door_lock -> {V2_DOOR_BUTTON}",
    }

    # A reload has nothing left to migrate: no new warning, the issue stays.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_reload(mock_entry.entry_id)
        await hass.async_block_till_done()
    assert not [r for r in caplog.records if "intercom output" in r.getMessage()]
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"intercom_switches_replaced_{mock_entry.entry_id}") is not None

    await _unload(hass, mock_entry)


async def test_clean_install_creates_no_issue(hass, mock_connection, mock_entry, enable_custom_integrations):
    await _setup(hass, mock_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"intercom_switches_replaced_{mock_entry.entry_id}") is None
    await _unload(hass, mock_entry)


def test_issue_translations_present():
    for lang in TRANSLATION_LANGUAGES:
        data = json.loads((REPO_ROOT / f"custom_components/loxone/translations/{lang}.json").read_text("utf-8"))
        issue = data["issues"]["intercom_switches_replaced"]
        assert issue["title"], lang
        assert "{entities}" in issue["description"], lang
