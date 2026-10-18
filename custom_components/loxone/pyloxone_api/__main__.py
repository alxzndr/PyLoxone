"""
Connect to a Miniserver and print every state message, without Home Assistant.

Run it through the launcher, which loads this package on its own::

    scripts/pyloxone-cli <host> <port> <username>

The password is asked for (or read from ``LOXONE_PASSWORD``), never taken
from the command line, where other users could read it from the process
list. ``python -m custom_components.loxone.pyloxone_api`` does not work: it
imports the integration package, and with it Home Assistant.
"""

import asyncio
import getpass
import logging
import os
import sys

from .connection import LoxoneConnection

_LOGGER = logging.getLogger(__package__ or "pyloxone_api")


def _print_message(data) -> None:
    print(data)  # noqa: T201 - printing is this tool's purpose


def _print_state(connected: bool) -> None:
    print("connected" if connected else "disconnected")  # noqa: T201


async def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: pyloxone-cli <host> <port> <username>", file=sys.stderr)  # noqa: T201
        return 2
    host, port, username = argv
    password = os.environ.get("LOXONE_PASSWORD") or getpass.getpass(f"Password for {username}@{host}: ")
    api = LoxoneConnection(host=host, port=int(port), username=username, password=password)
    try:
        await api.run(_print_state, callback=_print_message)
    finally:
        await api.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG if os.environ.get("LOXONE_DEBUG") else logging.INFO)
    try:
        sys.exit(asyncio.run(main(sys.argv[1:])))
    except KeyboardInterrupt:
        sys.exit(130)
