"""Token / auth lifecycle tests for ``pyloxone_api.connection``.

Pins the parts of the connection layer that handle credentials and tokens:

* the periodic refresh loop nested in ``start_listening`` -- its sleep
  schedule, the single ``getkey`` per cycle, the "did the key really
  change?" guard, the 15s key timeout, the send-error path, the failure
  escalation and every shutdown/cancel exit (API-14/16),
* ``_refresh_token`` -- the outgoing command shape per firmware
  generation, and the response handler's persistence side effects
  (API-12/17),
* ``_hash_credentials`` / ``_hash_token`` -- the Loxone auth hash
  construction (API-07/12/25).

Every expected value is a hand-written literal or derived here with
``hashlib``/``hmac``; nothing is produced by calling the code under test.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.loxone.pyloxone_api import connection as connection_mod
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.pyloxone_api.const import (
    CMD_GET_KEY,
    DELAY_CHECK_TOKEN_REFRESH,
    KEEP_ALIVE_PERIOD,
    MAX_REFRESH_DELAY,
    TOKEN_REFRESH_MAX_FAILURES,
)
from custom_components.loxone.pyloxone_api.exceptions import (
    LoxoneConnectionClosedOk,
    LoxoneTokenError,
)
from custom_components.loxone.pyloxone_api.loxone_token import LoxoneToken
from custom_components.loxone.pyloxone_api.message import TextMessage

CONNECTION_LOGGER = "custom_components.loxone.pyloxone_api.connection"

# Kept before the harness below patches the module-level names for the
# duration of a test, so the harness itself can still really sleep/wait.
_REAL_SLEEP = asyncio.sleep
_REAL_WAIT_FOR = asyncio.wait_for

# A 16-byte hex key, as the Miniserver sends it in a getkey/getkey2 response.
HEX_KEY = "00112233445566778899aabbccddeeff"


def make_connection(**overrides) -> LoxoneConnection:
    kwargs = {"host": "192.168.1.5", "username": "admin", "password": "secret", "port": 8080}
    kwargs.update(overrides)
    conn = LoxoneConnection(**kwargs)
    # A truthy session key so start_listening's key-exchange guard passes.
    conn._session_key = b"\x00" * 32
    return conn


class FakeWS:
    """Just enough of the websockets client for start_listening's key exchange."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.protocol = SimpleNamespace(state=SimpleNamespace(name="OPEN"))

    async def send(self, message, **kwargs) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        return None


class ScriptedToken:
    """Token stub whose ``seconds_to_expire()`` is scripted call by call.

    A list entry that is an exception instance is raised instead of
    returned; the last entry is reused once the script runs out.
    """

    def __init__(self, lifetimes, token: str = "TOKEN-1") -> None:
        self._lifetimes = list(lifetimes)
        self.token = token
        self.valid_until = 1893456000
        self.hash_alg = "SHA1"
        self.unsecure_password = False

    def seconds_to_expire(self) -> int:
        value = self._lifetimes.pop(0) if len(self._lifetimes) > 1 else self._lifetimes[0]
        if isinstance(value, BaseException):
            raise value
        return value


class ShutdownWhenAuthAwaited(asyncio.Event):
    """``_authenticated_event`` stand-in that requests shutdown when awaited.

    Lets the test hit the refresh loop's "shutdown arrived while we were
    waiting for authentication" break deterministically, with no sleeps.
    """

    def __init__(self, conn: LoxoneConnection) -> None:
        super().__init__()
        self._conn = conn

    async def wait(self) -> bool:
        self._conn._shutdown_event.set()
        return True


def _find_refresh_task(conn: LoxoneConnection) -> asyncio.Task | None:
    """The ``check_refresh_token`` task start_listening created."""
    for task in conn._pending_task:
        if "check_refresh_token" in getattr(task.get_coro(), "__qualname__", ""):
            return task
    return None


