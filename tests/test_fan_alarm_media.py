"""WP-4.4 regression tests: fan (Ventilation), alarm panel, media player (AudioZoneV2).

Expected command values are hand-derived literals (e.g. profile *Auto* is
id 5, so ``set_preset_mode("Auto")`` must send ``setMode/5``) — never computed
by calling the code under test.

Items marked VERIFY in the catalogue are pinned here to their *intended*
semantics behind the named helpers; a live-Miniserver check is required before
relying on them (see the PR body).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components.alarm_control_panel.const import CodeFormat
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.config_entries import ConfigEntryState

from custom_components.loxone.alarm_control_panel import LoxoneAlarm
from custom_components.loxone.const import SECUREDSENDDOMAIN, SENDDOMAIN
from custom_components.loxone.fan import LoxoneVentilation
from custom_components.loxone.media_player import LoxoneAudioZoneV2


def _fan(**overrides) -> LoxoneVentilation:
    kwargs = {
        "hass": None,
        "name": "Test Ventilation",
        "nameRu": "Test Ventilation",
        "uuidAction": "U68KEE6-VOA-0001-0000-000000000001",
        "room": "",
        "device_class": None,
        "type": "ventilation",
        "details": {"format": "", "hasIndoorHumidity": False, "hasAirQuality": False, "hasPresence": False},
        "states": {
            "active": "36333734-01ad-9336-ffff-d303161632d3000173",
            "mode": "36333734-01ae-9336-ffff-d303161632d3000174",
            "speed": "36333734-01ae-9336-ffff-d303161632d3000175",
        },
        "isSecured": False,
    }
    kwargs.update(overrides)
    return LoxoneVentilation(**kwargs)


def _alarm(hass, **overrides) -> LoxoneAlarm:
    kwargs = {
        "hass": hass,
        "name": "Test Alarm",
        "nameRu": "Test Alarm",
        "uuidAction": "U68KEE6-ALA-0001-0000-000000000002",
        "room": "",
        "type": "Alarm",
        "code": None,
        "details": {"modeList": ["Disarmed", "Armed Home", "Armed Away"]},
        "states": {
            "armed": "36333734-01a8-9336-ffff-d303161372d3011111",
            "level": "36333734-01a9-9336-ffff-d303161372d3066666",
        },
        "isSecured": False,
    }
    kwargs.update(overrides)
    return LoxoneAlarm(**kwargs)


def _audio_zone(hass, **overrides) -> LoxoneAudioZoneV2:
    kwargs = {
        "name": "Test Audio Zone",
        "nameRu": "Test Audio Zone",
        "uuidAction": "U68KEE6-AUD-0001-0000-000000000003",
        "room": "",
        "type": "AudioZoneV2",
        "details": {},
        "states": {
            "active": "36333734-01b7-9336-ffff-d303162362d3000183",
            "volume": "36333734-01ba-9336-ffff-d303162362d3000186",
            "playState": "36333734-01b8-9336-ffff-d303162362d3000184",
        },
        "isSecured": False,
    }
    kwargs["hass"] = hass
    kwargs.update(overrides)
    return LoxoneAudioZoneV2(**kwargs)


def _hass_write_stub(e) -> None:
    e.async_schedule_update_ha_state = lambda *a, **k: None
    e.schedule_update_ha_state = lambda *a, **k: None


# ===========================================================================
# fan.py (PC-08, PC-09, PC-29, PC-30, PC-40/PC-41)
# ===========================================================================


def test_fan_supported_features_includes_turn_on_off() -> None:
    """PC-08: TURN_ON/TURN_OFF must be advertised (required since HA 2024.8)."""
    e = _fan()
    features = e.supported_features
    assert features & FanEntityFeature.TURN_ON
    assert features & FanEntityFeature.TURN_OFF
    assert features & FanEntityFeature.PRESET_MODE
    assert features & FanEntityFeature.SET_SPEED


@pytest.mark.parametrize(
    ("speed", "expected"),
    [
        (None, None),
        (0.0, 0),
        (42.0, 42),
        (37.4, 37),
        (99.6, 100),
        (103.0, 100),
        (-0.4, 0),
        (-25.0, 0),
        (float("nan"), None),
        ("nope", None),
    ],
)
def test_fan_speed_percentage_clamped_table(speed, expected) -> None:
    """PC-30: hand-derived clamp table (raw float, possibly off-band)."""
    from custom_components.loxone.fan import fan_speed_percentage

    assert fan_speed_percentage(speed) == expected


async def test_fan_percentage_is_clamped_int_after_event(hass) -> None:
    """PC-30: `percentage` is an int in 0..100 from a raw server value."""
    e = _fan()
    e.hass = hass
    _hass_write_stub(e)
    assert e.percentage is None

    await e.event_handler(SimpleNamespace(data={e.states["speed"]: 153.0}))
    assert e.percentage == 100

    await e.event_handler(SimpleNamespace(data={e.states["speed"]: 3.0}))
    assert e.percentage == 3
    assert isinstance(e.percentage, int)


def test_fan_speed_state_missing_from_structure() -> None:
    """PC-16: a control without a `speed` state must not raise."""
    e = _fan(states={"mode": "36333734-01ae-9336-ffff-d303161632d3000174"})
    assert e.percentage is None
    assert e.get_state_value("speed") is None


def test_ventilation_set_mode_command_table() -> None:
    """PC-09 (VERIFY — intended semantics): profile id, not name."""
    from custom_components.loxone.fan import ventilation_set_mode_command

    assert ventilation_set_mode_command("Low") == "setMode/2"
    assert ventilation_set_mode_command("Medium") == "setMode/3"
    assert ventilation_set_mode_command("High") == "setMode/4"
    assert ventilation_set_mode_command("Auto") == "setMode/5"
    assert ventilation_set_mode_command("Away") == "setMode/6"


async def test_fan_set_preset_mode_auto_sends_command(hass) -> None:
    """PC-09: `set_preset_mode("Auto")` is no longer a no-op."""
    e = _fan()
    e.hass = hass
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    e.set_preset_mode("Auto")
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-VOA-0001-0000-000000000001", "value": "setMode/5"},
    ]


async def test_fan_set_preset_mode_rejects_unknown_mode(hass) -> None:
    e = _fan()
    e.hass = hass
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    e.set_preset_mode("Turbo")
    await hass.async_block_till_done()

    assert fired == []


def test_fan_profile_id_table() -> None:
    """PC-29: only the raw integer profile ids 2..6 are valid modes."""
    from custom_components.loxone.fan import ventilation_profile_id

    assert ventilation_profile_id(2) == 2
    assert ventilation_profile_id(6.0) == 6
    assert ventilation_profile_id(None) is None
    assert ventilation_profile_id(99) is None
    assert ventilation_profile_id("nope") is None


def test_ventilation_set_timer_command_table() -> None:
    """PC-29 (VERIFY — intended semantics): raw integer mode in setTimer."""
    from custom_components.loxone.fan import ventilation_set_timer_command

    assert ventilation_set_timer_command(3600, 50, 2) == "setTimer/3600/50/2/-1"
    assert ventilation_set_timer_command(3600, 0, 6) == "setTimer/3600/0/6/-1"


async def test_fan_set_percentage_with_known_mode(hass) -> None:
    """PC-29: a plain speed change uses the raw mode id (no name, no None)."""
    e = _fan()
    e.hass = hass
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.event_handler(SimpleNamespace(data={e.states["mode"]: 2}))
    e.set_percentage(50)
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-VOA-0001-0000-000000000001", "value": "setTimer/3600/50/2/-1"},
    ]


async def test_fan_set_percentage_without_known_mode_sends_nothing(hass) -> None:
    """PC-29: before any mode value is known, no (broken) command is sent."""
    e = _fan()
    e.hass = hass
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    e.set_percentage(50)
    await hass.async_block_till_done()

    assert fired == []


async def test_fan_turn_on_preset_sends_mode_command(hass) -> None:
    """PC-09/PC-08: turn_on(preset_mode=...) routes through set_preset_mode."""
    e = _fan()
    e.hass = hass
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_turn_on(preset_mode="Away")
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-VOA-0001-0000-000000000001", "value": "setMode/6"},
    ]


# ===========================================================================
# alarm_control_panel.py (PC-06, PC-31, PC-16, PC-40/PC-41)
# ===========================================================================


async def test_alarm_secured_code_format_number_independent_of_property_order(hass) -> None:
    """PC-06: code attributes are setup-time facts, not read-order-dependent state."""
    e = _alarm(hass, isSecured=True)
    _hass_write_stub(e)

    assert e.code_arm_required is True
    assert e.code_format == CodeFormat.NUMBER
    # Re-evaluating in the opposite order must not change anything.
    assert e.code_format == CodeFormat.NUMBER
    assert e.code_arm_required is True


async def test_alarm_unsecured_requires_no_code(hass) -> None:
    """PC-06: an unsecured alarm needs no code at all."""
    e = _alarm(hass, isSecured=False)
    _hass_write_stub(e)

    assert e.code_arm_required is False
    assert e.code_format is None
    assert e.code_format is None
    assert e.code_arm_required is False


def test_alarm_arm_value_table() -> None:
    """PC-31 (VERIFY — intended semantics): parameter agrees with the state
    mapping (`armed and disabled_move` -> ARMED_HOME)."""
    from homeassistant.components.alarm_control_panel import AlarmControlPanelState

    from custom_components.loxone.alarm_control_panel import alarm_arm_value

    assert alarm_arm_value(AlarmControlPanelState.ARMED_HOME) == "delayedon/1"
    assert alarm_arm_value(AlarmControlPanelState.ARMED_AWAY) == "delayedon/0"


async def test_alarm_arm_away_sends_delayedon_0_via_send_domain(hass) -> None:
    e = _alarm(hass, isSecured=False)
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_alarm_arm_away()
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-ALA-0001-0000-000000000002", "value": "delayedon/0"},
    ]


async def test_alarm_arm_home_sends_delayedon_1_secured_with_code(hass) -> None:
    """PC-06/PC-31: a secured arm goes out on the secured channel with the code."""
    e = _alarm(hass, isSecured=True)
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SECUREDSENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_alarm_arm_home(code="1234")
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-ALA-0001-0000-000000000002", "value": "delayedon/1", "code": "1234"},
    ]


async def test_alarm_disarm_sends_off(hass) -> None:
    e = _alarm(hass, isSecured=False)
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_alarm_disarm()
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-ALA-0001-0000-000000000002", "value": "off"},
    ]


async def test_alarm_event_handler_survives_missing_state_uuids(hass) -> None:
    """PC-16: events must not crash for states the structure file lacks."""
    e = _alarm(hass, isSecured=False, states={"armed": "armed-uuid", "level": "level-uuid"})
    _hass_write_stub(e)

    # No armed/disabledMove/armedAt/... keys in states: must not raise.
    await e.event_handler(SimpleNamespace(data={"armed-uuid": 1}))
    assert e._state == 1
    assert e._disabled_move == 0

    await e.event_handler(SimpleNamespace(data={"level-uuid": 2}))
    assert e.level == 2
    assert e.alarm_state == "triggered"


# ===========================================================================
# media_player.py (PC-43 STOP feature, PC-16, PC-41)
# ===========================================================================


@pytest.mark.parametrize(
    ("play_state", "expected"),
    [
        (0, "idle"),
        (1, "paused"),
        (2, "playing"),
        (-1, "off"),
        # Unknown values fall back to a non-playing state instead of None.
        (-2, "idle"),
        (3, "idle"),
        (100, "idle"),
    ],
)
def test_play_state_to_media_player_state_table(play_state, expected) -> None:
    from custom_components.loxone.media_player import play_state_to_media_player_state

    assert play_state_to_media_player_state(play_state) == expected


def test_media_player_advertsts_stop_feature() -> None:
    """PC-43: STOP is advertised, so `async_media_stop` is reachable (not dead)."""
    e = _audio_zone(hass=None)

    assert e.supported_features & MediaPlayerEntityFeature.STOP
    assert e.supported_features & MediaPlayerEntityFeature.PAUSE
    assert e.supported_features & MediaPlayerEntityFeature.PLAY
    # Unchanged by this WP:
    assert not (e.supported_features & MediaPlayerEntityFeature.SELECT_SOURCE)


async def test_media_stop_sends_pause_command(hass) -> None:
    """PC-43: stop on an AudioZoneV2 = silence the zone (no stop sub-command)."""
    e = _audio_zone(hass)
    _hass_write_stub(e)
    fired = []
    hass.bus.async_listen(SENDDOMAIN, lambda ev: fired.append(ev.data))

    await e.async_media_stop()
    await hass.async_block_till_done()

    assert fired == [
        {"uuid": "U68KEE6-AUD-0001-0000-000000000003", "value": "pause"},
    ]


async def test_media_player_event_without_playstate_does_not_crash(hass) -> None:
    """PC-16: a zone without a `playState` state in the structure file."""
    e = _audio_zone(
        hass,
        states={
            "active": "36333734-01b7-9336-ffff-d303162362d3000183",
            "volume": "36333734-01ba-9336-ffff-d303162362d3000186",
        },
    )
    _hass_write_stub(e)

    # Pre-fix this raised KeyError("playState") on every bus event.
    await e.event_handler(SimpleNamespace(data={"36333734-01ba-9336-ffff-d303162362d3000186": 50}))
    assert e.volume_level == 0.5
    assert e.state == MediaPlayerState.OFF  # untouched, not None


async def test_media_player_playstate_event_updates_state(hass) -> None:
    e = _audio_zone(hass)
    _hass_write_stub(e)
    play_state_uuid = "36333734-01b8-9336-ffff-d303162362d3000184"

    await e.event_handler(SimpleNamespace(data={play_state_uuid: 2}))
    assert e.state == MediaPlayerState.PLAYING

    await e.event_handler(SimpleNamespace(data={play_state_uuid: 7}))  # unknown
    assert e.state == MediaPlayerState.IDLE


# ===========================================================================
# full setup: alarm fixture without `nextLevelAt` (PC-16 acceptance)
# ===========================================================================


async def test_alarm_fixture_without_nextlevel_at_sets_up(hass, loxapp3, mock_connection, mock_entry) -> None:
    """A structure file whose alarm lacks `nextLevelAt` must set up cleanly."""
    alarm_control = next(uuid for uuid in loxapp3["controls"] if uuid.endswith("0416c61726d3000167"))
    assert "nextLevelAt" in loxapp3["controls"][alarm_control]["states"]
    del loxapp3["controls"][alarm_control]["states"]["nextLevelAt"]
    try:
        mock_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_entry.entry_id)
    finally:
        loxapp3["controls"][alarm_control]["states"]["nextLevelAt"] = "36333734-01aa-9336-ffff-d303161372d3000170"

    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED

    alarm_states = [st for st in hass.states.async_all() if (st.attributes.get("device_type") == "Alarm")]
    assert alarm_states, "no alarm entity was created"
