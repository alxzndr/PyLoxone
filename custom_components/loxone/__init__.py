"""
Component to create an interface to the Loxone Miniserver.

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import asyncio
import logging
import re
import time
from functools import cached_property

import homeassistant.components.group as group
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import (
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.setup import async_setup_component

from .const import (
    ATTR_AREA_CREATE,
    ATTR_CODE,
    ATTR_DEVICE,
    ATTR_UUID,
    ATTR_VALUE,
    CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN,
    CONF_SCENE_GEN,
    CONF_SCENE_GEN_DELAY,
    CONF_VERIFY_SSL,
    DEFAULT,
    DEFAULT_DELAY_SCENE,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    EVENT,
    LOXONE_PLATFORMS,
    SECUREDSENDDOMAIN,
    SENDDOMAIN,
    cfmt,
)
from .coordinator import LoxoneCoordinator, loxone_connected_signal
from .miniserver import get_miniserver_from_hass
from .pyloxone_api.exceptions import (
    LoxoneConnectionClosedOk,
    LoxoneConnectionError,
    LoxoneServiceUnAvailableError,
    LoxoneUnauthorisedError,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Required(CONF_HOST): cv.string,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
                vol.Optional(CONF_SCENE_GEN, default=True): cv.boolean,
                vol.Optional(CONF_SCENE_GEN_DELAY, default=DEFAULT_DELAY_SCENE): cv.positive_int,
                vol.Required(CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN, default=False): bool,
            }
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

_UNDEF: dict = {}

# A Miniserver that is still booting (firmware update / reboot) answers 401 to
# authenticated requests for a short window after its HTTP server is back up
# (see 2026-09-02-pyloxone-401-setup-error.md).  A 401 during setup is therefore
# retried, but once consecutive failures span this long we escalate to an ERROR
# that points at the stored credentials.  WP-3.4 replaces the escalation branch
# with `ConfigEntryAuthFailed` + reauth.  (CORE-09)
AUTH_RETRY_MAX_ATTEMPTS = 5
AUTH_RETRY_MIN_ELAPSED_SECONDS = 300
_AUTH_FAILURES = "auth_failures"  # key in hass.data[DOMAIN]; a plain dict is not a coordinator

# TODO: get version and check for updates https://update.loxone.com/updatecheck.xml?serial=xxxxxxxxx


async def async_unload_entry(hass, config_entry):
    """Completely unloads the Loxone integration and closes all connections."""
    # Get the Miniserver instance from hass.data
    coordinator = None
    for co in getattr(hass.data.get(DOMAIN, {}), "values", lambda: [])():
        if hasattr(co, "config_entry") and co.config_entry.entry_id == config_entry.entry_id:
            coordinator = co
            break

    # Connection close
    if coordinator is not None:
        try:
            await coordinator.async_cleanup()
        except Exception as e:
            _LOGGER.warning("Error closing connection: %s", e)

            # Cancel and await the stored listening task (if any)
        try:
            task = getattr(coordinator, "_listening_task", None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    _LOGGER.debug(f"Error waiting for listening task to finish: {e}")

            # Remove event listeners (if any still present)
            if hasattr(coordinator, "listeners") and coordinator.listeners:
                for listener in coordinator.listeners:
                    try:
                        listener()  # Unsubscribe the listener
                    except Exception:
                        pass
                coordinator.listeners = []

            hass.data[DOMAIN].pop(config_entry.entry_id, None)
            # The in-flight 401-retry bookkeeping would otherwise survive the
            # reload; drop it too (CORE-09).
            _clear_auth_failure(hass, config_entry)
        except Exception as e:
            raise e

    # Services deregistrieren beim Entladen
    hass.services.async_remove(DOMAIN, "event_websocket_command")
    hass.services.async_remove(DOMAIN, "event_secured_websocket_command")
    hass.services.async_remove(DOMAIN, "sync_areas")
    hass.services.async_remove(DOMAIN, "quick_shade")
    hass.services.async_remove(DOMAIN, "enable_sun_automation")
    hass.services.async_remove(DOMAIN, "disable_sun_automation")
    hass.services.async_remove(DOMAIN, "reload")

    # Unload
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, LOXONE_PLATFORMS)
    return unload_ok


async def async_setup(hass, config):
    """setup loxone"""
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(DOMAIN, context={"source": "import"}, data=config[DOMAIN])
        )
    return True


async def async_migrate_entry(hass, config_entry):
    """Migrate a config entry using Home Assistant's supported update API."""
    old_version = config_entry.version
    version = old_version
    options = dict(config_entry.options)

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

    if version != old_version:
        hass.config_entries.async_update_entry(config_entry, options=options, version=version)
    return True