async def drive_refresh_loop(conn: LoxoneConnection, *, on_sleep=None) -> SimpleNamespace:
    """Run ``start_listening`` with the token-refresh loop as the only live task.

    * ``asyncio.sleep`` is replaced: the refresh loop's delays are recorded
      and return immediately, while the 30s keep-alive sleep parks forever
      so the keep-alive task cannot interfere.
    * ``asyncio.wait_for`` records the timeout the code asks for and
      shortens the protocol's 15s key wait to 50ms, so the timeout branch
      is reachable in a unit test (the asked-for value is asserted on).
    * the listen loop is stubbed with one that ends the session (clean
      close) only once shutdown was requested *and* the refresh loop has
      finished, so teardown can never race an assertion.

    Returns the recorded delays/timeouts and the exception that ended the
    session.
    """
    delays: list[float] = []
    timeouts: list[float | None] = []

    async def fake_sleep(delay, result=None):
        if delay == KEEP_ALIVE_PERIOD:
            await asyncio.Event().wait()  # park the keep-alive task for good
        delays.append(delay)
        if on_sleep is not None:
            on_sleep(delay)
        await _REAL_SLEEP(0)  # a scheduling point, but no wall-clock delay
        return result

    async def fake_wait_for(awaitable, *args, **kwargs):
        # ``timeout`` is deliberately not a named parameter (ASYNC109); the
        # code under test passes it as a keyword.
        requested = kwargs.get("timeout", args[0] if args else None)
        timeouts.append(requested)
        return await _REAL_WAIT_FOR(awaitable, 0.05 if requested == 15.0 else requested)

    async def listen_until_refresh_loop_ends(_callback, _connection):
        await conn._shutdown_event.wait()
        task = _find_refresh_task(conn)
        while task is not None and not task.done():
            await _REAL_SLEEP(0.001)
        raise LoxoneConnectionClosedOk("stub listen loop: session closed")

    conn._do_start_listening = listen_until_refresh_loop_ends
    error: BaseException | None = None
    with (
        patch.object(connection_mod.asyncio, "sleep", fake_sleep),
        patch.object(connection_mod.asyncio, "wait_for", fake_wait_for),
    ):
        try:
            await _REAL_WAIT_FOR(conn.start_listening(), timeout=5)
        except (LoxoneConnectionClosedOk, LoxoneTokenError) as exc:
            error = exc
    return SimpleNamespace(delays=delays, timeouts=timeouts, error=error)


def _authenticated_connection(lifetimes, *, key: str = "deadbeef") -> LoxoneConnection:
    """A connection that is past authentication, with a scripted token."""
    conn = make_connection()
    conn.connection = FakeWS()
    conn._authenticated_event.set()
    conn._token = ScriptedToken(lifetimes)
    conn._key = key
    return conn


def _warnings(caplog, needle: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and needle in r.getMessage()]


# --------------------------------------------------------------------------- #
# check_refresh_token: the periodic refresh loop (API-14/16)
# --------------------------------------------------------------------------- #
async def test_refresh_loop_sleeps_half_the_lifetime_then_refreshes(caplog) -> None:
    """Happy path: one getkey per cycle, a refresh once the key really changed,
    and a sleep of min(int(0.5 * lifetime), MAX_REFRESH_DELAY) with a floor of 1s."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([600, 400_000, 1])
    sent: list[tuple[str, bool]] = []
    refreshed_with: list[str] = []
    new_keys = iter(["aa" * 4, "bb" * 4, "cc" * 4])

    async def fake_send(command, encrypted=False):
        sent.append((command, encrypted))
        conn._key = next(new_keys)  # the getkey response lands...
        conn._key_update_event.set()  # ...and wakes the loop

    async def fake_refresh():
        refreshed_with.append(conn._key)
        if len(refreshed_with) == 3:
            conn._shutdown_event.set()  # end the loop after the third cycle

    conn._send_text_command = fake_send
    conn._refresh_token = fake_refresh

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    # 20s start-up delay, then per cycle: 600 -> 300; 400000 -> 200000 capped
    # to MAX_REFRESH_DELAY; 1 -> int(0.5) == 0 raised to the 1s floor.
    assert result.delays == [DELAY_CHECK_TOKEN_REFRESH, 300, MAX_REFRESH_DELAY, 1]
    assert sent == [(CMD_GET_KEY, False)] * 3
    assert refreshed_with == ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
    assert result.timeouts == [15.0, 15.0, 15.0]  # the protocol's key wait
    assert conn._refresh_failures == 0
    assert conn._key_update_event is None  # cleared in the cycle's finally
    # The human-readable countdown is hand-derived from d/h/m/s of each delay.
    assert "Seconds to refresh token: 0d 0h 5m 0s" in caplog.text
    assert "Seconds to refresh token: 1d 0h 0m 0s" in caplog.text
    assert "Seconds to refresh token: 0d 0h 0m 1s" in caplog.text


async def test_refresh_loop_does_not_refresh_when_the_key_did_not_change(caplog) -> None:
    """API-16: a getkey response that leaves the key unchanged is a failure,
    not a refresh trigger."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([120])
    sent: list[str] = []
    refreshes: list[int] = []

    async def fake_send(command, encrypted=False):
        sent.append(command)
        conn._key_update_event.set()  # signalled, but self._key is untouched
        conn._shutdown_event.set()  # exit after this cycle

    async def fake_refresh():
        refreshes.append(1)

    conn._send_text_command = fake_send
    conn._refresh_token = fake_refresh

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    assert result.delays == [DELAY_CHECK_TOKEN_REFRESH, 60]
    assert sent == [CMD_GET_KEY]
    assert refreshes == []  # no refresh sent with a stale key
    assert conn._key == "deadbeef"
    assert conn._refresh_failures == 1
    assert _warnings(caplog, "key was not updated despite event being set")


