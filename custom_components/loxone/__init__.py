"""
Component to create an interface to the Loxone Miniserver.

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import asyncio
import inspect
import contextlib
import logging
import re
import time
from functools import partial

from homeassistant.components import group

# 2026.11 dropped the ``async_get_component`` re-export from the group package;
# ``group.entity`` has defined it since well before the 2026.7 floor.
from homeassistant.components.group.entity import async_get_component as async_get_group_component
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, UnknownEntry, ConfigEntryAuthFailed, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import (
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.start import async_at_started

from .const import (
    ATTR_AREA_CREATE,
    ATTR_CODE,
    ATTR_DEVICE,
    ATTR_ENTRY_ID,
    ATTR_UUID,
    ATTR_VALUE,
    CONF_GENERATE_GROUPS,
    CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN,
    CONF_SCENE_GEN_DELAY,
    CONF_VERIFY_SSL,
    DEFAULT,
    DEFAULT_DELAY_SCENE,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DEVICE_TYPE_AC,
    DEVICE_TYPE_ANALOG,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_GATE,
    DEVICE_TYPE_IRC,
    DEVICE_TYPE_JALOUSIE,
    DEVICE_TYPE_LCV2,
    DEVICE_TYPE_PUSHBUTTON,
    DEVICE_TYPE_SLIDER,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_TEXT_INPUT,
    DEVICE_TYPE_TIMED_SWITCH,
    DEVICE_TYPE_VALUE_SELECTOR,
    DEVICE_TYPE_VENTILATION,
    DEVICE_TYPE_WINDOW,
    DOMAIN,
    EVENT,
    LOXONE_PLATFORMS,
    SECUREDSENDDOMAIN,
    SENDDOMAIN,
    TOKEN_DATA_KEYS,
    cfmt,
    loxone_uuid_signal,
)
from .coordinator import LoxoneCoordinator, loxone_connected_signal
from .helpers import (
    MINIMUM_SUPPORTED_FIRMWARE,
    LOCKED_BY,
    InPlaceEntityIdChange,
    leading_loxone_id,
    parse_control_lock,
    meets_minimum_firmware,
    structure_ids,
)
from .intercom import async_remove_obsolete_intercom_switches
from .pyloxone_api.exceptions import (
    LoxoneServiceUnAvailableError,
    LoxoneUnauthorisedError,
    SESSION_TRANSPORT_ERRORS,
)

_LOGGER = logging.getLogger(__name__)

# CORE-19: the YAML `loxone:` block never worked in the config-entry era
# (`async_setup` fired an `import` flow that no `async_step_import` ever
# served), so there is no CONFIG_SCHEMA anymore.  A leftover block still in
# ``configuration.yaml`` registers a repair issue instead (CORE-30).

# The four true *domain* services.  CORE-04: they are registered exactly
# once in ``async_setup`` (guarded by ``has_service``) and removed only
# when the last locked entry unloads.  The three cover *entity* services
# (``quick_shade``, ``enable_sun_automation``, ``disable_sun_automation``)
# registered by the cover platform are shared per ``has_service`` guard and
# belong to the platform — never remove them from ``async_unload_entry``;
# doing so killed them for every other still-running entry and logged
# "Unable to remove unknown service" on the second unload.
_DOMAIN_SERVICES = (
    "event_websocket_command",
    "event_secured_websocket_command",
    "sync_areas",
    "reload",
)

# CORE-11: exactly one of ``uuid`` / ``device`` per command service
# (``vol.Exclusive`` caps the group at one; "at least one" is checked by
# the handler and must not make the schema validation silently pass when
# both are missing, or the previous code would resolve "" as the uuid).
_TARGET_EXCLUSIVE = vol.Exclusive
EVENT_COMMAND_SCHEMA = vol.Schema(
    {
        _TARGET_EXCLUSIVE(ATTR_UUID, "target"): cv.string,
        _TARGET_EXCLUSIVE(ATTR_DEVICE, "target"): cv.entity_id,
        vol.Optional(ATTR_VALUE, default=DEFAULT): cv.string,
    }
)
SECURED_EVENT_COMMAND_SCHEMA = vol.Schema(
    {
        _TARGET_EXCLUSIVE(ATTR_UUID, "target"): cv.string,
        _TARGET_EXCLUSIVE(ATTR_DEVICE, "target"): cv.entity_id,
        vol.Optional(ATTR_VALUE, default=DEFAULT): cv.string,
        vol.Optional(ATTR_CODE, default=DEFAULT): cv.string,
    }
)
RELOAD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)

# A Miniserver that is still booting (firmware update / reboot) answers 401 to
# authenticated requests for a short window after its HTTP server is back up
# (see docs/incidents/2026-09-02-401-during-miniserver-reboot.md).  A 401 during setup is
# therefore retried; once the failures span this long we escalate to
# `ConfigEntryAuthFailed` + a reauth flow (with the repair issue HA raises
# for it, CORE-09 / WP-3.4).
# Seconds before an entry whose session died from an error that
# ``LoxoneConnection.run`` does not retry itself (an HTTP 500 or a truncated
# LoxAPP3.json during a reconnect bootstrap, or a bug in our own code) is
# reloaded. Without it the entry stayed loaded with every entity unavailable
# until someone reloaded it by hand (ported from upstream 0.10.1).
UNEXPECTED_FAILURE_RELOAD_DELAY = 30
AUTH_RETRY_MAX_ATTEMPTS = 5
AUTH_RETRY_MIN_ELAPSED_SECONDS = 300
_AUTH_FAILURES = "auth_failures"  # key in hass.data[DOMAIN]; a plain dict that must not outlive the last counting entry

# TODO: get version and check for updates https://update.loxone.com/updatecheck.xml?serial=xxxxxxxxx


async def async_unload_entry(hass, config_entry):
    """Completely unloads the Loxone integration and closes all connections."""
    # CORE-13: unload the platforms *first* and only clean up entry
    # resources when that unload actually succeeded.  The old order tore
    # down the connection, popped ``hass.data`` and removed the services
    # *before* unloading platforms, and discarded a ``False`` result —
    # leaving a zombie entry marked LOADED with a dead connection behind
    # it.
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, LOXONE_PLATFORMS)
    if not unload_ok:
        _LOGGER.error(
            "Unloading Loxone platforms failed for Miniserver %s; keeping the entry's "
            "listeners and connection so the unload can be retried.",
            config_entry.entry_id,
        )
        return unload_ok

    # Bus/dispatcher listeners, the options-update listener and the
    # coordinator shutdown have all been registered through
    # ``config_entry.async_on_unload`` during setup; HA fires them now
    # that the unload succeeded (CORE-06).  What ``async_on_unload``
    # cannot express (awaiting a cancel, killing the connection) is done
    # here, in dependency order.
    #
    # CORE-31 (WP-5.2): the coordinator lives on ``config_entry.runtime_data``,
    # not ``hass.data[DOMAIN][entry_id]``.  HA deletes the runtime_data
    # attribute itself once this callback reports success — and with nothing
    # else holding the domain key, ``hass.data[DOMAIN]`` can no longer rot
    # as a stale empty dict after the last unload.
    coordinator = getattr(config_entry, "runtime_data", None)
    if coordinator is not None:
        # Cancel the session supervisor *before* the connection drops:
        # it otherwise wakes on the socket close and re-raises into
        # teardown (CORE-03).  The task is the entry's tracked
        # background task, so HA's unload would cancel it as well — the
        # direct cancel keeps the ordering deterministic.
        session_task = coordinator.listening_task
        if session_task is not None and not session_task.done():
            session_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session_task

        try:
            await coordinator.async_cleanup()
        except SESSION_TRANSPORT_ERRORS as e:
            _LOGGER.warning("Error closing connection: %s", e)

    # The in-flight 401-retry bookkeeping would otherwise survive the
    # reload; drop it too (CORE-09).  It also removes a now-empty
    # ``hass.data[DOMAIN]`` (CORE-31: no stale dict after the last unload).
    _clear_auth_failure(hass, config_entry)

    # CORE-04: the domain services are integration-wide, not per entry —
    # remove them only when no other loxone entry is loaded anymore.
    # (This entry's own state is still LOADED during this callback, hence
    # the explicit exclusion.)  The cover entity services are untouchable
    # here: they are shared per platform and owned by the cover platform.
    if not any(
        other.state is ConfigEntryState.LOADED and other.entry_id != config_entry.entry_id
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        for service in _DOMAIN_SERVICES:
            hass.services.async_remove(DOMAIN, service)

    return unload_ok


def _loxone_coordinator_by_uuid(hass: HomeAssistant, uuid: str) -> LoxoneCoordinator | None:
    """The already-loaded loxone coordinator whose structure file contains ``uuid``."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, LoxoneCoordinator) and uuid in coordinator.known_uuids:
            return coordinator
    return None


