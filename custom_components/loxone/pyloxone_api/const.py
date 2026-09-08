"""
Loxone constants

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/pyloxone-api
"""

from __future__ import annotations

from typing import Final

# API-08: bounded connect retries (was RECONNECT_DELAY=5 / RECONNECT_TRIES=100,
# i.e. up to 8 minutes blocking config-entry setup instead of letting
# ConfigEntryNotReady retry). 3 tries with exponential backoff, applied to
# all three bootstrap GETs.
CONNECT_TRIES: Final = 3
CONNECT_RETRY_BASE_DELAY: Final = 1.0  # seconds; attempt N waits base * 2**(N-1)

# API-13: LL response codes that mean "unauthorised" (bad credentials /
# stale token) -- all auth handlers must check every response's code.
LLRSP_UNAUTHORISED_CODES: Final = frozenset({401, 4003})

# API-16: consecutive check_refresh_token failures before escalating to
# LoxoneTokenError (which forces a reload that retries auth from scratch).
TOKEN_REFRESH_MAX_FAILURES: Final = 3

# Loxone constants
MAX_WEBSOCKET_MESSAGE_SIZE: Final = 5 * 1024 * 1024  # 5 megabytes = 5,242,880 bytes
# API-06: explicit websocket close_timeout (stated instead of riding the
# websockets library default).
WEBSOCKET_CLOSE_TIMEOUT: Final = 10
DELAY_CHECK_TOKEN_REFRESH: Final = 20
TIMEOUT: Final = 30
KEEP_ALIVE_PERIOD: Final = 30
IV_BYTES: Final = 16
AES_KEY_SIZE: Final = 32

SALT_BYTES: Final = 16
SALT_MAX_AGE_SECONDS: Final = 60 * 60
SALT_MAX_USE_COUNT: Final = 100


# TOKEN_PERMISSION can be 2 for a 'short' lifespan token (days), or 4 for
# a longer lifespan (weeks). We ask for shorter token here. Renewing it
# is relatively easy, and the lifespan ensures that the tokens don't
# stick around for too long in the miniserver's memory if we have
# frequent restarts.
TOKEN_PERMISSION: Final = 2  # 2=web, 4=app

MAX_REFRESH_DELAY: Final = 86400  # 60 * 60 * 24  # 1 day


LOXAPPPATH: Final = "/data/LoxAPP3.json"

CMD_KEEP_ALIVE: Final = "keepalive"
CMD_GET_API_KEY: Final = "/jdev/cfg/apiKey"
CMD_GET_PUBLIC_KEY: Final = "/jdev/sys/getPublicKey"
CMD_KEY_EXCHANGE: Final = "jdev/sys/keyexchange/"
CMD_GET_KEY: Final = "jdev/sys/getkey"
CMD_GET_KEY_AND_SALT: Final = "jdev/sys/getkey2"
CMD_REQUEST_TOKEN: Final = "jdev/sys/gettoken"
CMD_REQUEST_TOKEN_JSON_WEB: Final = "jdev/sys/getjwt"
CMD_AUTH_WITH_TOKEN: Final = "authwithtoken/"
CMD_REFRESH_TOKEN: Final = "jdev/sys/refreshtoken"
CMD_REFRESH_TOKEN_JSON_WEB: Final = "jdev/sys/refreshjwt/"
CMD_ENABLE_UPDATES: Final = "jdev/sps/enablebinstatusupdate"
CMD_GET_VISUAL_PASSWD: Final = "jdev/sys/getvisusalt/"
# API-17: invalidate a token on the Miniserver when the entry is removed.
CMD_KILL_TOKEN: Final = "jdev/sys/killtoken"
