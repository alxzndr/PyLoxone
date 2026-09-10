"""Support for Loxone Audio zone media player."""

from __future__ import annotations

import json
import logging

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import DEFAULT_AUDIO_ZONE_V2_PLAY_STATE
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
DEFAULT_FORCE_UPDATE = False

# This is the "optimistic" view of supported features and will be returned until the
# actual set of supported feature have been determined (will always be all or a subset
# of these).
SUPPORT_LOXONE_AUDIO_ZONE = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.STOP
)


def _state_uuid(states: dict, name: str) -> str | None:
    """Un-guarded ``states[name]`` indexing crashes the media player platform when a
    structure file omits an attribute (PC-16); return ``None`` instead."""
    return states.get(name)


def play_state_to_media_player_state(play_state: int) -> MediaPlayerState:
    """Map a Loxone AudioZoneV2 ``playState`` value to the matching HA state.

    Unknown values fall back to ``IDLE`` (a non-playing state): the entity
    must never be left without a state just because the server sent a new
    value we do not know about.
    """
    match play_state:
        case 0:
            return MediaPlayerState.IDLE
        case 1:
            return MediaPlayerState.PAUSED
        case 2:
            return MediaPlayerState.PLAYING
        case -1:
            return MediaPlayerState.OFF
        case _:
            _LOGGER.debug("Unknown playState %r; defaulting to %s", play_state, MediaPlayerState.IDLE)
            return MediaPlayerState.IDLE


def audio_zone_stop_value() -> str:
    """Command value behind the ``STOP`` feature (PC-43).

    AudioZoneV2 has no ``stop`` sub-command; pausing makes the zone silent,
    which is the closest stop semantic the device offers.
    VERIFY — confirm a dedicated stop command (if any) exists on a live
    Miniserver.
    """
    return "pause"


def audio_zone_power_command(on: bool) -> str:
    """Command value behind ``TURN_ON`` / ``TURN_OFF`` (WP-6.7, PC-43).

    VERIFY — ``on`` / ``off`` mirrors the audio zone's power sub-command
    naming as observed in other LoxApp integrations; confirm against a
    live Miniserver before relying on zone power control.
    """
    return "on" if on else "off"


def audio_zone_mute_command(mute: bool) -> str:
    """Command value behind ``VOLUME_MUTE`` (WP-6.7, PC-43).

    VERIFY — the wire values ``mute`` / ``unmute`` are the intended
    semantics; confirm a live Miniserver accepts them for an AudioZoneV2.
    """
    return "mute" if mute else "unmute"


def audio_zone_source_command(source: str) -> str:
    """Command behind ``select_source`` (WP-6.7, PC-43).

    VERIFY — the ``source/<name>`` sub-command shape is assumed
    (``noun/<argument>`` like ``volume/<n>`` above); confirm against a
    live Miniserver.
    """
    return f"source/{source}"


