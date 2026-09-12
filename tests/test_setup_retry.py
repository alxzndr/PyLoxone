"""WP-1.5 regression tests: a 401 during setup must retry, not die (CORE-09).

Incident 2026-09-02 (see ``2026-09-02-pyloxone-401-setup-error.md`` at the
repo root): a Miniserver reboot answered 401 during the short boot window
after its HTTP server came back, ``async_setup_entry`` hit the
``LoxoneUnauthorisedError -> return False`` branch, and the entry sat in
``setup_error`` for 10.5 hours while every *other* failure type retried.

Acceptance criteria covered here:
  1. 401 twice then success  -> entry reaches LOADED after HA's own retries
     (retry timers advanced with ``async_fire_time_changed``), and
     ``api.close`` was awaited exactly once per *failed* attempt.
  2. 401 forever  -> entry stays SETUP_RETRY (never SETUP_ERROR), and after
     the fifth attempt (>= 5 min after the first) an ERROR record containing
     the word "credentials" exists.
  3. 503 still raises ConfigEntryNotReady (entry lands in SETUP_RETRY, not
     setup_error).

The pure-function tests below assert hand-derived literals (e.g. a failure at
monotonic 1000 followed by one at 1100 must still report first_failure_time
== 1000) so they cannot degenerate into a restatement of the implementation.

Before the fix, criterion 2 was demonstrably violated: a script that fails
``LoxoneConnection.open`` with 401 and fires one HA retry timer left the
entry in ``ConfigEntryState.SETUP_ERROR`` — *before* the change.  After the
change the same sequence reaches LOADED (criterion 1) or keeps retrying.

Note on the environment: HA runs a failed entry's retry through
``async_create_background_task`` (config_entries ``_async_setup_again``),
which ``async_block_till_done()`` does not wait for, and its backoff is
real-time (5 s, doubling per failure).  The harness therefore polls
observable state while re-firing the retry timer every 5 s.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from unittest.mock import Mock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.loxone import (
    AUTH_RETRY_MAX_ATTEMPTS,
    AUTH_RETRY_MIN_ELAPSED_SECONDS,
    _clear_auth_failure,
    _record_auth_failure,
    _should_escalate_auth_failure,
)
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.pyloxone_api.exceptions import (
    LoxoneServiceUnAvailableError,
    LoxoneUnauthorisedError,
)

# HA schedules an entry retry ~5-80 s (doubling) after a
# ConfigEntryNotReady failure; an 11 sim-minute fire covers every backoff
# step, and across attempts it exceeds the 5-minute escalation window
# measured from the *first* failure.
RETRY_STEP = datetime.timedelta(minutes=11)

# caplog is attached to the root logger, so a narrowly-targeted logger name
# does not include the integration's child loggers (.coordinator,
# .pyloxone_api).  "lo" matches neither "homeassistant.*" nor our modules,
# so it captures exactly the Loxone integration's own loggers.
CAPLOG_TARGET = "lo"


def _fire_retry(hass) -> None:
    """Advance HA's clock far enough that the pending entry-retry timer fires."""
    async_fire_time_changed(hass, dt_util.utcnow() + RETRY_STEP)


async def _wait_until(hass, predicate, *, message="condition", seconds=90.0) -> None:
    """Poll (waking the loop) until ``predicate()`` is true.

    Entry retries run as *background* tasks that ``async_block_till_done()``
    does not wait for, so the observable condition is polled instead of
    relying on a barrier.  The pending retry timer is re-fired every 5 s so
    the next attempt is not left waiting on a real-time backoff of up to
    ~80 s (the rescheduled timer appears only in the *next* fire's snapshot).
    """
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


def _seed_connection(self, loxapp3):
    """Same seeding the WP-0.2 ``mock_connection`` fixture performs on a
    successful ``open`` (structure file + an "open" websocket protocol)."""
    self.structure_file = loxapp3
    self.miniserver_version = loxapp3.get("softwareVersion")
    self.connected = True
    self.connection = Mock()
    self.connection.protocol.state.name = "OPEN"
    self._session_key = b"\x00" * 32
    self.event_bus = None
    return self


# --------------------------------------------------------------------------- #
# Pure-function tests for the retry bookkeeping helpers
# --------------------------------------------------------------------------- #


class _FakeHass:
    """Just the ``hass.data`` slice the helpers touch."""

    def __init__(self):
        self.data = {}


class _FakeEntry:
    entry_id = "fake-entry"


def test_auth_failure_counter_counts_and_keeps_first_timestamp():
    """Count increments per failure; the first-failure timestamp is sticky.

    Hand-derived: failures at 1000/1100/1200 -> count 1, 2, 3 and the first
    timestamp must stay 1000 for all three readings (not 1200).
    """
    hass = _FakeHass()
    entry = _FakeEntry()

    count, first = _record_auth_failure(hass, entry, now=1000.0)
    assert (count, first) == (1, 1000.0)
    count, first = _record_auth_failure(hass, entry, now=1100.0)
    assert (count, first) == (2, 1000.0)
    count, first = _record_auth_failure(hass, entry, now=1200.0)
    assert (count, first) == (3, 1000.0)


