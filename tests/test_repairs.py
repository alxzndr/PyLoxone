"""WP-5.2 regression tests: the CORE-30 repair issues.

Acceptance (reproduced verbatim from the work package):

  * an issue for ``yaml_config_present`` when a ``loxone:`` YAML block is
    still in ``configuration.yaml``,
  * an issue for ``auth_failed`` while the stored credentials do not work
    — present while reauth is pending, gone once a re-authentication
    completes,
  * an issue for ``unsupported_firmware`` when the structure file reports
    a firmware below the floor, removed again at and above 7.0.0,
  * translations for every key in the shipped languages (en, de, cs).

The firmware-floor comparisons are asserted against hand-derived literal
versions ("6.9.9" below, "7.0.0" exactly the floor, "7.0.1"/"8.0.0"
above), NOT against values computed from the implementation, so the test
cannot survive a broken comparison formula.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from unittest.mock import Mock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_setup_component

import custom_components.loxone as loxone_module
from custom_components.loxone import DOMAIN
from custom_components.loxone.helpers import meets_minimum_firmware, parse_firmware_version
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.pyloxone_api.exceptions import LoxoneUnauthorisedError

TRANSLATION_LANGUAGES = ("en", "de", "cs")
REQUIRED_ISSUE_KEYS = ("yaml_config_present", "auth_failed", "unsupported_firmware")

# HA schedules an entry retry ~5-80 s (doubling) after a
# ConfigEntryNotReady failure; an 11 sim-minute fire covers every
# backoff step (same mechanics as tests/test_setup_retry.py).
RETRY_STEP = datetime.timedelta(minutes=11)
REPO_ROOT = Path(__file__).resolve().parent.parent


def _fire_retry(hass) -> None:
    """Advance HA's clock far enough that the pending entry-retry timer fires."""
    async_fire_time_changed(hass, dt_util.utcnow() + RETRY_STEP)


