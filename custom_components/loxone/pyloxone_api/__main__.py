"""
A quick test of the pyloxone_api module

From the command line, run:

> python -m custom_components.loxone.pyloxone_api <host> <port> <username> <password>

where host, port, username and password are your Loxone login credentials

"""

import asyncio
import logging
import sys

from .connection import LoxoneConnection

_LOGGER = logging.getLogger("pyloxone_api")
_LOGGER.setLevel(logging.DEBUG)
_LOGGER.addHandler(logging.StreamHandler())
# If you want to see what is going on at the websocket level, uncomment the following
# lines:


async def call_back_loxone(data) -> None:
    print("call_back_loxone", data)


async def main() -> None:
    api = LoxoneConnection(
        host=sys.argv[1],
        port=int(sys.argv[2]),
        username=sys.argv[3],
        password=sys.argv[4],
    )
    await api.start_listening(callback=call_back_loxone)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit()
