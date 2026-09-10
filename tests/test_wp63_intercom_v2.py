"""WP-6.3 regression tests: IntercomV2 support on the switch platform (JoDehli/PyLoxone#466).

Firmware 16.2+ reports the same intercom block as ``IntercomV2``.
Before WP-6.3 the switch platform only matched ``Intercom`` ("IntercomV2"
was not in its match list), so users with a V2 intercom got nothing
from the block.  Adding the V2 suffix to the matched type worked for
the reporter, showing the sub-controls are handled the same way; WP-6.3
makes that match permanent behind one constant (``INTERCOM_TYPES``)
and one pure helper (``intercom_sub_control_kwargs``).

The fixture carries one hand-modelled ``IntercomV2`` control
("Entrance Intercom", room Hall, category Security) with one
sub-control ("Door Lock") that advertises the ``active`` state stream
the entity's handler needs (PS-05).  Expected values below are derived
by hand from ``tests/fixtures/LoxAPP3.json`` and written as literals,
never from the code under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.loxone import DOMAIN
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.switch import intercom_sub_control_kwargs

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hand-transcribed from tests/fixtures/LoxAPP3.json ("Entrance Intercom").
V2_ACTION_UUID = "63746c3a-9c7d-96f2-ffff-e301696e743276000024"
V2_ACTIVE_UUID = "36333734-9c8d-9336-ffff-d303136a2b42d300025"
V2_SUB_UUID = "7375623a-9c9d-9696-ffff-c74c6f6c6b3276000026"
V2_SUB_ACTIVE_UUID = "37333735-9cad-9336-ffff-d303136b2c42d300027"
V2_CONTROL_NAME = "Entrance Intercom"
V2_SUB_NAME = "Door Lock"

# Hand-transcribed from the fixture's rooms/cats tables (uuid → name).
HALL_ROOM_UUID = "726f6f6d-0105-9616-ffff-c726f6f6d3a4000005"  # "Hall"
SECURITY_CAT_UUID = "6361743a-0164-9637-ffff-269747963617000100"  # "Security"


def _fresh_loxapp3() -> dict:
    return json.loads((REPO_ROOT / "tests/fixtures/LoxAPP3.json").read_text())


def _fake_open_with(config):
    async def _fake_open(self, session=None):
        self.structure_file = config
        self.miniserver_version = config.get("softwareVersion")
        self.connected = True
        self.connection = Mock()
        self.connection.protocol.state.name = "OPEN"
        self._session_key = b"\x00" * 32
        return self

    return patch.object(LoxoneConnection, "open", _fake_open)


def _v2(sub_states: dict) -> dict:
    """A hand-built IntercomV2 control (test-only; not read from the fixture)."""
    return {
        "name": "Test Intercom",
        "type": "IntercomV2",
        "uuidAction": "ic-v2-action",
        "room": "Hall",
        "cat": "Security",
        "details": {},
        "states": {"active": "ic-v2-active"},
        "subControls": {
            "ic-v2-sub": {
                "name": "Test Sub",
                "type": "IntercomSubControl",
                "uuidAction": "ic-v2-sub",
                "room": HALL_ROOM_UUID,
                "cat": SECURITY_CAT_UUID,
                "details": {},
                "states": sub_states,
            }
        },
    }


# --------------------------------------------------------------------------- #
# Pure helper: intercom_sub_control_kwargs
# --------------------------------------------------------------------------- #
def test_v2_sub_control_has_kwargs_with_parent_device():
    loxconfig = _fresh_loxapp3()
    kwargs_list = intercom_sub_control_kwargs(_v2({"active": "ic-v2-sub-active"}), None, loxconfig)
    assert len(kwargs_list) == 1
    kwargs = kwargs_list[0]
    # The sub-control keeps its *own* name (the device carries the
    # intercom's), the parent's uuid wires in as ``parent_id``, and the
    # shared device info is built from the parent's identity.
    assert kwargs["name"] == "Test Sub"
    assert kwargs["uuidAction"] == "ic-v2-sub"
    assert kwargs["parent_id"] == "ic-v2-action"
    assert kwargs["type"] == "IntercomSubControl"
    # Room and category uuids are resolved to names via the structure
    # file ("Hall" / "Security", hand-read from the fixture's rooms/cats
    # tables).
    assert kwargs["room"] == "Hall"
    assert kwargs["cat"] == "Security"
    info = kwargs["device_info"]
    assert info["identifiers"] == {(DOMAIN, "ic-v2-action")}
    assert info["name"] == "Test Intercom"
    assert info["suggested_area"] == "Hall"
    # The device model is the *block* type that carried the sub-control.
    assert info["model"] == "IntercomV2"


def test_legacy_intercom_has_legacy_model():
    control = _v2({"active": "a"})
    control["type"] = "Intercom"
    kwargs_list = intercom_sub_control_kwargs(control, None)
    assert kwargs_list[0]["device_info"]["model"] == "Intercom"


def test_sub_control_without_active_is_skipped():
    """PS-05: a sub-control without an `active` stream cannot report;
    skip it (empty list, no crash) rather than raise on every event."""
    assert intercom_sub_control_kwargs(_v2({"on": "only-on"}), None) == []


def test_sub_control_missing_states_is_skipped():
    # No `states` key at all, and a `states` that is not a dict: both must
    # yield [] instead of crashing on the `.get` chain.
    control = _v2({})
    del control["subControls"]["ic-v2-sub"]["states"]
    assert intercom_sub_control_kwargs(control, None) == []
    assert intercom_sub_control_kwargs(_v2(None), None) == []  # states = None
    assert intercom_sub_control_kwargs(_v2("garbage"), None) == []  # states = "garbage"


def test_control_without_subcontrols_yields_none():
    for shape in ({}, {"subControls": None}, {"subControls": []}):
        control = _v2({"active": "a"})
        if "subControls" in shape:
            control["subControls"] = shape["subControls"]
        else:
            del control["subControls"]
        assert intercom_sub_control_kwargs(control, None) == []


def test_control_without_uuid_action_yields_none():
    control = _v2({"active": "a"})
    control["uuidAction"] = ""
    assert intercom_sub_control_kwargs(control, None) == []
    control["uuidAction"] = None
    assert intercom_sub_control_kwargs(control, None) == []
    assert intercom_sub_control_kwargs(None, None) == []


def test_helper_does_not_mutate_the_structure_file():
    """Entity kwargs must not leak ``parent_id``/``device_info`` back into
    the cached structure file (the loop used to mutate sub-controls in
    place before being extracted)."""
    control = _v2({"active": "a"})
    intercom_sub_control_kwargs(control, None)
    sub = control["subControls"]["ic-v2-sub"]
    assert "parent_id" not in sub
    assert "device_info" not in sub


def test_only_actable_subcontrols_survive():
    control = _v2({"active": "a"})
    control["subControls"]["ic-v2-sub2"] = {
        "name": "No Active",
        "type": "IntercomSubControl",
        "uuidAction": "ic-v2-sub2",
        "states": {"on": "x"},
    }
    control["subControls"]["not-a-dict"] = "junk"
    kwargs_list = intercom_sub_control_kwargs(control, None)
    assert [k["uuidAction"] for k in kwargs_list] == ["ic-v2-sub"]


# --------------------------------------------------------------------------- #
# Setup from the fixture: the V2 sub-switch appears and lands on the
# intercom's device
# --------------------------------------------------------------------------- #
async def test_setup_creates_intercom_v2_sub_switch(hass, mock_connection, mock_entry, enable_custom_integrations):
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("switch", "loxone", V2_SUB_UUID)
    # Derived by hand: area (Hall) + device name ("Entrance Intercom")
    # + short sub-entity name ("Door Lock").
    assert entity_id == "switch.hall_entrance_intercom_door_lock"

    # The switch is a *sub-entity* of the intercom device, named after
    # the intercom control, modelled as IntercomV2.
    devices = dr.async_get(hass)
    device = devices.async_get(registry.async_get(entity_id).device_id)
    assert device.name == V2_CONTROL_NAME
    assert device.model == "IntercomV2"

    # ... and its stored name is the sub-control's own (short) name.
    # (``name`` is None: with has_entity_name that means "inherit the
    # device name"; the short name lives in ``original_name``.)
    assert registry.async_get(entity_id).name is None
    assert registry.async_get(entity_id).original_name == V2_SUB_NAME

    # The legacy "Main Intercom" is untouched: it still has no
    # sub-control with an `active` state, so it creates no switches.
    assert not any("main_intercom" in i for i in hass.states.async_entity_ids("switch"))

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_intercom_v2_without_subcontrols_is_inert(hass, mock_entry, enable_custom_integrations):
    """Guards: a V2 intercom with no (shared) sub-controls yields no
    entity; nothing crashes the switch platform."""
    config = _fresh_loxapp3()
    control = config["controls"][V2_ACTION_UUID]
    del control["subControls"]

    mock_entry.add_to_hass(hass)
    with _fake_open_with(config):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert not any("entrance_intercom" in i for i in hass.states.async_entity_ids("switch"))
    # The platform ran through (a plain Switch still set up fine).
    assert any("living_room_light_switch" in i for i in hass.states.async_entity_ids("switch"))

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------- #
# State updates: a fed state event on the sub-control's active stream
# flips the switch
# --------------------------------------------------------------------------- #
async def test_intercom_v2_sub_switch_updates_on_fed_state_event(
    hass, mock_connection, mock_entry, enable_custom_integrations
):
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "switch.hall_entrance_intercom_door_lock"
    state = hass.states.get(entity_id)
    assert state.state == "unavailable"  # no value seen yet

    mock_connection.feed(V2_SUB_ACTIVE_UUID, 1)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"

    mock_connection.feed(V2_SUB_ACTIVE_UUID, 0)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"

    # The master (intercom) active stream is not the sub-control's stream:
    # feeding it does not move the sub-switch.
    mock_connection.feed(V2_ACTIVE_UUID, 0)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