def audio_zone_two_state(value) -> bool | None:
    """Coerce a ``0`` / ``1`` state stream value (``active``, ``mute``) to
    a boolean, or ``None`` when nothing usable is in the payload.

    Structured values are recognised first (so the string ``"0"`` is
    ``False`` and not a truthy string); everything else goes through
    int()/bool() so a quirky server encoding degrades gracefully.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("0", "false", "off"):
        return False
    if text in ("1", "true", "on"):
        return True
    try:
        return int(text) != 0
    except ValueError:
        return bool(text)


def audio_zone_source_options(details) -> list[str]:
    """The selectable source names of one AudioZoneV2 (WP-6.7).

    Read from the control's ``details.sources`` (PC-16 discipline: nothing
    is indexed without a guard).  Accepts a list of names or a dict of
    ``{"id": "name"}`` pairs (dict order is kept); anything unusable
    yields no options rather than an exception.
    """
    return audio_zone_names_in_details(details, "sources")


def audio_zone_names_in_details(details, key: str) -> list[str]:
    """A names list under ``details[key]`` (``sources``, ``favourites``).

    Either a plain list of names or a ``{"id": "name"}`` dict (dict
    order is kept); anything unusable yields ``[]`` rather than an
    exception (PC-16 discipline).
    """
    if not isinstance(details, dict):
        return []
    raw = details.get(key)
    if isinstance(raw, list):
        return [str(name) for name in raw if isinstance(name, (str, int, float)) and str(name)]
    if isinstance(raw, dict):
        return [str(name) for name in raw.values() if isinstance(name, (str, int, float)) and str(name)]
    return []


def audio_zone_stream_names_list(raw) -> list[str] | None:
    """Parse a pushed names list from the AudioZoneV2 state streams
    (``favouriteList``, ``sourceList``) the same way ``Tracker`` parses
    its ``entries`` payload: a JSON list whose scalar entries become
    strings (``["A", 2] -> ["A", "2"]``).

    Returns ``None`` when the payload is not a usable list, so the
    previous list is kept.  VERIFY — the wire shape of both streams is
    assumed; confirm a live Miniserver pushes JSON name lists.
    """
    try:
        if isinstance(raw, str):
            raw = json.loads(raw)
    except ValueError, TypeError:
        return None
    if not isinstance(raw, list):
        return None
    return [str(item) for item in raw if isinstance(item, (str, int, float, bool)) and str(item)]


def audio_zone_metadata(raw) -> dict | None:
    """Parse the ``metadata`` stream of one AudioZoneV2 (WP-6.7).

    Intended shape: a JSON object with any of the keys ``title``,
    ``artist``, ``album``.  Only recognised non-empty string entries
    are kept (unknown keys are dropped, so a growing payload shape
    degrades gracefully); anything that is not a usable object yields
    ``None`` so the previous metadata is kept.

    VERIFY — the wire shape (one JSON metadata stream with these keys)
    is assumed; confirm against a live Miniserver.
    """
    try:
        if isinstance(raw, str):
            raw = json.loads(raw)
    except ValueError, TypeError:
        return None
    if not isinstance(raw, dict):
        return None
    known = {}
    for key in ("title", "artist", "album"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            known[key] = value.strip()
    return known or None


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Audio zones."""


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Load Loxone Audio zones based on a config entry."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []

    for audioZone in get_all(loxconfig, "AudioZoneV2"):
        audioZone = add_room_and_cat_to_value_values(loxconfig, audioZone)
        audioZone.update(
            {
                "hass": hass,
                "config_entry": config_entry,
            }
        )
        entities.append(LoxoneAudioZoneV2(**audioZone))

    async_add_entities(entities)