async def test_refresh_loop_times_out_waiting_for_the_new_key(caplog) -> None:
    """A getkey response that never arrives expires after the 15s wait, counts
    as one failure, and leaves the loop alive."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([120])
    refreshes: list[int] = []

    async def fake_send(command, encrypted=False):
        conn._shutdown_event.set()  # exit after this cycle; no key event set

    async def fake_refresh():
        refreshes.append(1)

    conn._send_text_command = fake_send
    conn._refresh_token = fake_refresh

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    assert result.timeouts == [15.0]  # the harness shortens it; the ask is pinned
    assert refreshes == []
    assert conn._refresh_failures == 1
    assert conn._key_update_event is None
    assert _warnings(caplog, "timed out waiting for new key (15s)")


async def test_refresh_loop_escalates_after_three_failed_key_requests(caplog) -> None:
    """API-16: TOKEN_REFRESH_MAX_FAILURES consecutive send failures raise
    LoxoneTokenError out of the session so the entry reloads."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([120])
    attempts: list[str] = []

    async def failing_send(command, encrypted=False):
        attempts.append(command)
        raise LoxoneConnectionClosedOk("socket went away")

    conn._send_text_command = failing_send

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneTokenError)
    assert attempts == [CMD_GET_KEY] * TOKEN_REFRESH_MAX_FAILURES
    assert conn._refresh_failures == TOKEN_REFRESH_MAX_FAILURES
    assert conn._key_update_event is None  # cleared on the send-error path
    assert result.delays == [DELAY_CHECK_TOKEN_REFRESH, 60, 60, 60]
    assert _warnings(caplog, "Token refresh task failed")


async def test_refresh_loop_breaks_when_shutdown_arrives_during_auth_wait() -> None:
    """Shutdown while blocked on authentication exits the loop without sending."""
    conn = _authenticated_connection([120])
    conn._authenticated_event = ShutdownWhenAuthAwaited(conn)
    sent: list[str] = []

    async def fake_send(command, encrypted=False):
        sent.append(command)

    conn._send_text_command = fake_send

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    assert result.delays == [DELAY_CHECK_TOKEN_REFRESH]  # no refresh sleep
    assert sent == []


async def test_refresh_loop_breaks_when_shutdown_arrives_during_refresh_sleep() -> None:
    """Shutdown during the (long) refresh sleep exits before requesting a key."""
    conn = _authenticated_connection([120])
    sent: list[str] = []

    async def fake_send(command, encrypted=False):
        sent.append(command)

    conn._send_text_command = fake_send

    def on_sleep(delay):
        if delay == 60:
            conn._shutdown_event.set()

    result = await drive_refresh_loop(conn, on_sleep=on_sleep)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    assert result.delays == [DELAY_CHECK_TOKEN_REFRESH, 60]
    assert sent == []


