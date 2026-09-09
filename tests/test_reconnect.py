"""WP-2.3 regression tests: in-place reconnect with availability.

* API-09  a transient session failure (closed websocket, stale token,
          Miniserver restart) must be recovered *inside*
          ``LoxoneConnection`` -- re-open, re-auth, re-``enablebinstatusupdate``
          -- with exponential backoff, instead of propagating out and
          forcing the integration to reload the config entry.
* CORE-28  entities keep their identity through an outage: their
          ``available`` flips off/on from the coordinator, no entity is
          removed from the registry, and after the reconnect they report
          the state they had before the drop.
* CORE-05  reload-on-error is gone: five simulated drops leave the config
          entry's reload count at zero (no ``async_schedule_reload``, no
          ``async_unload`` of the entry, same coordinator object).

The pure-unit part drives a real ``LoxoneConnection`` with a scripted
``start_listening``/``open``/``close``; the harness part uses the WP-0.2
fixtures and drops the stub socket mid-session via ``mock_connection.drop()``.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.pyloxone_api.connection import LoxoneConnection, reconnect_backoff_seconds
from custom_components.loxone.pyloxone_api.exceptions import (
    LoxoneConnectionClosedOk,
    LoxoneUnauthorisedError,
)

# LoxAPP3 fixture (tests/fixtures/LoxAPP3.json): the only "Switch" control,
# with its action uuid and the "active" state-uuid hand-transcribed from the
# fixture (the literals the availability assertions reason about).
SWITCH_ACTION_UUID = "63746c3a-0157-9766-ffff-e6720526f6f6000087"
SWITCH_STATE_ACTIVE_UUID = "36333734-0158-9336-ffff-d303135372d3000088"


async def _wait_until(hass, predicate, *, message: str, seconds: float = 20.0) -> None:
    """Wake the loop and poll until ``predicate()`` or raise on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for: {message}")


async def _pump(predicate, *, seconds: float = 10.0) -> None:
    """Spin the loop until ``predicate()`` -- or raise."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("timed out waiting for scripted connection state")


# --------------------------------------------------------------------------- #
# Pure-unit: the LoxoneConnection.run reconnect supervisor
# --------------------------------------------------------------------------- #


def test_reconnect_backoff_schedule_literals():
    """The plan's min(2**n, 300) schedule with a 1 s base."""
    assert reconnect_backoff_seconds(0) == 1.0
    assert reconnect_backoff_seconds(1) == 2.0
    assert reconnect_backoff_seconds(2) == 4.0
    assert reconnect_backoff_seconds(3) == 8.0
    assert reconnect_backoff_seconds(8) == 256.0  # last value below the cap
    assert reconnect_backoff_seconds(9) == 300.0  # 512 clamps to the cap
    assert reconnect_backoff_seconds(100) == 300.0


