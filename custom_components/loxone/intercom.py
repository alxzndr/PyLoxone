"""
Loxone intercom blocks (``Intercom`` / ``IntercomV2``).

Shared, platform-independent helpers for the entities an intercom block
produces.  The entities themselves live on their platforms:

* ``button``: one button per output (sub-control) -- a press sends
  ``pulse`` -- plus an "Answer" button (``answer`` on the intercom);
* ``event``: a "Doorbell" event entity fed by the intercom's ``bell``
  state;
* ``switch``: the ``IntercomV2`` "Mute" switch (state ``muted``,
  commands ``mute/1`` / ``mute/0``).

All of them sit on the intercom's one device.

Structure file v16: the outputs of an ``Intercom`` are "0-3 Outputs of
type Pushbutton" (p. 91, ``pulse`` handled since Miniserver 8.0), those of
an ``IntercomV2`` "0-N Outputs of type Pushbutton" (p. 93).  A pushbutton
has the state ``active`` and the commands ``pulse`` (a tap), ``on`` (hit
and held) and ``off``.  Up to 0.10.9 the outputs were switches whose turn-on
sent ``on``: that *held* the output -- typically a door strike -- until a
turn-off followed.  :func:`async_remove_obsolete_intercom_switches` cleans
up the registry entries of those switches.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .helpers import add_room_and_cat_to_value_values, device_info_for, get_all

_LOGGER = logging.getLogger(__name__)

# WP-6.3 (#466): firmware 16.2+ reports the same block as ``IntercomV2``.
INTERCOM_TYPES = ("Intercom", "IntercomV2")
INTERCOM_V2 = "IntercomV2"

INTERCOM_SWITCHES_REPLACED_ISSUE = "intercom_switches_replaced"


def intercom_device_info(control, config_entry=None):
    """
    The intercom's device payload (fresh per call), or ``None``.

    Every intercom entity carries it, so the device registry merges them
    onto one device named after the intercom and modelled as the block
    type (``Intercom`` / ``IntercomV2``).  ``None`` when the control has
    no usable ``uuidAction`` (PC-05: there is no device to attach to).
    """
    if not isinstance(control, dict):
        return None
    parent_uuid = control.get("uuidAction")
    if not isinstance(parent_uuid, str) or not parent_uuid:
        return None
    model = control.get("type") or "Intercom"
    return device_info_for(config_entry, parent_uuid, control.get("name", ""), model, control.get("room", ""))


def intercom_state_uuid(control, key):
    """The uuid of the intercom's ``key`` state, or ``None`` when absent."""
    if not isinstance(control, dict):
        return None
    states = control.get("states")
    uuid = states.get(key) if isinstance(states, dict) else None
    return uuid if isinstance(uuid, str) and uuid else None


def intercom_output_kwargs(control, config_entry=None, loxconfig=None):
    """
    ``LoxoneIntercomOutputButton`` kwargs for the outputs of one intercom
    block (``Intercom`` / ``IntercomV2``) -- WP-6.3, #466.

    One kwargs dict per output (sub-control) with a ``uuidAction``.  Each
    keeps its own name (the device is named after the intercom -- WP-5.1),
    carries the parent's ``uuidAction`` as ``parent_id`` and a fresh copy of
    the parent's device payload.  An ``active`` state is *not* required: a
    button needs no state, the press echo is recorded only when the output
    advertises one.  Every ``states``/``subControls`` lookup is guarded:
    a malformed structure file yields no buttons, never an aborted
    platform.
    """
    if intercom_device_info(control, config_entry) is None:
        return []
    parent_uuid = control["uuidAction"]
    kwargs_list: list[dict] = []
    subcontrols = control.get("subControls")
    if not isinstance(subcontrols, dict):
        return kwargs_list
    for subcontrol in subcontrols.values():
        if not isinstance(subcontrol, dict):
            continue
        uuid_action = subcontrol.get("uuidAction")
        if not isinstance(uuid_action, str) or not uuid_action:
            continue
        # Work on a copy: the setup path hands the helper a copy of the
        # cached structure file, and entities mutate their kwargs.
        subcontrol = dict(subcontrol)
        subcontrol.setdefault("room", "")
        subcontrol.setdefault("cat", "")
        if loxconfig is not None:
            subcontrol = add_room_and_cat_to_value_values(loxconfig, subcontrol)
        if not isinstance(subcontrol.get("states"), dict):
            subcontrol["states"] = {}
        subcontrol["parent_id"] = parent_uuid
        # CORE-20: a fresh payload per entity, never one shared object.
        subcontrol["device_info"] = intercom_device_info(control, config_entry)
        kwargs_list.append(subcontrol)
    return kwargs_list


def intercom_output_uuids(loxconfig) -> list[str]:
    """The ``uuidAction`` of every intercom output in the structure file."""
    return [
        kwargs["uuidAction"]
        for control in get_all(loxconfig, list(INTERCOM_TYPES))
        for kwargs in intercom_output_kwargs(control)
    ]


def _issue_id(entry_id: str) -> str:
    return f"{INTERCOM_SWITCHES_REPLACED_ISSUE}_{entry_id}"


def async_remove_obsolete_intercom_switches(hass: HomeAssistant, config_entry: ConfigEntry, loxconfig) -> list[str]:
    """
    Remove the 0.10.x ``switch`` registry entries of intercom outputs.

    Up to 0.10.9 every intercom output was a ``switch`` (unique id: the
    output's ``uuidAction``); it is a ``button`` with the same unique id
    now.  The switch platform no longer creates those entities, so their
    registry entries would linger as "unavailable" switches that
    automations still reference.  Called after the platforms are set up
    (so the new buttons are registered): each obsolete entry is removed
    with one WARNING naming the replacement, and a persistent repair issue
    tells the user to move automations to the buttons.  Returns the
    removed entity ids.  A setup with nothing to remove is a no-op (the
    issue of an earlier migration stays until the user dismisses it).
    """
    registry = er.async_get(hass)
    moved: list[tuple[str, str | None]] = []
    for uuid in intercom_output_uuids(loxconfig):
        old_entity_id = registry.async_get_entity_id("switch", DOMAIN, uuid)
        if old_entity_id is None:
            continue
        old_entry = registry.async_get(old_entity_id)
        if old_entry is None or old_entry.config_entry_id != config_entry.entry_id:
            continue
        new_entity_id = registry.async_get_entity_id("button", DOMAIN, uuid)
        registry.async_remove(old_entity_id)
        _LOGGER.warning(
            "Removed the obsolete intercom output switch %s: intercom outputs are pushbuttons, "
            "use the button %s instead (a press sends a pulse)",
            old_entity_id,
            new_entity_id or "(not created)",
        )
        moved.append((old_entity_id, new_entity_id))
    if moved:
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(config_entry.entry_id),
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=INTERCOM_SWITCHES_REPLACED_ISSUE,
            translation_placeholders={
                "entities": ", ".join(f"{old} -> {new or '(not created)'}" for old, new in moved),
            },
        )
    return [old for old, _new in moved]
