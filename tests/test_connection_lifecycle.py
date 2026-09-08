"""WP-2.2 regression tests: clean-close detection, auth failure handling, logging hygiene.

Pure-unit tests against ``pyloxone_api.connection`` with a stub websocket
connection (no Miniserver, no HA harness):

* API-02  a clean end of the async-iterator (token expiry / restart /
          session-limit close) must raise ``LoxoneConnectionClosedOk``
          immediately -- not 30s later as a keep-alive ``ERROR``.
* API-27  expected reconnect control flow is logged at DEBUG/INFO, with one
          "lost / restored" WARNING+INFO pair per outage.
* API-16  repeated token-refresh failures escalate to ``LoxoneTokenError``.
* API-13  401/4003 on *every* auth response raises a typed error instead of
          hanging silently.
* API-08  ``open()`` GETs use 3 tries with backoff, not 100 x 5s.
* API-06  websocket options disable the protocol-level ping (VERIFY item).
* API-14/17 tracked task set, token-change persistence callback, killtoken.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from types import SimpleNamespace

import pytest

from custom_components.loxone.pyloxone_api.connection import (
    LoxoneConnection,
    build_websocket_options,
)
from custom_components.loxone.pyloxone_api.exceptions import (
    LoxoneConnectionClosedOk,
    LoxoneReconnectRequested,
    LoxoneTokenError,
    LoxoneUnauthorisedError,
)
from custom_components.loxone.pyloxone_api.message import MessageType

CONNECTION_LOGGER = "custom_components.loxone.pyloxone_api.connection"

# Hand-derived wire frame: 8-byte header (0x03 segment, cBinType=MessageType.TEXT=0,
# no cInfo bit, 4-byte LE payload length) followed by the LL JSON body.
# The TEXT notification format is pinned by tests/test_message_parsing.py.


def _text_frames(ll_payload: dict) -> tuple[bytes, bytes]:
    body = json.dumps(ll_payload).encode("utf-8")
    header = b"\x03" + bytes([MessageType.TEXT]) + b"\x00\x00" + struct.pack("<I", len(body))
    return header, body


class _StubState:
    """Just needs a ``CLOSED`` sentinel that never equals the state instance."""

    CLOSED = object()


class StubLoxoneConnection:
    """Mimics ``websockets``' async-iterator + send surface of the client.

    ``__aiter__`` yields the given frames and then *ends cleanly* -- which is
    exactly what websockets does when the server closes with code 1000.
    """

    def __init__(self, frames: tuple[bytes, ...] = (), close_code: int = 1000):
        self.frames = list(frames)
        self.close_code = close_code
        self.sent: list[str] = []
        self.state = _StubState()
        self.protocol = SimpleNamespace(state=SimpleNamespace(name="OPEN"))

    async def send(self, message, **kwargs):
        self.sent.append(message)

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        for frame in self.frames:
            yield frame


def make_connection(token_change_callback=None) -> LoxoneConnection:
    conn = LoxoneConnection(
        host="miniserver.local",
        username="admin",
        password="secret",
        token_change_callback=token_change_callback,
    )
    # A truthy 32-byte key so the key-exchange guard in start_listening passes.
    conn._session_key = b"\x00" * 32
    return conn


async def _drain_tasks(conn: LoxoneConnection) -> None:
    """Wait for fire-and-forget tracked tasks (e.g. the token callback)."""
    while conn._tasks:
        await asyncio.gather(*list(conn._tasks), return_exceptions=True)


def _error_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


# --------------------------------------------------------------------------- #
# API-02 clean close
# --------------------------------------------------------------------------- #
async def test_clean_close_raises_immediately_without_errors(caplog):
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = make_connection()
    conn.connection = StubLoxoneConnection(frames=(), close_code=1000)

    started = time.monotonic()
    with pytest.raises(LoxoneConnectionClosedOk):
        await asyncio.wait_for(conn.start_listening(), timeout=2.0)
    elapsed = time.monotonic() - started

    # The old code sat on the dead socket until the 30s keep-alive failed.
    assert elapsed < 1.0
    assert _error_records(caplog) == []
    # The close must be reported at INFO (once), not as an error.
    info = [r for r in caplog.records if r.levelno == logging.INFO and "closed normally" in r.getMessage().lower()]
    assert len(info) == 1


# --------------------------------------------------------------------------- #
# API-13 auth failure handling
# --------------------------------------------------------------------------- #
async def test_auth_response_401_raises_unauthorised(caplog):
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    header, body = _text_frames({"LL": {"code": 401, "control": "getjwt", "value": {}}})
    conn = make_connection()
    conn.connection = StubLoxoneConnection(frames=(header, body))

    with pytest.raises(LoxoneUnauthorisedError):
        await asyncio.wait_for(conn.start_listening(), timeout=2.0)
    assert _error_records(caplog) == []


async def test_auth_response_4003_raises_unauthorised():
    header, body = _text_frames({"LL": {"code": 4003, "control": "gettoken", "value": {}}})
    conn = make_connection()
    conn.connection = StubLoxoneConnection(frames=(header, body))

    with pytest.raises(LoxoneUnauthorisedError):
        await asyncio.wait_for(conn.start_listening(), timeout=2.0)


async def test_getkey2_401_raises_unauthorised():
    header, body = _text_frames({"LL": {"code": 401, "control": "getkey2", "value": {}}})
    conn = make_connection()
    conn.connection = StubLoxoneConnection(frames=(header, body))

    with pytest.raises(LoxoneUnauthorisedError):
        await asyncio.wait_for(conn.start_listening(), timeout=2.0)


async def test_authwithtoken_401_is_control_flow_not_unauthorised(caplog):
    # A stale token (federally valid *credentials*) must reset + request a
    # reconnect at DEBUG -- it must NOT be flagged as unauthorised.
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    header, body = _text_frames({"LL": {"code": 401, "control": "authwithtoken", "value": {}}})
    conn = make_connection()
    conn.connection = StubLoxoneConnection(frames=(header, body))

    # The handler consumes the message, so the clean close is what ends the loop.
    with pytest.raises(LoxoneConnectionClosedOk):
        await asyncio.wait_for(conn.start_listening(), timeout=2.0)

    assert conn._reconnect_event.is_set()
    assert _error_records(caplog) == []


async def test_token_change_invokes_persistence_callback():
    received: list[dict] = []

    async def persist(token):
        received.append(dict(token))

    header, body = _text_frames(
        {
            "LL": {
                "code": 200,
                "control": "getjwt",
                "value": {"token": "TOKENTOKEN", "validUntil": 1893456000, "key": "00112233445566778899aabbccddeeff"},
            }
        }
    )
    conn = make_connection(token_change_callback=persist)
    conn.connection = StubLoxoneConnection(frames=(header, body))

    with pytest.raises(LoxoneConnectionClosedOk):
        await asyncio.wait_for(conn.start_listening(), timeout=2.0)
    await _drain_tasks(conn)

    # Expected dict derived by hand from get_token_dict()'s contract.
    assert received == [{"token": "TOKENTOKEN", "valid_until": 1893456000, "hash_alg": "", "unsecure_password": False}]


# --------------------------------------------------------------------------- #
# API-16 refresh failure escalation
# --------------------------------------------------------------------------- #
def test_refresh_failures_escalate_after_three():
    conn = make_connection()
    conn._note_refresh_failure("timed out waiting for new key")
    conn._note_refresh_failure("timed out waiting for new key")
    with pytest.raises(LoxoneTokenError):
        conn._note_refresh_failure("timed out waiting for new key")


# --------------------------------------------------------------------------- #
# API-27 logging hygiene
# --------------------------------------------------------------------------- #
def test_reconnect_requested_is_token_error_subclass():
    # Compatibility with the unhandled-task handlers outside this WP's file set
    # (they catch LoxoneTokenError); the type itself is the control-flow marker.
    assert issubclass(LoxoneReconnectRequested, LoxoneTokenError)


async def test_reconnect_requested_logged_at_debug_not_error(caplog):
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = make_connection()

    async def flaky_listening(callback, connection):
        raise LoxoneReconnectRequested("token expired, reconnect required")

    conn._do_start_listening = flaky_listening
    conn.connection = StubLoxoneConnection()

    with pytest.raises(LoxoneReconnectRequested):
        await asyncio.wait_for(conn.start_listening(), timeout=2.0)

    assert _error_records(caplog) == []
    assert any("reconnect requested" in r.getMessage().lower() for r in caplog.records if r.levelno == logging.DEBUG)


def test_outage_lost_restored_pair_logged_once(caplog):
    caplog.set_level(logging.DEBUG, logger=CONNECTION_LOGGER)
    conn = make_connection()

    # Before the first confirmed connection, issues are DEBUG only.
    conn._note_connection_lost("transient setup failure")
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    conn._note_connection_restored()
    conn._note_connection_lost("server restarted")
    conn._note_connection_lost("still down")

    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "lost connection" in r.getMessage().lower()
    ]
    assert len(warnings) == 1

    conn._note_connection_restored()
    infos = [r for r in caplog.records if r.levelno == logging.INFO and "restored" in r.getMessage().lower()]
    assert len(infos) == 1


# --------------------------------------------------------------------------- #
# API-08 bounded open() retries
# --------------------------------------------------------------------------- #
async def test_open_get_retries_until_success():
    conn = make_connection()
    attempts: list[str] = []

    async def fake_get(endpoint):
        attempts.append(endpoint)
        if len(attempts) < 3:
            raise ConnectionError("unreachable")
        return "response"

    connector = SimpleNamespace(get=fake_get)
    assert await conn._get_with_retry(connector, "/jdev/cfg/apiKey", "API key", base_delay=0) == "response"
    assert attempts == ["/jdev/cfg/apiKey", "/jdev/cfg/apiKey", "/jdev/cfg/apiKey"]


async def test_open_get_gives_up_after_three_tries():
    conn = make_connection()
    attempts: list[str] = []

    async def fake_get(endpoint):
        attempts.append(endpoint)
        raise ConnectionError("unreachable")

    connector = SimpleNamespace(get=fake_get)
    with pytest.raises(ConnectionError, match="unreachable"):
        await conn._get_with_retry(connector, "/data/LoxAPP3.json", "structure file", base_delay=0)
    assert len(attempts) == 3


# --------------------------------------------------------------------------- #
# API-06 websocket options (VERIFY: ping_interval=None against a live Miniserver)
# --------------------------------------------------------------------------- #
def test_websocket_options_disable_protocol_ping():
    options = build_websocket_options(open_timeout=30.0)
    # websockets' default 20s ping must be explicitly off: the Loxone
    # keepalive (30s) is the single liveness channel.
    assert options["ping_interval"] is None
    # close_timeout stated explicitly instead of relying on the library default.
    assert options["close_timeout"] == 10
    assert options["compression"] is None
    assert options["open_timeout"] == 30.0
    assert options["max_size"] == 5 * 1024 * 1024
    assert "ssl" not in options
    assert options["create_connection"] is not None


def test_websocket_options_pass_through_ssl_and_max_size():
    ssl_ctx = SimpleNamespace()
    options = build_websocket_options(open_timeout=1.0, max_size=7, ssl_context=ssl_ctx)
    assert options["ssl"] is ssl_ctx
    assert options["max_size"] == 7
    assert options["open_timeout"] == 1.0


# --------------------------------------------------------------------------- #
# API-17 killtoken
# --------------------------------------------------------------------------- #
async def test_kill_token_sends_killtoken_command():
    conn = make_connection()
    conn._token.token = "KILLMETOKEN"
    conn._token.valid_until = 1893456000
    stub = StubLoxoneConnection()
    conn.connection = stub

    await conn.kill_token()
    assert stub.sent == ["jdev/sys/killtoken/KILLMETOKEN/admin"]


async def test_kill_token_noop_when_not_connected():
    conn = make_connection()
    conn._token.token = "KILLMETOKEN"
    conn.connection = None
    # Must not raise: entry removal has to succeed even with a dead socket.
    await conn.kill_token()
    assert True


async def test_kill_token_noop_without_token():
    conn = make_connection()
    conn.connection = StubLoxoneConnection()
    await conn.kill_token()
    assert True
