"""Config flow for PyLoxone.

CORE-19: hand-written ``ConfigFlow`` replacing ``SchemaConfigFlowHandler``:

* the ``user`` step *connects* to the Miniserver before it creates anything,
  so an entry can never be stored that cannot log in;
* the connection keys (host/port/username/password/verify_ssl) live in
  ``entry.data`` (entry version 5), not ``entry.options`` -- the plaintext
  password no longer round-trips to the frontend as a suggested value in an
  options form, and reauth/diagnostics can address them directly;
* the flow stamps ``unique_id`` with the Miniserver serial, so the same
  Miniserver can no longer be added twice;
* errors use translation keys, never free text (CORE-18).

CORE-09 note: a 401 *here* is a fresh credential the user just typed, so it
aborts with ``invalid_auth`` immediately -- the "retry first" window applies
to 401s seen during entry *setup* and in-place reconnect, not to typed
input.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_GENERATE_GROUPS,
    CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN,
    CONF_SCENE_GEN,
    CONF_SCENE_GEN_DELAY,
    CONF_VERIFY_SSL,
    DEFAULT_DELAY_SCENE,
    DEFAULT_GENERATE_GROUPS,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .pyloxone_api.connection import LoxoneConnection
from .pyloxone_api.exceptions import LoxoneUnauthorisedError

_LOGGER = logging.getLogger(__name__)

# The connection keys that moved from ``options`` to ``data`` in version 5.
CONNECTION_KEYS: tuple[str, ...] = (CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD, CONF_VERIFY_SSL)

# Error keys (CORE-18: translation keys, never free text).  ``invalid_host``
# in addition to the plan's four: ``LoxoneConnection`` rejects an empty
# host/username/password and an unparseable host in its constructor, which
# is an input problem the user can fix *in this form* -- lumping it into
# "cannot connect" would point them at the network instead.
ERR_CANNOT_CONNECT = "cannot_connect"
ERR_INVALID_AUTH = "invalid_auth"
ERR_INVALID_HOST = "invalid_host"
ERR_INVALID_USERNAME_ENCODING = "invalid_username_encoding"
ERR_INVALID_PASSWORD_ENCODING = "invalid_password_encoding"


def _connection_fields(host: str = "", port: int = DEFAULT_PORT, username: str = "", password: str = "") -> dict:
    """The shared host/port/username/password form fields (pre-fillable)."""
    return {
        vol.Required(CONF_HOST, default=host): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_PORT, default=port): NumberSelector(
            NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=1, max=65535)
        ),
        vol.Required(CONF_USERNAME, default=username): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_PASSWORD, default=password): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }


DATA_SCHEMA_USER = vol.Schema(
    {
        **_connection_fields(),
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
    }
)


def _reauth_confirm_schema(current: Mapping[str, Any]) -> vol.Schema:
    """The reauth confirm form, pre-filled with the stored values (the
    password itself is deliberately not pre-filled).  Reauth re-takes the
    whole connection description: since version 5 the options page only
    carries *preferences*, so this is the one place a user can point an
    existing entry at a new address again."""
    return vol.Schema(
        _connection_fields(
            host=str(current.get(CONF_HOST, "")),
            port=int(current.get(CONF_PORT, DEFAULT_PORT) or DEFAULT_PORT),
            username=str(current.get(CONF_USERNAME, "")),
        )
    )


def _entry_title(host: str) -> str:
    """Config entry title: the old handler used ``PyLoxone (<host>)``."""
    return f"PyLoxone ({host})" if host else "PyLoxone"


def _normalise(creds: Mapping[str, Any], current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The connection keys in canonical types (``str`` host, ``int`` port).

    ``current`` supplies the defaults for fields the form does not ask for
    (reauth pre-fills from the stored entry data); ``creds`` wins wherever it
    carries a value.
    """
    current = current or {}
    return {
        CONF_HOST: str(creds.get(CONF_HOST) if creds.get(CONF_HOST) is not None else current.get(CONF_HOST, "")),
        CONF_PORT: int(
            creds.get(CONF_PORT) if creds.get(CONF_PORT) is not None else current.get(CONF_PORT, DEFAULT_PORT)
        ),
        CONF_USERNAME: str(creds.get(CONF_USERNAME) or ""),
        CONF_PASSWORD: str(creds.get(CONF_PASSWORD) or ""),
        CONF_VERIFY_SSL: bool(
            creds.get(CONF_VERIFY_SSL)
            if creds.get(CONF_VERIFY_SSL) is not None
            else current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        ),
    }


