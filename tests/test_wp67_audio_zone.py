"""WP-6.7 regression tests: AudioZoneV2 sources / favourites / metadata /
mute / on-off (PC-43 feature gaps).

All expected values are hand-derived literals: command values like
``source/Radio`` or ``unmute`` are the *intended* semantics pinned behind
the named helpers in ``media_player.py`` (each carries a VERIFY note —
a live-Miniserver check is required before relying on them, see
``docs/review/LIVE-MINISERVER-CHECKS.md``); stream payloads mirror the
shapes the fixture (``tests/fixtures/LoxAPP3.json``) and the tests feed.
Nothing here calls the code under test to compute an expectation.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.media_player import (
    LoxoneAudioZoneV2,
    audio_zone_metadata,
    audio_zone_mute_command,
    audio_zone_power_command,
    audio_zone_source_command,
    audio_zone_source_options,
    audio_zone_stream_names_list,
    audio_zone_two_state,
)


def _audio_zone(hass, **overrides) -> LoxoneAudioZoneV2:
    kwargs = dict(
        name="Test Audio Zone",
        uuidAction="U68KEE6-AUD-0002-0000-000000000003",
        room="",
        type="AudioZoneV2",
        details={"sources": ["Radio", "Library"], "favourites": ["News"]},
        states={
            "active": "36333734-01b7-9336-ffff-d303162362d3000183",
            "source": "36333734-01b8-9336-ffff-d303162362d3000184",
            "sourceList": "36333734-01b9-9336-ffff-d303162362d3000185",
            "volume": "36333734-01ba-9336-ffff-d303162362d3000186",
            "mute": "36333734-01bb-9336-ffff-d303162362d3000187",
            "playState": "36333734-01be-9336-ffff-d303162362704c73",
            "metadata": "36333734-01bd-9336-ffff-d303162362726d74",
            "favouriteList": "36333734-01c3-9336-ffff-d303162362666c73",
        },
        isSecured=False,
    )
    kwargs["hass"] = hass
    kwargs.update(overrides)
    return LoxoneAudioZoneV2(**kwargs)


def _write_stub(e) -> None:
    e.async_schedule_update_ha_state = lambda *a, **k: None
    e.schedule_update_ha_state = lambda *a, **k: None
    e.async_write_ha_state = lambda *a, **k: None


# ===========================================================================
# Pure helpers (literals only — the wire values are the VERIFY contract)
# ===========================================================================
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, False),
        (1, True),
        (0.0, False),
        (3.5, True),
        (True, True),
        (False, False),
        ("0", False),
        ("1", True),
        ("off", False),
        ("on", True),
        (" false ", False),
        ("2", True),
        (None, None),
        ("junk", True),
    ],
)
def test_audio_zone_two_state_table(value, expected) -> None:
    assert audio_zone_two_state(value) is expected


@pytest.mark.parametrize(
    ("flag", "expected"),
    [(True, "on"), (False, "off")],
)
def test_audio_zone_power_command_table(flag, expected) -> None:
    assert audio_zone_power_command(flag) == expected


@pytest.mark.parametrize(
    ("flag", "expected"),
    [(True, "mute"), (False, "unmute")],
)
def test_audio_zone_mute_command_table(flag, expected) -> None:
    assert audio_zone_mute_command(flag) == expected


def test_audio_zone_source_command() -> None:
    assert audio_zone_source_command("Radio") == "source/Radio"
    assert audio_zone_source_command("My Radio 1") == "source/My Radio 1"


def test_audio_zone_source_options_shapes() -> None:
    # list of names is kept verbatim
    assert audio_zone_source_options({"sources": ["Radio", "AirPlay"]}) == ["Radio", "AirPlay"]
    # {id: name} uses the names, in dict order
    assert audio_zone_source_options({"sources": {"1": "Radio", "2": "AirPlay"}}) == [
        "Radio",
        "AirPlay",
    ]
    # numeric names are coerced to strings
    assert audio_zone_source_options({"sources": [1, "Radio"]}) == ["1", "Radio"]
    # nothing usable -> no options, never an exception (PC-16 discipline)
    assert audio_zone_source_options({}) == []
    assert audio_zone_source_options({"sources": "junk"}) == []
    assert audio_zone_source_options({"sources": None}) == []
    assert audio_zone_source_options(None) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["Radio", "AirPlay"], ["Radio", "AirPlay"]),
        ('["Radio", "AirPlay"]', ["Radio", "AirPlay"]),
        ('["A", 2]', ["A", "2"]),
        ([1, True], ["1", "True"]),
        ("[]", []),
        # non-list payloads keep the previous list
        ("not a list", None),
        ('{"a": 1}', None),
        (42, None),
        (None, None),
    ],
)
def test_audio_zone_stream_names_list_table(raw, expected) -> None:
    assert audio_zone_stream_names_list(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"title": "Blue Moon", "artist": "Cab", "album": "Drum"},
            {
                "title": "Blue Moon",
                "artist": "Cab",
                "album": "Drum",
            },
        ),
        # the values arrive as a JSON payload on the state stream
        (
            json.dumps({"title": "T", "artist": "A", "album": "B"}),
            {
                "title": "T",
                "artist": "A",
                "album": "B",
            },
        ),
        # partial payloads are fine
        ({"title": "Solo"}, {"title": "Solo"}),
        # unknown keys are dropped, not forwarded
        ({"title": "T", "name": "ignored", "url": "x"}, {"title": "T"}),
        # whitespace is squeezed off
        ({"title": "  T  "}, {"title": "T"}),
        # non-string noise per key is dropped
        ({"title": 42, "artist": "A"}, {"artist": "A"}),
        # nothing usable + non-objects -> None
        ({}, None),
        ({"title": ""}, None),
        ("[]", None),
        ("not json", None),
        ([1, 2], None),
        (None, None),
    ],
)
def test_audio_zone_metadata_table(raw, expected) -> None:
    assert audio_zone_metadata(raw) == expected


# ===========================================================================
# Entity behaviour (unit style, bus fallback routing)
# ===========================================================================
def test_audio_zone_advertises_wp67_features() -> None:
    """WP-6.7: on/off, mute, source selection are advertised (with the
    WP-4.4 standard set)."""
    f = _audio_zone(hass=None).supported_features
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    for flag in (
        MediaPlayerEntityFeature.TURN_ON,
        MediaPlayerEntityFeature.TURN_OFF,
        MediaPlayerEntityFeature.VOLUME_MUTE,
        MediaPlayerEntityFeature.SELECT_SOURCE,
        # the pre-WP-6.7 set is unchanged:
        MediaPlayerEntityFeature.PLAY,
        MediaPlayerEntityFeature.PAUSE,
        MediaPlayerEntityFeature.STOP,
        MediaPlayerEntityFeature.VOLUME_SET,
        MediaPlayerEntityFeature.VOLUME_STEP,
        MediaPlayerEntityFeature.NEXT_TRACK,
        MediaPlayerEntityFeature.PREVIOUS_TRACK,
    ):
        assert f & flag, f"missing flag {flag}"


async def test_audio_zone_on_off_and_mute_send_commands(hass) -> None:
    sent: list[dict] = []
    e = _audio_zone(hass)
    _write_stub(e)
    hass.bus.async_listen(SENDDOMAIN, lambda ev: sent.append(ev.data))

    await e.async_turn_on()
    await e.async_turn_off()
    await e.async_mute_volume(True)
    await e.async_mute_volume(False)
    await e.async_select_source("Library")
    await hass.async_block_till_done()

    assert sent == [
        {"uuid": "U68KEE6-AUD-0002-0000-000000000003", "value": "on"},
        {"uuid": "U68KEE6-AUD-0002-0000-000000000003", "value": "off"},
        {"uuid": "U68KEE6-AUD-0002-0000-000000000003", "value": "mute"},
        {"uuid": "U68KEE6-AUD-0002-0000-000000000003", "value": "unmute"},
        {"uuid": "U68KEE6-AUD-0002-0000-000000000003", "value": "source/Library"},
    ]


async def test_audio_zone_refuses_unknown_source(hass) -> None:
    sent: list[dict] = []
    e = _audio_zone(hass)
    _write_stub(e)
    hass.bus.async_listen(SENDDOMAIN, lambda ev: sent.append(ev.data))

    await e.async_select_source("Bogus")
    await hass.async_block_till_done()
    assert sent == []


async def test_audio_zone_power_off_forces_state_off(hass) -> None:
    e = _audio_zone(hass)
    _write_stub(e)

    e.event_handler({"36333734-01be-9336-ffff-d303162362704c73": 2})
    assert e.state == "playing"

    # the zone reports power-off: it must read off even while
    # playState still says playing
    e.event_handler({"36333734-01b7-9336-ffff-d303162362d3000183": 0})
    assert e.state == "off"

    # power back on: the previous playState is restored
    e.event_handler({"36333734-01b7-9336-ffff-d303162362d3000183": 1})
    assert e.state == "playing"


async def test_audio_zone_mute_stream_updates(hass) -> None:
    e = _audio_zone(hass)
    _write_stub(e)

    assert e.is_volume_muted is False  # unknown -> not muted
    e.event_handler({"36333734-01bb-9336-ffff-d303162362d3000187": 1})
    assert e.is_volume_muted is True
    e.event_handler({"36333734-01bb-9336-ffff-d303162362d3000187": 0})
    assert e.is_volume_muted is False


async def test_audio_zone_source_streams(hass) -> None:
    e = _audio_zone(hass)
    _write_stub(e)

    assert e.source_list == ["Radio", "Library"]  # seeded from details.sources
    assert e.source is None

    # the live source stream sets the current source and doubles as the
    # title fallback
    e.event_handler({"36333734-01b8-9336-ffff-d303162362d3000184": "Radio"})
    assert e.source == "Radio"
    assert e.media_title == "Radio"

    # the sourceList stream replaces the selectable list
    e.event_handler({"36333734-01b9-9336-ffff-d303162362d3000185": '["Radio", "AirPlay"]'})
    assert e.source_list == ["Radio", "AirPlay"]

    # a malformed list keeps the previous one
    e.event_handler({"36333734-01b9-9336-ffff-d303162362d3000185": "junk"})
    assert e.source_list == ["Radio", "AirPlay"]


async def test_audio_zone_metadata_stream_updates(hass) -> None:
    e = _audio_zone(hass)
    _write_stub(e)

    e.event_handler(
        {
            "36333734-01bd-9336-ffff-d303162362726d74": json.dumps(
                {"title": "Blue Moon", "artist": "Cab Calloway", "album": "Drum"}
            )
        }
    )
    assert e.media_title == "Blue Moon"
    assert e.media_artist == "Cab Calloway"
    assert e.media_album_name == "Drum"

    # a partial payload only replaces what it carries
    e.event_handler({"36333734-01bd-9336-ffff-d303162362726d74": '{"title": "Tina"}'})
    assert e.media_title == "Tina"
    assert e.media_artist == "Cab Calloway"

    # garbage keeps the last metadata
    e.event_handler({"36333734-01bd-9336-ffff-d303162362726d74": "not json"})
    assert e.media_title == "Tina"
    assert e.extra_state_attributes["source"] is None


async def test_audio_zone_favourite_list_stream(hass) -> None:
    e = _audio_zone(hass)
    _write_stub(e)

    assert e.extra_state_attributes["favourites"] == ["News"]  # seeded from details

    e.event_handler({"36333734-01c3-9336-ffff-d303162362666c73": '["Classical Mix", "News"]'})
    assert e.extra_state_attributes["favourites"] == ["Classical Mix", "News"]

    e.event_handler({"36333734-01c3-9336-ffff-d303162362666c73": "junk"})
    assert e.extra_state_attributes["favourites"] == ["Classical Mix", "News"]


# ===========================================================================
# Full setup with the LoxAPP3 fixture (acceptance criteria of WP-6.7)
# ===========================================================================
# Stream uuids, hand-read from tests/fixtures/LoxAPP3.json (Parlour Audio Zone).
AUDIO_ACTION = "63746c3a-01b6-9726-ffff-f75722041756000182"
AUDIO_ACTIVE = "36333734-01b7-9336-ffff-d303162362d3000183"
AUDIO_SOURCE = "36333734-01b8-9336-ffff-d303162362d3000184"
AUDIO_SOURCE_LIST = "36333734-01b9-9336-ffff-d303162362d3000185"
AUDIO_VOLUME = "36333734-01ba-9336-ffff-d303162362d3000186"
AUDIO_MUTE = "36333734-01bb-9336-ffff-d303162362d3000187"
AUDIO_PLAY_STATE = "36333734-01be-9336-ffff-d303162362704c73"
AUDIO_METADATA = "36333734-01bf-9336-ffff-d303162362726d74"
AUDIO_FAVOURITES = "36333734-01c3-9336-ffff-d303162362666c73"


async def test_wp67_audio_zone_appear_update_send(hass, mock_connection, mock_entry) -> None:
    """Acceptance: the fixture AudioZoneV2 appears after setup, tracks its
    fed state streams (playState / active / mute / source / metadata /
    favouriteList), and the on-off / mute / select-source services send
    the intended commands."""
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    player = next((e for e in registry.entities.values() if e.unique_id == AUDIO_ACTION), None)
    assert player is not None, "the fixture Parlour Audio Zone (AudioZoneV2) was not set up"
    assert player.domain == "media_player"
    state = hass.states.get(player.entity_id)
    assert state is not None
    # seeded from the fixture details
    assert state.attributes["source_list"] == ["Radio", "Library", "AirPlay"]
    assert state.attributes["favourites"] == ["Classical Mix", "Jazz Station"]

    def attrs() -> dict:
        return hass.states.get(player.entity_id).attributes

    # -- state streams ------------------------------------------------------
    mock_connection.feed(AUDIO_PLAY_STATE, 2)
    await hass.async_block_till_done()
    assert hass.states.get(player.entity_id).state == "playing"

    mock_connection.feed(AUDIO_ACTIVE, 0)
    await hass.async_block_till_done()
    assert hass.states.get(player.entity_id).state == "off"

    mock_connection.feed(AUDIO_ACTIVE, 1)
    await hass.async_block_till_done()
    assert hass.states.get(player.entity_id).state == "playing"

    mock_connection.feed(AUDIO_VOLUME, 65)
    mock_connection.feed(AUDIO_MUTE, 1)
    await hass.async_block_till_done()
    assert attrs()["volume_level"] == 0.65
    assert attrs()["is_volume_muted"] is True

    mock_connection.feed(AUDIO_MUTE, 0)
    await hass.async_block_till_done()
    assert attrs()["is_volume_muted"] is False

    mock_connection.feed(AUDIO_SOURCE, "Radio")
    mock_connection.feed(AUDIO_SOURCE_LIST, '["Radio", "Library", "AirPlay", "TuneIn"]')
    mock_connection.feed(
        AUDIO_METADATA,
        json.dumps({"title": "Blue Moon", "artist": "Cab Calloway", "album": "Drum"}),
    )
    mock_connection.feed(AUDIO_FAVOURITES, '["Classical Mix", "News"]')
    await hass.async_block_till_done()
    assert attrs()["source"] == "Radio"
    assert attrs()["source_list"] == ["Radio", "Library", "AirPlay", "TuneIn"]
    assert attrs()["media_title"] == "Blue Moon"
    assert attrs()["media_artist"] == "Cab Calloway"
    assert attrs()["media_album_name"] == "Drum"
    assert attrs()["favourites"] == ["Classical Mix", "News"]

    # -- outbound services ----------------------------------------------------
    sent_before = len(mock_connection.sent)

    async def service(service_name: str, **kwargs) -> None:
        data = {"entity_id": player.entity_id, **kwargs}
        await hass.services.async_call("media_player", service_name, data, blocking=True)
        await hass.async_block_till_done()

    await service("turn_on")
    await service("turn_off")
    await service("volume_mute", is_volume_muted=True)
    await service("select_source", source="AirPlay")
    await hass.async_block_till_done()

    assert mock_connection.sent[sent_before:] == [
        {"uuid": AUDIO_ACTION, "value": "on", "code": None},
        {"uuid": AUDIO_ACTION, "value": "off", "code": None},
        {"uuid": AUDIO_ACTION, "value": "mute", "code": None},
        {"uuid": AUDIO_ACTION, "value": "source/AirPlay", "code": None},
    ]
    # the zone still plays what it was fed; the commands are fire-and-
    # confirm: the state arrives via the server's echo streams, not
    # optimistically.
    assert hass.states.get(player.entity_id).state == "playing"
