"""
Exception classes of the pyloxone_api package.

The Loxone-typed exceptions (token, auth, connection) that the
connection layer raises, plus the explicit failure tuple the graceful
handlers catch instead of a broad ``except Exception``.
"""

import struct


class LoxoneException(Exception):
    """Base class for all Loxone Exceptions"""

    response = None


class LoxoneOutOfServiceException(Exception):
    """Raised when the Miniserver goes down for a reboot"""


class LoxoneConnectionClosedOk(Exception):
    """Raised when websocket ClosedOk received. Should we reconnect?"""


class LoxoneConnectionError(Exception):
    """Raised the network connection is interrupted"""


class LoxoneRequestError(LoxoneException):
    """An exception raised during a request to the miniserver"""


class LoxoneUnauthorisedError(LoxoneRequestError):
    """Unauthorised web request. Incorrect credentials"""


class LoxoneTokenError(LoxoneRequestError):
    """Token authentication or handling failed"""


class LoxoneReconnectRequested(LoxoneTokenError):
    """Control flow: the connection layer requests a reconnect.

    Raised for events the code *recovers* from (stale token, server-initiated
    close of an idle session) and must be logged at DEBUG, never ERROR
    (API-27, JoDehli/PyLoxone#514). Subclass of :class:`LoxoneTokenError` so
    the outer unhandled-task handlers (``__init__.py``) that this WP does not
    touch keep reacting on the LoxoneTokenError branch until WP-2.3 wires
    in-place reconnection.
    """


class LoxoneServiceUnAvailableError(LoxoneRequestError):
    """Service Unavailable; The Miniserver is restarting and not ready for requests"""


class LoxoneMaxNumOfConnectionsError(LoxoneRequestError):
    """Maximum number of allowed concurrent connections reached"""


class LoxoneUnrecognizedCommandError(LoxoneRequestError):
    """Unrecognized command"""


#: The failure types a Loxone websocket session can legitimately surface
#: from its transport, protocol and bootstrap code paths.  Graceful
#: handlers in this library and in the integration catch *this tuple* so a
#: broad, swallowing ``except Exception`` stays out of the codebase
#: (BLE001): an exception class outside it is a bug in our own code and
#: should propagate to the task boundary (where HA and the session supervisor
#: log it) instead of being silently consumed by a resilience loop.
#: ``OSError`` subsumes the ``ConnectionError`` family; on 3.11+ ``TimeoutError``
#: is the same class as ``asyncio.TimeoutError``.
#
#: Caveat: deliberately contains no third-party (aiohttp/websockets)
#: classes — the public surface of those libraries is wider and flatter
#: (anything can wrap them). The typed layers above translate before
#: they cross this boundary.
SESSION_TRANSPORT_ERRORS = (
    LoxoneException,
    LoxoneConnectionClosedOk,
    LoxoneConnectionError,
    ConnectionError,
    TimeoutError,
    OSError,
    ValueError,
    RuntimeError,
    TypeError,
    KeyError,
    struct.error,
)
