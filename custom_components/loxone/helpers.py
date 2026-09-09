"""
Helper functions

For more details about this component, please refer to the documentation at
https://home-assistant.io/components/loxone/
"""

import ast
import copy
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


# CORE-20: there is no longer a module-level device cache.  The old
# ``device_registry`` dict was (a) never cleared on reload, (b) shared
# across config entries, and (c) handed out as one *dict object* to every
# sibling entity with the same identifier — a shared registry value that a
# first writer (often a sub-sensor) poisoned for everyone else (upstream
# PR #513).  ``device_info_for`` builds a fresh ``_attr_device_info`` payload
# per call; HA's device registry de-duplicates on ``identifiers`` itself.

# Attribute name the Miniserver host-device tuple is stored on the config
# entry for at setup time (``async_setup_entry``).
MINISERVER_VIA_ATTR = "loxone_via"


def miniserver_via(config_entry):
    """The ``(DOMAIN, serial)`` tuple this entry's entities link to, or ``None``."""
    if config_entry is None:
        return None
    return getattr(config_entry, MINISERVER_VIA_ATTR, None)


def device_info_for(config_entry, uuid, name, model, room=None, via_override=None):
    """Build a *fresh* ``_attr_device_info`` dict for one entity (CORE-20).

    Parameters
    ----------
    config_entry:  the config entry owning the entity (optional: YAML
        platforms and unit-style entities pass ``None``).
    uuid:          the Loxone control's ``uuidAction`` — the device identifier
        must always be a real string (PC-05: a ``None`` identifier maps to a
        device no other entity can attach to).
    name / model:  the control's own name and its control type (e.g.
        ``"Ventilation"``), not e.g. a sub-sensor's.
    room:          resolved room name → ``suggested_area`` (optional).
    via_override:  explicit ``via_device``; by default the Miniserver host
        device of ``config_entry`` (CORE-20, see PS-20).

    Returns a dict — ``homeassistant`` accepts plain dicts as device info —
    freshly allocated on every call so no two entities share one object.
    """
    if not isinstance(uuid, str) or not uuid:
        # PC-05: a device with identifier None cannot be registered and
        # silently detaches every entity that carries it.
        raise ValueError(f"device_info_for requires a non-empty string uuid, got {uuid!r}")
    via = via_override if via_override is not None else miniserver_via(config_entry)
    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, uuid)},
        "manufacturer": "Loxone",
    }
    if isinstance(name, str) and name:
        info["name"] = name
    if model:
        info["model"] = model
    if room:
        info["suggested_area"] = room
    if via is not None:
        info["via_device"] = via
    return info


def get_or_create_device(device_uuid, device_name, device_type, device_room, config_entry=None):
    """Return a fresh device-info dict (see :func:`device_info_for`).

    Kept as the old call signature while the platform migration to
    ``device_info_for`` completes (switch/number/button/text/select and the
    lights subpackages still call this).  The name is historical — nothing
    is created, nothing is cached (CORE-20).
    """
    return device_info_for(config_entry, device_uuid, device_name, device_type, device_room)


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
    rooms = lox_config.get("rooms") or {}
    entry = rooms.get(room_uuid)
    if entry is not None:
        return entry.get("name", "")
    # Idempotent: the value may already be a resolved room *name* -- the
    # shared loxconfig is mutated in place, so later platforms re-run the
    # resolution and must not wipe the name back to "" (that left lights,
    # fans and covers without their room and lost their device's
    # suggested_area).
    for room in rooms.values():
        if isinstance(room, dict) and room.get("name") == room_uuid:
            return room_uuid
    return ""


def get_cat_name_from_cat_uuid(lox_config: dict, cat_uuid: str):
    cats = lox_config.get("cats") or {}
    entry = cats.get(cat_uuid)
    if entry is not None:
        return entry.get("name", "")
    for cat in cats.values():
        if isinstance(cat, dict) and cat.get("name") == cat_uuid:
            return cat_uuid
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


def software_version_string(version):
    """Normalise the Loxone ``softwareVersion`` to a dot-joined string (CORE-16).

    The structure file carries it either as a list of numeric parts
    (``["7", "1", "0", "28"]``) or, depending on Miniserver generation,
    as the finished string (``"7.1.0"``).  The old ``".join(str(x) for x in
    ...)`` split a *string* into single characters (``"7.1.0"`` became
    ``"7.1.0"`` — with a dot *between every character*).  ``None`` and
    empty stay ``""`` so the caller can drop the field instead of
    registering junk.
    """
    if version is None:
        return ""
    if isinstance(version, str):
        return version.strip()
    if isinstance(version, (list, tuple)):
        return ".".join(str(part) for part in version if part is not None and str(part) != "")
    return str(version)


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
            # Deep copy: the structure file is cached per Miniserver and
            # *shared* across setups/platforms, while several platforms
            # mutate their control (rooms, `type`, runtime references) in
            # place.  A shallow copy of just the top dict is not enough --
            # nested writes (e.g. the Intercom/Dimmer/LCV2 sub-controls,
            # ``sub_control.update(...)``) were leaking into the cached
            # file and poisoning every later setup in the process.
            controls.append(copy.deepcopy(control))
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
