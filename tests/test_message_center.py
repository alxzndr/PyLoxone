"""WP-6.2 regression tests: Message Center -> repairs (#515).

Ports the approach from JoDehli/PyLoxone#515 by @mpcaddy, re-implemented
against the WP-3.2 coordinator (entry-scoped dispatcher fan-out) and the
WP-5.1/5.2 architecture (has_entity_name, entry.runtime_data, per-entry
issue ids).  Every expected value below is derived by hand:

* epoch conversions: 0 ms since 2009-01-01T00:00:00Z is that moment;
  one day (86400000 ms) lands on 2009-01-02T00:00:00Z; 486825600000 ms is
  486825600 s on top of the epoch = 5634 days + 48000 s (13 h 20 min) on
  2024-06-05, i.e. 13:20:00Z;
* 1700000000 s == 2023-11-14T22:13:20Z;
* severities, counts and entity ids come from the literal fixture
  entries below, never from the implementation.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.loxone import DOMAIN
from custom_components.loxone.helpers import loxone_timestamp
from custom_components.loxone.sensor import (
    MESSAGE_CENTER_GET_ENTRIES_COMMAND,
    _as_int_severity,
    _is_get_entries_response,
    message_center_entry_timestamp,
    message_center_issue_id,
    message_center_issue_severity,
    message_center_summary,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hand-transcribed from tests/fixtures/LoxAPP3.json.
MC_ACTION_UUID = "4d536763-7474-6c63-ffff-4d536774746c6301"
MC_CHANGED_UUID = "4d536763-6867-2020-ffff-4d53686720202001"
NOTIF_UUID = "4d53676c-616c-6c4e-ffff-4e4f474c31303041"
# The structure file's "Living Room Light Switch" control (registry
# unique_id == uuidAction, as for every primary loxone entity).
SWITCH_UUID = "63746c3a-0157-9766-ffff-e6720526f6f6000087"
# A device uuid with no matching control in the structure file.
UNLINKED_UUID = "00000000-0000-0000-1111-111111111111"

ENTRY_A_UUID = "ent-0001-1e4a-f704-a0001"
ENTRY_B_UUID = "ent-0002-9f4d-9ec7-b0002"


def _entry(
    entry_uuid, severity, title, affected, source=None, affected_name="Unknown", timestamps=None, is_historic=False
):
    e = {
        "entryUuid": entry_uuid,
        "severity": severity,
        "title": title,
        "affectedUuids": affected,
        "affectedName": affected_name,
        "desc": "A problem description.",
        "eventId": f"EV-{entry_uuid}",
        "helpLink": "https://support.loxone.com",
        "isHistoric": is_historic,
        "timestamps": timestamps if timestamps is not None else [1700000000],
    }
    if source is not None:
        e["sourceUuid"] = source
    return e


def _resp(entries):
    """A well-formed getEntries response value."""
    return json.dumps({"entries": entries})


def _issue_or_none(hass, issue_id):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


# --------------------------------------------------------------------------- #
# Pure helpers (hand-computed expected values)
# --------------------------------------------------------------------------- #


def test_loxone_timestamp_converts_miniserver_epoch():
    """0 ms = 2009-01-01T00:00:00Z; +1 day lands on 2009-01-02T00:00:00Z;
    hand-computed large value: 2024-06-05T13:20:00Z."""
    assert loxone_timestamp(0) == datetime(2009, 1, 1, tzinfo=UTC)
    assert loxone_timestamp(86400000) == datetime(2009, 1, 2, tzinfo=UTC)
    assert loxone_timestamp(486825600000) == datetime(2024, 6, 5, 13, 20, 0, tzinfo=UTC)
    # Fractional milliseconds are fine: 1.5 s after the epoch.
    assert loxone_timestamp(1500) == datetime(2009, 1, 1, 0, 0, 1, 500000, tzinfo=UTC)


def test_loxone_timestamp_rejects_junk():
    assert loxone_timestamp(None) is None
    assert loxone_timestamp("486825600000") is None
    assert loxone_timestamp(True) is None
    assert loxone_timestamp([486825600000]) is None
    assert loxone_timestamp(float("inf")) is None
    assert loxone_timestamp(float("nan")) is None


def test_as_int_severity():
    assert _as_int_severity(2) == 2
    assert _as_int_severity(3.0) == 3
    assert _as_int_severity("4") == 4
    assert _as_int_severity("-1") == -1
    assert _as_int_severity("x") == 0
    assert _as_int_severity(True) == 0
    assert _as_int_severity(None) == 0


def test_message_center_summary_counts_only_active_entries():
    entries = [
        _entry("e1", 1, "t1", []),
        _entry("e2", 2, "t2", []),
        _entry("e2b", 2, "t2b", [], is_historic=True),  # historic: excluded
        _entry("e3", 4, "t3", []),
        "junk",  # non-dict: skipped
        {"entryUuid": "e-bad", "severity": "no"},  # unparseable severity: 0
    ]
    counts, max_severity = message_center_summary(entries)
    assert counts == {"1": 1, "2": 1, "4": 1, "0": 1}
    assert max_severity == 4
    assert message_center_summary([]) == ({}, 0)
    assert message_center_summary(None) == ({}, 0)
    assert message_center_summary({"entries": []}) == ({}, 0)


def test_message_center_issue_severity_mapping():
    """Upstream mapping: 1/2 -> WARNING, 3 -> ERROR, > 3 -> CRITICAL."""
    assert message_center_issue_severity(1).value == "warning"
    assert message_center_issue_severity(2).value == "warning"
    assert message_center_issue_severity(3).value == "error"
    assert message_center_issue_severity(4).value == "critical"
    assert message_center_issue_severity(5).value == "critical"
    assert message_center_issue_severity(0).value == "warning"
    assert message_center_issue_severity("not-a-number").value == "warning"


def test_message_center_entry_timestamp():
    """Entry timestamps are Unix epoch seconds (upstream PR contract)."""
    assert message_center_entry_timestamp([1700000000]) == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert message_center_entry_timestamp([]) is None
    assert message_center_entry_timestamp(None) is None
    assert message_center_entry_timestamp(["junk"]) is None
    assert message_center_entry_timestamp(1700000000) is None  # bare int: not a list


def test_is_get_entries_response():
    assert _is_get_entries_response({MC_ACTION_UUID: ["getEntries/2"]}) is True
    assert _is_get_entries_response("jdev/sps/io/mc/getEntries/2") is True
    assert _is_get_entries_response({MC_ACTION_UUID: "getEntries/2"}) is True
    assert _is_get_entries_response({MC_ACTION_UUID: ["on"]}) is False
    assert _is_get_entries_response({}) is False
    assert _is_get_entries_response(None) is False
    assert _is_get_entries_response({"other": "turnon"}) is False


def test_message_center_issue_id_namespaced_by_entry_and_message():
    assert message_center_issue_id("ENTRY123", "ent-7") == "message_center_ENTRY123_ent-7"
    assert message_center_issue_id("A", "ent-7") != message_center_issue_id("B", "ent-7")


def test_translations_in_every_language():
    for language in ("en", "de", "cs"):
        data = json.loads((REPO_ROOT / "custom_components" / DOMAIN / "translations" / f"{language}.json").read_text())
        for key in ("loxone_status", "loxone_device_status"):
            block = data["issues"][key]
            assert block["title"], f"{language}.json issues.{key} has no title"
            assert block["description"], f"{language}.json issues.{key} has no description"


# --------------------------------------------------------------------------- #
# Setup: fixture control -> entity
# --------------------------------------------------------------------------- #


async def _setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _feed(hass, entry, message):
    """Route one full state message through the entry's coordinator —
    the same funnel as ``mock_connection.feed`` (conftest), just with an
    arbitrary payload (per-uuid *and* getEntries response shapes)."""
    entry.runtime_data.handle_message(message)
    await hass.async_block_till_done()


async def test_setup_creates_message_center_and_notifications(
    hass, mock_connection, mock_entry, enable_custom_integrations
):
    """The new entities appear after setup from the fixture: one diagnostic
    sensor per messageCenter control (here "Message Center") plus the
    global notification text sensor."""
    await _setup(hass, mock_entry)

    registry = er.async_get(hass)
    mc = registry.async_get_entity_id("sensor", "loxone", MC_ACTION_UUID)
    assert mc, "message center sensor not registered"
    notif = registry.async_get_entity_id("sensor", "loxone", NOTIF_UUID)
    assert notif, "notifications sensor not registered"

    device_registry = dr.async_get(hass)
    mc_device = device_registry.async_get(registry.async_get(mc).device_id)
    assert mc_device is not None
    assert mc_device.name == "Message Center"

    # Diagnostic; zero before any entries have been fetched.
    assert registry.async_get(mc).entity_category == "diagnostic"
    assert hass.states.get(mc).state == "0"
    assert hass.states.get(mc).attributes["status"] == {}

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_without_message_center_creates_nothing(
    hass, loxapp3, mock_connection, mock_entry, enable_custom_integrations
):
    """Guard: a structure file with no (valid) message center block still
    sets up and creates neither sensor (guarded lookups, no KeyError)."""
    mc_block = loxapp3.pop("messageCenter")
    notif = loxapp3["globalStates"]["notifications"]
    loxapp3["globalStates"]["notifications"] = None
    try:
        await _setup(hass, mock_entry)
    finally:
        loxapp3["messageCenter"] = mc_block
        loxapp3["globalStates"]["notifications"] = notif

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", "loxone", MC_ACTION_UUID) is None
    assert registry.async_get_entity_id("sensor", "loxone", NOTIF_UUID) is None

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------- #
# State updates + repair issues
# --------------------------------------------------------------------------- #


async def test_changed_stream_requests_entries(hass, mock_connection, mock_entry, enable_custom_integrations):
    """A new value on the `changed` stream sends the getEntries command
    addressed at the message center's uuidAction."""
    await _setup(hass, mock_entry)

    # A hand-picked Loxone-epoch value (2024-06-05T13:20:00Z).
    mock_connection.feed(MC_CHANGED_UUID, 486825600000)
    await hass.async_block_till_done()

    sent = [s for s in mock_connection.sent if s["uuid"] == MC_ACTION_UUID]
    assert len(sent) == 1
    assert sent[0]["value"] == MESSAGE_CENTER_GET_ENTRIES_COMMAND

    # Same value again: dedup (no second request).
    mock_connection.feed(MC_CHANGED_UUID, 486825600000)
    await hass.async_block_till_done()
    assert len([s for s in mock_connection.sent if s["uuid"] == MC_ACTION_UUID]) == 1

    # An *older* value: never requests backwards.
    mock_connection.feed(MC_CHANGED_UUID, 86400000)
    await hass.async_block_till_done()
    assert len([s for s in mock_connection.sent if s["uuid"] == MC_ACTION_UUID]) == 1

    # A corrupt value: no crash, no request.
    mock_connection.feed(MC_CHANGED_UUID, "not-a-number")
    await hass.async_block_till_done()
    assert len([s for s in mock_connection.sent if s["uuid"] == MC_ACTION_UUID]) == 1

    # A *newer* value: requests again.
    mock_connection.feed(MC_CHANGED_UUID, 486825686_400)  # +86.4 s
    await hass.async_block_till_done()
    assert len([s for s in mock_connection.sent if s["uuid"] == MC_ACTION_UUID]) == 2

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_get_entries_response_populates_state_and_issues(
    hass, mock_connection, mock_entry, enable_custom_integrations
):
    """A getEntries response updates the sensor and mirrors active entries
    into repair issues (severity, translation key, learn-more url, links)."""
    await _setup(hass, mock_entry)

    entries = [
        # Device-linked: affectedUuids resolves to the switch, source matches.
        _entry(
            ENTRY_A_UUID,
            3,
            "LoxAPP Alarm",
            [SWITCH_UUID],
            source=SWITCH_UUID,
            affected_name="Living Room Light Switch",
        ),
        # Not linked: unknown device id -> the "loxone_status" key.
        _entry(ENTRY_B_UUID, 2, "Unlinked Thing", [UNLINKED_UUID], affected_name="Ghost Relay"),
        # Historic: resolved, no issue, not counted.
        _entry("hist-0", 4, "Old", [], is_historic=True),
    ]
    await _feed(hass, mock_entry, {"value": _resp(entries), "control": {MC_ACTION_UUID: ["getEntries/2"]}})

    registry = er.async_get(hass)
    mc_id = registry.async_get_entity_id("sensor", "loxone", MC_ACTION_UUID)
    state = hass.states.get(mc_id)
    # Max active severity = 3 (the historic 4 does not count).
    assert state.state == "3"
    assert state.attributes["status"] == {"3": 1, "2": 1}

    issue_a_id = f"message_center_{mock_entry.entry_id}_{ENTRY_A_UUID}"
    issue_b_id = f"message_center_{mock_entry.entry_id}_{ENTRY_B_UUID}"
    issue_hist_id = f"message_center_{mock_entry.entry_id}_hist-0"

    issue_a = _issue_or_none(hass, issue_a_id)
    assert issue_a is not None
    assert issue_a.severity.value == "error"  # 3 -> ERROR
    assert issue_a.translation_key == "loxone_device_status"  # linked
    assert issue_a.learn_more_url == "https://support.loxone.com"
    assert issue_a.is_persistent is True
    ph_a = issue_a.translation_placeholders
    assert "A problem description." in ph_a["description"]
    # The source entity is linked with a more-info anchor.
    switch_id = registry.async_get_entity_id("switch", "loxone", SWITCH_UUID)
    assert f"more-info-entity-id={switch_id}" in ph_a["description"]
    # Occurred at: hand-computed from 1700000000 s.
    assert "2023-11-14T22:13:20+00:00" in ph_a["description"]
    # The source uuid selected the entity's display name for {name}
    # (device name: WP-5.1 primary entities store no registry name).
    assert ph_a["name"] == "Living Room Light Switch"

    issue_b = _issue_or_none(hass, issue_b_id)
    assert issue_b is not None
    assert issue_b.severity.value == "warning"  # 2 -> WARNING
    assert issue_b.translation_key == "loxone_status"  # unlinked
    assert issue_b.translation_placeholders["name"] == "Ghost Relay"

    # Historic entries never become issues.
    assert _issue_or_none(hass, issue_hist_id) is None

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_resolved_entries_remove_their_issues(hass, mock_connection, mock_entry, enable_custom_integrations):
    """When an entry flips to isHistoric (or simply disappears) its issue
    is removed again and the state follows the remaining entries."""
    await _setup(hass, mock_entry)

    a = _entry(
        ENTRY_A_UUID, 3, "LoxAPP Alarm", [SWITCH_UUID], source=SWITCH_UUID, affected_name="Living Room Light Switch"
    )
    b = _entry(ENTRY_B_UUID, 2, "Unlinked Thing", [UNLINKED_UUID], affected_name="Ghost Relay")
    await _feed(hass, mock_entry, {"value": _resp([a, b]), "control": {MC_ACTION_UUID: ["getEntries/2"]}})

    issue_a_id = f"message_center_{mock_entry.entry_id}_{ENTRY_A_UUID}"
    issue_b_id = f"message_center_{mock_entry.entry_id}_{ENTRY_B_UUID}"
    assert _issue_or_none(hass, issue_a_id) is not None
    assert _issue_or_none(hass, issue_b_id) is not None

    # B resolves (isHistoric); A disappears from the list entirely.
    b_resolved = _entry(
        ENTRY_B_UUID, 2, "Unlinked Thing", [UNLINKED_UUID], is_historic=True, affected_name="Ghost Relay"
    )
    await _feed(hass, mock_entry, {"value": _resp([b_resolved]), "control": {MC_ACTION_UUID: ["getEntries/2"]}})

    assert _issue_or_none(hass, issue_b_id) is None, "historic entry must delete its issue"
    assert _issue_or_none(hass, issue_a_id) is None, "disappeared entry must be stale-cleaned up"

    mc_id = er.async_get(hass).async_get_entity_id("sensor", "loxone", MC_ACTION_UUID)
    assert hass.states.get(mc_id).state == "0"
    assert hass.states.get(mc_id).attributes["status"] == {}

    # An empty authoritative list keeps the sensor at zero.
    await _feed(hass, mock_entry, {"value": _resp([]), "control": {MC_ACTION_UUID: ["getEntries/2"]}})
    assert hass.states.get(mc_id).state == "0"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_irrelevant_commands_do_not_touch_state_or_issues(
    hass, mock_connection, mock_entry, enable_custom_integrations
):
    """Responses that are NOT getEntries acks (unrelated control/value) are
    ignored by the message center sensors."""
    await _setup(hass, mock_entry)

    a = _entry(ENTRY_A_UUID, 3, "Alarm", [SWITCH_UUID], source=SWITCH_UUID)
    await _feed(hass, mock_entry, {"value": _resp([a]), "control": {MC_ACTION_UUID: ["getEntries/2"]}})

    # An unrelated full message carrying a control+value pair:
    await _feed(hass, mock_entry, {"value": json.dumps({"entries": []}), "control": {"deadbeef": ["turnon"]}})

    issue_a_id = f"message_center_{mock_entry.entry_id}_{ENTRY_A_UUID}"
    assert _issue_or_none(hass, issue_a_id) is not None, "unrelated command must not clear issues"
    mc_id = er.async_get(hass).async_get_entity_id("sensor", "loxone", MC_ACTION_UUID)
    assert hass.states.get(mc_id).state == "3"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_malformed_response_is_ignored(hass, mock_connection, mock_entry, enable_custom_integrations):
    """A junk `value` is swallowed and keeps the prior state and issues."""
    await _setup(hass, mock_entry)
    a = _entry(ENTRY_A_UUID, 3, "Alarm", [SWITCH_UUID], source=SWITCH_UUID)
    await _feed(hass, mock_entry, {"value": _resp([a]), "control": {MC_ACTION_UUID: ["getEntries/2"]}})

    await _feed(hass, mock_entry, {"value": "not-json", "control": {MC_ACTION_UUID: ["getEntries/2"]}})

    mc_id = er.async_get(hass).async_get_entity_id("sensor", "loxone", MC_ACTION_UUID)
    assert hass.states.get(mc_id).state == "3"
    issue_a_id = f"message_center_{mock_entry.entry_id}_{ENTRY_A_UUID}"
    assert _issue_or_none(hass, issue_a_id) is not None

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_notifications_sensor_updates_on_fed_state(hass, mock_connection, mock_entry, enable_custom_integrations):
    """The global notification text sensor stream-updates."""
    await _setup(hass, mock_entry)
    mock_connection.feed(NOTIF_UUID, "Miniserver maintenance mode active")
    await hass.async_block_till_done()
    notif_id = er.async_get(hass).async_get_entity_id("sensor", "loxone", NOTIF_UUID)
    assert notif_id
    assert hass.states.get(notif_id).state == "Miniserver maintenance mode active"

    mock_connection.feed(NOTIF_UUID, "Second message")
    await hass.async_block_till_done()
    assert hass.states.get(notif_id).state == "Second message"

    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