async def async_set_options(hass, config_entry):
    options_in = {**config_entry.options}
    options = {
        CONF_HOST: options_in.pop(CONF_HOST, ""),
        CONF_PORT: options_in.pop(CONF_PORT, DEFAULT_PORT),
        CONF_USERNAME: options_in.pop(CONF_USERNAME, ""),
        CONF_PASSWORD: options_in.pop(CONF_PASSWORD, ""),
        CONF_VERIFY_SSL: options_in.pop(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        CONF_SCENE_GEN: options_in.pop(CONF_SCENE_GEN, ""),
        CONF_SCENE_GEN_DELAY: options_in.pop(CONF_SCENE_GEN_DELAY, DEFAULT_DELAY_SCENE),
        CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN: options_in.pop(CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN, ""),
    }
    hass.config_entries.async_update_entry(config_entry, data=config_entry.data, options=options)


async def async_config_entry_updated(hass, entry) -> None:
    """Handle signals of config entry being updated.

    This is a static method because a class method (bound method), can not be used with weak references.
    Causes for this is either discovery updating host address or config entry options changing.
    """
    pass


async def create_group_for_loxone_entities(hass, entities, name, object_id):
    try:
        await group.Group.async_create_group(
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
        await group.Group.async_create_group(
            hass,
            name,
            created_by_service=True,
            entity_ids=entities,
            icon=None,
            mode=None,
            object_id=object_id,
            order=None,
        )
        _LOGGER.error("Can't create group '%s' with error: %s", name, err)
    except Exception as e:
        _LOGGER.error(
            "Can't create group '%s'. Try to make at least one group manually. ("
            "https://www.home-assistant.io/integrations/group/)",
            e,
        )


def _hass_data(hass) -> dict:
    """Return ``hass.data[DOMAIN]``, creating it if needed."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    return hass.data[DOMAIN]


def _auth_failure_tracker(hass) -> dict:
    """Return the per-entry consecutive-auth-failure store.

    HA re-creates the coordinator on every setup attempt, so the counter and
    first-failure timestamp must not live on the coordinator.  They are kept
    in ``hass.data[DOMAIN]["auth_failures"]`` (a plain dict keyed by entry id,
    ignored by the coordinator-lookup helpers which test
    ``hasattr(value, ...)``).  Removed again by :func:`_clear_auth_failure`
    once all counters are reset, so the domain data never holds stale
    non-coordinator keys. (CORE-09)
    """
    return _hass_data(hass).setdefault(_AUTH_FAILURES, {})


def _record_auth_failure(hass, config_entry, now=None):
    """Record one 401 during setup. Returns ``(consecutive_count, first_failure_time)``.

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
    """Reset the consecutive-auth-failure counter after a successful setup.

    Also removes the empty ``auth_failures`` dict from ``hass.data[DOMAIN]``
    so the domain data contains no non-coordinator keys while every entry
    that should be live is live (``system_health`` iterates that dict and
    reports "Unavailable" for any value lacking a ``miniserver``).
    """
    tracker = _hass_data(hass).setdefault(_AUTH_FAILURES, {})
    tracker.pop(config_entry.entry_id, None)
    if not tracker:
        _hass_data(hass).pop(_AUTH_FAILURES, None)


def _should_escalate_auth_failure(consecutive_count, first_failure_time, now) -> bool:
    """Escalate only after the bounded retry window is exhausted.

    Intent: a booting Miniserver emits 401 for a short window, so a *short* run
    of failures is retried quietly; 5+ consecutive failures spanning >= 5
    minutes means the credentials are probably wrong (or the server is not the
    one we think it is).  VERIFY against a live Miniserver's boot behaviour
    before relying on the escalation actually firing for genuinely bad
    credentials: until then the ERROR is only *guidance*, setup still keeps
    retrying (WP-3.4 makes it terminal via reauth).
    """
    return consecutive_count >= AUTH_RETRY_MAX_ATTEMPTS and (now - first_failure_time) >= AUTH_RETRY_MIN_ELAPSED_SECONDS


async def async_setup_entry(hass, config_entry):
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    if not config_entry.options:
        await async_set_options(hass, config_entry)

    coordinator = LoxoneCoordinator(hass, config_entry)
    host = config_entry.options.get(CONF_HOST)

    _LOGGER.info(
        "Setting up Loxone integration for Miniserver at %s:%s",
        host,
        config_entry.options.get(CONF_PORT),
    )

    # Every branch of the inner block re-raises on failure, so the ``finally``
    # below is the single place that guarantees the (partially opened) API
    # handle is closed on *every* setup failure — previously the 401 branch
    # returned False (no retry, connection leaked) and the 503 branch leaked
    # too (CORE-09).
    entry_setup_failed = True
    try:
        try:
            await coordinator.async_config_entry_first_refresh()
            entry_setup_failed = False
        except LoxoneServiceUnAvailableError as err:
            _LOGGER.warning(
                "Loxone Miniserver at %s is unavailable (service restarting?). Will retry automatically",
                host,
            )
            raise ConfigEntryNotReady from err
        except LoxoneUnauthorisedError as err:
            # A Miniserver coming out of a reboot answers 401 before auth is
            # ready: retry, and only after the bounded window escalate (CORE-09).
            attempt, first_attempt = _record_auth_failure(hass, config_entry)
            if _should_escalate_auth_failure(attempt, first_attempt, time.monotonic()):
                _LOGGER.error(
                    "Miniserver at %s answered 401 %i consecutive times during setup over at least %i "
                    "minutes. Please check the stored credentials (username and password) for this "
                    "Miniserver in Settings > Devices & Services; if they are correct the Miniserver may "
                    "simply still be booting. Retrying automatically.",
                    host,
                    attempt,
                    AUTH_RETRY_MIN_ELAPSED_SECONDS // 60,
                )
            else:
                _LOGGER.warning(
                    "Miniserver answered 401 during setup; retrying (attempt %i)",
                    attempt,
                )
            raise ConfigEntryNotReady from err
        except OSError as err:
            _LOGGER.warning(
                "Network error connecting to Loxone Miniserver at %s: %s. Will retry automatically",
                host,
                err,
            )
            raise ConfigEntryNotReady from err
        except (
            LoxoneConnectionError,
            LoxoneConnectionClosedOk,
            TimeoutError,
            ConnectionError,
        ) as err:
            _LOGGER.warning(
                "Could not connect to Loxone Miniserver at %s: %s. Will retry automatically",
                host,
                err,
            )
            raise ConfigEntryNotReady from err
        except Exception as err:
            _LOGGER.warning(
                "Unexpected error connecting to Loxone Miniserver at %s: %s. Will retry automatically",
                host,
                err,
            )
            raise ConfigEntryNotReady from err
    finally:
        if entry_setup_failed:
            if coordinator.api is not None:
                await coordinator.api.close()

    _clear_auth_failure(hass, config_entry)

    _LOGGER.info(
        "Successfully connected to Loxone Miniserver at %s",
        host,
    )

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    setup_tasks = []
    await hass.config_entries.async_forward_entry_setups(config_entry, LOXONE_PLATFORMS)
    for platform in LOXONE_PLATFORMS:
        setup_tasks.append(hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config_entry)))

    if setup_tasks:
        await asyncio.wait(setup_tasks)

    async def message_callback(message):
        """Fire message on HomeAssistant Bus."""
        _LOGGER.debug(f"{message}")
        hass.bus.async_fire(EVENT, message)

    async def run_loxone_session() -> None:
        """API-09: run the in-place reconnect supervisor for this entry.

        Transient failures never reach here -- ``LoxoneConnection.run``
        retries inside the API layer and the entities merely flip their
        ``available`` flag (CORE-28). The only exit is
        ``LoxoneUnauthorisedError`` (credentials rejected): log an ERROR
        and stop; the reauth flow itself lands in WP-3.4. Reloading the
        entry on connection errors is eliminated entirely (CORE-05).
        """
        try:
            await coordinator.api.run(coordinator.set_connected_state, callback=message_callback)
        except LoxoneUnauthorisedError as e:
            coordinator.set_connected_state(False)
            _LOGGER.error(
                "Miniserver at %s rejected the stored credentials (%s); re-authentication is "
                "required before the connection can be retried. Check Settings > Devices & Services.",
                host,
                e,
            )
        except asyncio.CancelledError:
            raise

    def start_event() -> None:
        # A *background* task: it runs for the lifetime of the entry and
        # must not hold up startup or ``async_block_till_done`` (API-09).
        listening_task = hass.async_create_background_task(run_loxone_session(), name="loxone-session")
        coordinator._listening_task = listening_task

    async def handle_websocket_command(call):
        """Handle websocket command services."""
        value = call.data.get(ATTR_VALUE, DEFAULT)
        if call.data.get(ATTR_DEVICE) is None:
            entity_uuid = call.data.get(ATTR_UUID, DEFAULT)
        else:
            entity_registry = er.async_get(hass)
            entity_id = call.data.get(ATTR_DEVICE)
            entity = entity_registry.async_get(entity_id)
            entity_uuid = entity.unique_id
        await coordinator.api.send_websocket_command(entity_uuid, value)

    async def handle_secured_websocket_command(call):
        """Handle websocket command services."""
        value = call.data.get(ATTR_VALUE, DEFAULT)
        code = call.data.get(ATTR_CODE, DEFAULT)
        if call.data.get(ATTR_DEVICE) is None:
            entity_uuid = call.data.get(ATTR_UUID, DEFAULT)
        else:
            entity_registry = er.async_get(hass)
            entity_id = call.data.get(ATTR_DEVICE)
            entity = entity_registry.async_get(entity_id)
            entity_uuid = entity.unique_id
        await coordinator.api.send_secured__websocket_command(entity_uuid, value, code)

    async def sync_areas_with_loxone(data=None):
        data = data or {}
        create_areas = data.get(ATTR_AREA_CREATE, DEFAULT)
        if create_areas not in [True, False]:
            create_areas = False
        lox_items = []
        er_registry = er.async_get(hass)
        ar_registry = ar.async_get(hass)
        for _id, entry in er_registry.entities.items():
            if entry.platform == DOMAIN:
                state = hass.states.get(entry.entity_id)
                if hasattr(state, "attributes") and "room" in state.attributes:
                    area = ar_registry.async_get_area_by_name(state.attributes["room"])
                    if area is None and create_areas:
                        area = ar_registry.async_get_or_create(state.attributes["room"])
                    if area and entry.area_id is None:
                        lox_items.append((entry.entity_id, area.id))

        for _ in lox_items:
            er_registry.async_update_entity(_[0], area_id=_[1])

    async def handle_sync_areas_with_loxone(call):
        await sync_areas_with_loxone(call.data)

    async def handle_reload(call):
        """Handle the service call to reload the integration.

        Per entry via ``async_schedule_reload`` (HA handles the safe
        unload-then-load ordering), instead of unloading *every* entry and
        then reloading them -- which unloaded each entry twice and churned
        unrelated Miniserver instances as well (CORE-05).
        """
        entries = hass.config_entries.async_entries(DOMAIN)
        _LOGGER.info("Reloading %i Loxone config entry(ies) via service call", len(entries))
        for entry in entries:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    async def loxone_discovered(event):
        _LOGGER.info("Creating groups")
        miniserver = get_miniserver_from_hass(hass, config_entry)
        if miniserver.miniserver_type < 2 and "component" in event.data:
            if event.data["component"] == DOMAIN:
                try:
                    _LOGGER.info("loxone discovered")
                    await asyncio.sleep(0.1)
                    # await sync_areas_with_loxone()
                    entity_ids = hass.states.async_all()
                    sensors_analog = []
                    sensors_digital = []
                    switches = []
                    covers = []
                    lights = []
                    dimmers = []
                    climates = []
                    fans = []
                    accontrols = []
                    numbers = []
                    texts = []
                    buttons = []

                    for s in entity_ids:
                        s_dict = s.as_dict()
                        attr = s_dict["attributes"]
                        if "platform" in attr and attr["platform"] == DOMAIN:
                            device_type = attr.get("device_type", "")
                            if device_type in ["analog_sensor", "Meter"]:
                                sensors_analog.append(s_dict["entity_id"])
                            elif device_type == "digital_sensor":
                                sensors_digital.append(s_dict["entity_id"])
                            elif device_type in ["Jalousie", "Gate", "Window"]:
                                covers.append(s_dict["entity_id"])
                            elif device_type in ["Switch", "TimedSwitch"]:
                                switches.append(s_dict["entity_id"])
                            elif device_type == "Pushbutton":
                                buttons.append(s_dict["entity_id"])
                            elif device_type in ["LightControllerV2"]:
                                lights.append(s_dict["entity_id"])
                            elif device_type == "Dimmer":
                                dimmers.append(s_dict["entity_id"])
                            elif device_type == "IRoomControllerV2":
                                climates.append(s_dict["entity_id"])
                            elif device_type == "Ventilation":
                                fans.append(s_dict["entity_id"])
                            elif device_type == "AcControl":
                                accontrols.append(s_dict["entity_id"])
                            elif device_type == "Slider":
                                numbers.append(s_dict["entity_id"])
                            elif device_type == "TextInput":
                                texts.append(s_dict["entity_id"])

                    sensors_analog.sort()
                    sensors_digital.sort()
                    covers.sort()
                    switches.sort()
                    buttons.sort()
                    lights.sort()
                    climates.sort()
                    dimmers.sort()
                    fans.sort()
                    accontrols.sort()
                    numbers.sort()
                    texts.sort()
                    await async_setup_component(hass, "group", {})
                    await create_group_for_loxone_entities(
                        hass, sensors_analog, "Loxone Analog Sensors", "loxone_analog"
                    )
                    await create_group_for_loxone_entities(
                        hass,
                        sensors_digital,
                        "Loxone Digital Sensors",
                        "loxone_digital",
                    )
                    await create_group_for_loxone_entities(hass, switches, "Loxone Switches", "loxone_switches")
                    await create_group_for_loxone_entities(hass, buttons, "Loxone Buttons", "loxone_buttons")
                    await create_group_for_loxone_entities(hass, covers, "Loxone Covers", "loxone_covers")
                    await create_group_for_loxone_entities(hass, lights, "Loxone LightControllers", "loxone_lights")
                    await create_group_for_loxone_entities(hass, lights, "Loxone Dimmer", "loxone_dimmers")
                    await create_group_for_loxone_entities(hass, climates, "Loxone Room Controllers", "loxone_climates")
                    await create_group_for_loxone_entities(
                        hass,
                        fans,
                        "Loxone Ventilation Controllers",
                        "loxone_ventilations",
                    )
                    await create_group_for_loxone_entities(
                        hass,
                        accontrols,
                        "Loxone AC Controllers",
                        "loxone_accontrollers",
                    )
                    await create_group_for_loxone_entities(hass, numbers, "Loxone Numbers", "loxone_numbers")
                    await create_group_for_loxone_entities(hass, texts, "Loxone Texts", "loxone_texts")
                    await hass.async_block_till_done()
                    await create_group_for_loxone_entities(
                        hass,
                        [
                            "group.loxone_analog",
                            "group.loxone_digital",
                            "group.loxone_switches",
                            "group.loxone_buttons",
                            "group.loxone_covers",
                            "group.loxone_lights",
                            "group.loxone_ventilations",
                            "group.loxone_numbers",
                            "group.loxone_texts",
                        ],
                        "Loxone Group",
                        "loxone_group",
                    )
                except Exception as err:
                    _LOGGER.error(
                        "Can't create group '%s'. Try to make at least one group manually. ("
                        "https://www.home-assistant.io/integrations/group/)",
                        err,
                    )

    async def stop_event(_):
        token = coordinator.api.get_token_dict()
        hass.config_entries.async_update_entry(
            config_entry,
            data={
                **config_entry.data,  # preserve existing data
                "token": token["token"],
                "hash_alg": token["hash_alg"],
                "valid_until": token["valid_until"],
            },
        )
        await coordinator.api.close()

    async def loxone_send(event):
        """Listen for change Events from Loxone Components"""
        try:
            if event.event_type == SENDDOMAIN and isinstance(event.data, dict):
                value = event.data.get(ATTR_VALUE, DEFAULT)
                device_uuid = event.data.get(ATTR_UUID, DEFAULT)
                if value is None:
                    value = DEFAULT
                if device_uuid is None:
                    device_uuid = DEFAULT

                _ = asyncio.create_task(  # noqa: RUF006  # TODO(WP-3.1): store/cancel task
                    coordinator.api.send_websocket_command(device_uuid, value)
                )

            elif event.event_type == SECUREDSENDDOMAIN and isinstance(event.data, dict):
                value = event.data.get(ATTR_VALUE, DEFAULT)
                device_uuid = event.data.get(ATTR_UUID, DEFAULT)
                code = event.data.get(ATTR_CODE, DEFAULT)
                if code is None:
                    code = DEFAULT
                if value is None:
                    value = DEFAULT
                if device_uuid is None:
                    device_uuid = DEFAULT
                _ = asyncio.create_task(  # noqa: RUF006  # TODO(WP-3.1): store/cancel task
                    coordinator.api.send_secured__websocket_command(device_uuid, value, code)
                )

        except Exception as e:
            _LOGGER.error(e)

    hass.services.async_register(DOMAIN, "event_websocket_command", handle_websocket_command)

    hass.services.async_register(DOMAIN, "event_secured_websocket_command", handle_secured_websocket_command)
    hass.services.async_register(DOMAIN, "sync_areas", handle_sync_areas_with_loxone)
    hass.services.async_register(DOMAIN, "reload", handle_reload)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_event)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, loxone_discovered)

    # Store listeners for cleanup
    coordinator.listeners = [
        hass.bus.async_listen(SENDDOMAIN, loxone_send),
        hass.bus.async_listen(SECUREDSENDDOMAIN, loxone_send),
    ]

    start_event()

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    return True