async def test_refresh_loop_waits_for_reauth_when_the_token_was_reset(caplog) -> None:
    """A token reset between cycles (seconds_to_expire raises ValueError) is not
    fatal: the cycle is skipped and the loop waits for the new token."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([ValueError("Cannot have valid_until == 0"), 120])
    sent: list[str] = []

    async def fake_send(command, encrypted=False):
        sent.append(command)

    conn._send_text_command = fake_send

    def on_sleep(delay):
        if delay == 60:
            conn._shutdown_event.set()

    result = await drive_refresh_loop(conn, on_sleep=on_sleep)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    # The reset cycle sleeps nothing at all; the next one computes 120/2.
    assert result.delays == [DELAY_CHECK_TOKEN_REFRESH, 60]
    assert sent == []
    assert "Token reset; waiting for re-authentication before refresh" in caplog.text


async def test_refresh_loop_backs_off_one_second_on_a_transport_error(caplog) -> None:
    """A transport-level error escaping the cycle body warns and backs off 1s
    instead of killing the loop or spinning."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([OSError("connection reset by peer"), 120])

    async def fake_send(command, encrypted=False):
        return None

    conn._send_text_command = fake_send

    def on_sleep(delay):
        if delay == 60:
            conn._shutdown_event.set()

    result = await drive_refresh_loop(conn, on_sleep=on_sleep)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    assert result.delays == [DELAY_CHECK_TOKEN_REFRESH, 1, 60]
    assert _warnings(caplog, "Error in token refresh cycle")


async def test_refresh_loop_counts_a_failing_refresh_as_one_failure(caplog) -> None:
    """A transport error from the refresh itself is one counted failure, not a
    crash: the loop keeps running and the key event is cleared."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([120])

    async def fake_send(command, encrypted=False):
        conn._key = "cafebabe"  # the key really changed...
        conn._key_update_event.set()
        conn._shutdown_event.set()  # ...exit after this cycle

    async def failing_refresh():
        raise LoxoneConnectionClosedOk("socket closed while refreshing")

    conn._send_text_command = fake_send
    conn._refresh_token = failing_refresh

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    assert conn._refresh_failures == 1
    assert conn._key_update_event is None
    assert _warnings(caplog, "token refresh cycle failed")


async def test_refresh_loop_propagates_a_token_error_from_the_refresh() -> None:
    """A LoxoneTokenError raised by the refresh is control flow for the owner
    (reload); it must propagate instead of being counted as a soft failure."""
    conn = _authenticated_connection([120])

    async def fake_send(command, encrypted=False):
        conn._key = "cafebabe"
        conn._key_update_event.set()

    async def failing_refresh():
        raise LoxoneTokenError("token not valid anymore")

    conn._send_text_command = fake_send
    conn._refresh_token = failing_refresh

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneTokenError)
    assert str(result.error) == "token not valid anymore"
    assert conn._refresh_failures == 0  # not a soft failure
    assert conn._key_update_event is None


async def test_refresh_loop_propagates_a_cancel_landing_on_the_key_request() -> None:
    """A cancel during the getkey send is re-raised, never swallowed as a
    transport failure (it would otherwise count towards the escalation)."""
    conn = _authenticated_connection([120])

    async def cancelled_send(command, encrypted=False):
        conn._shutdown_event.set()
        raise asyncio.CancelledError

    conn._send_text_command = cancelled_send

    result = await drive_refresh_loop(conn)

    assert isinstance(result.error, LoxoneConnectionClosedOk)  # from the stub listener
    assert conn._refresh_failures == 0


async def test_refresh_loop_cancellation_is_debug_not_error(caplog) -> None:
    """API-27: cancelling the refresh task at shutdown is control flow, logged
    at DEBUG -- never as an error."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = _authenticated_connection([120])

    async def fake_send(command, encrypted=False):
        return None

    conn._send_text_command = fake_send

    def on_sleep(delay):
        if delay == 60:
            conn._shutdown_event.set()
            task = _find_refresh_task(conn)
            assert task is not None
            task.cancel()

    result = await drive_refresh_loop(conn, on_sleep=on_sleep)

    assert isinstance(result.error, LoxoneConnectionClosedOk)
    task = _find_refresh_task(conn)
    assert task is None or task.cancelled()
    assert "Token refresh task cancelled" in caplog.text
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