def test_auth_failure_counter_is_scoped_per_entry():
    """Two entries failing interleave independently: A=1, B=1, B=2, A=2."""
    hass = _FakeHass()
    entry_a = type("EntryA", (), {"entry_id": "entry-a"})()
    entry_b = type("EntryB", (), {"entry_id": "entry-b"})()

    assert _record_auth_failure(hass, entry_a, now=1000.0) == (1, 1000.0)
    assert _record_auth_failure(hass, entry_b, now=1001.0) == (1, 1001.0)
    assert _record_auth_failure(hass, entry_b, now=1002.0) == (2, 1001.0)
    assert _record_auth_failure(hass, entry_a, now=1003.0) == (2, 1000.0)


def test_clear_auth_failure_resets_counter_and_first_timestamp():
    """A successful setup resets the counter: the next failure is attempt 1
    with a fresh first-failure timestamp (hand-derived: 5000, not the
    cleared 1000)."""
    hass = _FakeHass()
    entry = _FakeEntry()

    _record_auth_failure(hass, entry, now=1000.0)
    _record_auth_failure(hass, entry, now=1100.0)
    _clear_auth_failure(hass, entry)

    assert _record_auth_failure(hass, entry, now=5000.0) == (1, 5000.0)


def test_escalation_bounds():
    """Escalation needs BOTH bounds: >= 5 consecutive failures AND >= 300 s
    since the first (hand-derived against the plan's 5 attempts / 5 minutes)."""
    assert (AUTH_RETRY_MAX_ATTEMPTS, AUTH_RETRY_MIN_ELAPSED_SECONDS) == (5, 300)

    assert _should_escalate_auth_failure(4, 0.0, 100000.0) is False  # long, but only 4 attempts
    assert _should_escalate_auth_failure(5, 0.0, 299.0) is False  # 5 attempts, window not over
    assert _should_escalate_auth_failure(5, 0.0, 300.0) is True  # both bounds met exactly
    assert _should_escalate_auth_failure(5, 1000.0, 1299.0) is False  # shifted window
    assert _should_escalate_auth_failure(5, 1000.0, 1300.0) is True
    assert _should_escalate_auth_failure(9, 0.0, 301.0) is True  # beyond the bounds


# --------------------------------------------------------------------------- #
# HA-harness tests (WP-0.2 fixtures; ``LoxoneConnection.open`` is scripted)
# --------------------------------------------------------------------------- #


async def test_transient_401_retries_and_recovers(hass, loxapp3, mock_connection, mock_entry, caplog) -> None:
    """401 twice then success -> entry reaches LOADED and ``api.close`` was
    awaited exactly once per failed attempt (twice total, never on success)."""
    caplog.set_level(logging.WARNING, CAPLOG_TARGET)
    open_calls = []
    close_calls = []

    async def scripted_open(self, session=None):
        open_calls.append(1)
        if len(open_calls) <= 2:  # first two attempts: boot-window 401
            raise LoxoneUnauthorisedError("Unauthorized (401)")
        return _seed_connection(self, loxapp3)

    async def counting_close(self, *args, **kwargs):
        close_calls.append(1)
        return None

    with (
        patch.object(LoxoneConnection, "open", new=scripted_open),
        patch.object(LoxoneConnection, "close", new=counting_close),
    ):
        mock_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        # The boot-window 401 must be treated as retryable, not fatal.
        assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.SETUP_RETRY
        assert open_calls == [1]
        assert close_calls == [1]

        # Attempts 2 (previously fatal) and 3 (success), then platforms.
        await _wait_until(hass, lambda: len(open_calls) >= 3, message="attempts 2 and 3 to run")
        await _wait_until(
            hass,
            lambda: hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED,
            message="the entry to reach LOADED",
        )

    entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert len(open_calls) == 3, "two failed attempts followed by the successful one"
    assert close_calls == [1, 1], "api.close awaited exactly once per FAILED attempt"
    # One retry WARNING per failed attempt (attempts 1 and 2) and no
    # escalation ERROR after just two failures.
    # Match the retry message itself, not any WARNING containing "401": on a
    # slow CI runner asyncio logs "Executing <Task ...> took N seconds" at
    # WARNING, and that task's name is this test's name, which contains "401".
    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "answered 401 during setup" in r.getMessage()
    ]
    assert len(warnings) == 2, f"expected two retry WARNINGs, got: {[r.getMessage() for r in warnings]}"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR and "credentials" in r.getMessage()]


