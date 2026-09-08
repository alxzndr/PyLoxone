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


class LoxoneServiceUnAvailableError(LoxoneRequestError):
    """Service Unavailable; The Miniserver is restarting and not ready for requests"""


class LoxoneMaxNumOfConnectionsError(LoxoneRequestError):
    """Maximum number of allowed concurrent connections reached"""


class LoxoneUnrecognizedCommandError(LoxoneRequestError):
    """Unrecognized command"""
