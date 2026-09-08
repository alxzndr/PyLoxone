"""
Component to create an interface to the Loxone Miniserver.

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/pyloxone-api
"""


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
