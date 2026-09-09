import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .helpers import get_miniserver_type, software_version_string

_LOGGER = logging.getLogger(__name__)


@callback
def get_miniserver_from_hass(hass, config_entry):
    """Return the Miniserver for this specific config entry.

    Returns ``None`` (not ``KeyError``) when the domain data or the entry has
    not been populated yet, so callers (diagnostics, system health) can
    degrade gracefully.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if entry_data is None:
        return None
    return entry_data.miniserver


@dataclass
class ConfigDataClass:
    json: Optional[Dict[str, Any]] = None

    def get(self, key, default=None):
        if self.json is not None:
            return self.json.get(key, default)
        return default

    def __contains__(self, key):
        if self.json is not None:
            return key in self.json
        return False

    def __getitem__(self, key):
        if self.json is not None:
            return self.json[key]
        raise KeyError(key)


class MiniServer:
    def __init__(self, hass, lox_config, config_entry):
        self.hass = hass
        self.lox_config: ConfigDataClass = ConfigDataClass(lox_config)
        self.config_entry = config_entry

    @property
    def serial(self):
        return self.lox_config.get("msInfo", {}).get("serialNr", None)

    @property
    def miniserver_type(self):
        return self.lox_config.get("msInfo", {}).get("miniserverType", None)

    @property
    def name(self):
        return self.lox_config.get("msInfo", {}).get("msName", None)

    @property
    def software_version(self):
        """The Miniserver firmware version as a string (CORE-16).

        The structure file carries ``softwareVersion`` either as a list of
        parts (``["7", "1", "0", "28"]``) or already as a string; the old
        ``".".join(...)`` split *strings* into characters.
        """
        return software_version_string(self.lox_config.get("softwareVersion"))

    def miniserver_device_info(self):
        """The device fields of this Miniserver's host device (CORE-16).

        Identifier is ``(DOMAIN, serial)`` — and nothing else: the old code
        additionally registered a "network connection" from the host IP
        (``CONNECTION_NETWORK_MAC = "mac"``), which HA treats as a MAC address
        and mislabels.  Builders of entity devices (``device_info_for``) and
        the setup-time registry write (:meth:`async_update_device_registry`)
        share this dict so the host device and its children can never drift
        in name/model.
        """
        info: dict[str, Any] = {}
        if isinstance(self.name, str) and self.name:
            info["name"] = self.name
        model = get_miniserver_type(self.miniserver_type)
        if model and model != "Unknown type":
            info["model"] = model
        sw_version = self.software_version
        if sw_version:
            info["sw_version"] = sw_version
        return info

    @callback
    def async_update_device_registry(self) -> None:
        """Create/update this entry's Miniserver host device (CORE-16).

        Called exactly once from ``async_setup_entry`` — before the platforms
        forward — so that the per-control devices created by the entity
        constructors can set ``via_device`` against an existing entry.
        Skipped (with a warning) when the structure file carries no serial:
        registering ``(None, None)`` identifiers is exactly what corrupted
        device lookups before.
        """
        serial = self.serial
        if not isinstance(serial, str) or not serial:
            _LOGGER.warning(
                "Miniserver structure file has no msInfo.serialNr; no host device will be registered"
            )
            return

        device_registry = dr.async_get(self.hass)
        fields: dict[str, Any] = dict(self.miniserver_device_info())
        host_options = self.config_entry.options or {}
        host = host_options.get("host", "")
        port = host_options.get("port", 8080)
        if host:
            fields["configuration_url"] = "http://{host}:{port}".format(host=host, port=port)

        device_registry.async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, serial)},
            manufacturer="Loxone",
            **fields,
        )
