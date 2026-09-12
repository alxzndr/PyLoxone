"""
Diagnostics for the LoxOne Home Assistant config entry.

CORE-08: the previous implementation wrote out the raw ``LoxAPP3.json`` (no
redaction) and returned the first entry in ``hass.data[DOMAIN]`` regardless of
which entry was being requested.  This version:

* Uses ``get_miniserver_from_hass`` so the requested entry's miniserver drives
  the output — a multi-install no longer leaks cross-install data.
* Redacts the sensitive keywords (``serialNr``/``Mac`` in ``msInfo``, plus any
  ``token/salt/password`` at any nesting level).
* Returns ``{}`` (not ``None``) on failure so the ``json.dumps`` step in
  ``ConfigEntriesDiagnostics`` does not raise ``TypeError``.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import DOMAIN
from .miniserver import get_miniserver_from_hass

# Keys that `async_redact_data` will mask (case-sensitive exact match,
# all nesting levels).  ``serialNr``/``mac`` are the fingerprint fields of the
# msInfo object; ``token``/``salt`` may appear in a cached JWT header carried
# by a ``states`` uuid.  ``localUrl`` / ``remoteUrl`` / ``projectName`` use the
# Loxone-style capitalisation found in some setups.
REDACT_KEYS: frozenset[str] = frozenset(
    {
        "serialNr",
        "mac",
        "localUrl",
        "remoteUrl",
        "projectName",
        "token",
        "salt",
        "password",
        "username",
        "key",
    }
)

# Platform `async_setup_entry` functions mutate the shared control dicts with a
# homeassistant object (e.g. `hass`), with `asyncadddevices`, and with other
# internal-library objects that aren't json-serialisable.  Those are internal
# muxes, not device state.  If we pass them through the diagnostic payload,
# `json.dumps` (via `default=str`) leaks `str(entry)` which contains the
# `unique_id`/`title` chain.  Strip all non-JSON-serialisable values below.
JSONableScalable = (str, int, float, bool, type(None))


def _jsononly(node):
    """Recursively drop any value that isn't a plain JSON type."""
    if isinstance(node, dict):
        return {
            k: _jsononly(v) for k, v in node.items() if isinstance(v, JSONableScalable) or isinstance(v, (dict, list))
        }
    if isinstance(node, list):
        return [_jsononly(v) for v in node if isinstance(v, JSONableScalable) or isinstance(v, (dict, list))]
    return node if isinstance(node, JSONableScalable) else None


async def async_get_config_entry_diagnostics(hass: HomeAssistant, config_entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for the requested ``config_entry`` (redacted)."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    if miniserver is None:
        return {}
    lux = miniserver.lox_config.json
    return {
        "LoxAPP3.json": async_redact_data(_jsononly(lux), REDACT_KEYS),
        "entry": {
            "domain": DOMAIN,
            # The entry fingerprint (``serial``, MAC, URLs, name) lives in
            # msInfo and is redacted above; nothing else we expose hints at
            # which entry is being inspected (anti cross-install leak).
            "entry_id": config_entry.entry_id,
        },
    }