def _resolve_outbound_target(hass: HomeAssistant, data: dict) -> tuple[LoxoneCoordinator, str]:
    """
    Resolve ``call.data`` to ``(owning entry coordinator, control uuid)``. (CORE-11/CORE-04)

    Exactly one of ``uuid`` / ``device`` may be given (the schema enforces
    at most one; this enforces at least one). ``device`` must be an entity
    of *this* integration, and its entry must be loaded. ``uuid`` must
    belong to a *loaded* miniserver (its structure file contains it). All
    other cases raise ``ServiceValidationError`` — before the fix,
    a ``None`` registry entry was dereferenced and missing targets
    silently sent ``""`` as the UUID.
    """
    uuid = data.get(ATTR_UUID)
    device_id = data.get(ATTR_DEVICE)
    if uuid is None and device_id is None:
        raise ServiceValidationError("Exactly one of 'uuid' or 'device' is required")
    if uuid is not None and device_id is not None:
        # The schema's ``vol.Exclusive`` already rejects both.
        raise ServiceValidationError("Provide either 'uuid' or 'device', not both")

    if uuid is not None:
        coordinator = _loxone_coordinator_by_uuid(hass, uuid)
        if coordinator is None:
            raise ServiceValidationError(f"UUID {uuid} does not belong to any loaded Loxone Miniserver")
        return coordinator, uuid

    registry_entry = er.async_get(hass).async_get(device_id)
    if registry_entry is None:
        raise ServiceValidationError(f"Unknown entity: {device_id}")
    if registry_entry.platform != DOMAIN:
        raise ServiceValidationError(f"Entity {device_id} does not belong to the loxone integration")
    entry = hass.config_entries.async_get_entry(registry_entry.config_entry_id)
    coordinator = getattr(entry, "runtime_data", None) if entry is not None else None
    if not isinstance(coordinator, LoxoneCoordinator):
        raise ServiceValidationError(f"Entity {device_id} belongs to a loxone entry that is not loaded")
    # Command-address semantics are unchanged from the pre-fix handler:
    # the LoXone entity's registry unique_id is the control uuidAction
    # (two legacy entities decorate it — same address as before,
    # now instead of being silently sent to *every* entry's API).
    target_uuid = registry_entry.unique_id
    if not isinstance(target_uuid, str) or not target_uuid:
        raise ServiceValidationError(f"Entity {device_id} has no resolvable Loxone UUID")
    return coordinator, target_uuid


def _async_register_domain_services(hass: HomeAssistant) -> None:
    """
    Register the Loxone domain services *once* for the whole integrations. (CORE-04)

    Previously `async_setup_entry` (re-)registered all four service names
    per config entry, overwriting each other, left the last entry's
    handler bound to its coordinator alone, and `async_unload_entry`
    removed them *unconditionally* — so the first unload killed the
    services for every other still-loaded entry, and the second unload
    logged "Unable to remove unknown service".
    """
    if hass.services.has_service(DOMAIN, "event_websocket_command"):
        return

    async def handle_event_websocket_command(call) -> None:
        coordinator, uuid = _resolve_outbound_target(hass, call.data)
        if coordinator.api is None:
            raise ServiceValidationError("The entry's Loxone connection is not ready")
        value = call.data.get(ATTR_VALUE, DEFAULT)
        await coordinator.api.send_websocket_command(uuid, value)

    async def handle_secured_event_websocket_command(call) -> None:
        coordinator, uuid = _resolve_outbound_target(hass, call.data)
        if coordinator.api is None:
            raise ServiceValidationError("The entry's Loxone connection is not ready")
        value = call.data.get(ATTR_VALUE, DEFAULT)
        code = call.data.get(ATTR_CODE, DEFAULT)
        await coordinator.api.send_secured_websocket_command(uuid, value, code)

    async def handle_sync_areas_with_loxone(call) -> None:
        await sync_areas_with_loxone(hass, call.data if isinstance(call.data, dict) else {})

    async def handle_reload(call) -> None:
        """
        Handle a service call to reload the integration.

        One ``async_schedule_reload`` per entry — HA owns the safe
        unload-then-load ordering — replaces the old full unload-then-
        full reload path that unloaded every entry twice and also churned
        unrelated miniserver instances (CORE-05).  The optional
        ``entry_id`` restricts the reload to a single miniserver that the
        user named; default: all entries.
        """
        entries = hass.config_entries.async_entries(DOMAIN)
        requested = call.data.get(ATTR_ENTRY_ID)
        if requested is not None:
            entries = [e for e in entries if e.entry_id == requested]
        _LOGGER.info("Reloading %i Loxone config entry(ies) via service call", len(entries))
        for entry in entries:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    hass.services.async_register(
        DOMAIN, "event_websocket_command", handle_event_websocket_command, schema=EVENT_COMMAND_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "event_secured_websocket_command",
        handle_secured_event_websocket_command,
        schema=SECURED_EVENT_COMMAND_SCHEMA,
    )
    hass.services.async_register(DOMAIN, "sync_areas", handle_sync_areas_with_loxone)
    hass.services.async_register(DOMAIN, "reload", handle_reload, schema=RELOAD_SCHEMA)


def _registry_entity_name(entry) -> str:
    """The entity's own name (``RegistryEntry.entity_name`` from HA 2026.11)."""
    if (name := getattr(entry, "entity_name", None)) is not None:
        return name
    return entry.name or entry.original_name or ""


async def sync_areas_with_loxone(hass: HomeAssistant, data: dict) -> None:
    """Sync HA areas from Loxone room attributes (``sync_areas`` service)."""
    data = data or {}
    create_areas = data.get(ATTR_AREA_CREATE, DEFAULT)
    if create_areas not in [True, False]:
        create_areas = False
    lox_items = []
    device_items = {}
    er_registry = er.async_get(hass)
    dr_registry = dr.async_get(hass)
    ar_registry = ar.async_get(hass)
    for _id, entry in er_registry.entities.items():
        if entry.platform == DOMAIN:
            state = hass.states.get(entry.entity_id)
            if hasattr(state, "attributes") and "room" in state.attributes:
                area = ar_registry.async_get_area_by_name(state.attributes["room"])
                if area is None and create_areas:
                    area = ar_registry.async_get_or_create(state.attributes["room"])
                if not area or entry.area_id is not None:
                    continue
                if entry.device_id is not None and not _registry_entity_name(entry):
                    # The device's main entity (``has_entity_name`` with no
                    # name of its own) follows its device's area; HA 2026.11
                    # rejects an own area for it ("set the area on its device
                    # instead").  Fill the device in instead, never overwrite.
                    device = dr_registry.async_get(entry.device_id)
                    if device is not None and device.area_id is None:
                        device_items.setdefault(device.id, area.id)
                    continue
                lox_items.append((entry.entity_id, area.id))
    for device_id, area_id in device_items.items():
        dr_registry.async_update_device(device_id, area_id=area_id)
    for _ in lox_items:
        er_registry.async_update_entity(_[0], area_id=_[1])


YAML_CONFIG_ISSUE_ID = "yaml_config_present"


