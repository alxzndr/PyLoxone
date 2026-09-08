"""
Component to create an interface to the Loxone Miniserver.

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/pyloxone-api
"""

import asyncio
import hashlib
import inspect
import json
import logging
import ssl
import time
import urllib
from base64 import b64decode, b64encode
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, NoReturn, Optional, Union
from urllib.parse import urlparse

import aiohttp
import websockets as wslib
import websockets.exceptions
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.Hash import HMAC, SHA1, SHA256
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
from Crypto.Util import Padding

from .const import (
    AES_KEY_SIZE,
    CMD_AUTH_WITH_TOKEN,
    CMD_ENABLE_UPDATES,
    CMD_GET_API_KEY,
    CMD_GET_KEY,
    CMD_GET_KEY_AND_SALT,
    CMD_GET_PUBLIC_KEY,
    CMD_GET_VISUAL_PASSWD,
    CMD_KEEP_ALIVE,
    CMD_KEY_EXCHANGE,
    CMD_KILL_TOKEN,
    CMD_REFRESH_TOKEN,
    CMD_REFRESH_TOKEN_JSON_WEB,
    CMD_REQUEST_TOKEN,
    CMD_REQUEST_TOKEN_JSON_WEB,
    CONNECT_RETRY_BASE_DELAY,
    CONNECT_TRIES,
    DELAY_CHECK_TOKEN_REFRESH,
    IV_BYTES,
    KEEP_ALIVE_PERIOD,
    LLRSP_UNAUTHORISED_CODES,
    LOXAPPPATH,
    MAX_REFRESH_DELAY,
    MAX_WEBSOCKET_MESSAGE_SIZE,
    SALT_BYTES,
    SALT_MAX_AGE_SECONDS,
    SALT_MAX_USE_COUNT,
    TIMEOUT,
    TOKEN_PERMISSION,
    TOKEN_REFRESH_MAX_FAILURES,
    WEBSOCKET_CLOSE_TIMEOUT,
)
from .exceptions import (
    LoxoneConnectionClosedOk,
    LoxoneConnectionError,
    LoxoneException,
    LoxoneOutOfServiceException,
    LoxoneReconnectRequested,
    LoxoneServiceUnAvailableError,
    LoxoneTokenError,
    LoxoneUnauthorisedError,
)
from .loxone_http_client import LoxoneAsyncHttpClient
from .loxone_token import LoxoneToken, LxJsonKeySalt
from .message import (
    BaseMessage,
    BinaryFile,
    Keepalive,
    LLResponse,
    MessageHeader,
    MessageType,
    TextMessage,
    check_and_decode_if_needed,
    parse_header,
    parse_message,
)
from .websocket_protocol import LoxoneClientConnection

_LOGGER = logging.getLogger(__name__)


def time_elapsed_in_seconds():
    return int(round(time.time()))


@dataclass
class MessageForQueue:
    command: str
    flag: bool


@dataclass
class SecuredCommand:
    """Parameters of a secured state change.

    API-15: queue *parameters*, never un-awaited coroutines (the salt they
    depend on only exists once the next ``getvisusalt`` response lands, so
    capturing closure state at send time was broken anyway). The queue is
    an unbounded ``deque`` and is cleared in ``close()``.
    """

    device_uuid: str
    value: Union[str, int, float]
    code: str


def percent_encode_credential(value: str) -> str:
    """Percent-encode a credential for use inside a protocol command (API-12).

    VERIFY: the Miniserver is assumed to expect *UTF-8* percent-encoding
    (e.g. ``"Ége" -> "%C3%89ge"``). Loxone's community knowledge points at
    latin-1 for command strings instead; confirm against a live Miniserver
    with non-ASCII credentials before relying on this (the helper is the
    single place to switch to ``value.encode("latin-1")`` if needed).
    """
    return urllib.parse.quote(value, safe="")


def parse_loxone_url(url: str) -> tuple[str, str, int, str]:
    """Decompose a full Miniserver URL into ``(scheme, hostname, port, path)``.

    ``scheme`` defaults to ``http`` when the URL carries none, and ``port``
    is always concrete (80/443 applied for the scheme's default). This is
    the single source of URL-building logic (API-11) and is covered by the
    URL table in ``tests/test_connection_unit.py`` — including the Cloud-DNS
    redirect case where the scheme itself changes.
    """
    if "://" not in url:
        # scheme-less host[:port] (the constructor accepts those too)
        url = f"//{url}"
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname or " " in hostname:
        raise ValueError(f"Cannot parse hostname from '{url}'")
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "http"
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, hostname, port, parsed.path or ""


def build_loxone_url(scheme: str, hostname: str, port: int, path: str = "") -> str:
    """Build the scheme-less base address ``host[:port][/path]``.

    Mirrors the construction in ``LoxoneBaseConnection.__init__``: the
    scheme's default port is omitted (``192.168.1.5`` for http, not
    ``192.168.1.5:80``).
    """
    default_port = 443 if scheme == "https" else 80
    hostpart = f"{hostname}:{port}" if port and port != default_port else hostname
    if path:
        path = path.rstrip("/")
    return f"{hostpart}{path}"


def build_websocket_options(
    *,
    open_timeout: float,
    max_size: int = MAX_WEBSOCKET_MESSAGE_SIZE,
    ssl_context: ssl.SSLContext | None = None,
    create_connection=LoxoneClientConnection,
) -> dict:
    """Build the ``websockets.connect`` options dict (API-06).

    ``ping_interval=None`` disables websockets' 20s protocol-level ping: the
    Loxone protocol already carries its own 30s keepalive, and a late pong
    from the transport ping killed healthy connections with a 1011 and no
    usable diagnostics (JoDehli/PyLoxone#486 #457 #514). ``close_timeout``
    is stated explicitly instead of silently riding the library default.

    VERIFY: confirm ``ping_interval=None`` against a live Miniserver with
    websockets DEBUG logging before depending on the Loxone keepalive as the
    single liveness channel — flip it back to a margin above 30s if the
    miniserver drops idle sockets.
    """
    options: dict[str, Any] = {
        "open_timeout": open_timeout,
        "create_connection": create_connection,
        "compression": None,
        "max_size": max_size,
        "ping_interval": None,
        "close_timeout": WEBSOCKET_CLOSE_TIMEOUT,
    }
    if ssl_context is not None:
        options["ssl"] = ssl_context
    return options


# Binary-header types whose message is followed by a body. OUT_OF_SERVICE
# (5) and KEEPALIVE (6) headers stand alone, and non-0x03 headers never
# carry one (message.py pins their payload length to 0). (API-18)
_BODY_CARRIERS = frozenset(
    {
        MessageType.TEXT,
        MessageType.VALUE_STATES,
        MessageType.TEXT_STATES,
        MessageType.DAYTIMER_STATES,
        MessageType.WEATHER_STATES,
    }
)


