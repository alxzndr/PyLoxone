"""
Helper functions

For more details about this component, please refer to the documentation at
https://home-assistant.io/components/loxone/
"""

import ast
import json
import re
from typing import Any, Iterator

from .const import DOMAIN, cfmt


def json_decoder(value):
    """Parse a Loxone value string with ``json.loads``.

    Replaces the old ``eval(...)`` calls: it cannot execute code.  Returns
    ``None`` on a parse failure so the caller can keep its prior state instead
    of crashing the event handler on a malformed value.
    """
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError, TypeError:
        return None


def literal_decoder(value):
    """Parse a Loxone value (e.g. ``"temp[0.85, 2700]"``) with
    ``ast.literal_eval`` — safe for lists/tuples of numbers, no code execution.
    Returns ``None`` on a parse failure.
    """
    if not isinstance(value, str):
        return value
    try:
        return ast.literal_eval(value)
    except ValueError, SyntaxError, TypeError:
        return None


# Initialize a device registry
device_registry: dict[str, dict[str, Any]] = {}


def get_or_create_device(device_uuid, device_name, device_type, device_room):
    if device_uuid not in device_registry:
        device_registry[device_uuid] = {
            "identifiers": {(DOMAIN, device_uuid)},
            "name": device_name,
            "manufacturer": "Loxone",
            "model": device_type,
            "suggested_area": device_room,
        }
    return device_registry[device_uuid]


def map_range(value, in_min, in_max, out_min, out_max):
    """Linearly map ``value`` from ``[in_min, in_max]`` onto ``[out_min, out_max]``.

    A degenerate input range (``in_min == in_max``) would divide by zero; it
    maps to ``out_min`` instead (the Miniserver knows nothing more precise
    about a zero-width range) (CORE-32, Uni Ulm fuzzing PR #292).
    """
    if in_max == in_min:
        return out_min
    return out_min + (((value - in_min) / (in_max - in_min)) * (out_max - out_min))


def hass_to_lox(level):
    """Convert the given HASS light level (0-255) to Loxone (0.0-100.0)."""
    return (level * 100.0) / 255.0


def lox_to_hass(lox_val):
    """Convert the given Loxone (0.0-100.0) light level to HASS (0-255)."""
    return (lox_val / 100.0) * 255.0


def lox_to_hass_range(lox_val, min_v, max_v):
    """Map a Loxone value in ``[min_v, max_v]`` to an HA brightness (0-255).

    The full span maps to ``0`` up to ``255`` (PC-17: the old
    ``lox2hass_mapped`` clamped at the edges but passed intermediate values
    through unrescaled, so a dimmer with ``max_v < 100`` could never read
    back as fully bright).  Any non-zero result is floored at ``1`` so a
    brightness that is above the minimum never rounds to ``0`` / off
    (PC-18).
    """
    if lox_val is None:
        return 0
    if max_v <= min_v:
        # a degenerate span sits at one fixed value: at/above it counts as
        # fully bright, below as off
        return 255 if lox_val >= min_v else 0
    if lox_val <= min_v:
        return 0
    return min(255, max(1, round(map_range(lox_val, min_v, max_v, 0, 255))))


def hass_to_lox_range(hass_level, min_v, max_v):
    """Map an HA brightness (1-255) to a Loxone value in ``[min_v, max_v]``
    (the inverse of :func:`lox_to_hass_range`; PC-17: the write path used to
    ignore ``min_v``/``max_v`` entirely).

    ``0`` (or ``None``) means off and maps to ``0``.  Anything above ``0``
    is rounded and floored at ``1`` so HA brightness ``1`` never becomes
    Loxone ``0`` = off (PC-18).
    """
    if not hass_level:
        return 0
    if max_v <= min_v:
        return max_v
    return max(1, round(map_range(hass_level, 1, 255, min_v, max_v)))


def get_room_name_from_room_uuid(lox_config: dict, room_uuid: str):
    if "rooms" in lox_config:
        if room_uuid in lox_config["rooms"]:
            return lox_config["rooms"][room_uuid]["name"]

    return ""


def get_cat_name_from_cat_uuid(lox_config: dict, cat_uuid: str):
    if "cats" in lox_config:
        if cat_uuid in lox_config["cats"]:
            return lox_config["cats"][cat_uuid]["name"]
    return ""


def add_room_and_cat_to_value_values(loxconfig: dict, sensor: dict):
    sensor.update(
        {
            "room": get_room_name_from_room_uuid(loxconfig, sensor.get("room", "")),
            "cat": get_cat_name_from_cat_uuid(loxconfig, sensor.get("cat", "")),
        }
    )
    return sensor


def get_miniserver_type(t):
    if t == 0:
        return "Miniserver (Gen 1)"
    elif t == 1:
        return "Miniserver Go (Gen 1)"
    elif t == 2:
        return "Miniserver (Gen 2)"
    elif t == 3:
        return "Miniserver Go (Gen 2)"
    elif t == 4:
        return "Miniserver Compact"
    return "Unknown type"


def get_all(json_data, name) -> list[dict]:
    """Return all controls of the given type (or list of types).

    Tolerates a structure file with no ``controls`` key and controls that
    lack a ``type``: those are skipped instead of raising (CORE-32, Uni Ulm
    fuzzing PR #292).
    """
    controls: list[dict] = []
    if not isinstance(json_data, dict):
        return controls
    all_controls = json_data.get("controls") or {}
    if not isinstance(all_controls, dict):
        return controls
    wanted = set(name) if isinstance(name, (list, tuple, set)) else {name}
    for control in all_controls.values():
        if isinstance(control, dict) and control.get("type") in wanted:
            controls.append(control)
    return controls


def iter_controls(hass, config_entry, types) -> Iterator[dict]:
    """Yield each control of ``types`` with its room/cat names resolved.

    Shared setup boilerplate (PS-24): replaces the repeated
    ``get_miniserver_from_hass`` -> ``lox_config.json`` -> ``get_all`` ->
    ``add_room_and_cat_to_value_values`` sequence in every platform's
    ``async_setup_entry``.
    Structure files are loaded per Miniserver; importing ``miniserver`` here
    (not at module top) avoids a circular import: ``miniserver`` already
    imports from this module.
    """
    from .miniserver import get_miniserver_from_hass

    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    for control in get_all(loxconfig, types):
        # Yield a shallow copy: some platforms write runtime references
        # (hass/config_entry/async_add_devices) into their control dict, and
        # the structure file is shared. A shallow copy keeps those writes
        # out of the structure file (the nested dicts are still shared, but
        # only scalar keys are ever written into the top-level dict).
        yield dict(add_room_and_cat_to_value_values(loxconfig, control))


def clean_unit(lox_format):
    """Extract the unit string from a Loxone format specifier like '%.1f °C'."""
    search = re.search(cfmt, lox_format, flags=re.X)
    if search:
        unit = lox_format.replace(search.group(0).strip(), "").strip()
        if unit == "%%":
            unit = "%"
        return unit
    return lox_format