# --------------------------------------------------------------------------- #
# _refresh_token: the outgoing command (API-12/17)
# --------------------------------------------------------------------------- #
async def test_refresh_token_command_legacy_firmware() -> None:
    """Pre-10.2 firmware gets the legacy refreshtoken command, HMAC'd with the
    current key.

    VERIFY: the legacy form has no separator between ``jdev/sys/refreshtoken``
    and the hash (the constant carries no trailing ``/``, unlike the JSON-web
    one); Loxone's docs show ``jdev/sys/refreshtoken/{hash}/{user}``. Pinned
    as-is here -- changing it is a source fix, not a test fix.
    """
    conn = make_connection()
    conn._hash_alg = "SHA1"
    conn._key = HEX_KEY
    conn._token = LoxoneToken(token="TOKEN-1", valid_until=1893456000, hash_alg="SHA1")
    conn.miniserver_version = [9, 3]

    await conn._refresh_token()

    expected = hmac.new(bytes.fromhex(HEX_KEY), b"TOKEN-1", hashlib.sha1).hexdigest()
    assert expected == "b1ad4f235515e65a9f93b77f51cd299ffbe484a1"  # hand-derived literal
    item = conn._message_queue.get_nowait()
    assert item.flag is True  # the command must go out encrypted
    assert item.command == f"jdev/sys/refreshtoken{expected}/admin"


async def test_refresh_token_command_json_web_firmware_encodes_the_username() -> None:
    """10.2+ firmware gets the refreshjwt command; the username is
    percent-encoded for the protocol string (API-12) but hashed raw."""
    conn = make_connection(username="admin user")
    conn._hash_alg = "SHA256"
    conn._key = HEX_KEY
    conn._token = LoxoneToken(token="TOKEN-1", valid_until=1893456000, hash_alg="SHA256")
    conn.miniserver_version = [14, 0]

    await conn._refresh_token()

    expected = hmac.new(bytes.fromhex(HEX_KEY), b"TOKEN-1", hashlib.sha256).hexdigest()
    assert expected == "43e75ec85c7314530623d4f70623ec730447e4d135683f7505ab64284dbe5494"
    item = conn._message_queue.get_nowait()
    assert item.command == f"jdev/sys/refreshjwt/{expected}/admin%20user"


async def test_refresh_token_raises_when_the_token_cannot_be_hashed() -> None:
    """No usable key -> RuntimeError, and nothing is queued (API-07: the old
    code queued a command built from a None hash)."""
    conn = make_connection()
    conn._hash_alg = "SHA1"
    conn._key = ""  # no getkey response yet
    conn._token = LoxoneToken(token="TOKEN-1", valid_until=1893456000, hash_alg="SHA1")

    with pytest.raises(RuntimeError):
        await conn._refresh_token()

    assert conn._message_queue.empty()


async def test_refresh_token_propagates_a_full_message_queue() -> None:
    """The bounded command queue must fail loudly: a put() timeout surfaces to
    the refresh loop (which counts it) instead of being swallowed."""

    class _FullQueue:
        async def put(self, item):
            raise TimeoutError("message queue is full")

    conn = make_connection()
    conn._hash_alg = "SHA1"
    conn._key = HEX_KEY
    conn._token = LoxoneToken(token="TOKEN-1", valid_until=1893456000, hash_alg="SHA1")
    conn._message_queue = _FullQueue()

    with pytest.raises(TimeoutError):
        await conn._refresh_token()


# --------------------------------------------------------------------------- #
# The refresh *response*: persistence side effects (API-09/13/17)
# --------------------------------------------------------------------------- #
async def _drain_tasks(conn: LoxoneConnection) -> None:
    while conn._tasks:
        await asyncio.gather(*list(conn._tasks), return_exceptions=True)


async def test_refresh_response_updates_the_token_and_notifies_the_owner() -> None:
    """API-17: a refreshed token is stored, marks the session authenticated and
    is handed to the owner's persistence callback (incl. unsecurePass)."""
    persisted: list[dict] = []

    async def persist(token):
        persisted.append(dict(token))

    conn = make_connection(token_change_callback=persist)
    conn._token = LoxoneToken(token="OLD-TOKEN", valid_until=1000, hash_alg="SHA256")
    conn._authenticated_event.clear()
    message = json.dumps(
        {
            "LL": {
                "control": "jdev/sys/refreshjwt/hash/admin",
                "code": 200,
                "value": {"token": "NEW-TOKEN", "validUntil": 1893456000, "unsecurePass": True},
            }
        }
    )

    await conn._websocket_event(TextMessage(message))
    await _drain_tasks(conn)

    assert conn._token.token == "NEW-TOKEN"
    assert conn._token.valid_until == 1893456000
    assert conn._token.unsecure_password is True
    assert conn._authenticated_event.is_set()
    assert persisted == [
        {"token": "NEW-TOKEN", "valid_until": 1893456000, "hash_alg": "SHA256", "unsecure_password": True}
    ]


