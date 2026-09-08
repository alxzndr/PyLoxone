# WP-4.4 — Fan, alarm, media player

Findings fixed: **PC-08, PC-09 (VERIFY), PC-29 (VERIFY), PC-30, PC-06,
PC-31 (VERIFY), PC-16 (alarm + media players only), PC-40/PC-41
(fan/alarm/media lines), PC-43 (media `STOP` feature only)**.
Upstream issue: **JoDehli/PyLoxone#413** (alarm code format).

Prerequisites landed on `master` before this branch was cut:
WP-1.1 (`fix/wp-1.1-loxone-entity`) and WP-1.3 (`fix/wp-1.3-initial-state`).

## What changed (by file)

### `custom_components/loxone/fan.py`
- **PC-08**: `supported_features` now declares `FanEntityFeature.TURN_ON |
  TURN_OFF` in addition to `PRESET_MODE | SET_SPEED`.
- **PC-09 (VERIFY)**: `set_preset_mode` implemented behind the named helper
  `ventilation_set_mode_command(preset_mode)` → `setMode/<profile id>`
  (`setMode/5` for *Auto*). Unknown modes are rejected with a warning, no
  command. The empty no-op is gone; `async_turn_off` now actually does
  something.
- **PC-29 (VERIFY)**: `set_percentage` rewritten behind
  `ventilation_set_timer_command(interval, percentage, mode)` and
  `ventilation_profile_id(mode)`. It sends the **raw integer** profile id
  (`setTimer/3600/<pct>/<mode id>/-1`) instead of the profile *name* or
  `None`; if no valid mode (2..6) has been received yet it sends *nothing*
  instead of a broken command.
- **PC-30**: `percentage` goes through `fan_speed_percentage()` — rounded
  int clamped to 0..100; `None`/NaN/strings → `None` (unknown). Added the
  legacy contract `_attr_speed_count = 100`.
- **PC-16 (fan part)**: `get_state_value()` uses the unguarded-index-free
  `_state_uuid()` helper (`.get()`), so a Ventilation control without a
  `speed` (or any other) `states` entry no longer raises.
- **PC-40**: removed the commented-out `temperatureIndoor` block, the
  commented-out `turn_on`, and the dead sync `turn_off` stub (only the async
  variants exist). **PC-41**: wrong "Alarm.com" module docstring fixed.