def _encoding_error(creds: Mapping[str, Any]) -> str | None:
    """The Miniserver credentials are latin-1 only; return the error key."""
    try:
        creds[CONF_USERNAME].encode("latin-1")
    except UnicodeEncodeError:
        return ERR_INVALID_USERNAME_ENCODING
    try:
        creds[CONF_PASSWORD].encode("latin-1")
    except UnicodeEncodeError:
        return ERR_INVALID_PASSWORD_ENCODING
    return None


def _serial_from(api: LoxoneConnection) -> str:
    """``msInfo.serialNr`` of the structure file, else the API-key serial."""
    ms_info = api.structure_file.get("msInfo") if isinstance(api.structure_file, dict) else None
    if isinstance(ms_info, dict):
        serial = ms_info.get("serialNr")
        if isinstance(serial, str) and serial:
            return serial
    serial = getattr(api, "miniserver_serial", "")
    return serial if isinstance(serial, str) else ""


async def test_loxone_connection(
    hass: HomeAssistant,
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    verify_ssl: bool,
) -> str:
    """Connect to the configured Miniserver and return its serial.

    The single connection oracle of the flow: constructs a
    ``LoxoneConnection``, runs its full bootstrap (API key -> structure
    file -> public key -> websocket), reads the serial, and *always closes
    the connection again* before returning (the coordinator makes its own
    connection during setup, so this one must not leak).

    Exceptions carried to the caller (the flow maps them onto
    translation-keyed form errors):

    * ``ValueError`` -- the input is unusable (empty host/username/
      password, unparseable host) -> ``invalid_host``;
    * ``LoxoneUnauthorisedError`` -- the Miniserver rejected the
      credentials (API-13) -> ``invalid_auth``;
    * anything else -- a connectivity problem -> ``cannot_connect``.

    Module-level and free of flow state on purpose so tests can exercise
    the intended semantics directly.
    """
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError(f"port must be an integer between 1 and 65535, got {port!r}")
    if not host or not isinstance(host, str):
        raise ValueError("host is required")
    if not username or not password:
        raise ValueError("username and password are required")

    # LoxoneConnection.__init__ may raise ValueError for an unparseable
    # host; that is the "invalid_host" branch for the caller.
    api = LoxoneConnection(
        host=host,
        port=port,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    try:
        try:
            await api.open(async_get_clientsession(hass))
        except LoxoneUnauthorisedError:
            raise
        except ValueError:
            raise
        except Exception as e:
            raise ConnectionError(f"cannot reach Miniserver at {host}:{port}: {e}") from e
    finally:
        # The *whole* flow-owning connection test must not leak a handle:
        # close it after both a successful and a failed bootstrap (close()
        # is a guarded no-op for an unconnected instance).
        try:
            await api.close()
        except Exception as e:
            _LOGGER.debug("Closing the config-flow connection after the test failed: %s", e)
    serial = _serial_from(api)
    return serial


class LoxoneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Loxone config flow (user + reauth), entry version 5."""

    VERSION = 5
    MINOR_VERSION = 1

    def __init__(self) -> None:
        # Populated by ``async_step_reauth`` before the confirm step runs.
        self._reauth_data: dict[str, Any] = {}
        # Serial observed by the most recent successful connection test
        # (set per submitted form, read in the same step).
        self._last_serial: str = ""

    @staticmethod
    def async_get_options_flow(config_entry):
        """Options flows carry preferences only (CORE-19); the default
        ``ConfigFlow`` implementation aborts with ``UnknownHandler``."""
        return LoxoneOptionsFlow()

    async def _validate_and_connect(
        self, creds: Mapping[str, Any], current: Mapping[str, Any] | None = None
    ) -> str | None:
        """Check encoding, connect, and return an error key or ``None``."""
        normalised = _normalise(creds, current)
        if not normalised[CONF_HOST]:
            return ERR_INVALID_HOST
        if (error := _encoding_error(normalised)) is not None:
            return error
        try:
            self._last_serial = await test_loxone_connection(
                self.hass,
                host=normalised[CONF_HOST],
                port=normalised[CONF_PORT],
                username=normalised[CONF_USERNAME],
                password=normalised[CONF_PASSWORD],
                verify_ssl=normalised[CONF_VERIFY_SSL],
            )
        except LoxoneUnauthorisedError:
            _LOGGER.info("Loxone Miniserver rejected the provided credentials (form validation)")
            return ERR_INVALID_AUTH
        except ValueError:
            # The reference check passed, so this is a constructor-level
            # input failure (e.g. "host cannot be parsed" with a scheme).
            return ERR_INVALID_HOST
        except Exception as err:
            _LOGGER.warning(
                "Loxone connection test against %s:%s failed: %s",
                normalised[CONF_HOST],
                normalised[CONF_PORT],
                err,
            )
            return ERR_CANNOT_CONNECT
        return None

    # ------------------------------------------------------------------ #
    # user
    # ------------------------------------------------------------------ #
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """First run: connect, identify the Miniserver, create the entry."""
        if user_input is None:
            return self._show_user_form({})
        error = await self._validate_and_connect(user_input)
        if error is not None:
            return self._show_user_form({"base": error})
        serial = self._last_serial
        if serial:
            # ``raise_on_progress=True`` by default (standard behaviour);
            # the abort check below handles the already-configured case.
            await self.async_set_unique_id(serial)
            self._abort_if_unique_id_configured()
        else:
            _LOGGER.warning(
                "Miniserver answered without a serial number; creating the entry without a "
                "unique id (duplicates of it would not be caught by the flow, the serial "
                "stamp at setup would still make the second set of entries unusable)."
            )
        normalised = _normalise(user_input)
        return self.async_create_entry(
            title=_entry_title(normalised[CONF_HOST]),
            data=normalised,
            options={
                # New installs start with a preference-only options dict.
                # The values are the defaults the old user form stored, so
                # an install done with this version behaves like one done
                # before it.  CORE-15: auto-groups are OFF by default for
                # new installs.
                CONF_GENERATE_GROUPS: DEFAULT_GENERATE_GROUPS,
                CONF_SCENE_GEN: True,
                CONF_SCENE_GEN_DELAY: DEFAULT_DELAY_SCENE,
                CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN: False,
            },
        )

    def _show_user_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA_USER, errors=errors)

    # ------------------------------------------------------------------ #
    # reauth (CORE-19 / CORE-09)
    # ------------------------------------------------------------------ #
    async def async_step_reauth(self, entry_data: Mapping[str, Any] | None) -> ConfigFlowResult:
        """Called by HA when setup raises ``ConfigEntryAuthFailed`` or the
        reconnect supervisor is rejected by the Miniserver.  ``entry_data``
        is the entry's data as it was replaced (incl. the connected
        connection keys)."""
        self._reauth_data = dict(entry_data or {})
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Re-take the connection description and verify it *before* the
        entry is touched; the entry is updated and reloaded on success
        (the standard ``reauth_successful`` abort)."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm", data_schema=_reauth_confirm_schema(self._reauth_data))
        data = self._reauth_data
        error = await self._validate_and_connect(user_input, current=data)
        if error is not None:
            return self.async_show_form(
                step_id="reauth_confirm", data_schema=_reauth_confirm_schema(data), errors={"base": error}
            )
        normalised = _normalise(user_input, current=data)
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        _LOGGER.info(
            "Re-authentication against Loxone Miniserver %s succeeded",
            normalised.get(CONF_HOST) or data.get(CONF_HOST),
        )
        return self.async_update_reload_and_abort(entry, data_updates=normalised)


class LoxoneOptionsFlow(OptionsFlow):
    """Preferences only (CORE-19): no credentials survive here.

    CORE-15: ``generate_groups`` is always collected with the *stored* value
    (or the pre-option default ``True``) as its default, so saving an
    options form never flips an old install's group behaviour.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = dict(self.config_entry.options)
        if user_input is not None:
            options = {**current, **user_input}
            if CONF_SCENE_GEN_DELAY in options:
                options[CONF_SCENE_GEN_DELAY] = int(options[CONF_SCENE_GEN_DELAY])
            self.hass.config_entries.async_update_entry(self.config_entry, options=options)
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GENERATE_GROUPS, default=current.get(CONF_GENERATE_GROUPS, True)
                    ): BooleanSelector(),
                    vol.Required(CONF_SCENE_GEN, default=current.get(CONF_SCENE_GEN, True)): BooleanSelector(),
                    vol.Optional(
                        CONF_SCENE_GEN_DELAY, default=current.get(CONF_SCENE_GEN_DELAY, DEFAULT_DELAY_SCENE)
                    ): NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=3)),
                    vol.Required(
                        CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN,
                        default=current.get(CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN, False),
                    ): BooleanSelector(),
                }
            ),
        )