async def _wait_until(hass, predicate, *, message="condition", seconds=90.0) -> None:
    """Poll (waking the loop) until ``predicate()`` is true, re-firing the
    pending entry-retry timer every 5 s so a new attempt is never left
    waiting on a real-time backoff."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    last = loop.time()
    while not predicate():
        now = loop.time()
        if now > deadline:
            raise AssertionError(f"timed out waiting for: {message}")
        if now - last >= 5.0:
            last = now
            _fire_retry(hass)
        await asyncio.sleep(0.2)


async def _setup_entry(hass, entry) -> None:
    entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ok, "entry setup failed"
    assert entry.state is ConfigEntryState.LOADED


def _seed_connection(api, loxapp3):
    """Same seeding the WP-0.2 ``mock_connection`` fixture performs on a
    successful ``open`` (structure file + an "open" websocket protocol)."""
    api.structure_file = loxapp3
    api.miniserver_version = loxapp3.get("softwareVersion")
    api.connected = True
    api.connection = Mock()
    api.connection.protocol.state.name = "OPEN"
    api._session_key = b"\x00" * 32
    api.event_bus = None
    return api


def _reauth_flow_id(hass, entry_id: str) -> str | None:
    """The in-progress reauth flow for ``entry_id``, if any.

    ``hass.config_entries.flow.async_progress()`` returns either a list of
    partial flow results (this HA version) or, in older versions, a
    mapping; both shapes are normalised here (test_setup_retry.py relies on
    the same dual handling).
    """
    progress = hass.config_entries.flow.async_progress()
    items = progress.values() if isinstance(progress, dict) else progress
    for flow in items:
        if not isinstance(flow, dict):
            continue
        context = flow.get("context") or {}
        if flow.get("handler") == DOMAIN and context.get("source") == "reauth" and context.get("entry_id") == entry_id:
            return flow.get("flow_id") or flow.get("id")
    return None


def _issue_or_none(hass, issue_id: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


# --------------------------------------------------------------------------- #
# Pure-function tests: the firmware floor (hand-derived literals)
# --------------------------------------------------------------------------- #


def test_parse_firmware_version_accepts_both_structure_forms() -> None:
    assert parse_firmware_version("7.1.0.28") == (7, 1, 0, 28)
    assert parse_firmware_version(["7", "1", "0", "28"]) == (7, 1, 0, 28)
    assert parse_firmware_version("6.9.9") == (6, 9, 9)
    assert parse_firmware_version("8.0.0") == (8, 0, 0)


def test_parse_firmware_version_rejects_unparseable_input() -> None:
    assert parse_firmware_version("7.1.0.beta") is None
    assert parse_firmware_version("") is None
    assert parse_firmware_version(None) is None
    assert parse_firmware_version(["7", "1", None]) is None
    assert parse_firmware_version(7.1) is None


def test_firmware_floor_is_7_0_0() -> None:
    # Below the floor:
    assert meets_minimum_firmware("6.9.9") is False
    assert meets_minimum_firmware("6") is False
    assert meets_minimum_firmware("6.9.999") is False
    # Exactly the floor:
    assert meets_minimum_firmware("7.0.0") is True
    # Above the floor (later micro, later minor, later major, list form):
    assert meets_minimum_firmware("7.0.1") is True
    assert meets_minimum_firmware("7.9.0") is True
    assert meets_minimum_firmware("8.0.0") is True
    assert meets_minimum_firmware(["7", "1", "0", "28"]) is True
    # Unparseable / missing: never reported as unsupported (the repair must
    # not fire for versions it cannot verify):
    assert meets_minimum_firmware("garbage") is True
    assert meets_minimum_firmware("") is True
    assert meets_minimum_firmware(None) is True


def test_meets_minimum_firmware_uses_only_the_first_three_parts() -> None:
    # The 4th part (revision) must not affect the comparison:
    assert meets_minimum_firmware("7.0.0.1") is True
    assert meets_minimum_firmware("6.999.999.999") is False


# --------------------------------------------------------------------------- #
# Translations for every repair key in every shipped language
# --------------------------------------------------------------------------- #


def test_repair_translations_in_every_language() -> None:
    for language in TRANSLATION_LANGUAGES:
        translation_path = REPO_ROOT / "custom_components" / DOMAIN / "translations" / f"{language}.json"
        assert translation_path.is_file(), f"missing translation file {language}.json"
        data = json.loads(translation_path.read_text())
        issues = data.get("issues", {})
        for key in REQUIRED_ISSUE_KEYS:
            assert key in issues, f"{language}.json has no issues.{key}"
            block = issues[key]
            assert block.get("title"), f"{language}.json issues.{key} has no title"
            assert block.get("description"), f"{language}.json issues.{key} has no description"


# --------------------------------------------------------------------------- #
# YAML block -> persistent repair issue (CORE-19 / CORE-30)
# --------------------------------------------------------------------------- #


async def test_yaml_block_creates_persistent_repair_issue(hass, enable_custom_integrations) -> None:
    """A surviving ``loxone:`` YAML block must register the repair issue."""
    await async_setup_component(hass, DOMAIN, {DOMAIN: {"host": "192.168.1.50"}})
    issue = _issue_or_none(hass, "yaml_config_present")
    assert issue is not None, "the yaml_config_present repair issue was not created"
    assert issue.translation_key == "yaml_config_present"
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_persistent


async def test_no_yaml_block_no_issue(hass, enable_custom_integrations) -> None:
    """A domain load without a YAML block must not create the issue."""
    await async_setup_component(hass, DOMAIN, {})
    assert _issue_or_none(hass, "yaml_config_present") is None


# --------------------------------------------------------------------------- #
# auth_failed: present while reauth is pending, cleared when it completes
# --------------------------------------------------------------------------- #


async def test_auth_failed_issue_until_reauth_completes(
    hass, loxapp3, mock_connection, mock_entry, monkeypatch
) -> None:
    """401 forever -> the ``auth_failed`` issue exists while reauth is
    pending and is removed once the reauth flow completes with working
    credentials (the entry reloads, LOADED, issue gone).

    The integration clock is faked exactly like in test_setup_retry.py:
    each observed failure steps it 660 s so the bounded retry window
    (5 attempts / >= 300 s) is exhausted inside the test.
    """

    class _FakeClock:
        """Monotonic-clock stand-in; starts at 1000.0, +660 s per failure."""

        now = 1000.0

        @classmethod
        def monotonic(cls):
            return cls.now

    monkeypatch.setattr(loxone_module, "time", _FakeClock)
    open_calls = []
    seen = [0]
    open_ok = [False]

    async def scripted_open(api, session=None):
        open_calls.append(1)
        grown = len(open_calls) - seen[0]
        if grown > 0:
            seen[0] = len(open_calls)
        if not open_ok[0]:
            _FakeClock.now += 660.0 * grown
            raise LoxoneUnauthorisedError("Unauthorized (401)")
        return _seed_connection(api, loxapp3)

    issue_id = f"auth_failed_{mock_entry.entry_id}"
    with patch.object(LoxoneConnection, "open", new=scripted_open):
        mock_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
        # Attempt 1 is inside the transient window: retry, no issue yet.
        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert len(open_calls) == 1
        assert _issue_or_none(hass, issue_id) is None

        # Attempts 2..4 stay in the window: still retrying, still no issue.
        for target in (2, 3, 4):
            await _wait_until(hass, lambda t=target: len(open_calls) >= t, message=f"attempt {target} to run")
            entry_state = hass.config_entries.async_get_entry(mock_entry.entry_id).state
            assert entry_state is ConfigEntryState.SETUP_RETRY
            assert _issue_or_none(hass, issue_id) is None, (
                "auth_failed must not fire before the escalation window is exhausted"
            )

        # The 5th attempt (>= 300 s after the first) escalates to
        # ConfigEntryAuthFailed -> SETUP_ERROR + reauth flow + the issue.
        await _wait_until(hass, lambda: len(open_calls) >= 5, message="the fifth attempt to run")
        await _wait_until(
            hass,
            lambda: hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.SETUP_ERROR,
            message="the entry to land in SETUP_ERROR",
        )

        entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
        assert len(open_calls) == 5
        assert entry.state is ConfigEntryState.SETUP_ERROR

        issue = _issue_or_none(hass, issue_id)
        assert issue is not None, "auth_failed issue missing after the escalation"
        assert issue.translation_key == "auth_failed"
        assert issue.severity is ir.IssueSeverity.ERROR

        # HA started the reauth flow for this entry.
        flow_id = _reauth_flow_id(hass, mock_entry.entry_id)
        assert flow_id, f"no reauth flow in progress: {hass.config_entries.flow.async_progress()!r}"

        # --- the user enters working credentials in the reauth form ------
        open_ok[0] = True
        await hass.config_entries.flow.async_configure(
            flow_id,
            {
                CONF_HOST: "loxberry.local",
                CONF_PORT: 8080,
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "new-secret",
            },
        )
        await _wait_until(
            hass,
            lambda: hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED,
            message="the entry to come back LOADED after reauth",
        )

    entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    # The new credentials are exactly what the form carried.
    assert entry.data[CONF_USERNAME] == "admin"
    assert entry.data[CONF_PASSWORD] == "new-secret"
    # Reauth completed -> the issue goes away.
    assert _issue_or_none(hass, issue_id) is None, "auth_failed must be deleted once reauth completes"


async def test_live_session_401_creates_auth_failed_issue(hass, loxapp3, mock_connection, mock_entry) -> None:
    """A live session that the Miniserver rejects (``api.run`` raising
    ``LoxoneUnauthorisedError``) must create the same issue and start the
    reauth flow (``config_entry.async_start_reauth``)."""
    await _setup_entry(hass, mock_entry)

    async def failing_run(api, on_state, callback=None):
        raise LoxoneUnauthorisedError("Rejected by Miniserver")

    issue_id = f"auth_failed_{mock_entry.entry_id}"
    with patch.object(LoxoneConnection, "run", new=failing_run):
        # Re-arm the entry's session task; the next ``run`` dies immediately.
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    await _wait_until(
        hass,
        lambda: _reauth_flow_id(hass, mock_entry.entry_id) is not None,
        message="a reauth flow to be started by the session supervisor",
    )
    await _wait_until(hass, lambda: _issue_or_none(hass, issue_id) is not None, message="auth_failed issue")
    assert _issue_or_none(hass, issue_id).translation_key == "auth_failed"


# --------------------------------------------------------------------------- #
# unsupported_firmware: created below the floor, removed above it
# --------------------------------------------------------------------------- #


async def test_unsupported_firmware_creates_warning_issue(hass, loxapp3, mock_connection, mock_entry) -> None:
    """A Miniserver reporting a firmware below the floor must register a
    WARNING issue; the entry itself stays up (guidance, not a failure)."""
    original_version = loxapp3.get("softwareVersion")
    loxapp3["softwareVersion"] = ["6", "2", "1"]
    try:
        await _setup_entry(hass, mock_entry)
    finally:
        loxapp3["softwareVersion"] = original_version

    issue = _issue_or_none(hass, f"unsupported_firmware_{mock_entry.entry_id}")
    assert issue is not None, "unsupported_firmware issue missing despite 6.2.1"
    assert issue.translation_key == "unsupported_firmware"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert mock_entry.state is ConfigEntryState.LOADED


async def test_supported_firmware_creates_no_issue(hass, loxapp3, mock_connection, mock_entry) -> None:
    """The fixture firmware 7.1.0.28 is above the floor: no issue."""
    await _setup_entry(hass, mock_entry)
    assert _issue_or_none(hass, f"unsupported_firmware_{mock_entry.entry_id}") is None


async def test_firmware_upgrade_removes_the_issue(hass, loxapp3, mock_connection, mock_entry) -> None:
    """A below-floor setup creates the issue; after the (simulated) firmware
    upgrade and a new setup it must be gone again."""
    await _setup_entry(hass, mock_entry)
    assert _issue_or_none(hass, f"unsupported_firmware_{mock_entry.entry_id}") is None

    original_version = loxapp3.get("softwareVersion")
    loxapp3["softwareVersion"] = ["6", "1", "0"]
    try:
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
        await _setup_entry(hass, mock_entry)
        issue = _issue_or_none(hass, f"unsupported_firmware_{mock_entry.entry_id}")
        assert issue is not None and issue.translation_key == "unsupported_firmware"

        # Firmware upgraded to 7.1.0: the next setup clears the issue.
        loxapp3["softwareVersion"] = ["7", "1", "0"]
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
        await _setup_entry(hass, mock_entry)
    finally:
        loxapp3["softwareVersion"] = original_version

    assert _issue_or_none(hass, f"unsupported_firmware_{mock_entry.entry_id}") is None


# --------------------------------------------------------------------------- #
# The module-level helpers (unit level)
# --------------------------------------------------------------------------- #


async def test_async_report_auth_failure_is_idempotent(hass, mock_entry) -> None:
    issue_id = f"auth_failed_{mock_entry.entry_id}"
    assert _issue_or_none(hass, issue_id) is None
    loxone_module._async_report_auth_failure(hass, mock_entry)
    assert _issue_or_none(hass, issue_id) is not None
    loxone_module._async_report_auth_failure(hass, mock_entry)  # replace, not duplicate
    # Exactly one issue with that id.
    issues = [i for i in ir.async_get(hass).issues.values() if i.issue_id == issue_id]
    assert len(issues) == 1


async def test_reconcile_firmware_removes_on_supported(hass, mock_entry) -> None:
    issue_id = f"unsupported_firmware_{mock_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unsupported_firmware",
    )
    assert _issue_or_none(hass, issue_id) is not None
    loxone_module._async_reconcile_firmware_issue(hass, mock_entry, "7.1.0")
    assert _issue_or_none(hass, issue_id) is None


async def test_reconcile_firmware_creates_on_unsupported(hass, mock_entry) -> None:
    issue_id = f"unsupported_firmware_{mock_entry.entry_id}"
    assert _issue_or_none(hass, issue_id) is None
    loxone_module._async_reconcile_firmware_issue(hass, mock_entry, "6.9.9")
    issue = _issue_or_none(hass, issue_id)
    assert issue is not None
    assert issue.translation_key == "unsupported_firmware"