async def test_persistent_401_escalates_to_reauth(
    hass, loxapp3, mock_connection, mock_entry, caplog, monkeypatch
) -> None:
    """401 forever -> attempts inside the window stay SETUP_RETRY; the
    fifth attempt (>= 5 min after the first) raises
    ``ConfigEntryAuthFailed`` so the entry lands in ``SETUP_ERROR`` and
    HA starts the reauth flow (plus the ``config_entry_reauth`` repair
    issue).

    This replaces the WP-1.5 escalation branch ("keep raising
    ConfigEntryNotReady and log an ERROR after 5 attempts / 5 min") with
    the reauth path (CORE-09 / WP-3.4).  401s inside the transient
    window still retry: attempts 1..4 keep the entry in SETUP_RETRY.

    The integration's clock (``custom_components.loxone.time``) is
    replaced with a fake tracked by this test: wall-clock time can never
    span 5 minutes inside a test, and HA's retry timer does not advance
    Python's clock either.  Each newly-observed failed attempt steps the
    fake clock 660 s (hand-derived: attempts 1..5 are then recorded at
    1000/1660/2320/2980/3640; the fifth sits 2640 s after the first,
    over the 300 s window).  The *event loop* clock is advanced
    separately by the periodic fires inside ``_wait_until``.
    """
    from homeassistant.helpers import issue_registry as ir

    import custom_components.loxone as loxone_module

    caplog.set_level(logging.WARNING, CAPLOG_TARGET)

    class _FakeClock:
        """Monotonic-clock stand-in; starts at 1000.0, +660 s per failure."""

        now = 1000.0

        @classmethod
        def monotonic(cls):
            return cls.now

    monkeypatch.setattr(loxone_module, "time", _FakeClock)
    open_calls = []
    seen = [0]  # attempts observed so far (for the fake-clock step)

    async def failing_open(self, session=None):
        open_calls.append(1)
        grown = len(open_calls) - seen[0]
        if grown > 0:
            seen[0] = len(open_calls)
            _FakeClock.now += 660.0 * grown
        raise LoxoneUnauthorisedError("Unauthorized (401)")

    with patch.object(LoxoneConnection, "open", new=failing_open):
        mock_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
        # Attempt 1 is inside the transient window -> still setup retry.
        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert open_calls == [1]
        # Attempts 2..4 are still inside the window (< 5 attempts), so
        # every retry keeps the entry in SETUP_RETRY.
        for target in (2, 3, 4):
            await _wait_until(hass, lambda t=target: len(open_calls) >= t, message=f"attempt {target} to run")
            await _wait_until(
                hass,
                lambda e=entry: e.state is ConfigEntryState.SETUP_RETRY,
                message="the entry to settle in the retry state",
            )
        # The fifth attempt (>= 300 s after the first) escalates.
        await _wait_until(hass, lambda: len(open_calls) >= 5, message="the fifth attempt to run")
        await hass.async_block_till_done()

    entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
    assert open_calls == [1, 1, 1, 1, 1]
    assert entry.state is ConfigEntryState.SETUP_ERROR

    # HA started the reauth flow for this entry.
    progress = hass.config_entries.flow.async_progress()
    reauth_flows = [
        f
        for f in (progress.values() if isinstance(progress, dict) else progress)
        if isinstance(f, dict)
        and f.get("handler") == "loxone"
        and (f.get("context") or {}).get("source") == "reauth"
        and (f.get("context") or {}).get("entry_id") == mock_entry.entry_id
    ]
    assert reauth_flows, f"expected a reauth flow in progress, got: {progress!r}"

    # HA registers the config_entry_reauth repair issue for it.
    issue = ir.async_get(hass).async_get_issue("homeassistant", f"config_entry_reauth_loxone_{mock_entry.entry_id}")
    assert issue is not None

    # The fifth failure (2640 s after the first) must have escalated to an
    # ERROR pointing at the stored credentials.
    escalation = [r for r in caplog.records if r.levelno >= logging.ERROR and "credentials" in r.getMessage()]
    assert escalation, (
        "after 5 attempts >= 5 min apart an ERROR pointing at credentials is required, saw: "
        + repr([r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING])[:400]
    )
    # The old fatal 401 message must be gone.
    assert not any("Please check username and password." in r.getMessage() for r in caplog.records)


async def test_503_still_retries_and_closes(hass, loxapp3, mock_connection, mock_entry) -> None:
    """The 503 path keeps its existing behaviour: ConfigEntryNotReady (entry
    lands in SETUP_RETRY, never setup_error) and the API handle is closed on
    every failed attempt."""
    open_calls = []
    close_calls = []

    async def failing_open(self, session=None):
        open_calls.append(1)
        raise LoxoneServiceUnAvailableError("Service Unavailable (503)")

    async def counting_close(self, *args, **kwargs):
        close_calls.append(1)
        return None

    with (
        patch.object(LoxoneConnection, "open", new=failing_open),
        patch.object(LoxoneConnection, "close", new=counting_close),
    ):
        mock_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        entry = hass.config_entries.async_get_entry(mock_entry.entry_id)
        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert open_calls == [1]
        assert close_calls == [1], "the 503 path must close the API handle on every failure"

        # One retry cycle: still SETUP_RETRY afterwards.
        await _wait_until(hass, lambda: len(open_calls) >= 2, message="the retry attempt to run")
        await _wait_until(hass, lambda: entry.state is ConfigEntryState.SETUP_RETRY, message="the entry to settle")

    assert entry.state is ConfigEntryState.SETUP_RETRY
