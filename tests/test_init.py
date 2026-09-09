"""WP-3.1 regression tests: setup / unload / reload lifecycle.

Acceptance criteria covered (docs/review/2026-09-remediation-plan.md, WP-3.1):

  A. setup + unload leaves ``hass.bus.async_listeners()`` counts for
     ``loxone_event``, ``EVENT_HOMEASSISTANT_STOP`` and
     ``EVENT_HOMEASSISTANT_STARTED`` at baseline
     (CORE-06: the old ``listen_once`` unsubcriptions were discarded).
  B. five full reloads leave no extra tasks in ``asyncio.all_tasks()``
     (CORE-03 / CORE-13; the session must be the entry's tracked
     background task and every listener must deregister through the
     config entry).
  C. ``LoxoneServiceUnAvailableError`` on open -> ``ConfigEntryNotReady``
     (the entry lands in setup_retry, never setup_error) **and**
     ``api.close`` awaited (CORE-09 acceptance, kept intact from WP-1.5).
  D. changing an option schedules a reload of that entry (CORE-29).
  E. the ``async_setup_entry`` loop and the stub
     ``async_setup_platform``/``PLATFORM_SCHEMA`` leftovers are gone
     (CORE-14).
  F. the coordinator uses the stock first-refresh machinery: explicit
     ``config_entry=`` (no deprecation report), ``data``/
     ``last_update_success`` set (CORE-12; the override of
     ``async_config_entry_first_refresh`` is gone).
  G. a stored *empty* token is not handed to ``LoxoneConnection``
     (CORE-10, coordinator side).

The stale-stop-listener asymmetry that made this red before the change is
called out per test in a comment.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loxone import DOMAIN, LoxoneCoordinator
from custom_components.loxone.const import EVENT, SECUREDSENDDOMAIN, SENDDOMAIN
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.pyloxone_api.exceptions import LoxoneServiceUnAvailableError

_TRACKED_EVENTS = (
    EVENT,  # loxone_event
    EVENT_HOMEASSISTANT_STOP,
    EVENT_HOMEASSISTANT_STARTED,
    SENDDOMAIN,
    SECUREDSENDDOMAIN,
)


def _listener_counts(hass, events=_TRACKED_EVENTS) -> dict[str, int]:
    """Snapshot of the bus listener counts for ``events``."""
    listeners = hass.bus.async_listeners()
    return {event: listeners.get(event, 0) for event in events}


async def _setup_entry(hass, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert ok, "entry setup failed"
    assert mock_entry.state is ConfigEntryState.LOADED


# --------------------------------------------------------------------------- #
# A. setup/unload leaves listener counts at baseline (CORE-06 / CORE-13)
# --------------------------------------------------------------------------- #
async def test_setup_and_unload_leave_listener_counts_at_baseline(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    # Before the fix: ``async_listen_once(EVENT_HOMEASSISTANT_STOP, ...)``
    # and ``..._STARTED`` discarded their unsubcriptions, so both counts
    # grew by one on *every* setup and never came back.  The
    # homeassistant_* counts are also legitimately inflated by other
    # components while the platforms register, so the entry's own part
    # is asserted as a delta, and the purely entry-scoped events
    # (loxone_event, the two send events) as absolutes.
    baseline = _listener_counts(hass)

    await _setup_entry(hass, mock_entry)
    await hass.async_block_till_done()

    mid = _listener_counts(hass)
    # the entry adds its two outbound-command subscriptions while
    # loaded, *no* STARTED listener is registered by the entry
    # (``async_at_started`` already fired and ran its callback without
    # registering, because the test hass is running), and there is a
    # STOP contribution of at least its one-shot.  Other components may
    # load during setup and bring their own homeassistant_* listeners;
    # those persist after unload, hence the mid-based assertions below.
    assert mid[EVENT_HOMEASSISTANT_STOP] >= baseline[EVENT_HOMEASSISTANT_STOP] + 1
    assert mid[SENDDOMAIN] >= baseline[SENDDOMAIN] + 1
    assert mid[SECUREDSENDDOMAIN] >= baseline[SECUREDSENDDOMAIN] + 1
    assert mid[EVENT] >= 1  # the entities' loxone_event subscriptions

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.NOT_LOADED
    after = _listener_counts(hass)
    # the entry's listeners are all gone again: the send subscriptions
    # and the entity loxone_event subscriptions are back at baseline
    # (no cross-test memory: every listener is either an entry hook or
    # registered via async_on_remove), the entry's own one-shot STOP
    # listener is gone, and nothing the entry did to the STARTED count
    # survived (flat mid->after).
    assert after[EVENT] == baseline[EVENT]
    assert after[SENDDOMAIN] == baseline[SENDDOMAIN]
    assert after[SECUREDSENDDOMAIN] == baseline[SECUREDSENDDOMAIN]
    assert after[EVENT_HOMEASSISTANT_STOP] == mid[EVENT_HOMEASSISTANT_STOP] - 1
    assert after[EVENT_HOMEASSISTANT_STARTED] == mid[EVENT_HOMEASSISTANT_STARTED]

    # the coordinator object itself is gone from hass.data[DOMAIN]
    assert mock_entry.entry_id not in hass.data.get(DOMAIN, {})


# --------------------------------------------------------------------------- #
# A'. the STOP/STARTED listeners do not accumulate across reloads (CORE-06)
# --------------------------------------------------------------------------- #
async def test_stop_and_started_listeners_do_not_accumulate_across_reloads(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    baseline = _listener_counts(hass)
    assert baseline[EVENT] == 0
    await _setup_entry(hass, mock_entry)
    mid = _listener_counts(hass)

    for _ in range(3):
        assert await hass.config_entries.async_reload(mock_entry.entry_id)
        await hass.async_block_till_done()

    after = _listener_counts(hass)
    # Hand-derived: every tracked event count must be *flat* across the
    # three reloads — each cycle unregisters exactly what the previous
    # one registered, and no other component loads/unloads during a
    # config-entry reload.  Before the fix both the STOP and STARTED
    # counts grew by one per reload here (listen_once per setup,
    # unsubscribe discarded), so the flatness is precisely the bug.
    assert after == mid, f"listener counts drifted across 3 reloads: mid={mid} after={after}"


# --------------------------------------------------------------------------- #
# B. five reloads leave no extra tasks (CORE-03 / CORE-13)
# --------------------------------------------------------------------------- #
async def test_five_reloads_leave_no_extra_tasks(hass, mock_connection, mock_entry, enable_custom_integrations) -> None:
    await _setup_entry(hass, mock_entry)
    # The session task is stored in the entry's tracked task list
    # (config_entry.async_create_background_task) — nothing collected,
    # nothing dangling (CORE-03).
    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    session_task = coordinator.listening_task
    assert session_task is not None and not session_task.done()
    assert mock_entry.entry_id in hass.data.get(DOMAIN, {})

    baseline = set(asyncio.all_tasks())
    for _ in range(5):
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_entry.state is ConfigEntryState.LOADED

    # let cancellations / done-callbacks settle
    for _ in range(5):
        await asyncio.sleep(0)

    remaining = set(asyncio.all_tasks()) - baseline
    # hand-derived: exactly one extra pending task survives — the
    # session supervisor for the *current* (fifth) setup.  Every
    # earlier task was cancelled + awaited at unload; the pre-fix code
    # abandoned them (the local ``hass.async_create_background_task``
    # reference was not held anywhere unload could find, and the
    # untracked create_task sends in the bus listener were the same
    # class of bug, RUF006 TODO(WP-3.1)).
    assert len(remaining) == 1, f"expected exactly 1 extra task after 5 reloads, got {len(remaining)}: {remaining}"
    assert all(not t.done() for t in remaining)
    assert mock_entry.state is ConfigEntryState.LOADED
    new_coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert new_coordinator.listening_task is not None and not new_coordinator.listening_task.done()


# --------------------------------------------------------------------------- #
# C. 503 on open -> ConfigEntryNotReady + close awaited (CORE-09, kept)
# --------------------------------------------------------------------------- #
async def test_unavailable_on_open_raises_not_ready_and_closes_api(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    open_calls: list[int] = []
    close_calls: list[int] = []

    async def failing_open(self, session=None):
        open_calls.append(1)
        raise LoxoneServiceUnAvailableError("Service Unavailable (503)")

    async def counting_close(self, *args, **kwargs):
        close_calls.append(1)

    mock_entry.add_to_hass(hass)
    with (
        patch.object(LoxoneConnection, "open", new=failing_open),
        patch.object(LoxoneConnection, "close", new=counting_close),
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY, (
        "a 503 during open must be retried by HA (ConfigEntryNotReady), never park the entry in setup_error"
    )
    assert open_calls == [1]
    assert close_calls == [1], "the (partially opened) API handle must be closed on the failed branch"


# --------------------------------------------------------------------------- #
# D. option change -> reload of that entry scheduled (CORE-29)
# --------------------------------------------------------------------------- #
async def test_options_change_schedules_reload_of_its_entry(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    await _setup_entry(hass, mock_entry)
    before = {**mock_entry.options}

    scheduled: list[str] = []
    with patch.object(
        hass.config_entries,
        "async_schedule_reload",
        side_effect=lambda entry_id: scheduled.append(entry_id),
    ):
        # The manager method is a synchronous callback.
        changed = hass.config_entries.async_update_entry(mock_entry, options={**before, "port": 8081})
        assert changed, "port actually changed"
        await hass.async_block_till_done()

    # hand-derived: exactly the owning entry's id, exactly once.
    assert scheduled == [mock_entry.entry_id]


# --------------------------------------------------------------------------- #
# E. stub platforms / PLATFORM_SCHEMA deleted, no load_platform loop (CORE-14)
# --------------------------------------------------------------------------- #
def test_stub_platforms_and_dead_schemas_are_deleted() -> None:
    import custom_components.loxone

    # The six stub platforms from CORE-14 plus the alarms stub that feeds
    # nothing (its YAML schema is gone already, only the dead
    # ``async_setup_platform`` remained in the file).
    for name in ("switch", "button", "number", "text", "select", "scene", "alarm_control_panel"):
        module = importlib.import_module(f"custom_components.loxone.{name}")
        assert not hasattr(module, "async_setup_platform"), f"{name} still has a stub async_setup_platform"
    # ...and both PLATFORM_SCHEMA leftovers — including climate, whose
    # schema (a) had no platform and (b) was fed by the stub below.
    for name in ("alarm_control_panel", "climate"):
        module = importlib.import_module(f"custom_components.loxone.{name}")
        assert not hasattr(module, "PLATFORM_SCHEMA"), f"{name} still carries a PLATFORM_SCHEMA that feeds nothing"

    # ...and the discovery loop itself is gone from entry setup (a
    # comment may mention the name, only the call is forbidden).
    source = inspect.getsource(custom_components.loxone.async_setup_entry)
    assert "async_load_platform(" not in source
    assert "async_forward_entry_setups" in source
    # the coordinator override of async_config_entry_first_refresh is
    # gone too (CORE-12): the stock first-refresh runs _async_setup.
    assert not hasattr(LoxoneCoordinator, "async_config_entry_first_refresh") or (
        "async_config_entry_first_refresh" not in vars(LoxoneCoordinator)
    )


# --------------------------------------------------------------------------- #
# F. coordinator first-refresh machinery (CORE-12)
# --------------------------------------------------------------------------- #
async def test_coordinator_uses_stock_first_refresh(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    await _setup_entry(hass, mock_entry)
    coordinator = hass.data[DOMAIN][mock_entry.entry_id]

    # ``config_entry`` was passed to super() (the ContextVar fallback is
    # deprecated) and data/last_update_success were set by the stock
    # first-refresh — previously the override meant they were set
    # (coordinator.data stayed None only by accident and
    # last_update_success was never touched by a real refresh).
    assert coordinator.config_entry is mock_entry
    assert coordinator.last_update_success is True
    assert coordinator.data is None  # _async_update_data returns None
    assert "async_config_entry_first_refresh" not in vars(LoxoneCoordinator)


async def test_coordinator_passes_config_entry_to_super(hass, mock_entry) -> None:
    """CORE-12: no deprecation report from the coordinator constructor."""
    from homeassistant.helpers import frame

    with patch.object(
        frame,
        "report_usage",
        side_effect=AssertionError(
            "DataUpdateCoordinator raised the config_entry deprecation report; pass config_entry= explicitly"
        ),
    ):
        LoxoneCoordinator(hass, mock_entry)


# --------------------------------------------------------------------------- #
# G. an empty stored token is not sent (CORE-10)
# --------------------------------------------------------------------------- #
async def test_empty_stored_token_is_not_sent_to_connection(
    hass, loxapp3, mock_entry, enable_custom_integrations
) -> None:
    import custom_components.loxone.coordinator as coordinator_module

    class _CapturingConn:
        def __init__(self, **kwargs):
            _CapturingConn.kwargs = kwargs
            self.structure_file = loxapp3
            self.connected = True

        async def open(self, session=None):
            return self

    entry = MockConfigEntry(
        domain="loxone",
        version=4,
        # exactly what stop_event used to persist in the unfixed
        # pre-WP era: an *empty* token alongside the rest of the entry
        # data — must not reach LoxoneConnection (CORE-10).
        data={"token": "", "hash_alg": "SHA256", "valid_until": 1234567},
        options=dict(mock_entry.options),
        unique_id="TEST-SERIAL-0002",
    )
    entry.add_to_hass(hass)

    coordinator = LoxoneCoordinator(hass, entry)
    with (
        patch.object(coordinator_module, "LoxoneConnection", _CapturingConn),
        patch.object(coordinator_module, "async_get_clientsession", return_value=None),
    ):
        await coordinator._async_setup()

    assert "token" not in _CapturingConn.kwargs, "an empty ('' ) stored token was passed through to LoxoneConnection"
    assert coordinator.api is not None
    assert coordinator.miniserver is not None

    # and a *real* token must still be forwarded (regression guard):
    entry2 = MockConfigEntry(
        domain="loxone",
        version=4,
        data={"token": "abc123token", "hash_alg": "SHA256", "valid_until": 1234567},
        options=dict(mock_entry.options),
        unique_id="TEST-SERIAL-0003",
    )
    entry2.add_to_hass(hass)
    coordinator2 = LoxoneCoordinator(hass, entry2)
    with (
        patch.object(coordinator_module, "LoxoneConnection", _CapturingConn),
        patch.object(coordinator_module, "async_get_clientsession", return_value=None),
    ):
        await coordinator2._async_setup()
    assert _CapturingConn.kwargs.get("token") == entry2.data


# --------------------------------------------------------------------------- #
# B'. unload failure keeps the entry in a retryable state (CORE-13)
# --------------------------------------------------------------------------- #
async def test_failed_platform_unload_keeps_entry_resources(
    hass, mock_connection, mock_entry, enable_custom_integrations
) -> None:
    """CORE-13: unload = platforms first, cleanup only on success.

    Before the fix the coordinator was cleaned up, ``hass.data[DOMAIN]``
    popped and the services removed *before* the platforms were
    unloaded, and a ``False`` result was discarded — leaving a zombie
    LOADED entry with a dead connection.
    """
    await _setup_entry(hass, mock_entry)

    with patch.object(
        type(hass.config_entries),
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        ok = await hass.config_entries.async_unload(mock_entry.entry_id)

    assert ok is False
    assert mock_entry.state is ConfigEntryState.FAILED_UNLOAD
    # nothing was cleaned up: the coordinator still owns live resources
    assert mock_entry.entry_id in hass.data.get(DOMAIN, {})
    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.listening_task is not None and not coordinator.listening_task.done()
    # and the services are still registered
    assert hass.services.has_service(DOMAIN, "reload")
    assert hass.services.has_service(DOMAIN, "sync_areas")

    # Leave no FAILED_UNLOAD residue for the harness teardown.
    assert await hass.config_entries.async_remove(mock_entry.entry_id)
