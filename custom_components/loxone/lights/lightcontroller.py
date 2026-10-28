import math
from collections import OrderedDict
from functools import cached_property

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_EFFECT, ColorMode, LightEntity, LightEntityFeature
from homeassistant.core import callback

from .. import LoxoneEntity
from ..helpers import (
    device_info_for,
    hass_to_lox,
    hass_to_lox_range,
    json_decoder,
    literal_decoder,
    lox_to_hass,
    lox_to_hass_range,
)

# Mood id used by `turn_off` (PC-33: no inline magic numbers).  Spec:
# Structure File v16/v17, LightControllerV2 Commands: "changeTo/{moodId}
# ... The moodID 0 is always 'off'".
OFF_MOOD_ID = "0"

#: Mood ids that mean "off" (WP-7.1, upstream `466ea0e`).
#:
#: * ``0``   — documented: Structure File v16/v17, LightControllerV2
#:   Commands: "The moodID 0 is always 'off'".  ``turn_off`` sends it.
#: * ``778`` — the static off mood id, observed on upstream's installation
#:   as "Aus" (upstream ``466ea0e``) *and* on the maintainer's installation
#:   (WP-7.1b), where the scene entities generated from the mood lists are
#:   named "uit" / "off".  Neither Structure File version mentions 778, and
#:   the runtime mood list is not part of the structure file, so another
#:   Miniserver may use another id for its off mood.
OFF_MOOD_IDS = frozenset({0, 778})

#: The static "all on" mood id, preferred by a plain ``turn_on``
#: (WP-7.1b, LIVE-MINISERVER-CHECKS.md #29).  Observed on the maintainer's
#: installation: the scene entities generated from every controller's mood
#: list carry mood id 777 in their unique ids and are named "alles aan" /
#: "bright" / "on".  Like 778, it appears in no Structure File version, so
#: another Miniserver may name its full-light mood another id (or have no
#: such mood at all) — the turn-on helper then falls back to the first
#: non-off mood.
ALL_ON_MOOD_ID = 777

#: The one state per sub-control type that says how bright the circuit is
#: (Structure File v16/v17, LightControllerV2 Subcontrols: the circuits are
#: ``Dimmer`` / ``Switch`` / ``ColorPickerV2``).
CIRCUIT_LEVEL_STATES = {
    "Dimmer": "position",
    "EIBDimmer": "position",
    "Switch": "active",
    "ColorPickerV2": "color",
}