class LoxoneBaseConnection:
    _URL_FORMAT = "ws://{url}/ws/rfc6455"
    _SSL_URL_FORMAT = "wss://{url}/ws/rfc6455"

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        token: Optional[dict] = None,
        port: int = 8080,
        timeout: Optional[float] = None,
        verify_ssl: bool = True,
        executor: Optional[Callable[..., Any]] = None,
        token_change_callback: Optional[Callable[[dict], Any]] = None,
    ):
        # ``executor`` (API-19) runs a function with arguments off the event
        # loop and returns its result — e.g. ``hass.async_add_executor_job``
        # in HA, where parsing the multi-megabyte LoxAPP3.json in-loop used
        # to stall the heartbeat.
        # Validate input parameters
        if not host or not isinstance(host, str):
            raise ValueError("Host must be a non-empty string")
        if not username or not isinstance(username, str):
            raise ValueError("Username must be a non-empty string")
        if not password or not isinstance(password, str):
            raise ValueError("Password must be a non-empty string")
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError(f"Port must be an integer between 1 and 65535, got {port}")
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout < 0):
            raise ValueError(f"Timeout must be a non-negative number or None, got {timeout}")
        if not isinstance(verify_ssl, bool):
            raise ValueError("verify_ssl must be a boolean")

        self.host = host
        self.username = username
        # API-12: protocol-URL parts must be percent-encoded (see VERIFY note
        # on percent_encode_credential). Kept raw for credential *hashing*
        # (which is not a URL), and encoded for embedding in commands.
        self._encoded_username = percent_encode_credential(username)
        self.password = password
        self.token = token
        self.port = port
        self.timeout = None if timeout == 0 else timeout
        self.verify_ssl = verify_ssl
        self.connection: wslib.ClientConnection | None = None
        self._session: aiohttp.ClientSession | None = None
        self._executor = executor
        # API-17: the owner (coordinator) persists the token every time the
        # Miniserver changes it; may be sync or async, or None.
        self._token_change_callback = token_change_callback
        self._pending_task = []
        self._closed = False
        self._key_update_event: Optional[asyncio.Event] = None
        self._shutdown_event = asyncio.Event()
        self._reconnect_event: asyncio.Event = asyncio.Event()
        # API-14: fire-and-forget tasks must be tracked so their exceptions
        # are observed (the old bare create_task calls swallowed them).
        self._tasks: set[asyncio.Task] = set()
        # API-16: the token-refresh loop may only start once a token is
        # actually authenticatable; previously a missing token clamped the
        # sleep to 1s and spun at 1 Hz forever.
        self._authenticated_event = asyncio.Event()
        self._refresh_failures = 0
        # API-27: one lost/restored pair per outage.
        self._confirmed_connected = False
        self._outage_active = False

        # Parse the server input to extract scheme if present
        try:
            parsed = urlparse(host if "://" in host else f"//{host}", scheme="")
            self.scheme = parsed.scheme or ("https" if port == 443 else "http")
            netloc = parsed.hostname or parsed.path

            if not netloc:
                raise ValueError(f"Cannot parse hostname from '{host}'")
        except Exception as e:
            raise ValueError(f"Invalid host format '{host}': {e}") from e

        # do not use port 80 or 443 if the scheme is http/ws or https/wss
        default_port = 80 if self.scheme == "http" else 443
        used_port = port if port and port != default_port else None

        # Build the full address but without the scheme
        if used_port:
            self.url = f"{netloc}:{used_port}{parsed.path}"
        else:
            self.url = f"{netloc}{parsed.path}"

        # Generate random 16 byte AES initialisation vector (iv)
        try:
            self._iv: bytes = get_random_bytes(IV_BYTES)
            # Generate an AES256-CBC key.
            self._aes_key: bytes = get_random_bytes(AES_KEY_SIZE)
        except Exception as e:
            raise RuntimeError(f"Failed to generate cryptographic keys: {e}") from e

        self._public_key: str = ""
        # API-24: the annotation was never initialised, so the
        # ``if not self._session_key`` guard in start_listening raised
        # AttributeError instead of the intended RuntimeError when reached
        # before open().
        self._session_key: bytes | None = None

        self.miniserver_version: list[int] = []
        self.miniserver_serial: str = ""
        self.structure_file: dict = {}

        # Validate and initialize token
        try:
            if self.token and self.token.get("token") and self.token.get("valid_until") and self.token.get("hash_alg"):
                token_str = self.token.get("token")
                valid_until = self.token.get("valid_until")
                hash_alg = self.token.get("hash_alg")
                unsecure_password = self.token.get("unsecure_password", False)

                if not isinstance(token_str, str) or not token_str:
                    raise ValueError("Token must be a non-empty string")
                if not isinstance(valid_until, (int, float)) or valid_until < 0:
                    raise ValueError("valid_until must be a non-negative number")
                if hash_alg not in ("SHA1", "SHA256"):
                    raise ValueError(f"hash_alg must be 'SHA1' or 'SHA256', got '{hash_alg}'")

                self._token = LoxoneToken(
                    token=token_str,
                    valid_until=valid_until,
                    hash_alg=hash_alg,
                    key="",
                    unsecure_password=unsecure_password,
                )
            else:
                self._token = LoxoneToken()
        except Exception as e:
            _LOGGER.error(f"Failed to initialize token: {e}")
            self._token = LoxoneToken()

        self._key: str = ""
        self._user_salt: str = ""
        self._hash_alg: str = ""

        self._salt_has_expired: bool = False
        self._salt_time_stamp: int = 0
        self._salt: str = ""
        self._salt_used_count: int = 0
        self._visual_hash = None
        # Bounded queue for plain protocol commands: the websocket is a
        # single in-order writer, and a full queue must fail loudly.
        self._message_queue: asyncio.Queue[MessageForQueue] = asyncio.Queue(maxsize=1000)
        # API-15: unbounded deque of SecuredCommand parameters, drained (and
        # re-hashed with the fresh salt) by the getvisusalt handler, cleared
        # on close().
        self._secured_queue: deque[SecuredCommand] = deque()
        self.message_header = None

    def _apply_url(self, url: str) -> None:
        """Adopt scheme/host/port from a full URL (API-11).

        The Loxone Cloud-DNS redirect can change the *scheme* as well as the
        host (a ``http://<miniserver>`` address redirects to
        ``https://xxx.loxone.com``); the websocket (``wss://``/non-default
        port) and the http client both key off ``self.scheme``/``self.url``,
        so all three must be taken from the redirect target at once.
        """
        scheme, hostname, port, path = parse_loxone_url(url)
        self.scheme = scheme
        self.host = hostname
        self.port = port
        self.url = build_loxone_url(scheme, hostname, port, path)

    def _websocket_ssl_context(self) -> ssl.SSLContext | None:
        """Return an unverified TLS context when explicitly configured."""
        if self.scheme != "https" or self.verify_ssl:
            return None

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    @property
    def is_connected(self) -> bool:
        """Check if the websocket connection is open."""
        return (
            self.connection is not None
            and hasattr(self.connection, "protocol")
            and self.connection.protocol.state.name == "OPEN"
        )

    def get_token_dict(self) -> dict:
        try:
            return {
                "token": self._token.token,
                "valid_until": self._token.valid_until,
                "hash_alg": self._token.hash_alg,
                "unsecure_password": self._token.unsecure_password,
            }
        except AttributeError as e:
            _LOGGER.error(f"Token attributes missing: {e}")
            return {}

    def reset_token(self):
        try:
            self._token = LoxoneToken()
            self._authenticated_event.clear()  # API-16: no valid token anymore
            _LOGGER.debug("Token reset successfully")
        except Exception as e:
            _LOGGER.error(f"Failed to reset token: {e}")

    def _spawn_task(self, coro, name: str) -> asyncio.Task:
        """Create a tracked background task (API-14).

        Replaces every bare ``asyncio.create_task`` in this file: the task
        joins ``self._tasks`` and a done-callback observes its exception so
        a fire-and-forget failure is at least logged instead of swallowed.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.warning("Tracked task %s failed: %s", task.get_name(), exc)
            _LOGGER.debug("Tracked task %s failed (traceback)", task.get_name(), exc_info=True)

    def _note_refresh_failure(self, reason: str) -> None:
        """API-16: repeated token-refresh failures escalate to LoxoneTokenError.

        A single transient failure is a WARNING; ``TOKEN_REFRESH_MAX_FAILURES``
        in a row means auth is fundamentally broken, and raising
        LoxoneTokenError forces the reload that retries it from scratch.
        """
        self._refresh_failures += 1
        if self._refresh_failures >= TOKEN_REFRESH_MAX_FAILURES:
            raise LoxoneTokenError(f"token refresh failed {self._refresh_failures} times in a row (last: {reason})")
        _LOGGER.warning(
            "Token refresh attempt failed (%d/%d): %s",
            self._refresh_failures,
            TOKEN_REFRESH_MAX_FAILURES,
            reason,
        )

    def _check_auth_response(self, mess_obj: TextMessage, context: str) -> None:
        """API-13: check the LL code of every auth response.

        Only ok-ish codes pass silently. 401/4003 raise
        :class:`LoxoneUnauthorisedError` so bad credentials fail loudly
        instead of hanging the setup (the old code swallowed the empty
        token the 401 response carries). Any other non-ok code is a
        WARNING: the miniserver's exact error-code vocabulary beyond
        401/4003 is not documented, so we surface but do not raise.
        """
        code = getattr(mess_obj, "code", None)
        try:
            code = int(code) if code is not None else None
        except TypeError, ValueError:
            code = None
        if code in (None, 0, 200):
            return
        if code in LLRSP_UNAUTHORISED_CODES:
            raise LoxoneUnauthorisedError(f"Miniserver rejected {context}: LL code {code}")
        _LOGGER.warning("Unexpected LL code %s for %s", code, context)

    def _signal_token_changed(self) -> None:
        """API-17: notify the owner whenever a (new) token changes.

        The persisted token would otherwise only be written at HA shutdown,
        and the ``unsecurePass`` flag would be dropped entirely.
        """
        callback = self._token_change_callback
        if callback is None:
            return
        token = self.get_token_dict()
        if not token.get("token"):
            return
        try:
            result = callback(token)
        except Exception as e:
            _LOGGER.warning("Token change callback failed: %s", e)
            return
        if inspect.isawaitable(result):
            self._spawn_task(result, name="loxone-token-change-callback")

    def _note_connection_lost(self, reason: str) -> None:
        """API-27: one 'lost connection' WARNING per outage, DEBUG on repeats."""
        if not self._confirmed_connected:
            _LOGGER.debug("Connection issue before first confirmed connection: %s", reason)
            return
        if self._outage_active:
            _LOGGER.debug("Connection still lost (%s): %s", self.url, reason)
        else:
            self._outage_active = True
            _LOGGER.warning("Lost connection to Miniserver %s: %s", self.url, reason)

    def _note_connection_restored(self) -> None:
        """API-27: the 'restored connection' half of one lost/restored pair."""
        self._confirmed_connected = True
        if self._outage_active:
            self._outage_active = False
            _LOGGER.info("Restored connection to Miniserver %s", self.url)

    async def _send_text_command(self, command: str = "", encrypted: bool = False) -> None:
        """Send a (text) command to the Miniserver.

        If encrypted=True, the message will be encrypted, and will be sent using
        Loxone's 'jdev/sys/enc' command. We do not handle 'jdev/sys/fenc'
        full encryption, which encrypts the response as well.

        Miniserver gen 2 uses TLS, so most commands do not need encrypting. But
        some commands (to do with tokens) still seem to need it.

        Security note (API-25): the AES-CBC fixed IV and AES-256 key are
        established once per session by the RSA / key-exchange dance in
        open(); this is protocol-mandated and must not be "improved" into
        random-IV CBC without breaking the miniserver. The salt rotation
        thresholds (SALT_MAX_USE_COUNT / SALT_MAX_AGE_SECONDS) are the
        mitigation against IV reuse.

        Raises LoxoneConnectionClosedOk when the websocket is not open
        (API-04: the old code only *warned* here and then kept on, crashing
        on ``self.connection.send`` with AttributeError).
        """
        _LOGGER.debug("Send text command: %s", command)
        if encrypted:
            if self._new_salt_needed():
                old_salt = self._salt
                self._generate_salt()
                # The miniserver needs the text terminated with a zero byte,
                # though this is not documented
                command_string = f"nextSalt/{old_salt}/{self._salt}/{command}\x00"
            else:
                command_string = f"salt/{self._salt}/{command}\x00"
            padded_bytes = Padding.pad(bytes(command_string, "utf-8"), 16)
            aes_cipher = AES.new(self._aes_key, AES.MODE_CBC, self._iv)
            cipher = b64encode(aes_cipher.encrypt(padded_bytes))
            enc_cipher = urllib.parse.quote(cipher.decode())
            command = f"jdev/sys/enc/{enc_cipher}"
        if self.connection is None or not self.is_connected:
            raise LoxoneConnectionClosedOk("Cannot send command - connection is not open")
        try:
            # A plain str goes out as a single frame; a list would trigger
            # websockets fragmentation (API-10).
            await self.connection.send(command)
        except websockets.ConnectionClosedOK as e:
            raise LoxoneConnectionClosedOk("Connection closed normally while sending command") from e
        except Exception as e:
            _LOGGER.error("Error while sending command: %s", e)
            raise

    def _decrypt(self, command: str) -> bytes:
        """AES decrypt a command returned by the miniserver."""
        # control will be in the form:
        # "jdev/sys/enc/CHG6k...A=="
        # Encrypted strings returned by the miniserver are not %encoded (even
        # if they were when sent to the miniserver )
        remove_text = "jdev/sys/enc/"
        enc_text = command[len(remove_text) :] if command.startswith(remove_text) else command
        decoded = b64decode(enc_text)
        aes_cipher = AES.new(self._aes_key, AES.MODE_CBC, self._iv)
        decrypted = aes_cipher.decrypt(decoded)
        unpadded = Padding.unpad(decrypted, 16)
        # The miniserver seems to terminate the text with a zero byte
        return unpadded.rstrip(b"\x00")

    def _generate_salt(self) -> None:
        try:
            _LOGGER.debug("Generating a new salt")
            self._salt = get_random_bytes(SALT_BYTES).hex()
            self._salt_time_stamp = time_elapsed_in_seconds()
            self._salt_used_count = 0
        except Exception as e:
            _LOGGER.error(f"Failed to generate salt: {e}")
            raise RuntimeError(f"Salt generation failed: {e}") from e

    def _new_salt_needed(self):
        try:
            self._salt_used_count += 1
            if (
                self._salt_used_count > SALT_MAX_USE_COUNT
                or time_elapsed_in_seconds() - self._salt_time_stamp > SALT_MAX_AGE_SECONDS
            ):
                return True
            return False
        except Exception as e:
            _LOGGER.error(f"Error checking salt expiration: {e}")
            return True  # Generate new salt on error to be safe

    async def _refresh_token(self):
        try:
            token_hash = self._hash_token()
            if token_hash is None:
                raise RuntimeError("Failed to hash token")

            if self.miniserver_version < [10, 2]:
                command = f"{CMD_REFRESH_TOKEN}{token_hash}/{self._encoded_username}"
            else:
                command = f"{CMD_REFRESH_TOKEN_JSON_WEB}{token_hash}/{self._encoded_username}"

            try:
                await self._message_queue.put(MessageForQueue(command, True))
            except asyncio.TimeoutError:
                _LOGGER.error("Timeout adding refresh token command to queue")
                raise
        except Exception as e:
            _LOGGER.error(f"Token refresh failed: {e}")
            raise

    def _hash_token(self):
        try:
            if not self._token or not self._token.token:
                _LOGGER.error("No token available to hash")
                return None

            if not self._key:
                _LOGGER.error("No key available for token hashing")
                return None

            token_hash_str = f"{self._token.token}"

            if self._hash_alg == "SHA1":
                hash_module = SHA1
            elif self._hash_alg == "SHA256":
                hash_module = SHA256
            else:
                _LOGGER.error(f"Unrecognised hash algorithm: {self._hash_alg}")
                return None

            try:
                key_bytes = bytes.fromhex(self._key)
            except ValueError as e:
                _LOGGER.error(f"Invalid hex key format: {e}")
                return None

            try:
                digester = HMAC.new(key_bytes, token_hash_str.encode("utf-8"), hash_module)
                return digester.hexdigest()
            except Exception as e:
                _LOGGER.error(f"HMAC generation failed: {e}")
                return None

        except Exception as e:
            _LOGGER.error(f"Token hashing error: {e}")
            return None

    def _secured_command_text(self, secured: SecuredCommand) -> str | None:
        """Compute the ``jdev/sps/ios/...`` command text for a secured change.

        Called from the getvisusalt handler, by which time the visual key/salt
        are known. Returns ``None`` (and logs) when the visual hash is missing
        or unusable (API-15: the old code dereferenced
        ``self._visual_hash.salt`` without a None guard).
        """
        if self._visual_hash is None or not self._visual_hash.key or not self._visual_hash.salt:
            _LOGGER.warning(
                "Cannot send secured command for %s: visual salt not available, dropping it",
                secured.device_uuid,
            )
            return None
        pwd_hash_str = f"{secured.code}:{self._visual_hash.salt}"
        if self._visual_hash.hash_alg == "SHA1":
            m = hashlib.sha1()
            hash_module = SHA1
        elif self._visual_hash.hash_alg == "SHA256":
            m = hashlib.sha256()
            hash_module = SHA256
        else:
            _LOGGER.error("Unrecognised hash algorithm: %s", self._visual_hash.hash_alg)
            return None

        m.update(pwd_hash_str.encode("utf-8"))
        pwd_hash = m.hexdigest().upper()

        try:
            key_bytes = bytes.fromhex(self._visual_hash.key)
        except ValueError:
            _LOGGER.error("Visual salt key is not valid hex, dropping secured command for %s", secured.device_uuid)
            return None

        digester = HMAC.new(key_bytes, pwd_hash.encode("utf-8"), hash_module)
        new_hash = digester.hexdigest()
        return f"jdev/sps/ios/{new_hash}/{secured.device_uuid}/{secured.value}"

    def _hash_credentials(self):
        try:
            pwd_hash_str = f"{self.password}:{self._user_salt}"
            if self._hash_alg == "SHA1":
                m = hashlib.sha1()
                hash_module = SHA1

            elif self._hash_alg == "SHA256":
                m = hashlib.sha256()
                hash_module = SHA256
            else:
                _LOGGER.error(f"Unrecognised hash algorithm: {self._hash_alg}")
                return None

            m.update(pwd_hash_str.encode("utf-8"))
            pwd_hash = m.hexdigest().upper()
            pwd_hash = f"{self.username}:{pwd_hash}"
            digester = HMAC.new(bytes.fromhex(self._key), pwd_hash.encode("utf-8"), hash_module)
            _LOGGER.debug("hash_credentials successfully...")
            return digester.hexdigest()
        except ValueError:
            _LOGGER.debug("error hash_credentials...")
            return None

    async def kill_token(self) -> None:
        """API-17: invalidate the current token on the Miniserver.

        Best-effort by contract: a failure must never block close() or
        config-entry removal, so it only logs. Called before close() so
        the websocket is still usable (see coordinator.async_cleanup).

        VERIFY: the killtoken command form is taken from Loxone's legacy
        32-char-token documentation; confirm against a live Miniserver
        that the same form cancels JSON-web (SHA256) tokens before
        relying on it for clean-up of those.
        """
        token = self._token.token if self._token else ""
        if not token:
            _LOGGER.debug("killtoken: no token to invalidate, skipping")
            return
        if not self.is_connected:
            _LOGGER.debug("killtoken: connection is not open, skipping")
            return
        command = f"{CMD_KILL_TOKEN}/{token}/{self._encoded_username}"
        try:
            await self._send_text_command(command, encrypted=False)
            _LOGGER.debug("killtoken sent")
        except Exception as e:
            _LOGGER.warning("killtoken failed (token may linger server-side): %s", e)

    async def _get_with_retry(
        self, connector, endpoint: str, what: str, *, base_delay: float = CONNECT_RETRY_BASE_DELAY
    ):
        """API-08: one GET with bounded retries and exponential backoff.

        ``CONNECT_TRIES`` (3) tries total, retrying on the transport-level
        failures only. Short enough that a Miniserver that is down stays
        in `ConfigEntryNotReady` retry semantics, and long enough to beat
        the flakiest first-second network hiccups. Applied uniformly to
        all three bootstrap GETs (API key, LoxAPP3.json, public key).
        """
        for attempt in range(1, CONNECT_TRIES + 1):
            try:
                return await connector.get(endpoint)
            except (LoxoneServiceUnAvailableError, ConnectionError, OSError, TimeoutError) as e:
                if attempt == CONNECT_TRIES:
                    _LOGGER.warning("Giving up fetching %s after %d tries: %s", what, CONNECT_TRIES, e)
                    raise
                delay = base_delay * (2 ** (attempt - 1))
                _LOGGER.debug(
                    "Fetching %s failed (attempt %d/%d), retrying in %.1fs: %s",
                    what,
                    attempt,
                    CONNECT_TRIES,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)


class LoxoneConnection(LoxoneBaseConnection):
    connection: Optional[LoxoneClientConnection]
    _recv_loop: Optional["asyncio.Task[None]"]

    async def __aenter__(self) -> "LoxoneConnection":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    async def start_listening(self, callback: Optional[Callable[[str, Any], Optional[Awaitable[None]]]] = None) -> None:
        """Open, and start listening."""

        # API-01: open() stores the websocket on self.connection (and the
        # session that created it), so the coordinator's open() and this
        # method share exactly one socket. The fallback re-open only runs
        # for standalone users of the library who call start_listening
        # before open().
        if self.connection is None:
            _LOGGER.debug("No existing connection found. Opening a new connection.")
            await self.open(self._session)
        else:
            _LOGGER.debug("Using existing connection.")

        # Clear shutdown event when starting
        self._shutdown_event.clear()

        async def keep_alive() -> None:
            """Send keep-alive messages to the Miniserver."""
            try:
                while True:
                    await asyncio.sleep(KEEP_ALIVE_PERIOD)
                    try:
                        # API-14: await the send directly — a detached task
                        # used to continue while the exception was swallowed.
                        await self._send_text_command(CMD_KEEP_ALIVE, encrypted=False)
                        await asyncio.sleep(0)
                    except LoxoneConnectionClosedOk:
                        raise  # Re-raise to trigger
                    except Exception as exc:
                        _LOGGER.warning(f"Keep-alive message failed: {exc}")
                        raise
            except LoxoneConnectionClosedOk:
                raise
            except asyncio.CancelledError:
                _LOGGER.debug("Keep-alive task cancelled")
                raise
            except Exception as exc:
                _LOGGER.warning(f"Keep-alive task encountered an error: {exc}")
                raise

        async def check_refresh_token() -> None:
            """Check if the token needs to be refreshed.

            API-16: the loop blocks on ``_authenticated_event`` instead of
            spinning at 1 Hz while no token exists. The key request and the
            refresh are ``await``ed (API-14), and repeated failures escalate
            to LoxoneTokenError after ``TOKEN_REFRESH_MAX_FAILURES``.
            """
            _LOGGER.debug("Start check refresh token task...")
            await asyncio.sleep(DELAY_CHECK_TOKEN_REFRESH)
            try:
                while not self._shutdown_event.is_set():
                    try:
                        await self._authenticated_event.wait()
                        if self._shutdown_event.is_set():
                            break

                        # Calculate 50% of the token lifetime as an integer
                        # and limit it to MAX_REFRESH_DELAY
                        try:
                            candidate = int(self._token.seconds_to_expire() * 0.5)
                        except ValueError:
                            # Token was reset while waiting for auth (e.g. a
                            # 401): wait for the re-authenticated token.
                            _LOGGER.debug("Token reset; waiting for re-authentication before refresh")
                            continue

                        def generate_refresh_time_log(_seconds_to_refresh: int) -> str:
                            days, remainder = divmod(_seconds_to_refresh, 86400)
                            hours, seconds = divmod(remainder, 3600)
                            minutes, seconds = divmod(seconds, 60)
                            return f"{days}d {hours}h {minutes}m {seconds}s"

                        seconds_to_refresh = max(1, min(candidate, MAX_REFRESH_DELAY))
                        _LOGGER.debug(f"Seconds to refresh token: {generate_refresh_time_log(seconds_to_refresh)}")

                        await asyncio.sleep(seconds_to_refresh)
                        # Check shutdown before proceeding
                        if self._shutdown_event.is_set():
                            break

                        # gets a new key for the token refresh
                        old_key = self._key
                        key_updated_event = asyncio.Event()
                        self._key_update_event = key_updated_event  # Store for _websocket_event to signal
                        try:
                            # API-14: await the send (was a detached task).
                            await self._send_text_command(CMD_GET_KEY, encrypted=False)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            _LOGGER.debug("Error requesting new key: %s", exc)
                            self._key_update_event = None
                            self._note_refresh_failure(f"sending new-key request failed: {exc}")
                            continue

                        try:
                            await asyncio.wait_for(key_updated_event.wait(), timeout=15.0)
                            # Verify key actually changed
                            if self._key == old_key:
                                self._note_refresh_failure("key was not updated despite event being set")
                            else:
                                _LOGGER.debug("Key changed successfully.")
                                # API-14: await the refresh (was a detached
                                # task; failures were invisible).
                                await self._refresh_token()
                                self._refresh_failures = 0
                        except asyncio.TimeoutError:
                            self._note_refresh_failure("timed out waiting for new key (15s)")
                        except LoxoneTokenError:
                            raise
                        except Exception as e:
                            self._note_refresh_failure(f"token refresh cycle failed: {e}")
                        finally:
                            self._key_update_event = None

                    except LoxoneTokenError:
                        raise
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        _LOGGER.warning(f"Error in token refresh cycle: {e}")
                        await asyncio.sleep(1)  # Avoid tight loop on errors

            except asyncio.CancelledError:
                _LOGGER.debug("Token refresh task cancelled")
                raise
            except Exception as exc:
                _LOGGER.warning(f"Token refresh task failed: {exc}")
                raise

        try:
            if not self._session_key:
                raise RuntimeError("Session key not initialized")

            await self.connection.send(f"{CMD_KEY_EXCHANGE}{self._session_key.decode()}")
        except Exception as e:
            _LOGGER.error(f"Failed to send key exchange: {e}")
            raise

        async def reconnect_task() -> None:
            try:
                while True:
                    # Create explicit tasks for the event waits (coroutines are not allowed)
                    t_shutdown = asyncio.create_task(self._shutdown_event.wait())
                    t_reconnect = asyncio.create_task(self._reconnect_event.wait())

                    try:
                        _done, _pending = await asyncio.wait(
                            {t_shutdown, t_reconnect},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        # Ensure any still-pending tasks are canceled to avoid leaks
                        for p in (t_shutdown, t_reconnect):
                            if not p.done():
                                p.cancel()

                    # Shutdown requested -> exit cleanly
                    if self._shutdown_event.is_set():
                        return

                    # Reconnect requested (e.g. stale token) -> control flow,
                    # not an error: DEBUG (API-27), and typed as
                    # LoxoneReconnectRequested so callers can tell it apart.
                    if self._reconnect_event.is_set():
                        self._reconnect_event.clear()
                        raise LoxoneReconnectRequested("reconnect requested by connection layer")
            except asyncio.CancelledError:
                # Task was canceled during shutdown
                raise

        # noinspection PyUnreachableCode
        self._pending_task = [
            asyncio.create_task(self._do_start_listening(callback, self.connection)),
            asyncio.create_task(self._process_message()),
            asyncio.create_task(keep_alive()),
            asyncio.create_task(check_refresh_token()),
            asyncio.create_task(reconnect_task()),
        ]

        try:
            # API-02: return_when=FIRST_COMPLETED so a task that ends *normally*
            # (e.g. the listen loop ending on a clean close) also wakes us.
            waiters = [task for task in self._pending_task if not task.done()]
            while waiters:
                done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    waiters.remove(task)
                    if task.cancelled():
                        continue
                    try:
                        await task
                    except LoxoneConnectionClosedOk as e:
                        # Expected: token expiry, firmware restart, session
                        # limit. INFO once per outage - never ERROR
                        # (API-02/27). The raised exception is how the caller
                        # restarts the listen loop.
                        self._note_connection_lost(f"websocket closed normally: {e}")
                        _LOGGER.info("Loxone websocket closed normally; reconnecting (%s)", e)
                        raise
                    except LoxoneReconnectRequested as e:
                        # Control flow - the code recovers from this
                        # (API-27). DEBUG, not ERROR.
                        _LOGGER.debug("Reconnect requested: %s", e)
                        raise
                    except LoxoneOutOfServiceException as e:
                        self._note_connection_lost("miniserver out of service (restarting?)")
                        _LOGGER.warning("Miniserver out of service: %s", e)
                        raise
                    except LoxoneUnauthorisedError as e:
                        # Bad credentials surfaced by an auth response code
                        # (API-13). WARNING: the user can act on it.
                        self._note_connection_lost(f"authentication rejected: {e}")
                        _LOGGER.warning("Miniserver rejected authentication: %s", e)
                        raise
                    except LoxoneTokenError as e:
                        # Control flow: not-valid-anymore token -> reload.
                        # DEBUG (API-27): not an error, the reload recovers.
                        _LOGGER.debug("Token error (control flow): %s", e)
                        raise
                    except websockets.exceptions.ConnectionClosedError as e:
                        self._note_connection_lost(f"websocket closed with error: {e}")
                        _LOGGER.debug("Connection closed with error: %s", e, exc_info=True)
                        raise LoxoneConnectionError from e
                    except websockets.exceptions.ConnectionClosed:
                        self._note_connection_lost("websocket closed by server")
                        _LOGGER.debug("Connection closed by websocket, converting to LoxoneConnectionError")
                        raise LoxoneConnectionError("Connection closed") from None
                    except asyncio.CancelledError:
                        pass
                        # Don't raise, this is expected during shutdown
                    except Exception as e:
                        # A real failure: WARNING headline, first traceback
                        # at DEBUG (API-27).
                        self._note_connection_lost(f"connection loop failed: {e}")
                        _LOGGER.warning(f"Task {task.get_name()} failed: {e}")
                        _LOGGER.debug(f"Task {task.get_name()} failed (traceback)", exc_info=True)
                        raise
        except asyncio.CancelledError:
            _LOGGER.debug("Listening task cancelled")
            raise
        finally:
            # Cancel pending tasks
            for task in self._pending_task:
                if task and not task.done():
                    task.cancel()

            if self._pending_task:
                await asyncio.gather(*self._pending_task, return_exceptions=True)

    async def _process_message(self) -> NoReturn:
        """Process queued messages with graceful shutdown."""
        _LOGGER.debug("Message processing task started")

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Use asyncio.Queue.get() with timeout
                    msg = await self._message_queue.get()
                    try:
                        # API-14: await each send before task_done() so the
                        # queue's join() reflects completed sends (the old
                        # fire-and-forget task detached before the frame
                        # left the socket and its exceptions were lost).
                        try:
                            await self._send_text_command(msg.command, encrypted=msg.flag)
                        except Exception as e:
                            _LOGGER.warning(f"Error sending message: {e}")
                            _LOGGER.debug("Error sending message (traceback)", exc_info=True)
                    finally:
                        # Mark task as done for queue.join() - AFTER the
                        # send above has completed or failed.
                        self._message_queue.task_done()
                except asyncio.TimeoutError:
                    # Normal timeout, continue to check shutdown event
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    _LOGGER.error(f"Error in message processing loop: {e}")
                    await asyncio.sleep(0.1)  # Avoid tight loop on errors

        except asyncio.CancelledError:
            _LOGGER.debug("Message processing task cancelled - processing remaining messages")

            # Process any remaining messages before shutdown
            remaining_count = 0
            while not self._message_queue.empty():
                try:
                    msg = self._message_queue.get_nowait()
                    remaining_count += 1
                    try:
                        await self._send_text_command(msg.command, encrypted=msg.flag)
                    except Exception as e:
                        _LOGGER.error(f"Error processing final message: {e}")
                    finally:
                        self._message_queue.task_done()
                except asyncio.QueueEmpty:
                    break
                except Exception as e:
                    _LOGGER.error(f"Error draining message queue: {e}")

            if remaining_count > 0:
                _LOGGER.debug(f"Processed {remaining_count} remaining messages during shutdown")

            raise
        except Exception as e:
            _LOGGER.error(f"Message processing task failed: {e}")
            raise

    async def _do_start_listening(
        self,
        callback: Optional[Callable[[Any], Optional[Awaitable[None]]]],
        connection: LoxoneClientConnection,
    ) -> None:

        # Optimization: Use a set for O(1) lookup instead of creating a list every iteration
        callback_types = {
            MessageType.VALUE_STATES,
            MessageType.TEXT_STATES,
            MessageType.TEXT,
            MessageType.KEEPALIVE,
        }

        last_header: Optional[MessageHeader] = None

        async def _run_callback(msg) -> None:
            if callback is None:
                return
            try:
                await callback(msg.as_dict())
            except Exception as e:
                _LOGGER.error("Callback error: %s", e, exc_info=True)

        try:
            # Strict header→body sequencing (API-18), ported from the
            # removed ``LoxoneClientConnection.recv_message`` which knew
            # the wire format:
            #
            # * a frame received while no body is pending is a binary 8-byte
            #   MessageHeader — decided by *state*, not by length, so an
            #   8-byte body is no longer misread as a header;
            # * the out-of-service and keepalive headers stand alone (no
            #   body); the keepalive is handled inline;
            # * an Estimated-Header (cInfo bit set) is always followed by
            #   an exact header carrying the real length (Loxone docs), so
            #   the first 8-byte frame after an estimated header IS that
            #   exact header — even when a real body frame is 8 bytes long;
            # * body frames are concatenated until payload_length bytes
            #   have been received.
            body = bytearray()

            async for message in connection:
                if not connection or connection.state == connection.state.CLOSED:
                    raise LoxoneConnectionError("Connection is closed")

                if last_header is None:
                    if not isinstance(message, (bytes, bytearray)) or len(message) != 8:
                        _LOGGER.error("Message not handled: %r", message)
                        continue
                    last_header = parse_header(message)
                    if last_header.message_type == MessageType.OUT_OF_SERVICE:
                        raise LoxoneOutOfServiceException
                    if last_header.message_type == MessageType.KEEPALIVE:
                        # A keepalive header stands alone, no body follows
                        last_header = None
                        await _run_callback(Keepalive(""))
                        continue
                    if last_header.message_type not in _BODY_CARRIERS:
                        _LOGGER.debug("Header without body: %s", last_header.message_type.name)
                        last_header = None
                        continue
                    continue

                if last_header.estimated and not body and len(message) == 8 and isinstance(message, (bytes, bytearray)):
                    # The frame that follows an estimated header is always
                    # the exact header with the real payload length.
                    last_header = parse_header(message)
                    if last_header.message_type == MessageType.OUT_OF_SERVICE:
                        raise LoxoneOutOfServiceException
                    if last_header.message_type not in _BODY_CARRIERS:
                        last_header = None
                    continue

                if not isinstance(message, (bytes, bytearray)):
                    # Binary bodies are the only kind the miniserver sends;
                    # reset the stream so a later header can resync.
                    _LOGGER.error("Message not handled: %r", message)
                    last_header = None
                    continue

                body.extend(message)
                if len(body) < last_header.payload_length:
                    continue
                msg_type = last_header.message_type
                expected = last_header.payload_length
                frame = bytes(body[:expected])
                if len(body) > expected:
                    _LOGGER.error(
                        "Frame for message type %s is %d bytes, header expected %d - dropping %d trailing byte(s)",
                        msg_type,
                        len(body),
                        expected,
                        len(body) - expected,
                    )
                body.clear()
                last_header = None
                if msg_type == MessageType.TEXT:
                    frame = check_and_decode_if_needed(frame)
                parsed_message = parse_message(frame, msg_type)
                _LOGGER.debug("Parsing message type %s (%d bytes)", msg_type, expected)

                # API-26: handle the message inline instead of spawning one
                # untracked create_task per message — bursts no longer risk
                # losing the auth responses, and stream order is preserved.
                await self._websocket_event(parsed_message)
                if callback and msg_type in callback_types:
                    await _run_callback(parsed_message)

            # API-02: websockets swallows ConnectionClosedOK - the iterator
            # simply ends when the server closes the connection normally
            # (token expiry, firmware restart, session limit). Detect the
            # clean end here so the caller reconnects immediately instead of
            # sitting on a dead socket until the 30s keep-alive fails.
            close_code = getattr(connection, "close_code", None)
            raise LoxoneConnectionClosedOk(f"Miniserver closed the websocket normally (close code: {close_code})")
        except asyncio.CancelledError:
            _LOGGER.debug("Listening task cancelled")
            raise
        except (
            LoxoneTokenError,
            LoxoneOutOfServiceException,
            LoxoneConnectionError,
            LoxoneUnauthorisedError,
            LoxoneConnectionClosedOk,
        ):
            # Re-raise expected Loxone exceptions
            raise
        except Exception as e:
            # A real failure: WARNING headline, traceback at DEBUG (API-27).
            _LOGGER.warning(f"Error in listening loop: {e}")
            _LOGGER.debug("Error in listening loop (traceback)", exc_info=True)
            raise

    async def open(self, session: aiohttp.ClientSession | None = None) -> LoxoneClientConnection:

        if self._closed:
            raise RuntimeError("Cannot open a closed connection")

        # API-01: remember the session used for this connection so open()
        # and close() agree on who owns the aiohttp session.
        self._session = session

        async def _load_json(data) -> Any:
            """JSON parse that keeps LoxAPP3.json off the event loop (API-19)."""
            if self._executor is not None:
                result = self._executor(json.loads, data)
                if inspect.isawaitable(result):
                    return await result
                return result
            return json.loads(data)

        connector = None
        try:
            connector = LoxoneAsyncHttpClient(
                url=self.url,
                username=self.username,
                password=self.password,
                scheme=self.scheme,
                verify_ssl=self.verify_ssl,
                session=session,
            )
            api_resp = None
            # API-08: bounded retries with backoff on *every* bootstrap
            # GET (used to retry the first one 100 x 5s and none of the
            # other two). A down Miniserver now lands in
            # ConfigEntryNotReady retry semantics within seconds.
            api_resp = await self._get_with_retry(connector, CMD_GET_API_KEY, "API key")

            # API-20: always release the response (an early raise used to
            # leak it, pinning a connection in HA's shared aiohttp session).
            async with api_resp:
                data = await api_resp.content.read()

            try:
                _value = LLResponse(data).value
            except Exception as e:
                raise ValueError(f"Invalid API key response format: {e}") from e

            # The json returned by the miniserver is invalid. It contains " and '.
            # We need to normalize it
            try:
                value = json.loads(_value.replace("'", '"'))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in API key response: {e}") from e
            except Exception as e:
                raise ValueError(f"Failed to parse API key response: {e}") from e

            # Validate response structure
            if not isinstance(value, dict):
                raise ValueError(f"Expected dict response, got {type(value)}")

            version_str = value.get("version")
            if version_str:
                try:
                    self.miniserver_version = [int(x) for x in version_str.split(".")]
                except (ValueError, AttributeError) as e:
                    _LOGGER.warning(f"Invalid version format '{version_str}': {e}")
                    self.miniserver_version = []
            else:
                _LOGGER.warning("No version in API response")
                self.miniserver_version = []

            self.miniserver_serial = value.get("snr", "")
            local = value.get("local", True)

            if not local:
                try:
                    # API-11: the Cloud-DNS redirect changes the SCHEME too
                    # (an http://miniserver address redirects to
                    # https://xxx.loxone.com); scheme/host/port must all come
                    # from the redirect target, or the websocket would be
                    # dialed ws://-style against an https endpoint.
                    self._apply_url(str(api_resp.url).replace(CMD_GET_API_KEY, ""))
                    connector.base_url = f"{self.scheme}://{self.url}"
                except Exception as e:
                    _LOGGER.warning(f"Failed to update URL for remote access: {e}")

            # Get the structure file (API-08: same bounded retry policy)
            try:
                lox_app_data = await self._get_with_retry(connector, LOXAPPPATH, "structure file (LoxAPP3.json)")
            except Exception as e:
                _LOGGER.error(f"Failed to get structure file: {e}", exc_info=True)
                raise

            # API-20: read inside async with so a non-200 release does not
            # leak (it used to raise before the response was released).
            async with lox_app_data:
                if lox_app_data.status != 200:
                    raise RuntimeError(f"Failed to get structure file, status: {lox_app_data.status}")
                data = await lox_app_data.content.read()

            try:
                # Can be several megabytes — never parse it in the event loop.
                self.structure_file = await _load_json(data)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in structure file: {e}") from e
            self.structure_file["softwareVersion"] = (
                self.miniserver_version
            )  # FIXME Legacy use only. Need to fix pyloxone

            # Get the public key (API-08: same bounded retry policy)
            try:
                pk_data = await self._get_with_retry(connector, CMD_GET_PUBLIC_KEY, "public key")
            except Exception as e:
                raise RuntimeError(f"Failed to get public key: {e}") from e

            async with pk_data:
                pk_data_text = await pk_data.content.read()

            try:
                pk = LLResponse(pk_data_text).value
            except Exception as e:
                raise ValueError(f"Invalid public key response format: {e}") from e

            if not pk:
                raise ValueError("Empty public key received")

            # Loxone returns a certificate instead of a key
            self._public_key = pk.replace("-----BEGIN CERTIFICATE-----", "-----BEGIN PUBLIC KEY-----\n").replace(
                "-----END CERTIFICATE-----", "\n-----END PUBLIC KEY-----\n"
            )
        except LoxoneServiceUnAvailableError:
            raise
        except Exception as e:
            _LOGGER.error(f"Failed to initialize connection: {e}", exc_info=True)
            raise
        finally:
            # The aiohttp session is ours only if we had to create it
            # ourselves (API-20: "Async httpx" is a stale comment;
            # this is plain aiohttp).
            if self._session is None and connector:
                try:
                    await connector.session.close()
                except Exception as e:
                    _LOGGER.warning(f"Error closing HTTP session: {e}")

        # Init RSA cipher
        try:
            if not self._public_key:
                raise ValueError("Public key is empty")

            try:
                rsa_key = RSA.importKey(self._public_key)
            except (ValueError, IndexError, TypeError) as e:
                raise ValueError(f"Invalid RSA public key format: {e}") from e

            # RSA PKCS1 has been broken for a long time. Loxone uses it anyway
            rsa_cipher = PKCS1_v1_5.new(rsa_key)
            _LOGGER.debug("init_rsa_cipher successfully...")
        except Exception as exc:
            _LOGGER.error(f"Error creating RSA cipher: {exc}")
            raise LoxoneException(f"RSA cipher initialization failed: {exc}") from exc

        # Generate session key
        try:
            if not self._aes_key or not self._iv:
                raise RuntimeError("Encryption keys not initialized")

            aes_key = self._aes_key.hex()
            iv = self._iv.hex()
            session_key = f"{aes_key}:{iv}".encode("utf-8")

            try:
                encrypted = rsa_cipher.encrypt(session_key)
                if not encrypted:
                    raise ValueError("RSA encryption returned empty result")
                self._session_key = b64encode(encrypted)
            except Exception as e:
                raise RuntimeError(f"RSA encryption failed: {e}") from e

            _LOGGER.debug("generate_session_key successfully...")
        except Exception as exc:
            _LOGGER.error(f"Error generating session key: {exc}")
            raise LoxoneException(f"Session key generation failed: {exc}") from exc

        # generate first salt
        try:
            self._generate_salt()
        except Exception as e:
            _LOGGER.error(f"Failed to generate initial salt: {e}")
            raise

        # Establish websocket connection
        try:
            params = {"url": self.url}
            if self.scheme == "https":
                base_url = self._SSL_URL_FORMAT.format(**params)
            else:
                base_url = self._URL_FORMAT.format(**params)

            try:
                websocket_options = build_websocket_options(
                    open_timeout=self.timeout or TIMEOUT,
                    ssl_context=self._websocket_ssl_context(),
                )

                connection = await asyncio.wait_for(
                    wslib.connect(base_url, **websocket_options),
                    timeout=(self.timeout or TIMEOUT) * 2,
                )
            except asyncio.TimeoutError as e:
                raise TimeoutError(f"Timeout connecting to websocket at {base_url}") from e
            except websockets.exceptions.InvalidURI as e:
                raise ValueError(f"Invalid websocket URI '{base_url}': {e}") from e
            except websockets.exceptions.WebSocketException as e:
                raise LoxoneConnectionError(f"Websocket connection failed: {e}") from e
            except OSError as e:
                raise ConnectionError(f"Network error connecting to {base_url}: {e}") from e
            except Exception as e:
                raise RuntimeError(f"Unexpected error connecting to websocket: {e}") from e

            # API-01: store the open websocket so start_listening (and any
            # later re-entering caller) reuses it instead of dialling a
            # second socket per setup — each setup used to open *two*
            # websockets and leak one (Miniserver cap: error 901).
            self.connection = connection

            # API-27: connection established -> the 'restored' half of the
            # lost/restored pair (a no-op on the very first connection).
            self._note_connection_restored()

            _LOGGER.debug("Websocket connection established to %s", base_url)
            return connection

        except Exception as e:
            _LOGGER.error(f"Failed to establish websocket connection: {e}")
            raise

    async def close(self) -> None:
        """Gracefully close the connection and drain message queues."""
        if self._closed:
            _LOGGER.debug("Connection already closed")
            return

        _LOGGER.debug("Closing connection...")
        self._closed = True

        # API-15: secured commands queued before the salt was known must not
        # survive a close (stale visual salt => dropped after reconnect).
        self._secured_queue.clear()

        # Signal shutdown to all tasks
        self._shutdown_event.set()

        # Wait for message queue to drain (with timeout)
        if self._message_queue:
            queue_size = self._message_queue.qsize()
            if queue_size > 0:
                _LOGGER.debug(f"Waiting for {queue_size} messages to be processed...")
                try:
                    await asyncio.wait_for(self._message_queue.join(), timeout=5.0)
                    _LOGGER.debug("All messages processed")
                except asyncio.TimeoutError:
                    _LOGGER.warning(f"Timeout waiting for message queue to drain ({queue_size} messages remaining)")

        # Cancel all pending tasks
        if self._pending_task:
            _LOGGER.debug(f"Cancelling {len(self._pending_task)} pending tasks...")
            for task in self._pending_task:
                try:
                    if task and not task.done():
                        task.cancel()
                except Exception as e:
                    _LOGGER.warning(f"Error cancelling task: {e}")

            # Wait for tasks to finish or cancel
            if self._pending_task:
                try:
                    await asyncio.gather(*self._pending_task, return_exceptions=True)
                except Exception as e:
                    _LOGGER.warning(f"Error waiting for tasks to complete: {e}")

            # clear pending tasks
            self._pending_task = []

        # Close websocket connection if present
        if self.connection:
            try:
                if not self.connection.state == self.connection.state.CLOSED:
                    await asyncio.wait_for(self.connection.close(), timeout=5.0)
                    _LOGGER.debug("Websocket connection closed")
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout closing websocket connection")
            except Exception as e:
                _LOGGER.warning(f"Error closing websocket connection: {e}")
            finally:
                self.connection = None

        _LOGGER.debug("Connection closed successfully.")

    async def send_websocket_command(self, device_uuid: str, value: Union[str, int, float]):
        """Send a websocket command to the Miniserver.

        value may be a str, int or float — it will be converted to string when sent.
        """

        if not device_uuid or not isinstance(device_uuid, str):
            raise ValueError("device_uuid must be a non-empty string")

        # if value is None or not isinstance(value, (str, int, float)):
        #    raise ValueError("value must be a string, int, or float")

        try:
            command = "jdev/sps/io/{}/{}".format(device_uuid, str(value))
            _LOGGER.debug("Call send_websocket_command: {}".format(command))

            try:
                # Use put_nowait with QueueFull exception handling for backpressure
                self._message_queue.put_nowait(MessageForQueue(command=command, flag=True))
            except asyncio.QueueFull as e:
                _LOGGER.error(
                    f"Message queue full (size: {self._message_queue.maxsize}), dropping command for {device_uuid}"
                )
                raise RuntimeError("Message queue is full, cannot send command") from e
        except Exception as e:
            _LOGGER.error(f"Failed to send websocket command: {e}")
            raise

    async def send_secured_websocket_command(self, device_uuid: str, value: Union[str, int, float], code: str):
        """Queue a secured state change (API-24 renamed from
        ``send_secured__websocket_command``).

        The visual salt the HMAC needs is fetched in-flight (``getvisusalt``);
        the parameters are appended to an unbounded ``deque`` and hashed as
        soon as the salt arrives in ``_websocket_event`` (API-15).
        """
        if not device_uuid or not isinstance(device_uuid, str):
            raise ValueError("device_uuid must be a non-empty string")
        if value is None or not isinstance(value, (str, int, float)):
            raise ValueError("value must be a string, int, or float")
        if not code or not isinstance(code, str):
            raise ValueError("code must be a non-empty string")

        try:
            command = f"{CMD_GET_VISUAL_PASSWD}{self._encoded_username}"
            _LOGGER.debug("Call send_secured_websocket_command: %s", command)

            # Deque of plain parameters (never un-awaited coroutines), and
            # the salt request goes through the bounded single-writer queue.
            self._secured_queue.append(SecuredCommand(device_uuid=device_uuid, value=value, code=code))
            self._message_queue.put_nowait(MessageForQueue(command=command, flag=True))
        except asyncio.QueueFull as e:
            _LOGGER.error("Message queue is full, dropping secured command")
            raise RuntimeError("Message queue is full, cannot send secured command") from e
        except Exception as e:
            _LOGGER.error(f"Failed to send secured websocket command: {e}")
            raise

    async def send_secured__websocket_command(self, device_uuid: str, value: Union[str, int, float], code: str) -> None:
        """DEPRECATED alias of :meth:`send_secured_websocket_command` (API-24).
        Kept so callers outside this package's file set keep working; will be
        removed once all callers have been updated."""
        _LOGGER.warning("send_secured__websocket_command is deprecated; call send_secured_websocket_command instead")
        await self.send_secured_websocket_command(device_uuid, value, code)

    async def _websocket_event(self, message: dict[str, Any] | BaseMessage) -> None:
        """Handle websocket event."""
        if message is None:
            _LOGGER.warning("Received None message")
            return

        mess_obj = None
        try:
            if isinstance(message, str):
                if message.startswith("{"):
                    try:
                        mess_obj = parse_message(
                            message,
                            (self.message_header.message_type if self.message_header else None),
                        )
                    except Exception as e:
                        _LOGGER.error(f"Failed to parse string message: {e}")
                        return
            elif isinstance(message, bytes):
                try:
                    mess_obj = parse_message(
                        message,
                        (self.message_header.message_type if self.message_header else None),
                    )
                except Exception as e:
                    _LOGGER.error(f"Failed to parse bytes message: {e}")
                    return
            elif isinstance(message, BaseMessage):
                mess_obj = message
            else:
                _LOGGER.warning(f"Unexpected message type: {type(message)}")
                return

            if mess_obj is None:
                return

            # Decrypt if needed
            if hasattr(mess_obj, "control") and mess_obj.control and mess_obj.control.find("/enc/") > -1:
                try:
                    mess_obj.control = self._decrypt(mess_obj.control)
                except Exception as e:
                    _LOGGER.error(f"Failed to decrypt control message: {e}")
                    return

            # Handle key exchange
            if isinstance(mess_obj, TextMessage) and "keyexchange" in mess_obj.message:
                _LOGGER.debug("Key exchange with miniserver...")
                command = f"{CMD_GET_KEY_AND_SALT}/{self._encoded_username}"
                try:
                    # Use put() for critical protocol messages
                    await self._message_queue.put(MessageForQueue(command, True))
                except asyncio.TimeoutError:
                    _LOGGER.error("Timeout queueing key exchange command")

            # Handle getkey2
            elif isinstance(mess_obj, TextMessage) and "getkey2" in mess_obj.message:
                # API-13: check the code of *every* auth response (401 here
                # means the credentials themselves are wrong).
                self._check_auth_response(mess_obj, "getkey2 (key/salt request)")
                _LOGGER.debug("Got get key2")
                try:
                    value_dict = mess_obj.value_as_dict
                    if not isinstance(value_dict, dict):
                        raise ValueError("value_as_dict is not a dictionary")

                    self._key = value_dict.get("key", "")
                    self._user_salt = value_dict.get("salt", "")
                    # API-25: the miniserver pre-dates this field and omits
                    # hashAlg on some builds (#498); defaulting to SHA1 is the
                    # protocol-correct fallback and is regression-tested.
                    self._hash_alg = value_dict.get("hashAlg", "SHA1")

                    if not self._key:
                        raise ValueError("Key is empty")
                    if not self._user_salt:
                        raise ValueError("Salt is empty")

                    if self._token.seconds_to_expire() > 100:
                        _LOGGER.debug("Use old token...")
                        token_hash = self._hash_token()
                        if token_hash is None:
                            raise RuntimeError("Failed to hash token")
                        command = "{}{}/{}".format(CMD_AUTH_WITH_TOKEN, token_hash, self._encoded_username)
                        await self._message_queue.put(MessageForQueue(command, True))
                    else:
                        _LOGGER.debug("Acquire new token...")
                        new_hash = self._hash_credentials()
                        if new_hash is None:
                            raise RuntimeError("Failed to hash credentials")

                        # Request new Token
                        if self.miniserver_version < [10, 2]:
                            command = f"{CMD_REQUEST_TOKEN}/{new_hash}/{self._encoded_username}/{TOKEN_PERMISSION}/edfc5f9a-df3f-4cad-9dddcdc42c732b82/pyloxone_api"
                        else:
                            command = f"{CMD_REQUEST_TOKEN_JSON_WEB}/{new_hash}/{self._encoded_username}/{TOKEN_PERMISSION}/edfc5f9a-df3f-4cad-9dddcdc42c732b82/pyloxone_api"
                        await self._message_queue.put(MessageForQueue(command, True))

                except KeyError as e:
                    _LOGGER.error(f"Missing key in getkey2 response: {e}")
                except asyncio.TimeoutError:
                    _LOGGER.error("Timeout queueing getkey2 command")
                except Exception as e:
                    _LOGGER.error(f"Error processing getkey2: {e}")

            # Handle getkey
            elif isinstance(mess_obj, TextMessage) and "getkey" in mess_obj.message:
                self._check_auth_response(mess_obj, "getkey (token-key request)")
                _LOGGER.debug("Got get getkey")
                try:
                    value_dict = mess_obj.value_as_dict
                    if not isinstance(value_dict, dict):
                        raise ValueError("value_as_dict is not a dictionary")
                    self._key = value_dict.get("value", "")
                    # Signal that key has been updated
                    if self._key_update_event is not None:
                        self._key_update_event.set()

                except Exception as e:
                    _LOGGER.error(f"Error processing getkey: {e}")

            # Handle visual salt
            elif isinstance(mess_obj, TextMessage) and "getvisusalt" in mess_obj.message:
                self._check_auth_response(mess_obj, "getvisusalt (visual salt request)")
                try:
                    value_dict = mess_obj.value_as_dict
                    if not isinstance(value_dict, dict):
                        raise ValueError("value_as_dict is not a dictionary")
                    # API-07: do NOT overwrite self._key here. That field
                    # holds the *token* HMAC key (hex); the visual-salt
                    # response carries a different, non-hex key, and the old
                    # assignment made every later _hash_token() fail with
                    # bytes.fromhex ValueError, silently breaking token
                    # refresh. The visual key lives in self._visual_hash.

                    key_and_salt = LxJsonKeySalt()
                    key_and_salt.read_user_salt_response(mess_obj.message)
                    key_and_salt.time_elapsed_in_seconds = time_elapsed_in_seconds()
                    self._visual_hash = key_and_salt

                    # API-15: drain the parameter deque now that the salt is
                    # known and hash each queued secured command.
                    while self._secured_queue:
                        secured = self._secured_queue.popleft()
                        command = self._secured_command_text(secured)
                        if command is not None:
                            await self._message_queue.put(MessageForQueue(command, True))

                except Exception as e:
                    _LOGGER.error(f"Error processing visual salt: {e}")

            # Handle token response
            elif isinstance(mess_obj, TextMessage) and ("gettoken" in mess_obj.message or "getjwt" in mess_obj.message):
                # API-13: check the code BEFORE touching the value — the 401
                # response carries an empty token and the old code swallowed
                # the resulting ValueError, hanging on bad credentials.
                self._check_auth_response(mess_obj, "gettoken (token request)")
                try:
                    value_dict = mess_obj.value_as_dict
                    if not isinstance(value_dict, dict):
                        raise ValueError("value_as_dict is not a dictionary")

                    self._token.token = value_dict.get("token")
                    self._token.valid_until = value_dict.get("validUntil", 0)
                    self._token.key = value_dict.get("key", "")
                    self._token.hash_alg = self._hash_alg

                    if "unsecurePass" in value_dict:
                        self._token.unsecure_password = value_dict.get("unsecurePass", False)

                    if not self._token.token:
                        raise LoxoneTokenError("Miniserver returned an empty token")

                    await self._message_queue.put(MessageForQueue(f"{CMD_ENABLE_UPDATES}", True))

                    # API-16/17: the token is usable now, and the owning
                    # coordinator persists it (incl. unsecurePass).
                    self._authenticated_event.set()
                    self._signal_token_changed()

                except KeyError as e:
                    _LOGGER.error(f"Missing key in token response: {e}")
                except asyncio.TimeoutError:
                    _LOGGER.error("Timeout queueing enable updates command")
                except Exception as e:
                    _LOGGER.error(f"Error processing token: {e}")

            # Handle auth with token
            elif isinstance(mess_obj, TextMessage) and ("authwithtoken" in mess_obj.message):
                # API-13/27: a 401/4003 on authwithtoken means the *token*
                # is no longer valid (credentials were authenticated by
                # this point): reset and reconnect. That is control flow,
                # logged DEBUG — not LoxoneUnauthorisedError.
                if mess_obj.code in LLRSP_UNAUTHORISED_CODES:
                    _LOGGER.debug(
                        "Token no longer valid (authwithtoken code %s); resetting and reconnecting",
                        mess_obj.code,
                    )
                    self.reset_token()
                    self._reconnect_event.set()
                else:
                    self._check_auth_response(mess_obj, "authwithtoken")
                    _LOGGER.debug("Got message authwithtoken")
                    self._authenticated_event.set()
                    try:
                        await self._message_queue.put(MessageForQueue(f"{CMD_ENABLE_UPDATES}", True))
                    except asyncio.TimeoutError:
                        _LOGGER.error("Timeout queueing authwithtoken command")

            # Handle token refresh
            elif isinstance(mess_obj, TextMessage) and (
                "refreshjwt" in mess_obj.message or "refresh" in mess_obj.message
            ):
                # API-13/27: a 401/4003 on a refresh means the server-side
                # token is gone: reset + reconnect. Control flow, DEBUG.
                if mess_obj.code in LLRSP_UNAUTHORISED_CODES:
                    _LOGGER.debug(
                        "Token no longer valid (refresh code %s); resetting and reconnecting",
                        mess_obj.code,
                    )
                    self.reset_token()
                    self._reconnect_event.set()
                else:
                    _LOGGER.debug("Got token refresh response")
                    try:
                        self._check_auth_response(mess_obj, "token refresh")
                        value_dict = mess_obj.value_as_dict
                        if not isinstance(value_dict, dict):
                            raise ValueError("value_as_dict is not a dictionary")

                        token = value_dict.get("token")
                        valid_until = value_dict.get("validUntil")

                        if not token:
                            raise ValueError("Received empty token in refresh")
                        if valid_until is None:
                            raise ValueError("Missing validUntil in refresh")

                        self._token.token = token
                        self._token.valid_until = valid_until

                        if "unsecurePass" in value_dict:
                            self._token.unsecure_password = value_dict.get("unsecurePass", False)

                        # API-16/17: a refreshed token is persistable again.
                        self._authenticated_event.set()
                        self._signal_token_changed()

                        _LOGGER.debug(f"Token refreshed successfully, valid until: {valid_until}")

                    except KeyError as e:
                        _LOGGER.error(
                            f"Missing key in token refresh response: {e}. "
                            f"Response: {mess_obj.value_as_dict}, "
                            f"Message type: {type(mess_obj)}, "
                            f"Message: {getattr(mess_obj, 'message', 'N/A')}"
                        )
                    except Exception as e:
                        _LOGGER.error(
                            f"Unexpected error processing token refresh: {e}. "
                            f"Response: {getattr(mess_obj, 'value_as_dict', 'N/A')}"
                        )
            # Handle binary file
            elif isinstance(mess_obj, BinaryFile):
                pass

            elif isinstance(mess_obj, Keepalive):
                pass
            else:
                pass

        except LoxoneTokenError, LoxoneUnauthorisedError:
            raise

        except Exception as e:
            # A real failure (not a typed Loxone exception): WARNING
            # headline, traceback at DEBUG (API-27).
            _LOGGER.warning(f"Error in websocket event handler: {e}")
            _LOGGER.debug("Error in websocket event handler (traceback)", exc_info=True)