def _make_scripted_connection(failure, *, immediate=False):
    """A real ``LoxoneConnection`` whose session lifecycle is scripted.

    Each supervised pass: re-``open`` when the socket is gone (like the
    real ``start_listening`` fallback), signal *authenticated* (the moment
    ``run`` must flip ``on_state(True)`` -- enablebinstatusupdate), then
    stay live until either the test drops the session or ``close()`` is
    called; at the end the scripted ``failure`` is raised.
    """
    conn, state, counter = (
        LoxoneConnection(host="miniserver.local", username="admin", password="secret"),
        [],
        {"opens": 0, "closes": 0, "sessions": 0},
    )
    conn._session_key = b"\x00" * 32
    # Each supervised session owns its own "alive" event, so a drop can
    # only ever wake the session it targets (no re-arm race).
    live_events: list[asyncio.Event] = []

    def drop_current() -> None:
        for e in live_events:
            if not e.is_set():
                e.set()

    async def on_state(connected: bool) -> None:
        state.append(connected)

    async def fake_open(session=None):
        counter["opens"] += 1
        conn.connection = object()
        return conn

    async def fake_close(*args, **kwargs):
        counter["closes"] += 1
        conn.connection = None
        # Like the real close(): teardown latches the instance.
        conn._closed = True
        conn._shutdown_event.set()

    async def fake_listen(callback=None):
        if conn.connection is None:
            await conn.open(None)
        counter["sessions"] += 1
        if immediate:
            # The session fails before authentication ever completed
            # (e.g. a 401 on the first auth response).
            raise failure
        conn._authenticated_event.set()
        alive = asyncio.Event()
        live_events.append(alive)
        drop_waiter = asyncio.create_task(alive.wait())
        shutdown_waiter = asyncio.create_task(conn._shutdown_event.wait())
        try:
            await asyncio.wait({drop_waiter, shutdown_waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (drop_waiter, shutdown_waiter):
                t.cancel()
        raise failure

    conn.open = fake_open
    conn.close = fake_close
    conn.start_listening = fake_listen
    return conn, on_state, drop_current, state, counter


async def _finish(run_task):
    if not run_task.done():
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task


async def test_run_reconnects_in_place_after_closed_socket():
    """Clean socket death: on_state(False) exactly once, then a re-open and
    on_state(True) -- on the SAME instance, with the run loop still alive."""
    conn, on_state, drop_current, state, counter = _make_scripted_connection(LoxoneConnectionClosedOk("server closed"))
    run_task = asyncio.create_task(conn.run(on_state, base_delay=0.001, max_delay=0.05))
    try:
        await _pump(lambda: state == [True])
        drop_current()
        await _pump(lambda: state == [True, False, True])
        # In-place reopen on the same instance; the run loop never exited.
        assert counter["opens"] == 2  # initial open + one reopen after the drop
        assert conn.connection is not None
        assert not run_task.done()
    finally:
        await _finish(run_task)


async def test_run_propagates_unauthorised_without_retry():
    """Credentials rejected: not recoverable -- propagate, no second
    session, and the loss IS signalled."""
    conn, on_state, drop_current, state, counter = _make_scripted_connection(
        LoxoneUnauthorisedError("401 during auth"), immediate=True
    )
    run_task = asyncio.create_task(conn.run(on_state, base_delay=0.001, max_delay=0.05))
    with pytest.raises(LoxoneUnauthorisedError):
        await asyncio.wait_for(run_task, timeout=2.0)
    assert counter["sessions"] == 1  # no retry after credentials were refused
    assert state == [False]
    assert counter["closes"] == 1


async def test_run_propagates_programming_errors_without_retry():
    """A bug in our own code (``AttributeError`` raised mid-session) is
    NOT a connection failure: ``run()`` must tear the session down,
    sign the outage off, and PROPAGATE the error instead of re-spawning
    the same doomed session in a backoff loop -- which converts a loud
    crash into a silent hang (API-09/CORE-05)."""
    conn, on_state, drop_current, state, counter = _make_scripted_connection(
        AttributeError("'NoneType' object has no attribute 'open'")
    )
    run_task = asyncio.create_task(conn.run(on_state, base_delay=0.001, max_delay=0.05))
    with pytest.raises(AttributeError):
        # Give the session time to come up and drop before it must raise
        # (tight: a retry loop would simply re-arm and out-live any probe).
        await _pump(lambda: state == [True])
        drop_current()
        await asyncio.wait_for(run_task, timeout=2.0)
    assert counter["sessions"] == 1  # never retried
    assert counter["opens"] == 1
    # The lost session is not swallowed: outage signalled, instance torn
    # down, and the loop left behind the crashed session (nothing live
    # to reconnect to on a guaranteed-fatal error).
    assert state == [True, False]
    assert counter["closes"] == 1
    assert conn.connection is None


async def test_run_stops_after_close():
    """close() while the session is live (HA stop / entry unload) must end
    the run loop: the session disconnect reports False exactly once and no
    further session is started."""
    conn, on_state, drop_current, state, counter = _make_scripted_connection(
        LoxoneConnectionClosedOk("teardown closed the socket")
    )
    run_task = asyncio.create_task(conn.run(on_state, base_delay=0.001, max_delay=0.05))
    try:
        await _pump(lambda: state == [True] and counter["sessions"] == 1)
        await conn.close()
        await asyncio.wait_for(run_task, timeout=2.0)  # the loop must RETURN
        assert state == [True, False]
        assert counter["sessions"] == 1  # no new session after close
        assert counter["opens"] == 1
    finally:
        await _finish(run_task)


async def test_many_drops_reuse_the_instance():
    """Three drops: the loop never returns, and the state trace is exactly
    one False per outage and a True per recovered session."""
    conn, on_state, drop_current, state, counter = _make_scripted_connection(LoxoneConnectionClosedOk("server closed"))
    run_task = asyncio.create_task(conn.run(on_state, base_delay=0.001, max_delay=0.05))
    try:
        await _pump(lambda: state == [True])
        # Drop one at a time, re-arming the drop event only after the new
        # session is provably live (a cleared Event cannot be re-captured
        # by a wait that starts later).
        expected_sessions = 1
        for _ in range(3):
            drop_current()
            expected_sessions += 1
            expected_state = [True, False] * (expected_sessions - 1) + [True]
            await _pump(
                lambda state=state, expected_state=expected_state, counter=counter, expected_sessions=expected_sessions: (
                    state == expected_state and counter["sessions"] == expected_sessions
                )
            )
        assert counter["opens"] == 4  # initial + 3 reopens, same instance
        assert not run_task.done()
    finally:
        await _finish(run_task)


# --------------------------------------------------------------------------- #
# HA harness: drop the stub socket mid-session (WP-0.2 fixtures)
# --------------------------------------------------------------------------- #


async def _setup_entry(hass, mock_entry):
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_drop_unavailable_reconnect_restores_state(hass, loxapp3, mock_connection, mock_entry, caplog):
    """Drop the stub socket mid-session:

    * the fixture switch goes ``unavailable`` (CORE-28 coordinator flip),
    * no entity is removed from the registry,
    * after the in-place reconnect it is ``on`` again -- its previous
      state: the entity was never destroyed, only flipped
    * the entry never reloaded (same coordinator object, still LOADED).
    """
    caplog.set_level(logging.WARNING, "lo")
    entry = await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data  # CORE-31

    await _wait_until(hass, lambda: coordinator.connected is True, message="stub session to come up")

    reg = er.async_get(hass)
    entries_before = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert entries_before
    entity_ids_before = {e.entity_id for e in entries_before}

    # Drive a concrete state on the fixture switch ("active" state event).
    switch_entry = next(e for e in entries_before if e.unique_id == SWITCH_ACTION_UUID)
    assert switch_entry.domain == "switch"
    switch_id = switch_entry.entity_id
    assert hass.states.get(switch_id) is not None  # "unavailable" before any value
    mock_connection.feed(SWITCH_STATE_ACTIVE_UUID, True)
    await _wait_until(hass, lambda: hass.states.get(switch_id).state == "on", message="switch to turn on")

    # --- drop the stub socket mid-session --------------------------------
    mock_connection.drop()
    await _wait_until(
        hass, lambda: hass.states.get(switch_id).state == "unavailable", message="switch to go unavailable"
    )

    # No entity was removed (or recreated) from the registry.
    entries_mid = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert {e.entity_id for e in entries_mid} == entity_ids_before
    assert len(entries_mid) == len(entries_before)

    # --- the in-place reconnect brings everything back --------------------
    await _wait_until(hass, lambda: coordinator.connected is True, message="session to come back")
    await _wait_until(hass, lambda: hass.states.get(switch_id).state == "on", message="switch to be on again")

    # No reload of the entry: same coordinator object, same registry.
    assert mock_entry.runtime_data is coordinator  # CORE-31
    entries_after = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert {e.entity_id for e in entries_after} == entity_ids_before
    assert entry.state is ConfigEntryState.LOADED
    # The old reload mechanism must not have fired at all.
    assert not [r for r in caplog.records if "reloading Loxone integration" in r.getMessage().lower()]


async def test_five_drops_reload_count_stays_zero(hass, loxapp3, mock_connection, mock_entry):
    """The CORE-05 regression target: five simulated mid-session drops must
    leave the config entry's reload count at zero.

    "Reload count" is measured three independent ways: calls to
    ``async_schedule_reload``, calls to ``async_unload``, and the identity
    of the coordinator (a reload would replace it)."""
    entry = await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data  # CORE-31

    counts = {"schedule_reload": 0, "unload": 0}

    def counting_schedule(*args, **kwargs):
        counts["schedule_reload"] += 1
        raise AssertionError("async_schedule_reload must not be called during reconnect")

    def counting_unload(*args, **kwargs):
        counts["unload"] += 1
        raise AssertionError("entry must not be unloaded during reconnect")

    # Instance-level wrapping: the pre-fix reload storm ended in exactly
    # these bound methods (service -> handle_reload -> async_unload).
    hass.config_entries.async_schedule_reload = counting_schedule
    hass.config_entries.async_unload = counting_unload
    try:
        for i in range(5):
            assert coordinator.connected is True, f"session {i + 1} expected live before drop"
            mock_connection.drop()
            await _wait_until(hass, lambda: coordinator.connected is False, message="session to go down")
            await _wait_until(hass, lambda: coordinator.connected is True, seconds=15.0, message="session to come back")
        assert mock_connection.drops == 5
    finally:
        del hass.config_entries.async_schedule_reload
        del hass.config_entries.async_unload

    assert counts == {"schedule_reload": 0, "unload": 0}
    assert mock_entry.runtime_data is coordinator  # CORE-31
    assert entry.state is ConfigEntryState.LOADED
    reg = er.async_get(hass)
    assert er.async_entries_for_config_entry(reg, entry.entry_id)


async def test_entity_available_reflects_coordinator_state(hass):
    """The LoxoneEntity.available property combines the per-entity
    ``_attr_available`` (unchanged semantics) with the coordinator that
    the entity resolves through its platform's config entry."""
    from custom_components.loxone import LoxoneEntity

    class _FakeCoordinator:
        connected = True

    entity = LoxoneEntity(hass=hass, uuidAction="some-uuid", name="X")
    # No platform attached yet: no coordinator resolvable -> plain entity
    # semantics must not change.
    entity.platform = None
    assert entity.available is True

    # Resolved via the platform's config entry (CORE-31: the coordinator
    # lives on ``config_entry.runtime_data``).
    fake_entry = SimpleNamespace(entry_id="entry-x", runtime_data=_FakeCoordinator())
    entity.platform = SimpleNamespace(config_entry=fake_entry)
    assert entity.available is True

    # Per-entity availability is still effective ("value not yet seen").
    entity._attr_available = False
    assert entity.available is False
    entity._attr_available = True
    assert entity.available is True

    _FakeCoordinator.connected = False
    assert entity.available is False

    # Unresolvable platform -> back to plain entity semantics.
    entity.platform = None
    assert entity.available is True