async def async_setup(hass, config):
    """
    Domain-level setup (runs once, even with pure config entries).

    Registers the four domain services (CORE-04).  A leftover YAML
    ``loxone:`` block fires a persistent, non-fixable repair issue: the
    configuration it once carried is either ignored (the import flow is
    gone) or has to be recreated as a config entry. (CORE-19/CORE-30)
    """
    if DOMAIN in config:
        _LOGGER.error(
            "The YAML '%s:' block in configuration.yaml is no longer supported: the import "
            "step the block was passed to never existed, so it was never read. Recreate the "
            "Miniserver as a config entry (Settings > Devices & Services).",
            DOMAIN,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            YAML_CONFIG_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="yaml_config_present",
        )
    _async_register_domain_services(hass)
    return True


async def async_migrate_entry(hass, config_entry):
    """Migrate a config entry using Home Assistant's supported update API."""
    old_version = config_entry.version
    version = old_version
    options = dict(config_entry.options)
    data = dict(config_entry.data)

    if version == 1:
        options[CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN] = True
        version = 2
        _LOGGER.info("Migration to version %s successful", 2)

    if version == 2:
        options[CONF_SCENE_GEN_DELAY] = DEFAULT_DELAY_SCENE
        version = 3
        _LOGGER.info("Migration to version %s successful", 3)

    if version == 3:
        options[CONF_VERIFY_SSL] = DEFAULT_VERIFY_SSL
        version = 4
        _LOGGER.info("Migration to version %s successful", 4)

    if version == 4:
        # CORE-19 (WP-3.4): version 5 keeps connection keys in the entry
        # DATA (new current-version entries stored them there from day
        # one); "options" is settings only. This way the plaintext
        # password is no longer rediscovered as the form's suggested
        # value in the options UI, and re-authentication / diagnostics
        # can read it directly.
        #
        # unique_id: migration cannot guess the Miniserver serial; setup
        # already stamps it (CORE-16), leaving it None for entries that
        # have never successfully set up once, in which case the next
        # successful setup stamps it.
        for key, default in (
            (CONF_HOST, ""),
            (CONF_PORT, DEFAULT_PORT),
            (CONF_USERNAME, ""),
            (CONF_PASSWORD, ""),
            (CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        ):
            data[key] = options.pop(key, default)
        try:
            data[CONF_PORT] = int(data[CONF_PORT])
        except (TypeError, ValueError):
            data[CONF_PORT] = DEFAULT_PORT
        version = 5
        _LOGGER.info("Migration to version %s successful (connection keys moved to entry data)", 5)

    if version != old_version:
        hass.config_entries.async_update_entry(config_entry, options=options, data=data, version=version)
    return True


# Home Assistant 2026.9 added a required keyword-only ``context`` parameter
# to Group.async_create_group; 2026.7 and 2026.8 reject it. The HACS floor
# is 2026.7, so detect the signature once rather than pin either side.
_GROUP_ACCEPTS_CONTEXT = "context" in inspect.signature(group.Group.async_create_group).parameters


async def _create_group(hass, name, **kwargs):
    """Group.async_create_group across the 2026.8 -> 2026.9 signature change."""
    if _GROUP_ACCEPTS_CONTEXT:
        kwargs.setdefault("context", None)
    return await group.Group.async_create_group(hass, name, **kwargs)


async def create_group_for_loxone_entities(hass, entities, name, object_id):
    entity_id = f"group.{object_id}"
    # CORE-15: the auto-groups must *converge* on this entry's current
    # loxone entities, not just be created once.  Two things break a
    # bare re-``async_create_group`` of an existing group:
    #
    # * the platform ignores a re-added Group whose unique id it already
    #   knows ("Entity id already exists - ignoring"), so a group whose
    #   *state* went away (the user removed it) would stay gone; and
    # * a master group re-tracks its members the moment any subgroup's
    #   state is rewritten, so checking "state present?" too late holds
    #   a stale member list.
    #
    # So: when the group entity still exists on the platform, always
    # (re)point it at the freshly discovered members; only create fresh
    # when no such entity exists at all.
    component = async_get_group_component(hass)
    entity = component.get_entity(entity_id)
    if entity is not None and hasattr(entity, "async_update_tracked_entity_ids"):
        if tuple(sorted(entity.tracking)) != tuple(sorted(entities)):
            entity.async_update_tracked_entity_ids(list(entities))
        entity.async_write_ha_state()
        return
    try:
        await _create_group(
            hass,
            name,
            created_by_service=False,
            entity_ids=entities,
            icon=None,
            mode=None,
            object_id=object_id,
            order=None,
        )
    except HomeAssistantError as err:
        await _create_group(
            hass,
            name,
            created_by_service=True,
            entity_ids=entities,
            icon=None,
            mode=None,
            object_id=object_id,
            order=None,
        )
        _LOGGER.exception("Can't create group '%s' with error: %s", name, err)


# CORE-15 / PS-14: the auto-groups and the control type each one collects.
# The types are the PS-14 constants — the strings the platforms emit in
# their ``device_type`` state attribute.  The old table matched literals
# no platform emitted (``"analog_sensor"``, ``"digital_sensor"``,
# ``"TimedSwitch"``), so those groups were always empty; it grouped
# "Loxone Dimmer" with the ``lights`` list while ``dimmers`` sat unused;
# and the master group dropped dimmers/climates/accontrollers entirely.
LOXONE_GROUP_MASTER_OBJECT_ID = "loxone_group"
LOXONE_GROUP_MASTER_NAME = "Loxone Group"
LOXONE_GROUPS_BY_OBJECT_ID: dict[str, tuple[str, tuple[str, ...]]] = {
    "loxone_analog": ("Loxone Analog Sensors", (DEVICE_TYPE_ANALOG,)),
    "loxone_digital": ("Loxone Digital Sensors", (DEVICE_TYPE_BINARY_SENSOR,)),
    "loxone_switches": ("Loxone Switches", (DEVICE_TYPE_SWITCH, DEVICE_TYPE_TIMED_SWITCH)),
    "loxone_buttons": ("Loxone Buttons", (DEVICE_TYPE_PUSHBUTTON,)),
    "loxone_covers": ("Loxone Covers", (DEVICE_TYPE_JALOUSIE, DEVICE_TYPE_GATE, DEVICE_TYPE_WINDOW)),
    "loxone_lights": ("Loxone LightControllers", (DEVICE_TYPE_LCV2,)),
    "loxone_dimmers": ("Loxone Dimmers", (DEVICE_TYPE_DIMMER,)),
    "loxone_climates": ("Loxone Room Controllers", (DEVICE_TYPE_IRC, DEVICE_TYPE_AC)),
    "loxone_ventilations": ("Loxone Ventilation Controllers", (DEVICE_TYPE_VENTILATION,)),
    "loxone_accontrollers": ("Loxone AC Controllers", (DEVICE_TYPE_AC,)),
    "loxone_numbers": ("Loxone Numbers", (DEVICE_TYPE_SLIDER, DEVICE_TYPE_VALUE_SELECTOR)),
    "loxone_texts": ("Loxone Texts", (DEVICE_TYPE_TEXT_INPUT,)),
}


async def loxone_discovered(hass, _config_entry):
    """
    Collect this entry's entities for the auto-groups, keyed by group object id.

    Every state of *this integration* (``platform == loxone``) is matched
    against the PS-14 constants in :data:`LOXONE_GROUPS_BY_OBJECT_ID` via
    the ``device_type`` state attribute; empty groups are not returned.
    """
    found: dict[str, list[str]] = {}
    for state in hass.states.async_all():
        attributes = state.attributes
        if attributes.get("platform") != DOMAIN:
            continue
        device_type = attributes.get("device_type")
        for object_id, (_name, types) in LOXONE_GROUPS_BY_OBJECT_ID.items():
            if device_type in types:
                found.setdefault(object_id, []).append(state.entity_id)
    for object_id in found:
        found[object_id] = sorted(found[object_id])
    return {oid: ids for oid, ids in found.items() if ids}


async def create_loxone_groups(hass, config_entry):
    """
    Create the auto-groups once per install (CORE-15).

    Gated on the entry's ``generate_groups`` option: the config flow
    stamps new installs with ``False`` (default off), while pre-option
    installs never carry the key — an absent key keeps the old behaviour
    (groups on).  Idempotent: a ``group.loxone_group`` state means the
    groups exist and a reload must not run group creation again.
    """
    if not config_entry.options.get(CONF_GENERATE_GROUPS, True):
        _LOGGER.debug("generate_groups is off for %s; skipping auto-group creation", config_entry.title)
        return

    if hass.states.get(f"group.{LOXONE_GROUP_MASTER_OBJECT_ID}") is not None:
        _LOGGER.debug("Loxone auto-groups already exist; skipping group creation")
        return

    # The at-started job can race the platforms: entity states (and with
    # them the user extras that carry ``device_type``/``platform``) may not
    # be written yet.  Let the pending work settle before scanning.  (The
    # post-group-creation ``async_block_till_done`` of the old code that
    # only ran *before creating the master group* is gone — this one is
    # what makes the scan see the entities.)
    await hass.async_block_till_done()

    groups = await loxone_discovered(hass, config_entry)
    for object_id, (name, _types) in LOXONE_GROUPS_BY_OBJECT_ID.items():
        entity_ids = groups.get(object_id)
        if not entity_ids:
            continue
        await create_group_for_loxone_entities(hass, entity_ids, name, object_id)

    # CORE-15: the master group is assembled from the non-empty subgroups
    # *including* dimmers/climates/accontrollers — built members only, so
    # the master never references a group that was never created.
    master_members = [f"group.{oid}" for oid in LOXONE_GROUPS_BY_OBJECT_ID if groups.get(oid)]
    if master_members:
        await create_group_for_loxone_entities(
            hass, master_members, LOXONE_GROUP_MASTER_NAME, LOXONE_GROUP_MASTER_OBJECT_ID
        )


def _hass_data(hass) -> dict:
    """Return ``hass.data[DOMAIN]``, creating it if needed."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    return hass.data[DOMAIN]


def _auth_failure_tracker(hass) -> dict:
    """
    Return the per-entry consecutive-auth-failure store.

    HA re-creates the coordinator on every setup attempt, so the counter and
    first-failure timestamp must not live on the coordinator.  They are kept
    in ``hass.data[DOMAIN][_AUTH_FAILURES]`` (a plain dict keyed by entry id,
    ignored by the coordinator lookups, which now read
    ``config_entry.runtime_data`` — CORE-31).  Removed again by
    :func:`_clear_auth_failure` once all counters are reset, so the domain
    data never holds stale non-coordinator state (CORE-09).
    """
    return _hass_data(hass).setdefault(_AUTH_FAILURES, {})


def _record_auth_failure(hass, config_entry, now=None):
    """
    Record one 401 during setup. Returns ``(consecutive_count, first_failure_time)``.

    ``now`` defaults to the process monotonic clock (a persistent per-entry
    first-failure timestamp would survive HA restarts only via the entry,
    which is not where we store it), and is injectable for tests.
    """
    if now is None:
        now = time.monotonic()
    tracker = _auth_failure_tracker(hass)
    state = tracker.get(config_entry.entry_id)
    if state is None:
        state = {"count": 0, "first": now}
        tracker[config_entry.entry_id] = state
    state["count"] += 1
    return state["count"], state["first"]


def _clear_auth_failure(hass, config_entry) -> None:
    """
    Reset the consecutive-auth-failure counter after a successful setup.

    Also removes the empty ``auth_failures`` dict from ``hass.data[DOMAIN]`` —
    and with the coordinator now on ``config_entry.runtime_data`` (CORE-31)
    this is the *only* thing that key ever holds anymore, so a once-empty
    dict is dropped from ``hass.data`` entirely: no stale ``hass.data[DOMAIN]``
    survives the last unload.
    """
    tracker = _hass_data(hass).setdefault(_AUTH_FAILURES, {})
    tracker.pop(config_entry.entry_id, None)
    domain_data = hass.data.get(DOMAIN)
    if not tracker and isinstance(domain_data, dict):
        domain_data.pop(_AUTH_FAILURES, None)
        if not domain_data:
            hass.data.pop(DOMAIN, None)


def _should_escalate_auth_failure(consecutive_count, first_failure_time, now) -> bool:
    """
    Escalate only after the bounded retry window is exhausted.

    Intent: a booting Miniserver emits 401 for a short window, so a *short* run
    of failures is retried quietly; 5+ consecutive failures spanning >= 5
    minutes means the credentials are probably wrong (or the server is not the
    one we think it is).  VERIFY against a live Miniserver's boot behaviour
    before relying on the escalation actually firing for genuinely bad
    credentials: until then the ERROR is only *guidance*, setup still keeps
    retrying (WP-3.4 makes it terminal via reauth).
    """
    return consecutive_count >= AUTH_RETRY_MAX_ATTEMPTS and (now - first_failure_time) >= AUTH_RETRY_MIN_ELAPSED_SECONDS


# --------------------------------------------------------------------------- #
# CORE-30 (WP-5.2): repair issues
#
# Per-entry issue ids (``<key>_<entry_id>``): two Miniservers on one HA
# instance must never share an issue.  All issues here are created
# non-persistent: they re-derive themselves on the next setup, and HA drops
# them with the entry on removal.  ``ir.async_create_issue`` with the same
# id replaces, ``ir.async_delete_issue`` of a missing id is a no-op — so a
# create/delete pair on every setup is structurally idempotent.
# --------------------------------------------------------------------------- #


def _auth_failed_issue_id(entry_id: str) -> str:
    """The per-entry ``auth_failed`` repair issue id (CORE-30)."""
    return f"auth_failed_{entry_id}"


def _async_report_auth_failure(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """
    Register this entry's ``auth_failed`` repair issue (CORE-30).

    Called at both points where the Miniserver rejects the *stored*
    credentials: the bounded 401 escalation in ``async_setup_entry`` (which
    escalates to ``ConfigEntryAuthFailed`` + reauth) and the 401 that ends
    a live session in ``run_loxone_session`` (which starts reauth directly).
    The reauth flow, once the user submits working credentials, updates and
    reloads the entry; a successful setup then removes the issue (see
    :func:`_async_clear_auth_failure_issue`).  Until reauth completes, the
    issue stays — that is the acceptance of "auth_failed (until reauth
    completes)".
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        _auth_failed_issue_id(config_entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
        translation_placeholders={
            "host": str(config_entry.data.get(CONF_HOST, config_entry.options.get(CONF_HOST, ""))),
        },
    )


