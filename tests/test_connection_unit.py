"""WP-2.1 regression tests for pyloxone_api.connection (API-01/04/07/10/11/12/15/18/20/24).

Expected values are hand-derived literals; nothing here is produced by calling
the code under test.
"""

from __future__ import annotations

from base64 import b64decode
import hashlib
import json
import struct
import types
import uuid
from collections import deque
from enum import Enum
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

import pytest
from Crypto.Cipher import AES
from Crypto.Hash import SHA1, HMAC
from Crypto.Util import Padding

from custom_components.loxone.pyloxone_api import connection as connection_mod
from custom_components.loxone.pyloxone_api.connection import (
    LoxoneConnection,
    SecuredCommand,
    build_loxone_url,
    percent_encode_credential,
    parse_loxone_url,
)
from custom_components.loxone.pyloxone_api.exceptions import LoxoneConnectionClosedOk
from custom_components.loxone.pyloxone_api.loxone_token import LoxoneToken
from custom_components.loxone.pyloxone_api.message import TextMessage

FIXTURE = Path(__file__).parent / "fixtures" / "LoxAPP3.json"

# 1024-bit throwaway key for the harness test; the integration only encrypts
# the session key with it, and the corresponding private key never leaves this
# test module.
_THROWAWAY_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC1b6yRVjCItJSmqNgUP3Didexo
ilWyh0+c7BonAQlz9qQLVQUJ0O47/Yh1DthxQWf2d7Pcgt8A4ma/JYyEIpQRxDd0
wPY3CUnbeppLMrfQtNDgfPbh9/ts0RldkrabhFwpGlYCvzUwZAqd7vym1y27DZyL
nJMMRGJVrguwMAzjIwIDAQAB
-----END PUBLIC KEY-----"""


def make_connection(**overrides) -> LoxoneConnection:
    kwargs = dict(host="192.168.1.5", username="admin", password="secret", port=8080)
    kwargs.update(overrides)
    return LoxoneConnection(**kwargs)


class FakeWS:
    """Just enough of a websockets ClientConnection for command tests."""

    def __init__(self) -> None:
        self.sent: list = []
        self.closed = False
        self.protocol = types.SimpleNamespace(state=types.SimpleNamespace(name="OPEN"))

    async def send(self, message, text=None):
        self.sent.append(message)

    async def close(self):
        self.closed = True


# --------------------------------------------------------------------------- #
# API-11: URL building (incl. the Cloud-DNS http->https redirect case)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "parts", "rebuilt"),
    [
        ("http://192.168.1.5", ("http", "192.168.1.5", 80, ""), "192.168.1.5"),
        ("http://192.168.1.5:80", ("http", "192.168.1.5", 80, ""), "192.168.1.5"),
        ("http://192.168.1.5:8080", ("http", "192.168.1.5", 8080, ""), "192.168.1.5:8080"),
        ("https://11a72b.loxone.com", ("https", "11a72b.loxone.com", 443, ""), "11a72b.loxone.com"),
        # trailing slashes must not leak into the websocket target
        ("https://11a72b.loxone.com/", ("https", "11a72b.loxone.com", 443, "/"), "11a72b.loxone.com"),
        # non-default https port is kept
        (
            "https://192.168.1.5:8443/jdev/slack",
            ("https", "192.168.1.5", 8443, "/jdev/slack"),
            "192.168.1.5:8443/jdev/slack",
        ),
        # no scheme -> http default
        ("192.168.1.5:8080", ("http", "192.168.1.5", 8080, ""), "192.168.1.5:8080"),
        ("loxberry.local", ("http", "loxberry.local", 80, ""), "loxberry.local"),
    ],
)
def test_parse_build_url_table(url, parts, rebuilt) -> None:
    assert parse_loxone_url(url) == parts
    assert build_loxone_url(*parts) == rebuilt


def test_apply_url_cloud_dns_http_to_https_redirect() -> None:
    # The redirect that broke the old code (API-11): a miniserver behind
    # Loxone Cloud DNS answers a plain http:// httpRequest with a redirect to
    # an https:// Cloud URL. Scheme, host *and* port must all follow.
    conn = LoxoneConnection(host="http://192.168.1.5:8080", username="u", password="p")
    assert conn.scheme == "http"
    assert conn.url == "192.168.1.5:8080"
    assert conn._websocket_ssl_context() is None  # http -> no TLS context

    conn._apply_url("https://11a72b.loxone.com")

    assert conn.scheme == "https"
    assert conn.host == "11a72b.loxone.com"
    assert conn.port == 443
    assert conn.url == "11a72b.loxone.com"
    # The websocket dial URL must have flipped to wss:// as well:
    assert LoxoneConnection._SSL_URL_FORMAT.format(url=conn.url) == "wss://11a72b.loxone.com/ws/rfc6455"


def test_apply_url_requires_a_hostname() -> None:
    conn = LoxoneConnection(host="http://192.168.1.5:8080", username="u", password="p")
    with pytest.raises(ValueError):
        conn._apply_url("not a valid url")


# --------------------------------------------------------------------------- #
# API-12 (VERIFY): credential percent-encoding
# --------------------------------------------------------------------------- #
def test_percent_encode_credential_utf8_semantics() -> None:
    # VERIFY: intended semantics are UTF-8 percent-encoding. É (U+00C9) is
    # 0xC3 0x89 in UTF-8, so the expected value below is a hand-derivable
    # literal. If a live Miniserver turns out to expect latin-1, change the
    # helper (percent_encode_credential) and this test together.
    assert percent_encode_credential("admin") == "admin"
    assert percent_encode_credential("Égeard") == "%C3%89geard"
    assert percent_encode_credential("a b") == "a%20b"
    assert percent_encode_credential("a/b") == "a%2Fb"
    assert percent_encode_credential("12345") == "12345"


# --------------------------------------------------------------------------- #
# API-04 / API-10: send guard and single-frame str sends
# --------------------------------------------------------------------------- #
async def test_send_text_command_raises_when_disconnected() -> None:
    conn = make_connection()
    with pytest.raises(LoxoneConnectionClosedOk):
        await conn._send_text_command("jdev/sps/io/abc/1")


async def test_send_text_command_plain_sends_single_str() -> None:
    conn = make_connection()
    ws = FakeWS()
    conn.connection = ws
    await conn._send_text_command("jdev/sps/io/abc/1")
    assert ws.sent == ["jdev/sps/io/abc/1"]  # a str, sent as a single frame


async def test_send_text_command_encrypted_round_trip() -> None:
    conn = make_connection()
    conn._generate_salt()
    ws = FakeWS()
    conn.connection = ws

    await conn._send_text_command("jdev/sps/enabledattab/ex_1", encrypted=True)

    assert len(ws.sent) == 1
    payload = ws.sent[0]
    # API-10: send() must receive a single str, never a list of fragments.
    assert isinstance(payload, str)
    assert payload.startswith("jdev/sys/enc/")

    # Decrypt the %encoded base64 blob and check the whole construction.
    decoded = b64decode(unquote(payload[len("jdev/sys/enc/") :]).encode("latin1"))
    text = Padding.unpad(AES.new(conn._aes_key, AES.MODE_CBC, conn._iv).decrypt(decoded), 16).rstrip(b"\x00")
    # A fresh salt was generated explicitly, so the message uses the salt
    # form and is zero-terminated (the miniserver's wire-protocol quirk).
    assert text.decode("utf-8") == f"salt/{conn._salt}/jdev/sps/enabledattab/ex_1"


# --------------------------------------------------------------------------- #
# API-07: token key safety
# --------------------------------------------------------------------------- #
def test_hash_token_returns_none_on_non_hex_key() -> None:
    conn = make_connection()
    conn._token = LoxoneToken(token="token-1", valid_until=10_000_000_000, hash_alg="SHA1")
    conn._hash_alg = "SHA1"
    conn._key = "not-hex!"
    assert conn._hash_token() is None

    # A valid hex key does produce a 40-char hex SHA1 HMAC.
    conn._key = "deadbeef"
    assert conn._hash_token() is not None


_VISUSALT_MSG = json.dumps(
    {"LL": {"control": "jdev/sys/getvisusalt/admin/", "code": "15", "value": {"key": "deadbeef", "salt": "00112233"}}}
)


async def test_getvisusalt_handler_leaves_token_key_untouched() -> None:
    conn = make_connection()
    conn._key = "cafebabe"  # token key, must survive

    await conn._websocket_event(TextMessage(_VISUSALT_MSG))

    assert conn._key == "cafebabe"
    assert conn._visual_hash is not None
    assert conn._visual_hash.key == "deadbeef"
    assert conn._visual_hash.salt == "00112233"


async def test_getvisusalt_drains_secured_queue() -> None:
    # API-15 + API-07: queued secured params are hashed with the fresh salt
    # once the salt arrives, the token key is left alone, and the queue is
    # empty again. The expected HMAC is computed here with the *protocol*
    # (HMAC-SHA1 over "code:salt" with the hex key), independently of the
    # code under test.
    conn = make_connection()
    conn._key = "cafebabe"
    conn._secured_queue.append(SecuredCommand(device_uuid="abcd-uuid", value=1, code="open"))

    await conn._websocket_event(TextMessage(_VISUSALT_MSG))

    assert conn._key == "cafebabe"
    assert conn._secured_queue == deque()
    assert conn._message_queue.qsize() == 1
    item = conn._message_queue.get_nowait()
    assert item.flag is True

    m = hashlib.sha1()
    m.update(b"open:00112233")
    pwd_hash = m.hexdigest().upper()
    expected_hash = HMAC.new(bytes.fromhex("deadbeef"), pwd_hash.encode("utf-8"), SHA1).hexdigest()
    assert item.command == f"jdev/sps/ios/{expected_hash}/abcd-uuid/1"


def test_secured_command_text_none_without_visual_hash() -> None:
    # API-15 guard: no visual hash -> no AttributeError, just a None.
    conn = make_connection()
    assert conn._secured_command_text(SecuredCommand(device_uuid="u", value=1, code="c")) is None


# --------------------------------------------------------------------------- #
# API close(): idempotent
# --------------------------------------------------------------------------- #
async def test_close_is_idempotent() -> None:
    conn = make_connection()
    conn._secured_queue.append(SecuredCommand(device_uuid="u", value=1, code="c"))

    await conn.close()
    assert len(conn._secured_queue) == 0
    await conn.close()  # second call: cleanup, no error


# --------------------------------------------------------------------------- #
# API-18: strict header->body framing
# --------------------------------------------------------------------------- #
def _header(message_id: int, payload_length: int, estimated: bool = False) -> bytes:
    return b"\x03" + bytes([message_id, 0x80 if estimated else 0, 0]) + struct.pack("<I", payload_length)


class FakeFeed:
    """A websocket-shaped source returning canned frames."""

    class _WSEState(Enum):
        OPEN = 1
        CLOSED = 2

    state = _WSEState.OPEN

    def __init__(self, frames: list) -> None:
        self._frames = list(frames)

    def __aiter__(self) -> "FakeFeed":
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


async def _run_listening(frames: list, callback=None) -> list:
    conn = make_connection()
    seen: list = []

    async def cb(data) -> None:
        seen.append(data)
        if callback is not None:
            await callback(data)

    await conn._do_start_listening(cb, FakeFeed(frames))
    return seen


def _value_state_record(duuid: uuid.UUID, value: float) -> bytes:
    return duuid.bytes_le + struct.pack("<d", value)


async def test_body_is_concatenated_until_header_length() -> None:
    # The old code required a single frame whose length *equaled* the header
    # length, so a body split across frames was dropped with "Message not
    # handled". The strict reader must reassemble it.
    u = uuid.UUID("12345678-9abc-def0-1234-56789abcdef0")
    record = _value_state_record(u, 1.5)
    assert len(record) == 24

    seen = await _run_listening([_header(2, 24), record[:12], record[12:]])
    # Hand-derived: the libraries join the uuid groups, so field "d0" and
    # "1234-56789abcdef0" merge without a dash.
    assert seen == [{"12345678-9abc-def0-123456789abcdef0": 1.5}]


async def test_exact_header_follows_estimated_header() -> None:
    # The Loxone docs guarantee an exact header after an estimated header;
    # the estimator's fake length (999) must not be believed.
    u = uuid.UUID("12345678-9abc-def0-1234-56789abcdef0")
    record = _value_state_record(u, -3.25)

    seen = await _run_listening([_header(2, 999, estimated=True), _header(2, 24), record])
    assert seen == [{"12345678-9abc-def0-123456789abcdef0": -3.25}]


async def test_unknown_header_is_bodyless_and_resyncs() -> None:
    # A non-0x03 frame leaves no pending payload, so the next well-formed
    # header must still be parsed (the old code would have hung waiting for
    # an 8-byte body of length 0).
    u = uuid.UUID("12345678-9abc-def0-1234-56789abcdef0")
    record = _value_state_record(u, 0.5)

    seen = await _run_listening([b"\x07" + b"\x00" * 7, _header(2, 24), record])
    assert seen == [{"12345678-9abc-def0-123456789abcdef0": 0.5}]


async def test_keepalive_header_is_handled_inline() -> None:
    seen = await _run_listening([_header(6, 0)])
    assert seen == [{"keep_alive": "received"}]


async def test_listening_without_callback_does_not_crash_on_keepalive() -> None:
    await _run_listening([_header(6, 0)], callback=None)


# --------------------------------------------------------------------------- #
# API-01 (HA harness): exactly one wslib.connect per setup
# --------------------------------------------------------------------------- #
class _FakeHttpContent:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FakeHttpResponse:
    def __init__(self, content: bytes, url: str, status: int = 200) -> None:
        self._content = _FakeHttpContent(content)
        self.url = url
        self.status = status

    @property
    def content(self) -> _FakeHttpContent:
        return self._content

    async def __aenter__(self) -> "_FakeHttpResponse":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeHttpClient:
    """Stands in for LoxoneAsyncHttpClient: canned endpoint responses."""

    apikey = json.dumps(
        {
            "LL": {
                "control": "/jdev/cfg/apiKey/",
                "code": 15,
                "value": {"version": "7.1.0", "snr": "TESTSERIAL", "local": "True"},
            }
        }
    ).encode()
    pubkey = json.dumps(
        {"LL": {"control": "/jdev/sys/getPublicKey/", "code": 15, "value": _THROWAWAY_PUBLIC_KEY_PEM}}
    ).encode()

    def __init__(self, *, url, username, password, scheme, verify_ssl, session=None) -> None:
        self.base_url = f"{scheme}://{url}"
        self.session = session

    async def get(self, endpoint) -> _FakeHttpResponse:
        if endpoint == "/jdev/cfg/apiKey":
            return _FakeHttpResponse(self.apikey, "http://loxberry.local:8080/jdev/cfg/apiKey")
        if endpoint == "/data/LoxAPP3.json":
            return _FakeHttpResponse(FIXTURE.read_bytes(), "http://loxberry.local:8080/data/LoxAPP3.json")
        if endpoint == "/jdev/sys/getPublicKey":
            return _FakeHttpResponse(self.pubkey, "http://loxberry.local:8080/jdev/sys/getPublicKey")
        raise AssertionError(f"Unexpected endpoint {endpoint}")


class _FakeWebSocket:
    class _WSState(Enum):
        OPEN = 1
        CLOSED = 2

    state = _WSState.OPEN

    def __init__(self) -> None:
        self.sent: list = []
        self.protocol = types.SimpleNamespace(state=types.SimpleNamespace(name="OPEN"))

    async def send(self, message, text=None) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        pass

    def __aiter__(self) -> "_FakeWebSocket":
        return self

    async def __anext__(self) -> None:
        raise StopAsyncIteration  # zero frames: listener finishes cleanly


async def test_setup_opens_exactly_one_websocket(hass, mock_entry, enable_custom_integrations) -> None:
    # API-01: before the fix, every setup dialed the miniserver twice (once
    # from the coordinator, once from start_listening) and leaked one socket;
    # with self.connection stored in open(), setup must dial exactly once.
    ws_connects: list = []
    sockets: list = []

    async def fake_ws_connect(url, **kwargs):
        ws_connects.append(url)
        sock = _FakeWebSocket()
        sockets.append(sock)
        return sock

    with (
        patch.object(connection_mod, "LoxoneAsyncHttpClient", _FakeHttpClient),
        patch.object(connection_mod.wslib, "connect", fake_ws_connect),
    ):
        mock_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert len(ws_connects) == 1
    assert ws_connects[0] == "ws://loxberry.local:8080/ws/rfc6455"

    from custom_components.loxone.const import DOMAIN

    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    api = coordinator.api
    assert api.connection is not None  # open() stored the socket (API-01)
    assert api.connection is sockets[0]
    # The key exchange went out over the single socket as a plain str.
    assert len(sockets[0].sent) == 1
    assert sockets[0].sent[0].startswith("jdev/sys/keyexchange/")
    assert isinstance(sockets[0].sent[0], str)

    # LoxAPP3.json was downloaded *and parsed* once (also protected by the
    # duplicate open).
    assert api.structure_file["softwareVersion"] == [7, 1, 0]
