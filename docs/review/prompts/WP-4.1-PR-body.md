# WP-4.1: Climate platform correctness (PC-02/10/12/13/16/20/23/24/25/26/28/32/41)

## What changed (by file)

- `custom_components/loxone/climate.py`
  - **Pure helpers extracted first** (plan step 1): `temperature_unit_from_format(fmt)`,
    `legacy_mode_to_hvac` / `hvac_to_legacy_mode` (legacy table
    `LEGACY_ROOM_CONTROLLER_MODES`, single source for read *and* write),
    `LOXONE_TO_HVAC_V2` read table + `HVAC_TO_LOXONE_V2` write table +
    `hvac_to_loxone()`, `capabilities_to_hvac_modes(bits, range_allowed)`,
    and `plan_set_temperature(op_mode, active, kwargs, state) -> list[str]`.
    Every fix below routes through one of them; the old `OPMODETOLOXONE`
    dict and the three divergent `temperature_unit` properties are gone.
  - **PC-02** (#416) — `set_temperature` in BUILDING_PROTECT with only
    `target_temp_low` used to raise `NameError: comfort_cool` (the name
    was only bound in the high-target block). The planner now compares
    against `frostProtectTemperature` as intended.
  - **PC-10** (#479 #398) — `AcControl.get_state_value` mirrors the V2
    signature (`.get(name)` with default, `None` instead of `KeyError`);
    `hvac_mode`/`fan_mode`/`swing_mode`/`target_temperature` all read
    safely through it.
  - **PC-12** (#416) — `RoomControllerV2.target_temperature` no longer
    falls off the end in dual modes: `tempTarget` with the comfort
    temperature as fallback before the first value arrives.
    `supported_features` now advertises `TARGET_TEMPERATURE` **or**
    `TARGET_TEMPERATURE_RANGE`, never both (previously both, with the
    plain one returning `None` in range mode).
  - **PC-13** — unknown `operatingMode`/`activeMode` stream values keep
    the previous state, log a WARNING once per distinct value (repeats
    go to DEBUG) and never abort the event loop.
  - **PC-16 (climate lines)** — `_parse_mode_list()`: one tolerant JSON
    list parser for `timerModes`/`fanspeeds`/`airflows`;
    `details["timerModes"]` was a hard subscript that aborted the whole
    climate platform — now read via `.get()` and copied; entity
    construction no longer injects the "stop" entry into the shared
    structure dict; remaining hard state/details indices converted to
    guarded lookups.
  - **PC-20** — the schedule preset sends `setOperatingMode/0`
    (was `setOperationMode/0`, an unknown command).
  - **PC-23** (#398) — one `temperature_unit_from_format` shared by all
    three platforms; `"°C"` at index 0 reads as Celsius (no more
    `find("°")` truthiness) and the legacy platform's bare `C`/`F`
    fallback is preserved.
  - **PC-24** (#398) — `AcControl` advertises `FAN_MODE`/`SWING_MODE`
    only when the control has `fanspeeds`/`airflows` states; the lists
    are parsed once in `__init__` (no per-property `json.loads`),
    `fan_modes` / `swing_modes` return `[]` instead of `None`, and
    `set_fan_mode` / `set_swing_mode` never send `setFan/None` /
    `setAirDir/None` for unknown names.
  - **PC-25** — `AcControl.set_hvac_mode(OFF)` sends only `off` and
    returns (no follow-up `setMode/1`).
  - **PC-26 (VERIFY)** — the legacy `IRoomController` read map and the
    write dispatch now share `LEGACY_ROOM_CONTROLLER_MODES`
    (0=Auto, 1=Heat, 2=Cool, 3=Heat/Cool, 4=Off as documented in the
    code); the V2 pair `LOXONE_TO_HVAC_V2` / `HVAC_TO_LOXONE_V2` keeps
    the writer's explicit codes (`OFF -> -1`, `HEAT_COOL -> 3`,
    `HEAT -> 4`, `COOL -> 5`). Read/write round-trip is pinned by tests.
    V2 `hvac_modes` is now derived from the `possibleCapabilities`
    bits via `capabilities_to_hvac_modes` instead of a hardcoded five,
    which was the source of the contradiction with the legacy table.
  - **PC-28** — `hvac_action` falls back to the controller's own
    `openWindow`/`prepareState`/`valveHeat`/`valveCool` states when no
    `ClimateController` demand event has arrived (`_demand` starts as
    `None`; the unguarded `if self._demand == -1` duplicate line from
    `7561247` is gone).
  - **PC-32** — `preset_mode` returns the stable literals
    `PRESET_FIXED` / `PRESET_FIXED_DYNAMIC` for active modes 14/112
    (the old `get_mode_from_id` hole returned `None`), and
    `preset_modes` is a constant (no more dynamic removal of
    `schedule` that could remove the *current* value from its own
    option list). The extra names were added to the translations.
  - **PC-41 (climate lines)** — every f-string log call converted to
    lazy placeholder logging; the nested-same-quote f-string with the
    suspicious `//` (`7561247` line 567) is gone — the FIXED_DYNAMIC
    override command now goes through the planner's documented
    `override/<temp*2560+112>/<temp>` encoding (see VERIFY 3); the
    duplicate assignment at 408-409 is gone. `async_setup_platform`
    returning `True` under `-> None` is intentionally left: its
    deletion is WP-3.1's scope (the six stubs + `PLATFORM_SCHEMA`).
- `custom_components/loxone/sensor.py`
  - `LoxoneClimateController` ONLY: the redundant
    `self.hass = kwargs["hass"]` is removed (`LoxoneEntity` receives it
    from the platform). No other sensor-class changes (WP-4.5 file
    scope respected).
- `tests/test_climate.py` (new, 72 tests) — parametrised
  `plan_set_temperature` coverage of every branch (incl. the PC-02
  `target_temp_low`-alone BUILDING_PROTECT NameError regression, the
  ECONOMY 0.5 clamp floor, equal-value silencing, and the no-range
  manual fallback); mode-table round-trips and OFF codes;
  `capabilities_to_hvac_modes`; `temperature_unit_from_format` (10
  literal cases incl. `"°C"` at index 0); entity-level dispatch,
  preset literals, and full-entry HA-harness setups with the WP-0.2
  fixture (AcControl **without** `fanspeeds`, structure-file
  non-mutation, `hvac_action` from a valve feed without a
  ClimateController event). All platform tests run against a fake bus;
  no expected value is produced by the code under test.
- `tests/test_climate_loxone.py` — WP-1.2's `fan/swing defaults` test
  updated to the intended PC-24 contract (`fan_mode` `None`,
  `fan_modes` `[]`, FAN/SWING not advertised) replacing the fake
  "Auto" default it previously pinned.
- `tests/fixtures/LoxAPP3.json` — the `IRoomControllerV2` entry now
  carries `valveHeat`/`valveCool`/`isPreparing` state entries
  (unique, hand-generated fake uuids) so the PC-28 valve fallback is
  exercisable from the fixture.
- `custom_components/loxone/translations/{en,de,cs}.json` —
  `preset_mode` states `fixed` / `fixed_dynamic` added for the stable
  preset literals (PC-32 "add the extra names to translations").
- `CHANGELOG.md` — entry under *Unreleased*.

## Acceptance (all verified; local, offline session — no `gh`)

- Parametrised planner tests cover every branch incl. `target_temp_low`
  alone in BUILDING_PROTECT (NameError regression). Reproducer run
  against the pre-WP-4.1 module, hand-computed expectations:

      $ <old climate.py from git HEAD, driven the same way as the new code>
      PC-02 old: NameError -> cannot access local variable 'comfort_cool'
      PC-02 new: commands = ['setecoplusmintemperature/8.0']
      PC-12 old: dual BUILDING_PROTECT target_temperature = None
      PC-12 new: dual BUILDING_PROTECT target_temperature = 21.0 (comfort fallback)
      PC-12 new: advertised temperature features -> ['RANGE']   (not TARGET+RANGE)
      PC-25 old: commands = ['off', 'setMode/1']
      PC-25 new: commands = ['off']

- `"°C"` -> Celsius: `test_temperature_unit_from_format` case `("°C",
  CELSIUS)` and the AcControl/V2 property tests.
- Unknown `activeMode` leaves the previous state and does not raise:
  `test_v2_unknown_mode_keeps_state_warns_only` (plus pre-existing
  WP-1.2 `test_v2_unknown_operating_mode_logs_not_crash`).
- `target_temperature` never None while `TARGET_TEMPERATURE` is
  advertised: `test_v2_target_temperature_falls_back_to_comfort`
  (tempTarget-first, comfort fallback; range mode advertises only
  RANGE where dual targets are the advertised surface).
- AcControl from the fixture without `fanspeeds` sets up and writes
  state: `test_accontrol_from_fixture_without_fanspeeds`.

## Verification commands (run from the repo root)

```
$ .venv/bin/ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!
$ .venv/bin/ruff format --check .
61 files already formatted
$ .venv/bin/pytest -q
426 passed, 1 deselected, 1 xfailed in 41.27s   (coverage floor 20% reached)
```

## VERIFY — live-Miniserver check required before merge

1. **PC-26**: the legacy `IRoomController` table (0=Auto, 1=Heat,
   2=Cool, 3=Heat/Cool, 4=Off) is exactly what the pre-WP-4.1 code
   documented, but the V2 enum skews to the named-value side
   (`3=MANUAL_HEAT_COOL, 4=MANUAL_HEAT, 5=MANUAL_COOL`); if the V2 side
   is right, a V1 `set_hvac_mode(OFF)` would have been sending manual
   heating. Read and write now share one table and round-trip, but the
   V1 code-for-OFF needs confirming against a real structure file.
2. **PC-12**: the comfort-temperature fallback for `target_temperature`
   before the first stream value is an intended-semantics choice.
3. **PC-41/PC-02**: the FIXED_DYNAMIC override encoding
   `override/<temp*2560+112>/<temp>` (the old `//` was dropped; it
   never changed the value, but confirm the command shape on a live
   Miniserver).

## Follow-ups (discovered, out of this package's scope)

- `PRESET_FIXED` / `PRESET_FIXED_DYNAMIC` live in `climate.py` because
  `const.py` is owned by other WPs (parallel-safe). A one-line commit
  to move them to `const.py` is wanted once the file is free — or in
  WP-5.x.
- `AcControl` device info still uses `get_or_create_device` (the
  registry-oriented replacement is WP-3.3's scope).
- The L~C~ entry in `tests/fixtures/LoxAPP3.json` carries many
  room-controller-shaped state entries (e.g. `valveHeat`, `frostProtectTemperature`
  on an `AcControl`) that look like a fixture copy-paste; the fixture
  file is owned by WP-0.2 and was left as decoded — the new code reads
  only the entries it needs.
- `test_accontrol_fan_and_swing_defaults_without_states` in
  `tests/test_climate_loxone.py` was updated to the intended PC-24
  contract; whoever maintains that file should keep the new
  expectation.
- `async_setup_platform` (in it `-> None`, returns `True`) stays —
  WP-3.1 deletes the stub and `PLATFORM_SCHEMA`.

## Assumptions

- `AcControl.hvac_mode` keeps the `status` state as the on/off gate
  (pre-WP-4.1 behaviour); a control without a `status` state now reads
  `OFF` instead of `KeyError`.
- Legacy `set_temperature`'s `currHeatTempIx`/`currCoolTempIx`
  indirection is untouched (not called out by the catalogue).
- No `manifest.json` version bump (WP-0.3 owns releases).