### `custom_components/loxone/alarm_control_panel.py`
- **PC-06** (#413): `_attr_code_arm_required = isSecured` and
  `_attr_code_format = CodeFormat.NUMBER if isSecured else None`, evaluated
  once in `__init__` — reading the properties in any order now returns the
  same facts. The side-effecting `code_arm_required` setter, the dead
  `code_format` property, `_code`, and `_validate_code` are
  deleted. Codes are still forwarded to `SECUREDSENDDOMAIN` as before.
- **PC-31 (VERIFY)**: arm parameter value extracted to
  `alarm_arm_value(arm_state)` and **swapped**: arm-home sends
  `delayedon/1`, arm-away `delayedon/0`, agreeing with the state mapping
  (`armed and disabled_move` → `ARMED_HOME`).
- **PC-16 (alarm lines)**: all seven `states[...]` lookups in
  `event_handler` go through `_state_uuid()` + `(u := …) and u in e.data`;
  an alarm fixture without `armed`/`disabledMove`/`armedAt`/`nextLevelAt`/…
  (e.g. *no `nextLevelAt`*) now sets up and survives events.
- **PC-40**: removed the no-op YAML `PLATFORM_SCHEMA` (+ `voluptuous` /
  `cv` imports and the `CONF_*` constants that existed only for it), the
  `hidden`/`icon` stubs, the dead sync `alarm_*` stubs, and
  `DEFAULT_FORCE_UPDATE`. **PC-41**: wrong "Alarm.com" docstring fixed;
  `async_setup_*` no longer return `True` from `-> None` signatures.

### `custom_components/loxone/media_player.py`
- **PC-43 (STOP feature only)**: `MediaPlayerEntityFeature.STOP` added to
  `SUPPORT_LOXONE_AUDIO_ZONE`, so `async_media_stop` is reachable, not
  dead. AudioZoneV2 has no dedicated stop sub-command, so stop sends the
  best available — the zone-silencing `pause` value, behind the named
  helper `audio_zone_stop_value()` (see VERIFY note below).
- **PC-41 / unknown states**: `play_state_to_media_player_state` now returns
  `MediaPlayerState.IDLE` (a non-playing state, logged at DEBUG) for unknown
  values instead of falling off `None`.
- **PC-16 (media lines)**: `event_handler` uses `_state_uuid()` guards; a
  zone without a `playState` state (as in the bundled `LoxAPP3.json`) no
  longer raises `KeyError` on every `loxone_event`.
- **PC-40/PC-41**: the `kwargs`-dump debug log now logs only the
  `uuidAction`; `async_setup_platform` annotation fixed; dead
  `DEFAULT_FORCE_UPDATE` removed.

### `tests/test_fan_alarm_media.py` (new, 40 tests)
Red→green proven against `master`: **31 failed / 9 passed before** the fix,
**40 passed after** (see "Verification").

## VERIFY items — live-Miniserver check required before merge

1. **PC-09** `ventilation_set_mode_command`: the subcommand name
   (`setMode`) and the id-as-argument layout are the *intended* semantics
   (e.g. `setMode/5` for Auto). Test table:
   Low→`setMode/2`, Medium→`setMode/3`, High→`setMode/4`, Auto→`setMode/5`,
   Away→`setMode/6`. Verify against a live Ventilation device.
2. **PC-29** `ventilation_set_timer_command` / `ventilation_profile_id`:
   raw-integer mode in `setTimer/<interval>/<pct>/<mode id>/-1` (kept the
   3600 s interval constant). Verify the argument layout, and check whether
   a non-timed speed command exists at all.
3. **PC-31** `alarm_arm_value`: arm parameter swapped relative to
   `master` (home→`delayedon/1`, away→`delayedon/0`) because the old values
   round-trip an armed-home to away in the `armed and disabled_move` state
   mapping. Verify the round-trip on a live secured/unsecured Alarm.
4. **PC-43 / media stop**: `pause` used as the stop value because
   AudioZoneV2 has no `stop` sub-command. Confirm no dedicated stop command
   exists (if it does, `audio_zone_stop_value()` is the one-line change).

## Verification (run from the repo root)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
56 files already formatted

$ pytest -q
207 passed, 1 deselected, 1 xfailed, 2 warnings in 38.51s
Required test coverage of 20% reached. Total coverage: 55.84%
```

New package alone: `tests/test_fan_alarm_media.py` — 40 passed.
Pre-fix (same tests on stashed `master` sources): 31 failed, 9 passed.
(2 of the pre-fix failures are `ImportError` — the new named helpers do not
exist in `master`; the rest are genuine assertion failures, including the
`KeyError: 'playState'` event crash and the no-op `set_preset_mode`.)

## Follow-ups (outside this WP's file list, not fixed)

- `helpers.py` / `climate.py` / `cover.py` / `light*.py` still carry the
  remaining PC-16 and PC-40/PC-41 lines (owned by WP-4.1/WP-4.2/WP-4.3).
- The PC-16 "shared" `_state_uuid()` helper cannot live in `helpers.py`
  (untouchable in Phase 4); it is module-local in `fan.py`,
  `alarm_control_panel.py`, `media_player.py`. A later Phase-5 sweep can
  consolidate them.
- Fan: `async_turn_off` sets preset *Auto* before percentage 0 — whether
  turning "off" should set a specific ventilation profile is a product
  decision, untouched here.
- `DEFAULT_FAN_SPEED_HOME/AWAY/BOOST` in `fan.py` remain (unused, but not
  on PC-40's fan line list).
- Alarm: `ARM_NIGHT` / `ARM_VACATION` / `TRIGGER` and arming-delay
  surfacing (#323) are PC-43's alarm lines and out of this WP's scope
  (its PC-43 item is the media `STOP` feature only).

## Observation (ground-rule report)

While checking prerequisites with `git log --oneline -20` on `master`, I
saw the **already-merged** commit `0ccffb8`
(`fix(HVAC): climate KeyError + … (CVE-1.10/43, PF-19)`) cite finding
prefixes **CVE-** and **PF-**, which are not part of the catalogue's ID
scheme (API-/CORE-/PS-/PC-/TOOL-). I did not use those IDs anywhere in this
WP; flagging where they were seen, per the ground rules.