class LoxoneAudioZoneV2(LoxoneEntity, MediaPlayerEntity):
    """Representation of a AudioZoneV2 Loxone device."""

    def __init__(self, **kwargs):
        _LOGGER.debug("Input AudioZoneV2 uuidAction=%s", kwargs.get("uuidAction"))
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]

        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER
        self._state = play_state_to_media_player_state(DEFAULT_AUDIO_ZONE_V2_PLAY_STATE)
        self._volume = 0
        # WP-6.7: power / mute / sources / favourites / metadata.
        # ``None`` values mean "the server has not said yet" and are
        # readable as the previous/default value.
        self._powered: bool | None = None
        self._volume_muted: bool | None = None
        self._current_source: str | None = None
        details = kwargs.get("details")
        self._source_options = audio_zone_source_options(details)
        self._favourites = audio_zone_names_in_details(details, "favourites")
        self._media_title: str | None = None
        self._media_artist: str | None = None
        self._media_album_name: str | None = None

        self.type = "AudioZoneV2"
        self._attr_device_info = get_or_create_device(self.unique_id, self._lox_name, self.type, self.room)

    def _state_uuids(self) -> frozenset[str]:
        # CORE-27: the state streams this zone reacts to (WP-6.7 broadened
        # the set from volume/playState with the power/zone-ability streams).
        keys = (
            "volume",
            "playState",
            "active",
            "mute",
            "source",
            "sourceList",
            "favouriteList",
            "metadata",
        )
        uuids = [_state_uuid(self.states, key) for key in keys]
        return frozenset(uuid for uuid in uuids if isinstance(uuid, str) and uuid)

    @callback
    def event_handler(self, event):
        should_update = False

        if (u := _state_uuid(self.states, "volume")) and u in event:
            self._volume = float(event[u]) / 100
            should_update = True

        if (u := _state_uuid(self.states, "playState")) and u in event:
            self._state = play_state_to_media_player_state(event[u])
            should_update = True

        if (u := _state_uuid(self.states, "active")) and u in event:
            # Power state of the zone: when the server reports the zone
            # switched off, nothing can be playing regardless of
            # playState.
            self._powered = audio_zone_two_state(event[u])
            should_update = True

        if (u := _state_uuid(self.states, "mute")) and u in event:
            self._volume_muted = audio_zone_two_state(event[u])
            should_update = True

        if (u := _state_uuid(self.states, "source")) and u in event:
            self._current_source = str(event[u]).strip() or None
            # The active source doubles as the playback title when the
            # server does not push explicit metadata.
            if self._media_title is None:
                self._media_title = self._current_source
            should_update = True

        if (u := _state_uuid(self.states, "sourceList")) and u in event:
            if (new_list := audio_zone_stream_names_list(event[u])) is not None:
                self._source_options = new_list
                should_update = True

        if (u := _state_uuid(self.states, "favouriteList")) and u in event:
            if (new_favourites := audio_zone_stream_names_list(event[u])) is not None:
                self._favourites = new_favourites
                should_update = True

        if (u := _state_uuid(self.states, "metadata")) and u in event:
            if (meta := audio_zone_metadata(event[u])) is not None:
                self._media_title = meta.get("title", self._media_title)
                self._media_artist = meta.get("artist", self._media_artist)
                self._media_album_name = meta.get("album", self._media_album_name)
                should_update = True

        if should_update:
            self.async_write_ha_state()

    # properties
    @property
    def state(self) -> MediaPlayerState:
        """Return the playback state (powered-off zones read as off)."""
        if self._powered is False:
            return MediaPlayerState.OFF
        return self._state

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self) -> bool:
        """Whether the zone is muted (unknown reads as not-muted)."""
        return self._volume_muted is True

    @property
    def source(self) -> str | None:
        """The currently active source name, if the server reported one."""
        return self._current_source

    @property
    def source_list(self) -> list[str] | None:
        """The selectable source names (``select_source`` options)."""
        return self._source_options or None

    @property
    def media_title(self) -> str | None:
        """Title of the currently-playing media, or the active source."""
        return self._media_title

    @property
    def media_artist(self) -> str | None:
        """Artist of the currently-playing media."""
        return self._media_artist

    @property
    def media_album_name(self) -> str | None:
        """Album of the currently-playing media."""
        return self._media_album_name

    @property
    def extra_state_attributes(self):
        """Zone-specific state attributes (current source, favourite list)
        on top of the common Loxone attributes (PC-16: no unguarded
        indexing)."""
        return {
            **self._attr_extra_state_attributes,
            "source": self._current_source,
            "favourites": list(self._favourites),
        }

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        return SUPPORT_LOXONE_AUDIO_ZONE

    # commands
    async def async_media_play(self) -> None:
        """Send play command to device."""
        self._send("play")
        self.async_schedule_update_ha_state()

    async def async_media_pause(self) -> None:
        """Send pause command to device."""
        self._send("pause")
        self.async_schedule_update_ha_state()

    async def async_media_stop(self) -> None:
        """Send stop command to device (PC-43)."""
        self._send(audio_zone_stop_value())
        self.async_schedule_update_ha_state()

    async def async_media_next_track(self) -> None:
        """Send next track command to device."""
        self._send("next")
        self.async_schedule_update_ha_state()

    async def async_media_previous_track(self) -> None:
        """Send previous track command to device."""
        self._send("prev")
        self.async_schedule_update_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        """Send new volume_level to device."""
        volume_int = int(volume * 100)
        self._send(f"volume/{volume_int}")
        self.async_schedule_update_ha_state()

    async def async_volume_up(self) -> None:
        """Send volume UP to device."""
        self._send("volUp")
        self.async_schedule_update_ha_state()

    async def async_volume_down(self) -> None:
        """Send volume DOWN to device."""
        self._send("volDown")
        self.async_schedule_update_ha_state()

    async def async_turn_on(self) -> None:
        """Power the zone on (WP-6.7)."""
        self._send(audio_zone_power_command(True))
        self.async_schedule_update_ha_state()

    async def async_turn_off(self) -> None:
        """Power the zone off (WP-6.7)."""
        self._send(audio_zone_power_command(False))
        self.async_schedule_update_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute / un-mute the zone (WP-6.7)."""
        self._send(audio_zone_mute_command(mute))
        self.async_schedule_update_ha_state()

    async def async_select_source(self, source: str) -> None:
        """Select a zone source by name (WP-6.7).

        Selecting an unknown source refuses to send a command: with
        elective sources, a typo should be a no-op, not a guess.
        """
        if self._source_options and source not in self._source_options:
            _LOGGER.debug(
                "AudioZoneV2 %s: refusing unknown source %r (known %s)",
                self.unique_id,
                source,
                self._source_options,
            )
            return
        self._send(audio_zone_source_command(source))
        self.async_schedule_update_ha_state()
