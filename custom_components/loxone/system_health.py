"""Provide info to system health."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register(_hass: HomeAssistant, register: system_health.SystemHealthRegistration) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Get info for the info page.

    CORE-31 (WP-5.2): the coordinators are read from
    ``config_entry.runtime_data`` — ``hass.data[DOMAIN]`` no longer mirrors
    them (and used to raise ``KeyError`` while no entry was loaded, and
    report only whatever dict happened to come first).
    """
    miniserver_serial = "Unavailable"
    software_version = "Unavailable"
    project_name = "Unavailable"
    local_url = "Unavailable"
    remote_url = "Unavailable"
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        miniserver = getattr(coordinator, "miniserver", None) if coordinator is not None else None
        lox_config = getattr(miniserver, "lox_config", None)
        json_data = getattr(lox_config, "json", None) if lox_config is not None else None
        ms_info = json_data.get("msInfo") if isinstance(json_data, dict) else None
        if not isinstance(ms_info, dict):
            continue  # coordinator not fully connected yet: try the next entry
        miniserver_serial = getattr(miniserver, "serial", None) or miniserver_serial
        software_version = getattr(miniserver, "software_version", None) or software_version
        project_name = ms_info.get("projectName", project_name)
        local_url = ms_info.get("localUrl", local_url)
        remote_url = ms_info.get("remoteUrl", remote_url)
        break
    return {
        "Loxone Miniserver Serial": miniserver_serial,
        "Project Name": project_name,
        "Local Url": local_url,
        "Remote Url": remote_url,
        "Loxone Software Version": software_version,
    }