def _async_clear_auth_failure_issue(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Remove the ``auth_failed`` issue once setup succeeds (reauth done) (CORE-30)."""
    ir.async_delete_issue(hass, DOMAIN, _auth_failed_issue_id(config_entry.entry_id))


def _unsupported_firmware_issue_id(entry_id: str) -> str:
    """The per-entry ``unsupported_firmware`` repair issue id (CORE-30)."""
    return f"unsupported_firmware_{entry_id}"


def _async_reconcile_firmware_issue(hass: HomeAssistant, config_entry: ConfigEntry, software_version: str) -> None:
    """
    Create/remove the ``unsupported_firmware`` issue from the reported firmware (CORE-30).

    Called on every successful setup with the Miniserver's
    ``softwareVersion`` (string form; see ``MiniServer.software_version``).
    A firmware that is at least :data:`MINIMUM_SUPPORTED_FIRMWARE` removes the
    issue (a no-op when it has never been created); a confirmed too-low
    firmware creates the WARNING issue.  Unparseable/missing versions are
    treated as supported by :func:`meets_minimum_firmware` — the repair
    must never fire on versions the integration cannot verify (VERIFY note
    in ``helpers.py`` about the actual floor applies before merge).
    """
    issue_id = _unsupported_firmware_issue_id(config_entry.entry_id)
    if meets_minimum_firmware(software_version):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unsupported_firmware",
        translation_placeholders={
            "version": str(software_version),
            "minimum": ".".join(str(part) for part in MINIMUM_SUPPORTED_FIRMWARE),
        },
    )


async def _persist_token_and_close(hass, config_entry, coordinator, _event) -> None:
    """
    Persist the current token and close the connection on HA stop.

    Module-scoped (not a nested closure): ``EventBus._async_listen_once``
    repackages the callback into its own wrapper, which poisons
    coroutine-bound *locally defined* functions; this is the STOP
    handler that CORE-06 (WP-3.1) registers for life with
    ``config_entry.async_on_unload``.
    """
    api = coordinator.api
    if api is None:
        # Cleanup already ran (e.g. a stop during unload): nothing to save.
        return
    token = api.get_token_dict()
    if token:  # CORE-10: persist a token only if we actually hold one
        # The entry may already be gone: a stop can race entry removal, and a
        # failed platform unload deliberately keeps this handler alive so the
        # unload can be retried (CORE-13). Writing to an entry Home Assistant
        # has dropped raises UnknownEntry, which escapes into the event-bus
        # task as "Task exception was never retrieved".
        if hass.config_entries.async_get_entry(config_entry.entry_id) is None:
            _LOGGER.debug("Config entry is gone; not persisting the Loxone token on stop")
        else:
            try:
                hass.config_entries.async_update_entry(
                    config_entry,
                    data={
                        **config_entry.data,  # preserve existing data
                        "token": token["token"],
                        "hash_alg": token.get("hash_alg", ""),
                        "valid_until": token.get("valid_until", 0),
                    },
                )
            except UnknownEntry:
                _LOGGER.debug("Config entry removed while persisting the Loxone token on stop")
    # Best effort: Home Assistant is stopping, and a handler that raises here
    # only produces an unretrievable event-bus task exception. Nothing can act
    # on a failure to close a socket we are about to drop anyway.
    try:
        await api.close()
    except Exception:  # noqa: BLE001 - shutdown path, nothing can act on it
        _LOGGER.debug("Closing the Loxone connection on stop failed", exc_info=True)


def _entry_config_snapshot(config_entry: ConfigEntry) -> tuple[dict, dict]:
    """
    CORE-05: the part of a config entry a change of which must reload it.

    All of ``entry.options``, plus ``entry.data`` *minus* the token keys
    (API-17, ``TOKEN_DATA_KEYS``).  Since the entry-version-5 migration the
    credentials (host/port/username/password) live in ``entry.data``
    (CORE-19), so a reauth or reconfigure still changes this snapshot and
    still reloads — only the four integration-written token keys are exempt.
    """
    return (
        dict(config_entry.options),
        {key: value for key, value in config_entry.data.items() if key not in TOKEN_DATA_KEYS},
    )


async def async_setup_entry(hass, config_entry):
    """Set up Loxone from a config entry."""
    coordinator = LoxoneCoordinator(hass, config_entry)
    # Since version 5, the description of the connection is in the entry
    # data; for entries that (abnormally) are not yet migrated, they are in options (fallback, provisional)
    host = config_entry.data.get(CONF_HOST, config_entry.options.get(CONF_HOST, ""))

    _LOGGER.info(
        "Setting up Loxone integration for Miniserver at %s:%s",
        host,
        config_entry.data.get(CONF_PORT, config_entry.options.get(CONF_PORT, DEFAULT_PORT)),
    )

    # Changing the entry's options (host/port/password/scene settings)
    # must take effect without a manual reload: schedule a reload of
    # *this* entry (CORE-29; the old ``async_config_entry_updated`` stub
    # was neither registered nor did anything).
    # HA fires update listeners as tasks (`listener(hass, entry)`), so
    # this must be a coroutine function.  Signatures beyond the entry
    # vary by HA version; only the entry id matters (CORE-29).
    #
    # CORE-05 / API-17: NOT every entry update is a configuration change.
    # The integration writes the refreshed Miniserver token back into
    # ``entry.data`` itself (``LoxoneCoordinator._on_token_changed``, and
    # the HA-stop handler above).  Reloading for that reconnects, the
    # reconnect makes the Miniserver issue a new token, the new token is
    # persisted -- an endless reload loop.  So compare the *non-token*
    # part of the entry against the snapshot taken when this listener was
    # registered and skip the reload when only token keys moved.
    async def _entry_updated(_updated_entry: ConfigEntry, *_args, **_kwargs) -> None:
        snapshot = _entry_config_snapshot(config_entry)
        if snapshot == coordinator.entry_config_snapshot:
            _LOGGER.debug("Config entry update carries only a refreshed token; not reloading")
            return
        coordinator.entry_config_snapshot = snapshot
        hass.config_entries.async_schedule_reload(config_entry.entry_id)

    # The coordinator opens the websocket connection inside
    # ``_async_setup``, which the stock
    # ``async_config_entry_first_refresh`` (now no longer overridden —
    # CORE-12) runs once, sets ``data``/``last_update_success`` from, and
    # wraps *any* failure in ``ConfigEntryNotReady`` (entry ->
    # setup_retry with HA's automatic backoff).  The actual exception
    # travels as ``__cause__``.  The ``finally`` below is the single
    # place that guarantees the (partially opened) API handle is closed
    # on *every* setup failure — previously the 401 branch returned
    # False (no retry, connection leaked) and the 503 branch failed to
    # close at all (CORE-09).
    entry_setup_failed = True
    try:
        await coordinator.async_config_entry_first_refresh()
        entry_setup_failed = False
    except ConfigEntryNotReady as err:
        cause = err.__cause__
        if isinstance(cause, LoxoneUnauthorisedError):
            # A Miniserver coming out of a reboot answers 401 before auth is
            # ready: retry, and only after the bounded window escalate
            # (CORE-09; WP-3.4 turned the escalation into a real
            # `ConfigEntryAuthFailed`, which makes HA park the entry in a
            # `setup_error` state AND start the reauth flow — where the
            # new credentials are verified against the Miniserver
            # before they are stored). The 401s seen *within* the
            # window are still just ConfigEntryNotReady retries.
            attempt, first_attempt = _record_auth_failure(hass, config_entry)
            if _should_escalate_auth_failure(attempt, first_attempt, time.monotonic()):
                _LOGGER.exception(
                    "Miniserver at %s answered 401 %i consecutive times during setup over at least %i "
                    "minutes; escalating to a reauth flow. Please check the stored credentials "
                    "(username and password) for this Miniserver in Settings > Devices & "
                    "Services; if they are correct the Miniserver may simply still be booting.",
                    host,
                    attempt,
                    AUTH_RETRY_MIN_ELAPSED_SECONDS // 60,
                )
                # CORE-30 (WP-5.2): the rejected-credential state is a repair
                # issue until reauth completes (a successful setup removes it).
                _async_report_auth_failure(hass, config_entry)
                raise ConfigEntryAuthFailed(
                    f"Miniserver at {host} rejected the stored credentials {attempt} consecutive times during setup"
                ) from err
            _LOGGER.warning(
                "Miniserver answered 401 during setup; retrying (attempt %i)",
                attempt,
            )
        elif isinstance(cause, LoxoneServiceUnAvailableError):
            _LOGGER.warning(
                "Loxone Miniserver at %s is unavailable (service restarting?). Will retry automatically",
                host,
            )
        else:
            _LOGGER.warning(
                "Could not connect to Loxone Miniserver at %s: %r. Will retry automatically",
                host,
                cause,
            )
        raise
    finally:
        if entry_setup_failed:
            if coordinator.api is not None:
                await coordinator.api.close()

    _clear_auth_failure(hass, config_entry)
    # CORE-30 (WP-5.2): setup succeeded, so the stored credentials work (or
    # reauth just completed with new ones) — remove this entry's auth_failed
    # repair issue.  No-op if it was never created.
    _async_clear_auth_failure_issue(hass, config_entry)

    _LOGGER.info(
        "Successfully connected to Loxone Miniserver at %s",
        host,
    )

    # CORE-16: the config flow rarely sets ``config_entry.unique_id``;
    # the Miniserver serial is the host's identity.  Stamp it before the
    # option-update listener is registered, below, so the stamp itself
    # doesn't schedule a reload.
    serial = coordinator.miniserver.serial
    if serial and config_entry.unique_id != serial:
        hass.config_entries.async_update_entry(config_entry, unique_id=serial)

    # CORE-20 / PS-20: the tuple is linked to the Miniserver's host
    # device that the platforms' entities attach to.  Stored on the
    # entry — one object that both setup-time handles, and the
    # entity constructors, can reach.
    config_entry.loxone_via = (DOMAIN, serial) if serial else None

    # CORE-16: register the host device (the first-ever caller of
    # ``async_update_device_registry``) *before* the platforms create
    # the child devices that point at it.  Its registry id is what
    # ``device_info_for`` links with (``via_device_id``; the tuple above
    # is the fallback for Home Assistant 2026.7).
    host_device = coordinator.miniserver.async_update_device_registry()
    config_entry.loxone_via_device_id = host_device.id if host_device is not None else None

    # CORE-30 (WP-5.2): a firmware below the floor is a repair issue, not a
    # silent accident; at or above it removes a stray issue (no-op).
    _async_reconcile_firmware_issue(hass, config_entry, coordinator.miniserver.software_version)

    # CORE-05: the baseline ``_entry_updated`` compares against.  Taken here,
    # after the unique-id stamp above and immediately before the listener is
    # registered, so it describes exactly the entry the listener starts from.
    coordinator.entry_config_snapshot = _entry_config_snapshot(config_entry)
    config_entry.async_on_unload(config_entry.add_update_listener(_entry_updated))

    # CORE-31 (WP-5.2): the coordinator is owned by the config entry itself
    # (``runtime_data``), not by a long-lived ``hass.data[DOMAIN]`` dict.
    # HA removes the attribute when the entry unloads; platforms, services,
    # diagnostics and entity code all read it from the entry (see
    # ``miniserver.get_miniserver_from_hass``).
    config_entry.runtime_data = coordinator

    # Platforms create their entities exclusively from the config entry
    # in their ``async_setup_entry``; the redundant
    # ``async_load_platform`` discovery loop passed a ``ConfigEntry``
    # where a YAML dict was expected and created no entities at all, and
    # its DomainPlatform was never unloaded (CORE-14).
    await hass.config_entries.async_forward_entry_setups(config_entry, LOXONE_PLATFORMS)
    _async_remove_stale_entries(hass, config_entry, coordinator.api.structure_file)

    # Up to 0.10.9 intercom outputs were switches; they are pushbuttons now.
    # Runs after the platforms so the replacement buttons are registered.
    async_remove_obsolete_intercom_switches(hass, config_entry, coordinator.miniserver.lox_config.json)

    async def run_loxone_session() -> None:
        """
        API-09: run the in-place reconnect supervisor for this entry.

        Wire messages are funneled through the coordinator's single
        :meth:`handle_message` (CORE-27): it fires the public
        ``loxone_event`` bus event (payload + ``entry_id``) for user
        automations and dispatches an entry-scoped per-uuid signal that
        only this entry's entities subscribed to.

        Transient failures never reach here -- ``LoxoneConnection.run``
        retries inside the API layer, and the entities merely flip their
        ``available`` flag (CORE-28). The only exit that means *the stored
        credentials no longer work* is ``LoxoneUnauthorisedError``:
        start the reauth flow (WP-3.4 / CORE-19). A 401 immediately after
        a reconnection is sometimes a Miniserver boot artefact -- the
        reauth form revalidates the credentials live, so nothing is lost
        by going through it: leaving the unchanged set in place simply
        brings the entry back up. Reloading the entry on connection
        errors is eliminated entirely (CORE-05).
        """
        try:
            await coordinator.api.run(coordinator.set_connected_state, callback=coordinator.handle_message)
        except LoxoneUnauthorisedError as e:
            coordinator.set_connected_state(False)
            _LOGGER.exception(
                "Miniserver at %s rejected the stored credentials (%s); starting the "
                "reauth flow - confirm or correct the credentials in "
                "Settings > Devices & Services.",
                host,
                e,
            )
            # CORE-30 (WP-5.2): make the rejected-credential state visible as
            # a repair issue; the reauth completion (successful setup) removes
            # it.
            _async_report_auth_failure(hass, config_entry)
            config_entry.async_start_reauth(hass)
        except asyncio.CancelledError:
            raise
        # The last line of defence for this entry: run() has already logged
        # the error with its traceback. A delayed reload (instead of an
        # immediate one) keeps a deterministic failure from hammering the
        # Miniserver; the sleep is cancelled with this task on unload.
        except Exception as e:  # noqa: BLE001
            coordinator.set_connected_state(False)
            _LOGGER.warning(
                "Loxone session for %s stopped after an unexpected error (%s); reloading the entry in %d s",
                host,
                e,
                UNEXPECTED_FAILURE_RELOAD_DELAY,
            )
            await asyncio.sleep(UNEXPECTED_FAILURE_RELOAD_DELAY)
            hass.config_entries.async_schedule_reload(config_entry.entry_id)

    async def create_groups(_hass: HomeAssistant) -> None:
        """
        Create the auto-groups, once per install.

        CORE-06: ran at HA-started via ``async_at_started`` (immediately
        when HA is already running, as after a reload) with the
        unsubscribe kept for unload.  All of the decisions moved to
        :func:`create_loxone_groups` (option gate, idempotence, the
        fixed PS-14 matching table).
        """
        await create_loxone_groups(_hass, config_entry)

    async def loxone_send(event):
        """
        Outbound commands fired on the bus by external users.

        ``SENDDOMAIN`` / ``SECUREDSENDDOMAIN`` are documented for user
        automations and stay — but each entry now forwards only uuids
        its own Miniserver actually knows (CORE-27: before the fix
        *every* entry executed *every* command, so one fired event
        reached both Miniservers).  Verified against the structure file
        once per entry setup (``coordinator.known_uuids``).
        """
        try:
            if not isinstance(event.data, dict):
                return
            secured = event.event_type == SECUREDSENDDOMAIN
            value = event.data.get(ATTR_VALUE, DEFAULT)
            device_uuid = event.data.get(ATTR_UUID, DEFAULT)
            if value is None:
                value = DEFAULT
            if device_uuid is None:
                device_uuid = DEFAULT
            if not isinstance(device_uuid, str) or device_uuid not in coordinator.known_uuids:
                _LOGGER.debug(
                    "Skipping %s: uuid %r does not belong to this Miniserver",
                    event.event_type,
                    device_uuid,
                )
                return
            if coordinator.api is None:
                _LOGGER.warning("Skipping %s: connection not ready", event.event_type)
                return
            if secured:
                code = event.data.get(ATTR_CODE, DEFAULT)
                if code is None:
                    code = DEFAULT
                coro = coordinator.api.send_secured_websocket_command(device_uuid, value, code)
            else:
                coro = coordinator.api.send_websocket_command(device_uuid, value)

            # Tracked on the entry (CORE-03/RUF006): HA awaits it on
            # unload; the old bare ``asyncio.create_task`` discarded
            # the reference the task could be garbage-collected.
            config_entry.async_create_background_task(hass, coro, name="loxone-send-command")
        except Exception as e:
            _LOGGER.exception(e)

    # CORE-04: the four loxone domain services are registered once for
    # the whole integration in ``async_setup`` — re-registering them
    # per entry only rebound the handler to the newest coordinator —
    # and are removed when the last entry unloads.

    # Every listener is registered-for-life with the config entry
    # (CORE-06): previously the two ``listen_once`` subscriptions for
    # ``EVENT_HOMEASSISTANT_STOP``/``STARTED`` discarded their unsubs and
    # accumulated on every reload, and the two persistent send
    # subscriptions were cleaned up by a coordinator hook that dead code
    # skipped.
    config_entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, partial(_persist_token_and_close, hass, config_entry, coordinator)
        )
    )
    config_entry.async_on_unload(async_at_started(hass, create_groups))
    config_entry.async_on_unload(hass.bus.async_listen(SENDDOMAIN, loxone_send))
    config_entry.async_on_unload(hass.bus.async_listen(SECUREDSENDDOMAIN, loxone_send))

    # The in-place reconnect supervisor (API-09) is the entry's
    # long-lived background task.  CORE-03: it now lives through
    # ``config_entry.async_create_background_task`` — the reference is
    # stable (the old local variable could be collected), HA keeps it
    # through the entry lifecycle, and unload cancels it on purpose in
    # ``async_unload_entry`` before the connection drops.
    coordinator.listening_task = config_entry.async_create_background_task(
        hass,
        run_loxone_session(),
        name="loxone-session",
    )

    return True


# Above this share of stale entries, the structure file is more likely
# incomplete (a Miniserver mid-upload) than the house rebuilt: nothing is removed.
STALE_ENTRY_MAX_SHARE = 0.5


@callback
def _async_remove_stale_entries(hass: HomeAssistant, config_entry: ConfigEntry, lox_config: dict | None) -> None:
    """
    Remove entities and devices whose Loxone object is gone.

    A block deleted in Loxone Config left its entities behind for good,
    shown as "no longer provided" (and its device, empty). Only registry
    entries whose unique id or device identifier starts with a Loxone id
    are judged; one that occurs nowhere in the structure file is stale.
    Entities created later at run time (scenes, WindowMonitor lock
    entities) reuse ids from the structure file, so they are kept.
    """
    present = structure_ids(lox_config)
    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
    judged = [e for e in entries if leading_loxone_id(e.unique_id)]
    stale = [e for e in judged if leading_loxone_id(e.unique_id) not in present]
    if not stale:
        return
    if len(stale) > STALE_ENTRY_MAX_SHARE * len(judged):
        _LOGGER.warning(
            "%d of %d Loxone entities are not in the structure file; not removing any, "
            "the structure file looks incomplete",
            len(stale),
            len(judged),
        )
        return
    for entry in stale:
        _LOGGER.info("Removing %s: its Loxone object is no longer in the structure file", entry.entity_id)
        ent_reg.async_remove(entry.entity_id)

    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id):
        ids = [leading_loxone_id(ident) for domain, ident in device.identifiers if domain == DOMAIN]
        if not ids or not all(ids) or any(i in present for i in ids):
            continue
        if er.async_entries_for_device(ent_reg, device.id, include_disabled_entities=True):
            continue
        if set(device.config_entries) != {config_entry.entry_id}:
            continue  # shared with another integration: not ours to delete
        _LOGGER.info("Removing device %s: its Loxone object is no longer in the structure file", device.name)
        # async_remove_device, not async_update_device(remove_config_entry_id=...):
        # from 2026.10 a device belongs to one config entry and the latter
        # logs a deprecation (removed in 2027.8).
        dev_reg.async_remove_device(device.id)


async def async_remove_config_entry_device(
    _hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """
    Allow deleting a device only when its Loxone object is gone.

    A device whose object is still in the structure file (or the Miniserver
    itself) would come straight back at the next setup.
    """
    coordinator = getattr(config_entry, "runtime_data", None)
    api = getattr(coordinator, "api", None)
    if api is None:
        return True
    present = structure_ids(api.structure_file)
    serial = getattr(getattr(coordinator, "miniserver", None), "serial", None)
    for domain, ident in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if ident == serial or leading_loxone_id(ident) in present:
            return False
    return True


def _device_info_name(device_info) -> str | None:
    """The ``name`` of a DeviceInfo: a dict before 2026.11, a dataclass from then."""
    if device_info is None:
        return None
    name = device_info.get("name") if isinstance(device_info, dict) else getattr(device_info, "name", None)
    return name if isinstance(name, str) and name.strip() else None


class LoxoneEntity(InPlaceEntityIdChange, Entity):
    """
    @DynamicAttrs
    """

    # Loxone entities are event-driven; never poll the Miniserver.
    _attr_should_poll = False
    # Keep the recorder from logging these high-churn / low-signal attributes.
    _unrecorded_attributes = frozenset({"uuid", "platform", "room", "category", "state_uuid", "device_type"})
    # WP-5.1 (CORE-26): entity names are interpreted relative to the
    # device.  Primary entities therefore carry *no* name of their own
    # (the device name — which is the control's own name — is what the
    # UI shows), and sub-entities carry short names ("Override Reason",
    # "Total", …).  Without this, the UI rendered
    # "Living Room Light Switch Living Room Light Switch": the entity
    # name repeated its device's name.
    _attr_has_entity_name = True
    # Default entity name: empty (= adopt the device name).  Sub-entities
    # set a short instance value in their ``__init__``; the Miniserver
    # diagnostic sensors and the device-less YAML entries keep an explicit
    # name.
    _attr_name: str | None = None
    # (state key, attribute name) pairs of 0/1 states shown as boolean
    # attributes, e.g. a Gate's ``preventOpen``; set by the platforms.
    _flag_states: tuple[tuple[str, str], ...] = ()

    @property
    def name(self):
        """
        The entity's own name, without a leading copy of its device's name.

        Loxone installers name an output after its controller ("home cinema
        led achter" on the controller "Home Cinema"); with
        ``has_entity_name`` the UI showed "Home Cinema home cinema led
        achter". A name that equals the device name is kept as it is: as a
        second unnamed entity it would only collide with the main one.
        """
        name = super().name
        device_name = _device_info_name(getattr(self, "_attr_device_info", None))
        if not isinstance(name, str) or not device_name:
            return name
        prefix = f"{device_name.casefold()} "
        if name.casefold().startswith(prefix) and (rest := name[len(prefix) :].strip()):
            return rest
        return name

    def __init__(self, **kwargs):
        # The control's raw name from the structure file.  With
        # ``has_entity_name`` an empty ``_attr_name`` means "inherit the
        # device name", so the raw name is kept apart: it names the
        # *device*, drives classification, and sub-entities take it as
        # their own short name.
        self._lox_name = kwargs.get("name")
        if uuid := kwargs.get("uuidAction"):
            # CORE-26: set as an ``_attr_unique_id``; the old
            # ``functools.cached_property`` override (and the ones the
            # platforms repeated) bypassed HA's registry/language logic
            # and is deleted with it.
            self._attr_unique_id = uuid
        for key, value in kwargs.items():
            if key == "name":
                continue
            if not hasattr(self, key):
                setattr(self, key, value)
            else:
                try:
                    setattr(self, key, value)
                except AttributeError:
                    _LOGGER.exception("Could not set %s=%r for %s", key, value, type(self).__name__)
                except Exception:
                    _LOGGER.exception("Could not set %s=%r for %s", key, value, type(self).__name__)

        # Initialize base extra state attributes with common Loxone fields
        self._attr_extra_state_attributes = {
            "uuid": kwargs.get("uuidAction", ""),
            "platform": "loxone",
        }

        # Add optional common attributes from Loxone JSON if they exist
        if kwargs.get("room"):
            self._attr_extra_state_attributes["room"] = kwargs["room"]
        if kwargs.get("cat"):
            self._attr_extra_state_attributes["category"] = kwargs["cat"]

    async def async_added_to_hass(self):
        """
        Subscribe to this entry's state signals (CORE-27) and its
        connection-liveness signal (API-09/CORE-28).  HA fires the
        ``async_on_remove`` hooks on entity removal, so an in-place
        reload leaks nothing.

        Entry entities receive one callback per *subscribed* uuid — the
        coordinator's per-(entry-id, uuid) dispatcher signals — instead
        of one bus task per entity per message.
        """
        coordinator = self._connection_coordinator()
        if coordinator is None:
            # YAML-defined entities (no config entry to namespace the
            # signals with) keep the documented-but-legacy behavior:
            # the global loxone_event bus event.
            self.async_on_remove(self.hass.bus.async_listen(EVENT, self._on_unscoped_message))
            return
        entry_id = coordinator.config_entry.entry_id
        self.async_on_remove(
            async_dispatcher_connect(self.hass, loxone_connected_signal(entry_id), self._on_connection_state)
        )
        for uuid in self._state_uuids():
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    loxone_uuid_signal(entry_id, uuid),
                    partial(self._on_scoped_state_value, uuid),
                )
            )
        lock_uuid = self._lock_state_uuid()
        # Subscribed even when a platform also reads the lock itself (the
        # Radio select): every entity must know it to refuse commands.
        if lock_uuid is not None:
            self.async_on_remove(
                async_dispatcher_connect(self.hass, loxone_uuid_signal(entry_id, lock_uuid), self._on_lock_state)
            )
        states = getattr(self, "states", None)
        for state_key, attribute in self._flag_states:
            uuid = states.get(state_key) if isinstance(states, dict) else None
            if isinstance(uuid, str) and uuid:
                self.async_on_remove(
                    async_dispatcher_connect(
                        self.hass, loxone_uuid_signal(entry_id, uuid), partial(self._on_flag_state, attribute)
                    )
                )

    @callback
    def _on_flag_state(self, attribute: str, value) -> None:
        """A 0/1 state a platform exposes as a boolean attribute (``_flag_states``)."""
        try:
            flag = bool(float(value))
        except (TypeError, ValueError):
            return
        self._flag_attributes = {**getattr(self, "_flag_attributes", {}), attribute: flag}
        self.async_write_ha_state()

    def _lock_state_uuid(self) -> str | None:
        states = getattr(self, "states", None)
        uuid = states.get("jLocked") if isinstance(states, dict) else None
        return uuid if isinstance(uuid, str) and uuid else None

    @callback
    def _on_lock_state(self, value) -> None:
        """
        The control's ``jLocked`` state: a locked control ignores commands
        from the API (structure file v16), so the lock is shown as
        attributes and commands to it are refused (see ``_send``).
        """
        locked, reason = parse_control_lock(value)
        self._control_lock = (locked, reason)
        self._lock_attributes = (
            {"locked": True, "locked_by": LOCKED_BY[locked], "lock_reason": reason} if locked else {"locked": False}
        )
        self.async_write_ha_state()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Platforms build their own attribute dicts; the lock attributes are
        # merged into every one of them here, so no platform can miss them.
        prop = cls.__dict__.get("extra_state_attributes")
        if isinstance(prop, property) and prop.fget is not None and not getattr(prop.fget, "_merges_lock", False):

            def extra_state_attributes(self, _fget=prop.fget):
                attributes = _fget(self)
                flags = getattr(self, "_flag_attributes", None)
                lock = getattr(self, "_lock_attributes", None)
                if not flags and not lock:
                    return attributes
                return {**(attributes or {}), **(flags or {}), **(lock or {})}

            extra_state_attributes._merges_lock = True
            cls.extra_state_attributes = property(extra_state_attributes)

    def _state_uuids(self) -> frozenset[str]:
        """
        CORE-27 (PS-13): the stream uuids this entity reacts to.

        Precomputed ``frozenset`` (no per-event set allocations); classes
        override it in ``__init__``.  Defaults to the control's
        ``uuidAction`` — the stream that plain single-state entities
        update.  The pseudo-key ``keep_alive`` is stream message's
        special key and can be subscribed exactly like a uuid.
        """
        uuid = getattr(self, "uuidAction", None)
        return frozenset({uuid}) if isinstance(uuid, str) and uuid else frozenset()

    @callback
    def _on_scoped_state_value(self, uuid: str, value) -> None:
        """Per-uuid entry signal (the normal path)."""
        self.event_handler({uuid: value})

    @callback
    def _on_unscoped_message(self, event) -> None:
        """Global bus fallback for YAML entities (no entry to scope to)."""
        self.event_handler(event.data)

    @callback
    def _on_connection_state(self, _connected: bool) -> None:
        """Re-publish state (unavailable / concrete) on a connection flip."""
        self.async_write_ha_state()

    def _send(self, value, uuid: str | None = None, secured: bool = False, code: str | None = None) -> None:
        """
        CORE-27: route outbound commands through this entity's *own*
        entry's coordinator.

        ``uuid`` defaults to ``self.uuidAction`` (sub-control commands
        pass their own).  No coordinator (e.g. a bare entity in unit
        style tests, or the moment during entry reload) falls back to
        the outbound bus event, which the owning entry's filtered
        listener executes.
        """
        target = uuid if uuid is not None else self.uuidAction
        locked, reason = getattr(self, "_control_lock", (0, ""))
        if locked and target == self.uuidAction:
            # The Miniserver ignores commands to a locked control; say so
            # instead of sending one that silently does nothing.
            raise HomeAssistantError(
                f"{self._lox_name or target} is locked by the Loxone {LOCKED_BY[locked]}"
                + (f": {reason}" if reason else "")
            )
        coordinator = self._connection_coordinator()
        api = getattr(coordinator, "api", None)

        def _dispatch() -> None:
            # Always runs on the event-loop thread (see below).
            if coordinator is None or api is None:
                _LOGGER.debug("No live Loxone coordinator for %s; falling back to the bus", target)
                if secured:
                    self.hass.bus.async_fire(SECUREDSENDDOMAIN, dict(uuid=target, value=value, code=code))
                else:
                    self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=target, value=value))
                return
            if secured:
                coro = api.send_secured_websocket_command(target, value, code if code is not None else DEFAULT)
            else:
                coro = api.send_websocket_command(target, value)
            # Tracked on the entry (CORE-03/RUF006): unload cancels it.
            coordinator.config_entry.async_create_background_task(self.hass, coro, name=f"loxone-send-{target}")

        # Thread safety: HA runs a *sync* service handler (``def open_cover``,
        # ``def set_temperature``, ``def press`` ...) in its executor, and both
        # ``async_create_background_task`` and ``bus.async_fire`` are
        # loop-thread-only -- creating the task from a worker thread raised
        # "loop ... is not the running loop" (eager task factory) and the
        # command was silently lost. Hop onto the loop when we are not on it.
        loop = getattr(self.hass, "loop", None)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or running is loop:
            _dispatch()
        else:
            loop.call_soon_threadsafe(_dispatch)

    def _connection_coordinator(self) -> LoxoneCoordinator | None:
        """
        The owning entry's :class:`LoxoneCoordinator`, or None if not resolvable.

        Resolution prefers ``platform.config_entry``: HA reuses entity
        objects across setup/reload cycles (the entity registry keeps the
        unique-id → object mapping), so the ``config_entry`` instantiated
        at construction time can point at a replaced/superseded entry
        while ``platform.config_entry`` tracks the live platform owner.

        YAML-defined entities and bare unit-test entities resolve to
        ``None`` and keep the bus fallback.
        """
        hass_obj = getattr(self, "hass", None)
        if hass_obj is None:
            return None
        platform = getattr(self, "platform", None)
        entry = getattr(platform, "config_entry", None)
        if entry is None:
            entry = getattr(self, "config_entry", None)
        if entry is None:
            return None
        # CORE-31 (WP-5.2): the coordinator is reached via the config entry
        # (``runtime_data``), not via ``hass.data[DOMAIN]``.
        coordinator = getattr(entry, "runtime_data", None)
        return coordinator if (coordinator is not None and hasattr(coordinator, "connected")) else None

    @property
    def available(self) -> bool:
        """
        Available only while the Miniserver session is live (CORE-28).

        Combines the coordinator's live reconnect state with the per-entity
        ``_attr_available`` ("value not yet seen"), which keeps working.
        """
        if not super().available:
            return False
        coordinator = self._connection_coordinator()
        return coordinator is None or coordinator.connected

    @callback
    def async_write_ha_state(self) -> None:
        """
        Skip the state write while HA has not attached the entity.

        Event-driven writes (and unit-style tests driving
        :meth:`event_handler` directly on raw instances) can happen before
        the entity has an ``entity_id``; the stock write raises
        ``NoEntitySpecifiedError`` in that window, which would abort the
        whole message callback. Dropping that write mirrors what HA itself
        rejects, and the next event (or the scheduled write after add)
        republishes.
        """
        if self.entity_id is None:
            return
        super().async_write_ha_state()

    @callback
    def event_handler(self, e) -> None:
        """
        CORE-27: one invocation per subscribed uuid.

        ``e`` is a dict mapping the updated uuid(s) to their values —
        a single-uuid slice on the normal dispatcher path, the entry's
        full message on the unscoped bus fallback.  Yields a
        synchronous (``@callback``) handler, not one task per entity
        per message (PS-13).  Subclasses keep the previous single
        handler body verbatim and just drop the ``async`` and the
        ``.data`` attribute access.
        """

    @staticmethod
    def _get_format(lox_format):
        search = re.search(cfmt, lox_format, flags=re.VERBOSE)
        if search:
            return search.group(0).strip()
        return None
