"""
Component to create an interface to the Loxone Miniserver.

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/pyloxone-api
"""

from __future__ import annotations

import asyncio
import re
import socket

# A responding Miniserver announces itself as
#   ``LoxLIVE:<software version> <ip>:<port> (Mac ...) Hardware: ...``
# and the HTTP service then runs on the announced port.  The trailing
# space after the port is part of the observed format.
_DISCOVERY_RESPONSE_RE = re.compile(r"^LoxLIVE:.* (?P<ip>(?:[0-9]{1,3}\.){3}[0-9]{1,3}):(?P<port>\d+) ")


def parse_discovery_response(response: str) -> tuple[str, int] | None:
    """Parse a LoxLIVE broadcast reply into ``(ip, port)``.

    Pure function, hand-testable: returns the announced IPv4 address and
    port, or ``None`` when the reply is not a LoxLIVE announcement or
    announces unusable values (an IP octet above 255 or a port above
    65535).  Callers may treat ``None`` as "no usable Miniserver
    advertised" and fall back to manual entry.
    """
    if not isinstance(response, str):
        return None
    if (found := _DISCOVERY_RESPONSE_RE.match(response)) is None:
        return None
    ip = found.group("ip")
    port = int(found.group("port"))
    if port > 65535:
        return None
    octets = ip.split(".")
    if len(octets) != 4 or any(int(octet) > 255 for octet in octets):
        return None
    return ip, port


async def discover(wait: int = 5) -> tuple[str, int, str] | None:
    """
    Attempt to discover a miniserver on the local network.

    Returns a tuple of (IPv4_address:string, port:int, response:string) if a
    miniserver is found on the local network within `wait` seconds (default 5).
    The discovery runs off the event loop on a worker thread so the blocking
    UDP socket work never stalls the loop. Returns `None` if no miniserver is
    found.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _discover_blocking, wait)


def _discover_blocking(wait: int) -> tuple[str, int, str] | None:
    """Blocking Loxone discovery. Runs on a worker thread, never on the event loop."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as read_sock:
        read_sock.setblocking(False)
        read_sock.bind(("0.0.0.0", 7071))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as write_sock:
            write_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # broadcast 3 packets of 0x00 byte to UDP port 7070 (UDP is unreliable)
            for _ in range(3):
                write_sock.sendto(b"\x00", ("255.255.255.255", 7070))

            # wait for a single response within `wait` seconds (non-blocking wait)
            write_sock.close()
            read_sock.settimeout(float(wait))
            try:
                response = read_sock.recv(1024).decode()
            except TimeoutError, OSError:
                return None

            # Look for a Loxone Response.
            if (found := parse_discovery_response(response)) is not None:
                return found[0], found[1], response
            return None
