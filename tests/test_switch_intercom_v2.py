"""Tests for IntercomV2 sub-control discovery on the switch platform (#466)."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.loxone.switch import LoxoneIntercomSubControl, async_setup_entry

# Trimmed from a real LoxAPP3.json: an "IntercomV2" block (Intercom Gen 2)
# with one sub-control, exactly the shape the classic "Intercom" block has.
LOXCONFIG = {
    "controls": {
        "1a2b3c4d-0001-aaaa-ffff112233445566": {
            "name": "Front Door",
            "type": "IntercomV2",
            "uuidAction": "1a2b3c4d-0001-aaaa-ffff112233445566",
            "room": "room-1",
            "cat": "cat-1",
            "subControls": {
                "1a2b3c4d-0001-aaaa-ffff112233445566/AI1": {
                    "name": "Open Door",
                    "type": "Switch",
                    "uuidAction": "1a2b3c4d-0001-aaaa-ffff112233445566/AI1",
                },
            },
        },
    },
    "rooms": {"room-1": {"name": "Entrance"}},
    "cats": {"cat-1": {"name": "Access"}},
}


def _setup(loxconfig):
    """Run the platform setup against a fake Miniserver, return the entities."""
    # The platform setup annotates the control dicts in place (room/cat
    # names, the "parent - sub" entity name), so every run gets its own copy.
    miniserver = SimpleNamespace(lox_config=SimpleNamespace(json=copy.deepcopy(loxconfig)))
    added = []
    with patch("custom_components.loxone.switch.get_miniserver_from_hass", return_value=miniserver):
        asyncio.run(async_setup_entry(None, None, lambda entities, *a, **k: added.extend(entities)))
    return added


class TestIntercomV2:
    def test_sub_controls_become_intercom_switches(self):
        # Before the fix an IntercomV2 block was not in the type list at all,
        # so the door opener never appeared in Home Assistant.
        entities = _setup(LOXCONFIG)
        assert len(entities) == 1
        assert isinstance(entities[0], LoxoneIntercomSubControl)

    def test_sub_control_is_named_after_parent_and_output(self):
        (entity,) = _setup(LOXCONFIG)
        assert entity.name == "Front Door - Open Door"
        assert entity.unique_id == "1a2b3c4d-0001-aaaa-ffff112233445566/AI1"

    def test_classic_intercom_still_works(self):
        classic = {**LOXCONFIG, "controls": {k: {**v, "type": "Intercom"} for k, v in LOXCONFIG["controls"].items()}}
        (entity,) = _setup(classic)
        assert isinstance(entity, LoxoneIntercomSubControl)
