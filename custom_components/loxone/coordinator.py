import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
from .miniserver import MiniServer
from .pyloxone_api.connection import LoxoneConnection, LoxoneException

_LOGGER = logging.getLogger(__name__)


def loxone_connected_signal(config_entry_id: str) -> str:
    """API-09/CORE-28: per-entry dispatcher signal for connection liveness.

    Per entry (not global) so two Miniservers on one HA instance do not
    cross-flip each other's entities when one of them reconnects.
    """
    return f"loxone_connected_{config_entry_id}"


class LoxoneCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Loxone Miniserver."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name="PyLoxone Coordinator",
            update_method=None,  # Not polling!
        )
        self.config_entry = config_entry
        self._username = config_entry.options[CONF_USERNAME]
        self._password = config_entry.options[CONF_PASSWORD]
        self._host = config_entry.options[CONF_HOST]
        self._port = config_entry.options[CONF_PORT]
        self._verify_ssl = config_entry.options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

        self.api: LoxoneConnection | None = None
        self.miniserver: MiniServer | None = None
        self.listeners = []
        # CORE-28: live-ness of the websocket session, flipped by the
        # ``LoxoneConnection.run`` supervisor (API-09) via
        # :meth:`set_connected_state`. Entities combine this with their own
        # ``_attr_available`` (per-entity "value never seen yet").
        self.connected = False
        # Stored by ``__init__.py`` so entry unload can cancel the session.
        self._listening_task: asyncio.Task | None = None

    def _on_token_changed(self, token: dict) -> None:
        """API-17: the Miniserver issued a new token - persist it now.

        The token (incl. the ``unsecurePass`` flag) used to be written only
        at HA shutdown, and ``unsecure_password`` was dropped entirely. The
        merged dict preserves all other ``ConfigEntry.data`` keys.
        """
        if not token or not token.get("token"):
            return
        data = {**self.config_entry.data}
        data.update(
            {
                "token": token["token"],
                "hash_alg": token.get("hash_alg", ""),
                "valid_until": token.get("valid_until", 0),
                "unsecure_password": token.get("unsecure_password", False),
            }
        )
        _LOGGER.debug("Persisting Loxone token change (valid_until=%s)", data["valid_until"])
        self.hass.async_create_task(self._persist_token_data(data))

    async def _persist_token_data(self, data: dict) -> None:
        try:
            await self.config_entry.async_update_entry(data=data)
            _LOGGER.debug("Loxone token persisted")
        except Exception as e:
            _LOGGER.warning("Failed to persist Loxone token change: %s", e)

    async def async_config_entry_first_refresh(self) -> None:
        _LOGGER.debug("async_config_entry_first_refresh")
        if self.api and self.api.connection:
            await self.api.close()
            self.api.connection = None

        if "token" in self.config_entry.data:
            self.api = LoxoneConnection(
                host=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                token=self.config_entry.data,
                verify_ssl=self._verify_ssl,
                # API-19: parse the multi-MB LoxAPP3.json off the event loop.
                executor=self.hass.async_add_executor_job,
                # API-17: persist every token change, not just at shutdown.
                token_change_callback=self._on_token_changed,
            )
        else:
            self.api = LoxoneConnection(
                host=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                verify_ssl=self._verify_ssl,
                # API-19: parse the multi-MB LoxAPP3.json off the event loop.
                executor=self.hass.async_add_executor_job,
                # API-17: persist every token change, not just at shutdown.
                token_change_callback=self._on_token_changed,
            )
        try:
            session = async_get_clientsession(self.hass)
            await self.api.open(session)
        except LoxoneException as e:
            _LOGGER.error("Could not connect to Loxone Miniserver")
            raise e
        except Exception as e:
            _LOGGER.error("Could not connect to Loxone Miniserver")
            raise e

        self.miniserver = MiniServer(self.hass, self.api.structure_file, self.config_entry)

        return None

    def set_connected_state(self, connected: bool) -> None:
        """``on_state`` callback of ``LoxoneConnection.run`` (API-09).

        Invoked from the connection's session task: True after
        ``enablebinstatusupdate`` (the Miniserver accepted us), False when
        the session dies or is being torn down. Only *flips* are re-sent on
        the per-entry dispatcher so entities flip their ``available`` and
        re-publish their state instead of being destroyed on a reload
        (CORE-28, CORE-05).
        """
        connected = bool(connected)
        if self.connected == connected:
            return
        self.connected = connected
        if connected:
            _LOGGER.info("Loxone connection to %s established", self._host)
        else:
            _LOGGER.warning("Loxone connection to %s lost; reconnecting in place", self._host)
        async_dispatcher_send(self.hass, loxone_connected_signal(self.config_entry.entry_id), connected)

    async def _async_update_data(self) -> None:
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        return None

    async def async_cleanup(self):
        """Clean up resources."""
        if hasattr(self, "listeners"):
            # Clean up all event listeners
            for listener in self.listeners:
                if listener is not None:
                    listener()
            self.listeners = []

        # Close API connection
        if hasattr(self, "api"):
            # API-17: kill the token on the Miniserver *before* the socket
            # goes away (entry removal/unload) so it does not linger
            # server-side. kill_token() is best-effort and a no-op without
            # a live connection.
            try:
                await self.api.kill_token()
            except Exception as e:
                _LOGGER.debug("kill_token on cleanup failed: %s", e)
            await self.api.close()
