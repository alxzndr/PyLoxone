# WP-4.5 — Sensor, binary sensor, switch, select, number, button, scene

Findings fixed: **PS-04, PS-05, PS-06, PS-07, PS-08, PS-09 (remainder),
PS-15 (remainder), PS-16, PS-17, PS-19, PS-21, PS-24, PS-25, CORE-22,
CORE-32** (`map_range`/`get_all` hardening from the Uni Ulm fuzzing PR #292).
Upstream Loxone issues listed by the catalogue for these findings:
**JoDehli/PyLoxone#402 #481 #492** (sensor/number direction),
**JoDehli/PyLoxone#292** (fuzzing findings in `get_all`/`map_range`).

Prerequisites landed on `master` before this branch was cut (verified via
`git log --oneline -20` at the start): WP-1.1 (`8b2ab6e`/`dfecbc6`) and
WP-1.3 (`947b0be`, the commit that flipped the CORE-07 xfail for initial
state correctness — the only WP-1.3-identified commit that has landed; see
"A Note on Prerequisites" at the bottom).

## What changed (by file)

### `custom_components/loxone/helpers.py`
- **CORE-32 (fuzzing #292)**: `get_all` no longer assumes `controls`
  exists or that every control has a `type` — `get_all({}, "Switch") == []`
  (acceptance, previously a `KeyError` — verified on the old file);
  non-dict entries are skipped. `map_range` returns `out_min` for a
  degenerate `in_min == in_max` range instead of raising
  `ZeroDivisionError` (verified on the old file).
- **PS-24**: new shared `iter_controls(hass, config_entry, types)` helper
  that replaces the repeated `get_miniserver_from_hass` →
  `lox_config.json` → `get_all` → `add_room_and_cat_to_value_values`
  boilerplate in every platform's `async_setup_entry`. It yields a
  *shallow copy* of each control dict: some older platforms (light.py,
  climate.py, …) still write runtime objects (`hass`, `config_entry`,
  `async_add_devices`) into the shared structure dict, and a shared dict
  makes that poison leak out of setup (discovered while writing the
  setup-level tests — see Follow-ups). `miniserver` is imported inside the
  function to avoid a circular import.
- Deleted the commented-out numpy helpers (CORE-32). `LoxoneEntity._clean_unit`
  was already removed in WP-1.1, so the `clean_unit` duplicate is gone.

### `custom_components/loxone/sensor.py`
- **PS-07**: YAML sensor `unique_id` no longer does
  `self.uuidAction + self._attr_name` (a `TypeError` when `name` is absent;
  verified the old expression on the old file). Without a name the unique
  id is the `uuidAction` alone; with a name, `uuidAction + "-" + name`
  (stable for a given pair). The three-line `value_template` handling in
  `async_setup_platform` is *deleted* (not `cv.template`): `value_template`
  is not in `PLATFORM_SCHEMA`, so it arrived as a raw string and
  `value_template.hass = hass` raised; the template was never applied
  anyway.
- **PS-08**: Meter setup now reads format keys via
  `sensor.get("details", {}).get(format_key, "%.1f")` and the whole
  per-Meter block is `try/except` with a log — a Meter without
  `storageFormat` yields the remaining sub-sensors (and a neutral-format
  "Level") instead of aborting the whole sensor platform.
- **PS-09 (remainder)**: `ERROR_VALUE`/`None` values publish `unknown`
  (via the named helper `_analog_value`); the dead `_get_lox_rounded_value`
  and `self._format` are deleted, the `available` override that re-ran the
  state pipeline is deleted (availability is the WP-1.3/WP-2.3 model);
  `state_class` is only advertised for *numeric* format strings
  (`_is_numeric_format`; a `"%s"` string format previously triggered the
  blanket `MEASUREMENT` and an HA `ValueError` on a text value).
- **PS-21**: explicit per-register Meter classifications
  (`actual` → power/measurement, `total`/`totalNeg` →
  energy/total_increasing, `storage` → energy/**measurement**), passed
  through `METER_STATE_CLASSES` and applied on top of the unit-based
  match; a plain `InfoOnlyAnalog` whose name/category does **not** indicate
  a meter ("Consumption today") gets `total`/water `MEASUREMENT` instead of
  the blanket `TOTAL_INCREASING` (the energy-dashboard spike), via the
  `_metering_indicated` helper. Unit tested with hand-derived literals
  (`"%d kWh"`, names "Consumption today" / "Meter Total").
- **PS-25**: `_parse_digits_after_decimal` on a non-str is safe;
  `if precision:` → `if precision is not None:` ("%.0f" now yields 0, not
  nothing); `LoxoneVersionSensor` loses its redundant
  `_attr_should_poll = False`; `LoxoneService... no; `LoxoneMeterSensor`
  loses its duplicate; the `async_add_devices` kwarg that no sensor reads
  is gone.
- **CORE-22**: `LoxoneRoomControllerOverrideSensor` now has
  `_attr_translation_key = "override_reason"` and publishes *slugs*
  (`OVERRIDE_REASON_SLUGS`: none/presence/window_open/comfort_override/
  eco_override/eco_plus_override/prepare_heat_up/prepare_cool_down/
  overridden_by_source/fixed) with a single `unknown` option instead of
  runtime-minted `Unknown (n)` strings; the magic clamp to 14 is deleted.
  The previously misplaced `entity.sensor.override_reason` translation
  blocks in `translations/{en,de,cs}.json` were moved to the path HA
  actually resolves for a custom integration —
  `entity.sensor.loxone.override_reason.state.<slug>` — and gained the
  `unknown` key (file-list deviation: required for the CORE-22 behaviour,
  no other key touched, de↔en key parity kept).
- **PS-24**: all platform loops go through `iter_controls` with a
  per-control `try/except` + `_LOGGER.exception("Skipping …")`.
- Note: `ERROR_VALUE` (const.py) previously unused is now used by
  `_analog_value` (PS-09 mandated the sentinel mapping).

### `custom_components/loxone/binary_sensor.py`
- **PS-04**: a `SmokeAlarm` is read from `states["level"]` (on when
  level > 0; `areAlarmSignalsOff` only says the beeper is muted and is now
  ignored); `InfoOnlyDigital`/`PresenceDetector` use the explicit
  `active` state (previously digital fell back to `uuidAction`); the
  broken `if`/`if`/`elif` chain is a proper `if`/`elif`/`elif`/`else`;
  every lookup is `.get()` with fallback, never a platform-aborting
  `KeyError`; `async_setup_entry` got the per-control `try/except`;
  the event handler maps a value to on/off bool instead of requiring an
  exact `1.0` (sub-normal values used to read "off").

### `custom_components/loxone/switch.py`
- **PS-05**: `LoxoneSwitch` (and the intercom sub-control) resolve their
  state uuid once in the handler/`__init__` via `.get()` — a control
  without `active` no longer raises on every event; intercom sub-controls
  lacking `active` are skipped in setup with a warning (regression test
  asserts the warning lands).
- **PS-06**: the LightControllerV2 presence guard
  (`switch_entity.get("presence")`, top-level key) now matches the
  constructor (`states["presence"]`) — previously either dead code or a
  `KeyError` aborting the platform; the name is set on `_attr_name`
  instead of assigning over the `name` cached property. The `_`
  double-assign in intercom setup is replaced by a named variable
  (PS-25). Per-control `try/except` in setup; `json.JSONDecodeError,
  TypeError` → parenthesised (PS-25).

### `custom_components/loxone/select.py`
- **PS-19**: empty Radios (no `outputs` and no `allOff`) are skipped in
  setup with a warning instead of crashing the platform;
  `async_select_option` raises `HomeAssistantError` while the block is
  locked (`jLocked`); the output and lock updates fold into a single
  `async_write_ha_state` per event; the redundant
  `options`/`current_option` property overrides are deleted (uses
  `_attr_options`/`_attr_current_option`); `states.get("activeOutput")`
  everywhere.

### `custom_components/loxone/number.py`
- **PS-15 (remainder)**: `_attr_native_value` starts `None` (was the
  `STATE_UNKNOWN` *string* returned as the native value); listens on
  `states.get("value") or uuidAction` (previously advertised `states["value"]`
  in `extra_state_attributes` but listened on `uuidAction`); the event
  path is now `async_schedule_update_ha_state` (was a sync
  `schedule_update_ha_state` from the loop); `details` keys go through
  `.get()` (missing min/max → `ValueError` → control skipped with a log in
  setup); step defaults to 1.0; unit/device-class/precision are derived
  from `details["format"]` via the shared `clean_unit` +
  `match_sensor_description` (previously ignored).

### `custom_components/loxone/button.py`
- **PS-16**: the `@final` `ButtonEntity.state` override (the
  `# noinspection PyFinal` `@final`-marked `state` property that reported
  a press timestamp) is deleted; the Miniserver's press echo is exposed as
  a `last_pressed` attribute in `extra_state_attributes` instead; the
  bespoke divergent `DeviceInfo` property is replaced by the same
  `get_or_create_device` path as the other platforms; `press()` uses
  `async_fire`.

### `custom_components/loxone/scene.py`
- **PS-17**: scene generation is no longer a 3-second
  `hass.loop.call_later` that survived entry unload and neither tracked
  nor cancelled anything, nor a scrape of `hass.data["light"]` for
  *other-platform* entities filtered on a fake `device_class` string.
  `async_setup_entry` now walks `get_all/iter_controls(hass, entry,
  "LightControllerV2")` directly from the structure file: the device
  entry (with the `(DOMAIN, uuidAction)` identifier shared with the light
  platform) is created at setup, and as soon as the LCV2 pushes its
  `moodList` stream (every live Miniserver sends it right after
  connecting) the scene entities for that controller are added and
  exactly one tracked bus listener per LCV2 (stored in
  `miniserver.listeners`) fires the one-time add. No timer; nothing runs
  after unload. `LoxoneLightScene` uses `_attr_name`/`_attr_unique_id`
  and the old `self.name = name` / `return True` from `-> None` is gone.
  The "create immediately after setup" acceptance is satisfied by the
  unit of event arrival: the first `moodList` delivery creates the scenes
  with no fixed delay (test feeds the stream and asserts presence
  immediately). In a real structure file `states["moodList"]` is a state
  uuid, so the mood *names* can only come from that stream — that is the
  intended semantics tested here; see VERIFY note.

### `tests/test_simple_platforms.py` (new, 41 tests)
Maps acceptance → test (all expected values hand-derived literals, not
produced by the code under test):
- `get_all({}, "Switch") == []`; `map_range(5, 2, 2, 0, 100) == 0`
  (BEFORE evidence recorded: the old file raised `KeyError` and
  `ZeroDivisionError` respectively)
- SmokeAlarm on when `level > 0`, `areAlarmSignalsOff` event ignored,
  missing-level fallback
- Meter without `storageFormat` → other sub-sensors still created,
  unrelated sensor present, per-register device/state classes asserted
  on the registry
- kWh without metering name → `measurement`; with metering name →
  `total_increasing`
- `ERROR_VALUE`/`None` → `unknown`; precision `0` honoured; numeric-only
  `state_class`
- YAML sensor unique id with/without `name`
- override reason slugs + translation path present in en/de/cs +
  single `unknown` option
- presence switch constructs from `states["presence"]`; switch with no
  `active` does not raise
- empty Radio skipped with a warning (setup-level); locked radio raises
  `HomeAssistantError`; single state write per event
- number starts unknown, listens on `value`, unit/range from details
- button `state` not overridden (final), press echo → `last_pressed`,
  press sends `pulse`
- `parse_mood_list` pure table; scenes created off the `moodList` stream
  with the LCV2 device link, no duplication on a second delivery,
  listener cleaned up on unload; scene `activate` sends
  `changeTo/<moodId>`

## Verification (real output, from the repo root)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
59 files already formatted

$ pytest -q
Required test coverage of 20% reached. Total coverage: 57.59%
302 passed, 1 deselected, 1 xfailed, 2 warnings in 40.52s
```

(`1 deselected` is the online Mark test; `1 xfailed` is the pre-existing
WP-0.2 pin. Baseline before this WP: `261 passed, 1 deselected,
1 xfailed`.)

BEFORE/AFTER spot-checks performed against the pre-change files:
- `get_all({}, "Switch")` → `KeyError` (before), `[]` (after)
- `map_range(5, 2, 2, 0, 100)` → `ZeroDivisionError` (before), `0` (after)
- YAML sensor `unique_id` `self.uuidAction + self._attr_name` → `TypeError`
  for a nameless sensor (before distinction), `uuidAction` (after)

## VERIFY / live-Miniserver check before merge

None of the findings in this WP carry the **VERIFY** marker in the
catalogue, so no fix is gated on an assumption with a named helper.
Two judgement calls a live Miniserver should still confirm:

1. **PS-09 `_analog_value` sentinel mapping**: the catalogue mandates
   mapping `ERROR_VALUE` (−1) to `unknown`; on a live Miniserver confirm a
   real analog reading of exactly −1.0 (e.g. a negative temperature) is
   never published as `unknown`. The helper is deliberately isolated as
   `_analog_value` so this line is trivial to adjust.
2. **PS-17 scene timing**: `states["moodList"]` in the structure file is a
   state uuid, so "exists immediately after setup" is implemented as
   *immediately upon the first `moodList` stream* (a live Miniserver pushes
   it right after connect). Confirm with one live LCV2 that scene entities
   appear without a perceptible delay after reconnect.

## Follow-ups (discovered, not fixed — outside this WP's file list)

- **Shared-structure-file pollution**: older platforms (light.py
  async_add_devices, climate AC control, media_player audio zone, text.py
  config_entry) still assign runtime objects into their control dict from
  `add_room_and_cat_to_value_values`/raw `get_all` — i.e. into the shared
  `loXon_config.json` dict. `iter_controls` now insulates the seven
  platforms of this WP (shallow copy), but the old paths (light, climate,
  media_player, text) would also need copies or plain local dicts. Belongs
  to the owning WPs/Phase 5.
- **PS-05 in text.py:118** (`self.states["text"]` unguarded in
  event_handler/extra_state_attributes) — text.py is not in this WP's
  file list (it is WP-1.3's remainder); same one-line `.get()` fix applies.
- **`__init__.py` / core hygiene**: the rest of CORE-32 (f-string debug in
  `message_callback`, German comments, unused imports, `_UNDEF`, mutable
  `data={}` default) stays with Phase 5 / WP-5.3 as planned.
- **Fabricated finding IDs seen in the repo (ground rule 1)**:
  `0ccffb8 fix(HVAC): … (CVE-1.10/43, PF-19)` and
  `tests/test_climate_loxone.py` docstrings referencing `CVE-1.10` /
  `CVE-43`. These prefixes are not in the catalogue (valid prefixes:
  API-, CORE-, PS-, PC-, TOOL-). Noted here per the instructions; not
  "fixed" here (out of scope, and the WP-4.4 PR body already flags the
  commit side).
- **`manifest.json` bump**: not done (WP-0.3 mechanism).

## Branch note

The branch is the single per-WP branch provisioned for this run
(`wp/WP-4.5`); no other WP landed on it. The commit message follows the
`fix(<platforms>): … (finding ids)` convention of the plan.

## A Note on Prerequisites

WP-1.1 and WP-1.3 are merged per `git log --oneline -20` at branch start
(`8b2ab6e`/`dfecbc6` for WP-1.1; `947b0be` "entities start unknown, not a
concrete state" as the WP-1.3 landing — the same commit WP-4.2's PR body
cites). That committed WP-1.3 set also *deleted* `_attr_available`
initial-state-from-None switching (PS-02) and `text.py` from the
platforms, which is why this WP does not re-tile initial `_attr_available`
to `False` beyond the WP-1.3 contract. The precondition check passes;
proceeding.

## Assumptions

1. Upstream Loxone direction mentioned in the findings catalog for
   PS-09/PS-15 is `JoDehli/PyLoxone#402 #481 #492`; #292 for the fuzzing PR.
2. "Consumption today"-style resetting counters are not meters
   (name-based, tested). On a per-specific-name basis — this can be revisited
   on top of a curated per-control whitelist rather than name
   sniffing.
3. The option `generate_scenes` stays off-by-default for new installs
   (decision table); the *code path* it gates is now safe to run.
4. Coverage — the new modules keep the repo over the 20% floor (57.59%).