async def test_refresh_response_401_resets_the_token_and_asks_for_a_reconnect(caplog) -> None:
    """API-09/13/27: a 401 on refresh means the server-side token is gone --
    reset + reconnect, at DEBUG, and nothing is persisted."""
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    persisted: list[dict] = []

    async def persist(token):
        persisted.append(dict(token))

    conn = make_connection(token_change_callback=persist)
    conn._token = LoxoneToken(token="OLD-TOKEN", valid_until=1893456000, hash_alg="SHA1")
    conn._authenticated_event.set()
    message = json.dumps({"LL": {"control": "jdev/sys/refreshjwt/hash/admin", "code": 401, "value": {}}})

    await conn._websocket_event(TextMessage(message))
    await _drain_tasks(conn)

    assert conn._token.token == ""
    assert conn._token.valid_until == -1
    assert not conn._authenticated_event.is_set()
    assert conn._reconnect_event.is_set()
    assert persisted == []
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


# --------------------------------------------------------------------------- #
# _hash_credentials / _hash_token (API-07/12/25)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("hash_alg", "digest", "expected"),
    [
        ("SHA1", hashlib.sha1, "3a08afe77e0e39ee15fd7a9ead31f21c27074570"),
        ("SHA256", hashlib.sha256, "87542d565c93b5526d09f48383aa63e5b4b3851a8841424356efa53a57b8f50b"),
    ],
)
def test_hash_credentials_follows_the_loxone_auth_protocol(hash_alg, digest, expected) -> None:
    """HMAC(key, "user:UPPERHEX(H(password:userSalt))") with H = the announced
    hash algorithm; derived here with hashlib/hmac and pinned as a literal."""
    conn = make_connection(username="admin", password="s3cr3t")
    conn._hash_alg = hash_alg
    conn._user_salt = "0011223344556677"
    conn._key = HEX_KEY

    pwd_hash = digest(b"s3cr3t:0011223344556677").hexdigest().upper()
    derived = hmac.new(bytes.fromhex(HEX_KEY), f"admin:{pwd_hash}".encode(), digest).hexdigest()

    assert derived == expected
    assert conn._hash_credentials() == expected


def test_hash_credentials_hashes_raw_utf8_credentials() -> None:
    """API-12: credentials are percent-encoded only for the *URL-like* command
    strings; the hash is taken over the raw UTF-8 username/password."""
    conn = make_connection(username="Ége", password="pässwörd")
    conn._hash_alg = "SHA1"
    conn._user_salt = "AABBCCDD"
    conn._key = HEX_KEY

    assert conn._encoded_username == "%C3%89ge"  # what goes into a command
    assert conn._hash_credentials() == "876cad458140ffdb99549bb3ff6dd629c54b3c1a"


def test_hash_credentials_returns_none_for_an_unknown_hash_algorithm(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = make_connection()
    conn._hash_alg = "MD5"  # not a Loxone algorithm
    conn._user_salt = "0011"
    conn._key = HEX_KEY

    assert conn._hash_credentials() is None
    assert "Unrecognised hash algorithm: MD5" in caplog.text


def test_hash_credentials_returns_none_for_a_non_hex_key() -> None:
    """A non-hex key (e.g. the visual-salt key leaking into self._key, API-07)
    must degrade to None rather than raise."""
    conn = make_connection()
    conn._hash_alg = "SHA1"
    conn._user_salt = "0011"
    conn._key = "not-hex!"

    assert conn._hash_credentials() is None


def test_hash_token_returns_none_without_token_key_or_algorithm(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = make_connection()
    conn._hash_alg = "SHA1"
    conn._key = HEX_KEY

    conn._token = LoxoneToken()  # no token yet
    assert conn._hash_token() is None
    assert "No token available to hash" in caplog.text

    conn._token = LoxoneToken(token="TOKEN-1", valid_until=1893456000, hash_alg="SHA1")
    conn._key = ""  # no getkey response yet
    assert conn._hash_token() is None
    assert "No key available for token hashing" in caplog.text

    conn._key = HEX_KEY
    conn._hash_alg = "MD5"
    assert conn._hash_token() is None
    assert "Unrecognised hash algorithm: MD5" in caplog.text
