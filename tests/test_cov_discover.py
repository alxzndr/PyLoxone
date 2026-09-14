"""Coverage for ``pyloxone_api.discover._discover_blocking`` (was 53%).

``parse_discovery_response`` (the pure parser) is already pinned by
``tests/test_wp69_discovery.py``; this module complements it by covering the
socket choreography around it, with a fake ``socket`` module so no UDP packet
ever leaves the machine:

* the broadcast handshake (two datagram sockets, bind on :7071, SO_BROADCAST,
  three one-byte probes to 255.255.255.255:7070, the ``wait`` deadline),
* a LoxLIVE reply is parsed into ``(ip, port, raw_reply)``,
* no reply (timeout), a socket error and a non-LoxLIVE reply all return
  ``None``,
* both sockets are closed on every path,
* ``discover()`` runs the blocking work off the event loop.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from custom_components.loxone.pyloxone_api import discover as discover_module

# A hand-written Miniserver announcement. Expected parse (by hand, against
# the _DISCOVERY_RESPONSE_RE in discover.py): ip "192.168.178.20", port 8080.
_REPLY = "LoxLIVE:14.5.12.7 192.168.178.20:8080 (Mac 504F94A0B1C2) Hardware: Miniserver Go "


class _FakeSocket:
    """Records every call ``_discover_blocking`` makes on a datagram socket."""

    def __init__(self, family, kind, proto) -> None:
        self.ctor_args = (family, kind, proto)
        self.blocking = None
        self.bound = None
        self.sockopts: list[tuple] = []
        self.sent: list[tuple] = []
        self.timeout = None
        self.close_calls = 0
        self.recv_calls = 0
        self.recv_bufsize = None
        self.recv_result: bytes | None = None
        self.recv_error: BaseException | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    def setblocking(self, flag) -> None:
        self.blocking = flag

    def bind(self, address) -> None:
        self.bound = address

    def setsockopt(self, level, option, value) -> None:
        self.sockopts.append((level, option, value))

    def sendto(self, payload, address) -> None:
        self.sent.append((payload, address))

    def settimeout(self, value) -> None:
        self.timeout = value

    def close(self) -> None:
        self.close_calls += 1

    def recv(self, bufsize):
        self.recv_calls += 1
        self.recv_bufsize = bufsize
        if self.recv_error is not None:
            raise self.recv_error
        return self.recv_result


def _install_fake_socket(monkeypatch, *, recv_result=None, recv_error=None) -> list[_FakeSocket]:
    """Replace the module's ``socket`` import; return the created sockets."""
    created: list[_FakeSocket] = []

    def _factory(family, kind, proto):
        sock = _FakeSocket(family, kind, proto)
        if not created:
            # The first socket built is the read socket; only it ever recv()s.
            sock.recv_result = recv_result
            sock.recv_error = recv_error
        created.append(sock)
        return sock

    monkeypatch.setattr(
        discover_module,
        "socket",
        SimpleNamespace(
            AF_INET="AF_INET",
            SOCK_DGRAM="SOCK_DGRAM",
            IPPROTO_UDP="IPPROTO_UDP",
            SOL_SOCKET="SOL_SOCKET",
            SO_BROADCAST="SO_BROADCAST",
            socket=_factory,
        ),
    )
    return created


def test_discover_blocking_parses_a_loxlive_reply(monkeypatch) -> None:
    """A LoxLIVE announcement becomes (ip, port, raw reply)."""
    created = _install_fake_socket(monkeypatch, recv_result=_REPLY.encode())

    assert discover_module._discover_blocking(5) == ("192.168.178.20", 8080, _REPLY)
    assert created[0].recv_bufsize == 1024


def test_discover_blocking_performs_the_broadcast_handshake(monkeypatch) -> None:
    """Two UDP sockets: one bound to :7071 to listen, one broadcasting x3."""
    created = _install_fake_socket(monkeypatch, recv_result=_REPLY.encode())

    discover_module._discover_blocking(5)

    assert len(created) == 2
    read_sock, write_sock = created
    assert read_sock.ctor_args == ("AF_INET", "SOCK_DGRAM", "IPPROTO_UDP")
    assert write_sock.ctor_args == ("AF_INET", "SOCK_DGRAM", "IPPROTO_UDP")

    assert read_sock.blocking is False
    assert read_sock.bound == ("0.0.0.0", 7071)
    assert read_sock.timeout == 5.0

    assert write_sock.sockopts == [("SOL_SOCKET", "SO_BROADCAST", 1)]
    # UDP is unreliable, so the probe is sent three times.
    assert write_sock.sent == [(b"\x00", ("255.255.255.255", 7070))] * 3
    assert write_sock.recv_calls == 0


def test_discover_blocking_uses_wait_as_the_socket_deadline(monkeypatch) -> None:
    """``wait`` becomes the read socket's float timeout."""
    created = _install_fake_socket(monkeypatch, recv_result=_REPLY.encode())

    discover_module._discover_blocking(2)

    assert created[0].timeout == 2.0


def test_discover_blocking_closes_both_sockets(monkeypatch) -> None:
    """Both context managers exit; the write socket is also closed early."""
    created = _install_fake_socket(monkeypatch, recv_result=_REPLY.encode())

    discover_module._discover_blocking(5)

    read_sock, write_sock = created
    assert read_sock.close_calls == 1
    # close() explicitly before the recv wait, then again on __exit__.
    assert write_sock.close_calls == 2


@pytest.mark.parametrize(
    ("recv_error", "comment"),
    [
        (TimeoutError("no answer"), "nobody answered within `wait` seconds"),
        (OSError("network is unreachable"), "the socket itself failed"),
    ],
)
def test_discover_blocking_returns_none_on_recv_failure(monkeypatch, recv_error, comment) -> None:
    """A failed read is 'no Miniserver found', not an exception."""
    created = _install_fake_socket(monkeypatch, recv_error=recv_error)

    assert discover_module._discover_blocking(5) is None, comment
    assert created[0].close_calls == 1
    assert created[1].close_calls == 2


@pytest.mark.parametrize(
    "reply",
    [
        "HTTP/1.1 200 OK ",
        "LoxLIVE:14.5.12.7 192.168.178.20:8080",
        "LoxLIVE:14.5.12.7 192.168.178.20:99999 ",
    ],
)
def test_discover_blocking_returns_none_for_a_non_loxlive_reply(monkeypatch, reply) -> None:
    """Something answered on :7071, but it is not a usable announcement."""
    created = _install_fake_socket(monkeypatch, recv_result=reply.encode())

    assert discover_module._discover_blocking(5) is None
    assert created[0].close_calls == 1


async def test_discover_runs_the_blocking_work_off_the_event_loop(monkeypatch) -> None:
    """``discover()`` delegates to an executor thread and returns its result."""
    sentinel = ("10.0.0.7", 80, "LoxLIVE:1 10.0.0.7:80 ")
    seen: dict[str, object] = {}

    def _fake_blocking(wait):
        seen["wait"] = wait
        seen["thread"] = threading.current_thread()
        return sentinel

    monkeypatch.setattr(discover_module, "_discover_blocking", _fake_blocking)

    assert await discover_module.discover(3) == sentinel
    assert seen["wait"] == 3
    assert seen["thread"] is not threading.main_thread()