def lcv2_mood_id(value) -> int | None:
    """
    Normalise a mood id from the JSON states to an ``int``, or ``None``
    when it is not numeric.

    ``activeMoods`` and ``moodList`` carry the ids as numbers; numeric
    strings are accepted as well because Loxone streams do both shapes.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def lcv2_mood_is_off(mood_id) -> bool:
    """True when this mood id (int or numeric string) is an off mood."""
    return lcv2_mood_id(mood_id) in OFF_MOOD_IDS


def lcv2_is_on(active_moods, master_position, circuit_levels) -> bool | None:
    """
    Derive the LightControllerV2 on/off state (WP-7.1, upstream ``e43301f``
    redesigned — the gaps doc "do not copy" items 1 and 2).

    * ``active_moods is None`` — the state has not been received yet:
      ``None`` (unknown).  Nothing else may guess here.
    * any mood id outside :data:`OFF_MOOD_IDS` — on.
    * only off ids — off.
    * ``activeMoods == []`` — upstream observed on its installation that
      moving the master clears the mood list ("Stimmung Manuell") while
      the circuits stay lit, so an empty list is *manual mode*, decided by
      what is known: the master position when it is known, otherwise any
      known circuit level (dimmer ``position``, switch ``active``, picker
      brightness).  Only when nothing at all is known does this stay
      unknown.  Upstream's ``self._master_position_loxone or 0`` read this
      as off on every controller *without* a master dimmer — 13 of the
      maintainer's 20 — while their circuits were lit; a missing master is
      ``None`` here, never ``0``.

    The empty-list meaning and the mood ids behind it are unverified on
    the maintainer's Miniserver: LIVE-MINISERVER-CHECKS.md #29.
    """
    if active_moods is None:
        return None
    if any(not lcv2_mood_is_off(mood_id) for mood_id in active_moods):
        return True
    if len(active_moods) > 0:
        return False  # only off ids
    # Manual mode: master first, then the circuits.
    if master_position is not None:
        return master_position > 0
    known = [level for level in circuit_levels if level is not None]
    if not known:
        return None
    return any(level > 0 for level in known)


def lcv2_plain_turn_on_command(mood_list) -> str:
    """
    The command for a plain ``turn_on`` — no brightness, no effect (WP-7.1,
    upstream ``e43301f`` / ``466ea0e``; 777 preference added by WP-7.1b).

    The Structure File (v16 and v17, read 2026-10-28) lists no "on"
    command for LightControllerV2: moods are the only on/off vocabulary.
    So:

    1. play mood :data:`ALL_ON_MOOD_ID` (777) when ``moodList`` holds it —
       the static "all on" mood on the maintainer's installation ("alles
       aan" / "bright" / "on"); taking it wherever it sits in the list
       avoids surfacing a custom mood ("cinema", "nacht") on a plain
       turn-on just because the list happens to start with it,
    2. otherwise the first mood of ``moodList`` that is not an off mood,
    3. when the list is missing, empty or holds only off moods, ``plus``
       (spec: "Changes to the next mood").

    The old ``changeTo/99`` is documented nowhere and would be
    acknowledged with 200 even if the Miniserver did not know it.  Which
    mood actually comes up is unverified: LIVE-MINISERVER-CHECKS.md #29.
    """
    first_non_off = None
    for mood in mood_list or ():
        if not isinstance(mood, dict):
            continue
        mood_id = mood.get("id")
        if mood_id is None or lcv2_mood_is_off(mood_id):
            continue
        if lcv2_mood_id(mood_id) == ALL_ON_MOOD_ID:
            return f"changeTo/{mood_id}"
        if first_non_off is None:
            first_non_off = mood_id
    if first_non_off is not None:
        return f"changeTo/{first_non_off}"
    return "plus"


def _numeric_level(raw) -> float | None:
    """A plain numeric level (number or numeric string) or ``None``."""
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, str):
        try:
            raw = float(raw.strip())
        except ValueError:
            return None
    if not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return None
    return float(raw)


def lcv2_circuit_level(circuit_type, raw) -> float | None:
    """
    Normalise one circuit's level state to a known float level (0 is dark),
    or ``None`` when this feed says nothing.

    Dimmer ``position`` and switch ``active`` (0/1, per the Structure
    File's state list; anything at or above 1 counts as on) are numbers.
    The ColorPickerV2 ``color`` string is parsed exactly like the picker
    entities do in ``lights/colorpickers.py`` — strip the ``hsv``/``temp``
    word, ``literal_decoder`` the tuple — and contributes its brightness
    component (hsv value / temp level), no second parser (WP-7.1).
    """
    if circuit_type == "ColorPickerV2":
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if text.startswith("hsv"):
            parsed = literal_decoder(text[len("hsv") :])
            if isinstance(parsed, (list, tuple)) and len(parsed) == 3:
                return _numeric_level(parsed[2])
        elif text.startswith("temp"):
            parsed = literal_decoder(text[len("temp") :])
            if isinstance(parsed, (list, tuple)) and len(parsed) >= 1:
                return _numeric_level(parsed[0])
        return None
    level = _numeric_level(raw)
    if level is None:
        return None
    if circuit_type == "Switch":
        return 1.0 if level >= 1 else 0.0
    return level


class LoxoneLightControllerV2(LoxoneEntity, LightEntity):
    """Representation of a Light Controller V2."""

    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_is_on: bool | None = None
    _attr_available = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_is_on = None
        self._active_moods = []
        # WP-7.1: `activeMoods` decides is_on, and before the first value
        # the state is unknown — an empty list alone must not read as
        # "manual mode".
        self._active_moods_received = False
        self._moodlist = []
        self._additional_moodlist = []
        self._attr_brightness = None
        self._master_value = None
        self._master_value_uuid = None
        self._master_position_uuid = None
        self._master_min_uuid = None
        self._master_max_uuid = None
        self._master_min = None
        self._master_max = None
        # WP-7.1: the master dimmer's raw position (None = never seen);
        # decides is_on in manual mode.
        self._master_position = None
        # WP-7.1: per-circuit level states (circuit key -> last known
        # level), subscribed whether or not
        # `generate_lightcontroller_subcontrols` creates entities for them.
        self._circuit_level_uuids: dict[str, tuple[str, str]] = {}
        self._circuit_levels: dict[str, float] = {}
        self._async_add_devices = kwargs["async_add_devices"]

        self._sub_controls = OrderedDict({})
        for uuid, control in kwargs.get("subControls", {}).items():
            self._sub_controls[uuid] = {
                "name": control["name"],
                "type": control["type"],
            }
            if "masterValue" in uuid and control.get("type") in ["Dimmer", "EIBDimmer"]:
                self._master_value = control
            if "masterValue" in uuid or "masterColor" in uuid:
                # The masters are not circuits; the master brightness is
                # followed through `_master_position_uuid` instead.
                continue
            level_state = CIRCUIT_LEVEL_STATES.get(control.get("type"))
            if level_state:
                level_uuid = (control.get("states") or {}).get(level_state)
                if isinstance(level_uuid, str) and level_uuid:
                    self._circuit_level_uuids[level_uuid] = (uuid, control.get("type"))

        if self._master_value:
            self._master_value_uuid = self._master_value.get("uuidAction")
            self._master_position_uuid = self._master_value.get("states", {}).get("position")
            self._master_min_uuid = self._master_value.get("states", {}).get("min")
            self._master_max_uuid = self._master_value.get("states", {}).get("max")
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

        # WP-7.1 (upstream 64ad11d, the LightControllerV2 part; 64ad11d's
        # own comment: these states are optional per structure variant).
        # Read once, via `.get()`: a controller without e.g. `moodList`
        # used to raise a KeyError here and in `_state_uuids`, which runs
        # in `async_added_to_hass` and took the whole light platform down.
        states = kwargs.get("states") if isinstance(kwargs.get("states"), dict) else {}
        self._active_moods_uuid = states.get("activeMoods")
        self._mood_list_state_uuid = states.get("moodList")
        self._additional_moods_uuid = states.get("additionalMoods")

        self.type = "LightControllerV2"
        # WP-5.1: primary entity — the device is named after the control
        # itself and provides the entity's display name (CORE-26).
        self._attr_device_info = device_info_for(
            kwargs.get("config_entry"), self.unique_id, self._lox_name, self.type, self.room
        )

    @property
    def mood_list_uuid(self):
        # Optional state (WP-7.1): ``None`` when this controller does not
        # publish a mood list.
        return self._mood_list_state_uuid

    def get_moodname_by_id(self, _id):
        for mood in self._moodlist:
            if "id" in mood and "name" in mood:
                if mood["id"] == _id:
                    return mood["name"]
        return _id

    def get_id_by_moodname(self, _name):
        for mood in self._moodlist:
            if "id" in mood and "name" in mood:
                if mood["name"] == _name:
                    return mood["id"]
        return _name

    @property
    def effect_list(self):
        """Return the moods of light controller."""
        moods = []
        for mood in self._moodlist:
            if "name" in mood:
                moods.append(mood["name"])
        return moods

    @property
    def effect(self):
        """Return the current effect."""
        if len(self._active_moods) == 1:
            return self.get_moodname_by_id(self._active_moods[0])
        return None

    async def got_effect(self, **kwargs):
        effects = kwargs["effect"].split(",")
        if len(effects) == 1:
            mood_id = self.get_id_by_moodname(kwargs["effect"])
            if mood_id != kwargs["effect"]:
                self._send(f"changeTo/{mood_id}")
            else:
                self._send("plus")
        else:
            effect_ids = []
            for _ in effects:
                mood_id = self.get_id_by_moodname(_.strip())
                if mood_id != _:
                    effect_ids.append(mood_id)

            self._send(f"changeTo/{effect_ids[0]}")

            for _ in effect_ids[1:]:
                self._send(f"addMood/{_}")

    @property
    def _master_min_max_known(self) -> bool:
        return self._master_min is not None and self._master_max is not None

    def _hass_to_master(self, hass_level) -> float:
        """
        HA brightness (1-255) → Loxone value, honouring the master
        dimmer's min/max (PC-17) and never rounding a non-zero request to
        0 (PC-18); shares its scaling with LoxoneDimmer.
        """
        if self._master_min_max_known:
            return hass_to_lox_range(hass_level, self._master_min, self._master_max)
        if not hass_level:
            return 0
        return max(1, round(hass_to_lox(hass_level)))

    async def async_turn_on(self, **kwargs) -> None:
        sent_something = False
        if ATTR_EFFECT in kwargs:
            await self.got_effect(**kwargs)
            sent_something = True
        if ATTR_BRIGHTNESS in kwargs and self._master_value_uuid:
            self._send(self._hass_to_master(kwargs[ATTR_BRIGHTNESS]), uuid=self._master_value_uuid)
            sent_something = True
        # PC-33: a bare `turn_on` while off turns on, and a non-empty
        # `turn_on` that touched no sub-control (e.g. brightness without a
        # master dimmer) still emits a command, so a service call never
        # silently does nothing.
        if not sent_something:
            # WP-7.1: an *unknown* state counts as "not on" — a plain
            # turn-on of a controller whose moods have not arrived yet
            # sends the command (this was the behaviour before, and the
            # user asked for light).  Only a controller that is verifiably
            # already on stays silent, so the call never steals a running
            # mood.  `lcv2_plain_turn_on_command` names the one command.
            if not kwargs and self._attr_is_on is not True:
                self._send(lcv2_plain_turn_on_command(self._moodlist))
            elif kwargs:
                self._send("on")
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **_kwargs) -> None:
        self._send(f"changeTo/{OFF_MOOD_ID}")
        self.async_schedule_update_ha_state()

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the activeMoods/moodList/additionalMoods streams the
        # handler reads (all optional, WP-7.1), the optional master dimmer
        # streams, and the level stream of every circuit (WP-7.1: the
        # controller follows its circuits even when
        # `generate_lightcontroller_subcontrols` keeps their entities
        # disabled).  A `None` uuid must not end up in the set — the
        # dispatcher would subscribe to the string "None".
        uuids = [
            self.uuidAction,
            self._active_moods_uuid,
            self._mood_list_state_uuid,
            self._additional_moods_uuid,
            self._master_min_uuid,
            self._master_max_uuid,
            self._master_position_uuid,
        ] + list(self._circuit_level_uuids)
        return frozenset(uuid for uuid in uuids if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, event):
        request_update = False

        if self.uuidAction in event:
            self._attr_state = event[self.uuidAction]
            request_update = True

        if self._master_min_uuid and self._master_min_uuid in event:
            try:
                self._master_min = float(event[self._master_min_uuid])
            except (TypeError, ValueError):
                pass
            request_update = True

        if self._master_max_uuid and self._master_max_uuid in event:
            try:
                self._master_max = float(event[self._master_max_uuid])
            except (TypeError, ValueError):
                pass
            request_update = True

        if self._master_position_uuid and self._master_position_uuid in event:
            try:
                position = float(event[self._master_position_uuid])
            except (TypeError, ValueError):
                position = None
            self._master_position = position
            if position is not None:
                if self._master_min_max_known:
                    self._attr_brightness = lox_to_hass_range(position, self._master_min, self._master_max)
                else:
                    self._attr_brightness = round(lox_to_hass(position))
                request_update = True

        if self._active_moods_uuid and self._active_moods_uuid in event:
            active_moods = json_decoder(event[self._active_moods_uuid])
            # Spec: a JSON array of mood ids.  Anything else (a bare
            # number, an object) says nothing and keeps the last list
            # instead of crashing the derivation below.
            if isinstance(active_moods, list):
                self._active_moods = active_moods
                self._active_moods_received = True
                request_update = True

        if self._mood_list_state_uuid and self._mood_list_state_uuid in event:
            moodlist = json_decoder(event[self._mood_list_state_uuid])
            if isinstance(moodlist, list):
                self._moodlist = moodlist
                request_update = True

        if self._additional_moods_uuid and self._additional_moods_uuid in event:
            additional = json_decoder(event[self._additional_moods_uuid])
            if additional is not None:
                self._additional_moodlist = additional
                request_update = True

        # WP-7.1: the circuits' level states (dimmer position, switch
        # active, picker colour).  They decide is_on in manual mode.
        for level_uuid, (circuit_key, circuit_type) in self._circuit_level_uuids.items():
            if level_uuid in event:
                level = lcv2_circuit_level(circuit_type, event[level_uuid])
                if level is not None:
                    self._circuit_levels[circuit_key] = level
                request_update = True

        if request_update:
            # WP-7.1: is_on is derived on every state change — moods
            # first, then (manual mode) the master, then the circuits.
            self._attr_is_on = lcv2_is_on(
                self._active_moods if self._active_moods_received else None,
                self._master_position,
                self._circuit_levels.values(),
            )
            if not self._attr_available:
                attr_is_on_is_not_unknown = self._attr_is_on is True or self._attr_is_on is False
                both_master_values_are_not_unknown = self._master_min_max_known
                any_circuit_level_known = bool(self._circuit_levels)
                if attr_is_on_is_not_unknown or both_master_values_are_not_unknown or any_circuit_level_known:
                    self._attr_available = True
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """
        Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            **self._attr_extra_state_attributes,
            "selected_scene": self.effect,
            "selected_scenes": [self.get_moodname_by_id(id) for id in self._active_moods],
            "device_type": self.type,
            "subcontrols": self._sub_controls,
        }

    @cached_property
    def icon(self):
        """Return the sensor icon."""
        return "mdi:hubspot"
