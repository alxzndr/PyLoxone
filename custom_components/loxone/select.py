"""
Loxone Selects

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoxoneEntity
from .helpers import device_info_for, get_or_create_device, iter_controls

_LOGGER = logging.getLogger(__name__)

# Loxone Radio output value representing the "all off" state.
ALL_OFF_VALUE = 0
# Fallback label used when a Radio block enables the "all off" entry but does
# not provide a custom name for it.
ALL_OFF_DEFAULT_LABEL = "All off"


def jalousie_auto_command(option: str) -> str | None:
    """
    Command the Loxone Jalousie control accepts for a sun-automation mode.

    Options as displayed by :class:`LoxoneJalousieAuto` → CoverControl
    command: ``"Off"`` → ``NoAuto`` (sun automation off), ``"Auto"`` →
    ``auto`` (sun automation on), ``"Shade"`` → ``shade`` (move the slats
    to the Loxone-computed shade position, one-shot action). Unknown
    options yield ``None`` so the caller can warn instead of sending.
    """
    return {"Off": "NoAuto", "Auto": "auto", "Shade": "shade"}.get(option)


def jalousie_auto_option_from_state(value) -> str:
    """
    Map the Jalousie ``autoState`` stream value to the displayed option.

    Loxone reports the sun-automation state as a 0/1 (or true/false) value
    on ``autoState``; non-zero means the sun automation is active. The
    one-shot ``shade`` action has no distinct reported state — after a
    ``Shade`` selection the entity keeps pinning that option until the
    next ``autoState`` stream value arrives (**VERIFY** on a live
    Miniserver which state a shade command leaves ``autoState`` in).
    """
    return "Auto" if value else "Off"


def _dedupe_label(label: str, used: set[str]) -> str:
    """
    Return a label that is unique within ``used`` and register it.

    Home Assistant requires the options of a select entity to be unique. Loxone
    does not enforce unique output names, so duplicates are disambiguated with a
    numeric suffix (e.g. ``Stand 1 (2)``).
    """
    candidate = label
    index = 2
    while candidate in used:
        candidate = f"{label} ({index})"
        index += 1
    used.add(candidate)
    return candidate


def build_option_maps(
    details: dict,
) -> tuple[list[str], dict[int, str], dict[str, int], int | None]:
    """
    Build the option list and lookup maps for a Loxone Radio block.

    Returns a tuple of:
      * the ordered list of option labels,
      * a mapping of Loxone output number to option label,
      * a mapping of option label to Loxone output number,
      * the output number of the "all off" entry, or None if not present.

    The "all off" entry (output number ``0``) is only included when the Radio
    block exposes the ``allOff`` detail.
    """
    outputs = details.get("outputs", {}) or {}
    used: set[str] = set()
    options: list[str] = []
    num_to_opt: dict[int, str] = {}
    opt_to_num: dict[str, int] = {}
    all_off_num = None
    if "allOff" in details:
        label = details.get("allOff") or ALL_OFF_DEFAULT_LABEL
        label = _dedupe_label(label, used)
        options.append(label)
        num_to_opt[ALL_OFF_VALUE] = label
        opt_to_num[label] = ALL_OFF_VALUE
        all_off_num = ALL_OFF_VALUE

    for key in sorted(outputs.keys(), key=lambda k: int(k)):
        number = int(key)
        label = _dedupe_label(str(outputs[key]), used)
        options.append(label)
        num_to_opt[number] = label
        opt_to_num[label] = number

    return options, num_to_opt, opt_to_num, all_off_num


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    entities = []

    for select_entity in iter_controls(hass, config_entry, "Radio"):
        try:
            # PS-19: a Radio without any outputs yields options == [],
            # which Home Assistant rejects -- skip it with a log instead of
            # failing the whole platform.
            options, _, _, _ = build_option_maps(select_entity.get("details") or {})
            if not options:
                _LOGGER.warning(
                    "Skipping Radio control %s: it has no outputs, a select needs at least one option",
                    select_entity.get("name", select_entity.get("uuidAction", "?")),
                )
                continue
            new_select = LoxoneSelect(**select_entity)
            entities.append(new_select)
        except Exception:
            _LOGGER.exception("Skipping Radio control %s", select_entity.get("name", "?"))

    # Jalousie sun automation (off / auto / shade): the cover entity carries
    # the *services*; this select gives automations and the Mushroom-style
    # UI a first-class switch to operate it (WP-6.8). Only Jalousies that
    # report an ``autoState`` stream get one — the option display depends
    # on that stream.
    for jalousie in iter_controls(hass, config_entry, "Jalousie"):
        if not (jalousie.get("states") or {}).get("autoState"):
            continue
        jalousie.update({"hass": hass, "config_entry": config_entry})
        entities.append(LoxoneJalousieAuto(**jalousie))

    # WP-6.10 / PS-27: the LightsceneRGB scene select.  Only created when
    # the control's `sceneList` is populated: on the live Miniserver that
    # confirmed this control type (firmware 17.2.8.28) the sceneList is
    # empty, and an empty sceneList must degrade to *no* select (PS-19,
    # same guard shape as the empty-Radio skip above) rather than a
    # broken zero-option entity.
    for scene_light in iter_controls(hass, config_entry, "LightsceneRGB"):
        try:
            options, _raw = lightscene_scene_lookup(scene_light.get("details"))
            if not options:
                _LOGGER.debug(
                    "LightsceneRGB control %s has an empty sceneList; no scene select is created",
                    scene_light.get("name") or scene_light.get("uuidAction") or "?",
                )
                continue
            scene_light.update({"config_entry": config_entry})
            entities.append(LoxoneLightsceneRGBScene(**scene_light))
        except Exception:
            _LOGGER.exception("Skipping LightsceneRGB scene select %s", scene_light.get("name", "?"))

    async_add_entities(entities)


# --------------------------------------------------------------------------- #
# WP-6.10 (PS-27): LightsceneRGB scene select
# --------------------------------------------------------------------------- #


def lightscene_scene_lookup(details) -> tuple[list[str], list[str]]:
    """
    The scene names of one LightsceneRGB `details.sceneList` as
    ``(options, raw_labels)`` — pre-dedupe labels kept in the same order.

    The real entry shape is not documented in anything we hold (VERIFY —
    live check #22); both plausible shapes are accepted:

    * a dict `{scene number: scene name}` (the Radio `outputs` shape) —
      the key order is sorted, the name values are the labels;
    * a bare list of names.

    The PS-27 degrade rule: a missing, non-dict/non-list or otherwise
    *empty* `sceneList` (the state of the block on the live Miniserver)
    yields ``([], [])`` and the setup skips the select — a broken
    zero-option entity is never created (PS-19).
    """
    if not isinstance(details, dict):
        return [], []
    scene_list = details.get("sceneList")
    if isinstance(scene_list, dict):
        raw = [
            str(value).strip() if isinstance(value, str) and value.strip() else str(key)
            for key, value in sorted(scene_list.items(), key=lambda item: str(item[0]))
        ]
    elif isinstance(scene_list, (list, tuple)):
        raw = [str(item).strip() for item in scene_list if isinstance(item, str) and item.strip()]
    else:
        return [], []
    used: set[str] = set()
    options = [_dedupe_label(label, used) for label in raw]
    return options, raw


def lightscene_active_scene_option(value, options: list[str], raw_labels: list[str]) -> str | None:
    """
    Map the LightsceneRGB `activeScene` stream to a select option.

    Intended semantics (VERIFY — live check #22): `activeScene` reports
    either the scene *name* (matching a `sceneList` label) or the scene's
    *index* within the `sceneList` (numeric value or all-digit string).
    Unknown values — renamed scenes, out-of-range indexes, non-scalar
    garbage — return ``None`` so the select keeps its last known option
    instead of crashing or bouncing.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        index = int(value)
        return options[index] if 0 <= index < len(options) else None
    if isinstance(value, str):
        text = value.strip()
        if text in raw_labels:
            return options[raw_labels.index(text)]
        if text.lstrip("-").isdigit() and 0 <= int(text) < len(options):
            return options[int(text)]
    return None


def lightscene_scene_command(option: str) -> str:
    """
    The outbound command that activates one scene (VERIFY —
    live check #22).

    No wire capture documents the LightsceneRGB write path; this assumes
    the `scene/<name>` shape (the way scene-selecting controls elsewhere
    address their scenes by name).
    """
    return f"scene/{option}"


class LoxoneLightsceneRGBScene(LoxoneEntity, SelectEntity):
    """
    LightsceneRGB (WP-6.10, PS-27): a scene select over the control's
    `sceneList`.

    Only ever constructed for a *populated* `sceneList` (the setup
    guard); the `activeScene` stream reflects the currently active scene.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = "LightsceneRGB"
        # WP-5.1: short sub-entity name; the device is the scene light's
        # own control device (CORE-26).
        self._attr_name = "Scene"
        # The light entity takes the bare `uuidAction`; the select needs
        # its own registry identity on the same device.
        self._attr_unique_id = f"{self.uuidAction}/scene"
        self._attr_options, self._raw_labels = lightscene_scene_lookup(kwargs.get("details"))
        states = kwargs.get("states")
        states = states if isinstance(states, dict) else {}
        self._active_scene_uuid = (
            states.get("activeScene")
            if isinstance(states.get("activeScene"), str) and states.get("activeScene")
            else None
        )
        self._attr_current_option: str | None = None
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.uuidAction, self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the activeScene stream.
        return frozenset({self._active_scene_uuid}) if self._active_scene_uuid else frozenset()

    @callback
    def event_handler(self, e: dict) -> None:
        if self._active_scene_uuid and self._active_scene_uuid in e:
            option = lightscene_active_scene_option(e[self._active_scene_uuid], self._attr_options, self._raw_labels)
            if option is not None:
                self._attr_current_option = option
                self.async_write_ha_state()

    async def select_option(self, option: str) -> None:
        if option not in self._attr_options:
            raise HomeAssistantError(f"Option {option} is not a scene of {self._lox_name}")
        self._send(lightscene_scene_command(option))
        self._attr_current_option = option
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }


class LoxoneSelect(LoxoneEntity, SelectEntity):
    """Representation of a Loxone Radio block as a select entity."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the Loxone select."""
        self._icon = None
        self._locked = None

        (self._attr_options, self._num_to_option, self._option_to_num, self._all_off_num) = build_option_maps(
            self.details
        )
        self._attr_current_option = None

        self.type = "Radio"
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the activeOutput and jLocked streams.
        return frozenset(
            uuid
            for uuid in (self.states.get("activeOutput"), self.states.get("jLocked"))
            if isinstance(uuid, str) and uuid
        )

    @callback
    def event_handler(self, e):
        data = e
        request_update = False

        state_uuid = self.states.get("activeOutput")
        if state_uuid and state_uuid in data:
            value = data[state_uuid]
            try:
                number = int(float(value))
            except TypeError, ValueError:
                number = None
            self._attr_current_option = self._num_to_option.get(number)
            request_update = True

        # PS-19: the lock arrives as a separate stream; fold both updates
        # into a single state write instead of two per event.
        lock_uuid = self.states.get("jLocked")
        if lock_uuid and lock_uuid in data:
            self._locked = bool(data[lock_uuid])
            request_update = True

        if request_update:
            self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # PS-19: enforce the Loxone lock; HA renders the raised error to the
        # caller of the select service.
        if self._locked:
            raise HomeAssistantError(
                f"Loxone Radio block {self._lox_name or self.uuidAction} is locked and ignores selections"
            )
        number = self._option_to_num.get(option)
        if number is None:
            _LOGGER.warning("Unknown option '%s' for Loxone select %s", option, self._lox_name)
            return
        if number == self._all_off_num:
            self._send("reset")
        else:
            self._send(str(number))
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "state_uuid": self.states.get("activeOutput"),
            "device_type": self.type,
            "platform": "loxone",
            "locked": self._locked,
        }


class LoxoneJalousieAuto(LoxoneEntity, SelectEntity):
    """
    The sun-automation mode of a Loxone Jalousie as a select entity.

    Jalousies with ``sunAutoma``/``isAutomatic`` accept the CoverControl
    commands ``NoAuto`` (automation off), ``auto`` (automation on) and
    ``shade`` (one-shot move to the computed shade position). The cover
    entity exposes these as services; this select gives automations and
    cards a stateful control (WP-6.8). It attaches to the *same* device
    as the Jalousie cover.
    """

    _attr_options = ["Off", "Auto", "Shade"]
    _attr_name = "Sun auto"
    _attr_icon = "mdi:weather-sunny"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]
        # A distinct unique id keeps its own registry slot next to the
        # cover entity, which owns the bare control uuid.
        self._attr_unique_id = f"{kwargs.get('uuidAction')}/sun-auto"
        self._auto_state_uuid = (self.states or {}).get("autoState")
        self._auto_state = None
        self._attr_current_option = "Off"
        self.type = "Jalousie"
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), kwargs.get("uuidAction"), self._lox_name, self.type, self.room
        )

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the single autoState stream the handler consumes.
        return frozenset({self._auto_state_uuid}) if isinstance(self._auto_state_uuid, str) else frozenset()

    @callback
    def event_handler(self, e):
        data = e
        if self._auto_state_uuid is not None and self._auto_state_uuid in data:
            self._auto_state = data[self._auto_state_uuid]
            self._attr_current_option = jalousie_auto_option_from_state(self._auto_state)
            self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Send the matching sun-automation command."""
        command = jalousie_auto_command(option)
        if command is None:
            _LOGGER.warning("Unknown Jalousie sun-auto option %r for %s", option, self._lox_name)
            return
        self._send(command)
        if option == "Shade":
            # `shade` is one-shot and leaves no distinct autoState of its
            # own: keep the user's intent visible until the next stream
            # value arrives.
            self._attr_current_option = "Shade"
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
            "auto_state": self._auto_state,
        }