class LoxoneEntity(Entity):
    """
    @DynamicAttrs
    """

    # Loxone entities are event-driven; never poll the Miniserver.
    _attr_should_poll = False
    # Keep the recorder from logging these high-churn / low-signal attributes.
    _unrecorded_attributes = frozenset({"uuid", "platform", "room", "category", "state_uuid", "device_type"})

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if not hasattr(self, key):
                if key == "name":
                    self._attr_name = value
                else:
                    setattr(self, key, value)
            else:
                try:
                    setattr(self, key, value)
                except AttributeError:
                    _LOGGER.error("Could not set %s=%r for %s", key, value, type(self).__name__)
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
        """Subscribe to the bus; HA cancels this on entity removal, so an
        in-place reload no longer leaks ``loxone_event`` listeners (CORE-01).

        Also subscribe to the per-entry connection-liveness signal
        (API-09/CORE-28) so the state re-publishes whenever the
        Miniserver session goes live or down.
        """
        self.async_on_remove(self.hass.bus.async_listen(EVENT, self.event_handler))
        coordinator = self._connection_coordinator()
        if coordinator is not None:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    loxone_connected_signal(coordinator.config_entry.entry_id),
                    self._on_connection_state,
                )
            )

    @callback
    def _on_connection_state(self, _connected: bool) -> None:
        """Re-publish state (unavailable / concrete) on a connection flip."""
        self.async_write_ha_state()

    def _connection_coordinator(self) -> LoxoneCoordinator | None:
        """The owning entry's :class:`LoxoneCoordinator`, or None if not resolvable."""
        hass_obj = getattr(self, "hass", None)
        if hass_obj is None:
            return None
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        if entry is None:
            return None
        return self.hass.data.get(DOMAIN, {}).get(entry.entry_id)

    @property
    def available(self) -> bool:
        """Available only while the Miniserver session is live (CORE-28).

        Combines the coordinator's live reconnect state with the per-entity
        ``_attr_available`` ("value not yet seen"), which keeps working.
        """
        if not super().available:
            return False
        coordinator = self._connection_coordinator()
        return coordinator is None or coordinator.connected

    async def event_handler(self, e):
        pass

    @cached_property
    def name(self):
        return self._attr_name

    @staticmethod
    def _get_format(lox_format):
        search = re.search(cfmt, lox_format, flags=re.X)
        if search:
            return search.group(0).strip()
        return None

    @cached_property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self.uuidAction
