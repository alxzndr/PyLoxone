"""WP-3.2 acceptance: two Loxone Miniservers on one HA instance, isolated (CORE-04). with two Loxone entries (two Miniservers) loaded, a state
message fed to entry A must update only entry A's entities — including when
both servers expose the *same* control layout — and a command fired at an
entity of A must reach A's Miniserver only (never A's BUS listener, which
executes the most recently configured server's connection object).

Entry B is driven from a shifted copy of the fixture: every uuid gets its
final hex byte +1 and every room/control name gets a ``-B`` suffix, so the
entities are distinct while the control topology mirrors A's.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
import homeassistant.helpers.entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loxone import DOMAIN
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection


def _strip_non_json(obj):
    """Drop values that are not JSON scalars/containers.

    Several platforms' ``async_setup_entry`` write runtime references
    (``hass`` / ``config_entry`` / ``async_add_devices``) into the control
    dicts they receive — in production those writes land in per-session
    structure files, in tests they leak into the shared fixture dict.
    B's copy must be dumpable, so strip the foreign keys.
    """
    if isinstance(obj, dict):
        for key in list(obj):
            value = obj[key]
            if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
                _strip_non_json(value)
            else:
                del obj[key]
    elif isinstance(obj, list):
        for item in obj:
            _strip_non_json(item)
    return obj


def _shifted_structure(loxapp3: dict) -> dict:
    """B's structure file: every uuid's final hex byte +1, all names -B."""
    _UUID_RE = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-(?:[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{18}|[0-9a-f]{5}-[0-9a-f]{4}-[0-9a-f]{12})\b"
    )

    def _shift(match: re.Match) -> str:
        u = match.group(0)
        return u[:-2] + format(int(u[-2:], 16) + 1, "02x")

    text = _UUID_RE.sub(_shift, json.dumps(_strip_non_json(dict(loxapp3))))
    data = json.loads(text)
    for room in data.get("rooms", {}).values():
        if isinstance(room, dict) and room.get("name"):
            room["name"] = f"{room['name']}-B"
    for control in data.get("controls", {}).values():
        control["name"] = f"{control['name']}-B"
    # A second Miniserver has its own serial; the fixture must mirror that
    # or both entries collide on the device identifier and the
    # serial-scoped keep-alive/version sensor unique ids.
    ms_info = data.get("msInfo")
    if isinstance(ms_info, dict) and ms_info.get("serialNr"):
        ms_info["serialNr"] = f"{ms_info['serialNr']}-B"
    return data


@pytest.fixture
def shifted_structure(loxapp3) -> dict:
    return _shifted_structure(loxapp3)


@pytest.fixture
def mock_entry_b() -> MockConfigEntry:
    """Second Miniserver entry (different serial, same option shape)."""
    return MockConfigEntry(
        domain="loxone",
        version=4,
        data={},
        options={
            "host": "loxberry-b.local",
            "port": 8080,
            "username": "admin",
            "password": "secret",
            "verify_ssl": True,
            "generate_scenes": False,
            "generate_lightcontroller_subcontrols": False,
        },
        unique_id="TEST-SERIAL-0002",
    )


async def _setup_entry(hass, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ok
    assert entry.state is ConfigEntryState.LOADED


async def test_two_miniservers_are_isolated(
    hass, mock_connection, mock_entry, mock_entry_b, loxapp3, shifted_structure, enable_custom_integrations
) -> None:
    """A's state + commands must never touch B, and vice versa (CORE-04)."""
    # A roundtripped copy of the fixture (the shared session fixture can
    # hold runtime refs leaked by other tests' platform setups; A must see
    # a structure file like production would, not that polluted dict).
    structure_a = json.loads(json.dumps(_strip_non_json(dict(loxapp3))))
    structures = iter([structure_a, shifted_structure])
    orig_open = LoxoneConnection.open

    per_api_sent: dict[int, list] = {}

    async def _open_patched(self, session=None):
        structure = next(structures)
        result = await orig_open(self, session=session)
        self.structure_file = structure
        return result

    async def _routed_send(self, entity_uuid, value, *args, **kwargs):
        per_api_sent.setdefault(id(self), []).append((entity_uuid, value, kwargs.get("code")))

    with (
        patch.object(LoxoneConnection, "open", new=_open_patched),
        patch.object(LoxoneConnection, "send_websocket_command", new=_routed_send),
    ):
        await _setup_entry(hass, mock_entry)
        await _setup_entry(hass, mock_entry_b)

        coord_a = mock_entry.runtime_data  # CORE-31
        coord_b = mock_entry_b.runtime_data  # CORE-31
        api_a = coord_a.api
        api_b = coord_b.api
        # two independent sessions (different connection objects)
        assert api_a is not api_b
        assert api_a.connection is not api_b.connection

        # --- locate both switches (same control name, distinct rooms) ---
        all_switches = [s for s in hass.states.async_all("switch") if "living_room_light_switch" in s.entity_id]
        assert len(all_switches) == 2, all_switches
        # the -B room name ends up in B's friendly_name
        state_a = next(s for s in all_switches if not s.attributes.get("friendly_name", "").endswith("-B"))
        state_b = next(s for s in all_switches if s.attributes.get("friendly_name", "").endswith("-B"))

        # --- first feed per entry makes each switch known+available (off) ---
        switch_ctl = next((c for c in loxapp3["controls"].values() if c["name"] == "Living Room Light Switch"), None)
        assert switch_ctl is not None
        active_uuid_a = switch_ctl["states"]["active"]
        switch_ctl_b = next(
            (c for c in shifted_structure["controls"].values() if c["name"] == "Living Room Light Switch-B"), None
        )
        assert switch_ctl_b is not None
        active_uuid_b = switch_ctl_b["states"]["active"]
        assert active_uuid_b != active_uuid_a  # distinct uuids, mirrored topology

        coord_a.handle_message({active_uuid_a: 0.0})
        coord_b.handle_message({active_uuid_b: 0.0})
        await hass.async_block_till_done()
        assert hass.states.get(state_a.entity_id).state == "off"
        assert hass.states.get(state_b.entity_id).state == "off"

        # --- feed A only: A flips, B (mirrored control) stays off ---
        coord_a.handle_message({active_uuid_a: 1.0})
        await hass.async_block_till_done()

        assert hass.states.get(state_a.entity_id).state == "on"
        assert hass.states.get(state_b.entity_id).state == "off"

        # --- feed B only (its shifted active uuid): B flips, A stays on ---
        coord_b.handle_message({active_uuid_b: 1.0})
        await hass.async_block_till_done()

        assert hass.states.get(state_b.entity_id).state == "on"
        assert hass.states.get(state_a.entity_id).state == "on"  # A untouched

        # --- inbound: B's mirrored analog temperature sensor picks up a
        #     value on B's shifted uuid; A's same-named sensor must not
        #     follow along (uuids differ, signals are namespaced) ---
        temp_ctl_a = next((c for c in loxapp3["controls"].values() if c["name"] == "Temperature Parlour"), None)
        temp_ctl_b = next(
            (c for c in shifted_structure["controls"].values() if c["name"] == "Temperature Parlour-B"), None
        )
        assert temp_ctl_a is not None and temp_ctl_b is not None
        assert temp_ctl_a["uuidAction"] != temp_ctl_b["uuidAction"]
        # Resolve by unique_id (the control's own/shifted uuidAction) so
        # the assertion does not depend on HA's entity-id slug heuristics.
        registry = er.async_get(hass)
        b_sensor_entry = next((e for e in registry.entities.values() if e.unique_id == temp_ctl_b["uuidAction"]), None)
        assert b_sensor_entry is not None  # B's sensor entity exists
        a_sensor_entry = next((e for e in registry.entities.values() if e.unique_id == temp_ctl_a["uuidAction"]), None)
        assert a_sensor_entry is not None
        assert hass.states.get(a_sensor_entry.entity_id).state != "20.5"  # before feeding
        coord_b.handle_message({temp_ctl_b["uuidAction"]: 20.5})
        await hass.async_block_till_done()
        assert hass.states.get(b_sensor_entry.entity_id).state == "20.5"
        assert hass.states.get(a_sensor_entry.entity_id).state != "20.5"  # A's sensor untouched

        # --- commands route to the entity's own Miniserver (CORE-04) ---
        # Both switches are "on" from the isolation phase; drive them off
        # (known + available) so the entity-service calls can dispatch.
        coord_a.handle_message({active_uuid_a: 0.0})
        coord_b.handle_message({active_uuid_b: 0.0})
        await hass.async_block_till_done()
        assert hass.states.get(state_a.entity_id).state == "off"
        assert hass.states.get(state_b.entity_id).state == "off"

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": state_a.entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": state_b.entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()

        sent_a = per_api_sent.get(id(api_a), [])
        sent_b = per_api_sent.get(id(api_b), [])
        # A's command reaches only A's API, addressed with A's own uuid
        assert any(item[0] == switch_ctl["uuidAction"] for item in sent_a)
        assert not any(item[0] == switch_ctl_b["uuidAction"] for item in sent_a)
        assert any(item[0] == switch_ctl_b["uuidAction"] for item in sent_b)
        assert not any(item[0] == switch_ctl["uuidAction"] for item in sent_b)

        # --- both entries unload cleanly ---
        assert await hass.config_entries.async_unload(mock_entry_b.entry_id)
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
        # CORE-31: the coordinators are gone from the entries, and the
        # stale ``hass.data[DOMAIN]`` twin is gone as well.
        assert mock_entry.entry_id not in hass.data.get(DOMAIN, {})
        assert mock_entry_b.entry_id not in hass.data.get(DOMAIN, {})
        assert getattr(mock_entry, "runtime_data", None) is None
        assert getattr(mock_entry_b, "runtime_data", None) is None
