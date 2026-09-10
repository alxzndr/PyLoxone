# WP-6.5 — Meter family: `EnergyManager`, `EnergyManager2`, `PowerUnit`, `Wallbox`

Branch: `fix/wp-6-5-meter-family` (started as `wp/WP-6.5` from
`73f28d6` == `origin/master`; renamed to the plan's `fix/wp-<id>-<slug>`
pattern, same as WP-6.6).
Prerequisite **WP-5.3 is present on master** (commit `862f688`,
"widen the blocking rule set and sweep dead code").

Catalogue entry behind this WP: **PS-26** ("Control types that are
cheap to add with existing patterns") — specifically the line
"`EnergyManager`/`EnergyManager2`/`PowerUnit`/`Wallbox` (Meter sub-state
loop generalises)". There is **no upstream issue number** for these
control types (nothing to port; the plan's "check upstream PRs first"
was done — the known upstream PRs #512/#513/#515 cover other packages).

## Scope, per the plan

Feature gaps (Phase 6, optional), following the Phase 4 platform
pattern: extract pure helpers, guard every `states[...]`/`details[...]`
lookup with `.get()`, add fixture controls to `tests/fixtures/LoxAPP3.json`,
add setup + state-update tests. Acceptance: the new entities appear
after setup from the fixture, update on a fed state event, and
`tests/test_contracts.py` still passes.

## Assumptions (stated rather than asked, per the package brief)

1. The four newer metering controls advertise the **same register set
   as the legacy `Meter`** (`actual`, `total`, `totalNeg`, `storage`,
   with `actualFormat`/`totalFormat`/`storageFormat` formats). The
   catalogue says the "Meter sub-state loop generalises" to them,
   i.e. it treats the register set as identical. No structure file from
   a live Miniserver with these controls was available at the time of
   writing. The guards make this assumption **safe**: a control whose
   registers have different names simply yields fewer (even zero)
   sub-registers — missing data, never a crash or a misclassified
   entity. See the VERIFY section below.
2. Device model string: the legacy `Meter` keeps its historical
   behaviour (free-form `details.type` → `"<Type> Meter"`, otherwise
   `"Meter"`); the four newer types are modelled by their control type
   name (`EnergyManager`, `EnergyManager2`, `PowerUnit`, `Wallbox`).

## What changed, by file

- `custom_components/loxone/sensor.py`
  - New constant `METER_FAMILY_TYPES = ("Meter", "EnergyManager",
    "EnergyManager2", "PowerUnit", "Wallbox")`.
  - New pure helper `meter_device_model(control)`: the per-type model
    string (assumption 2 above).
  - New pure helper `meter_device_info(control, config_entry)`: the
    shared `(DOMAIN, uuidAction)` device info all registers of one
    control carry (PS-20, same pattern as the IRoomControllerV2 and
    presence sub-sensors); `None` without a usable `uuidAction` so a
    register falls back to its own device.
  - New pure helper `meter_sub_sensor_kwargs(control, config_entry)`:
    the generalised sub-state loop body — one `LoxoneMeterSensor`
    kwargs dict per *advertised* register, in `METER_STATE_CLASSES`
    order (Actual → power/measurement, Total → energy/
    total_increasing, Total Neg → energy/total_increasing, Level →
    energy/measurement, per PS-21). Every `states`/`details` lookup is
    `.get()`-guarded (PS-08 posture); a state value that is not a
    usable uuid string is skipped; a missing format key degrades only
    that register to the neutral `"%.1f"` fallback (`totalNeg` shares
    the `totalFormat` key, as `METER_FORMAT_KEYS` already said).
  - The setup loop previously iterated `iter_controls(hass,
    config_entry, "Meter")` with the register construction inlined; it
    now iterates `list(METER_FAMILY_TYPES)` and appends
    `LoxoneMeterSensor(**subsensor)` for each helper result, with the
    same per-control try/except (PS-08).
  - `LoxoneMeterSensor.create_device_info_from_sensor` (module-private
    dead entry point, only referenced from the setup loop) is removed;
    its logic lives in `meter_device_model`/`meter_device_info`.
    Behaviour for existing `Meter` controls is preserved: same
    registers, same formats, same shared device, same names
    (the entity-name snapshot diff below is *additive only*).
- `tests/fixtures/LoxAPP3.json` — four family members, each with a
  *different* register set so the per-register guard is exercised, not
  assumed:
  | control | type | room | advertised registers |
  |---|---|---|---|
  | Energy Manager | EnergyManager | Garden | actual, total, totalNeg, storage |
  | Energy Manager 2 | EnergyManager2 | Hall | total, totalNeg, storage (no `actual`) |
  | Power Unit | PowerUnit | Garden | actual, total, storage (no `totalNeg`) |
  | Wallbox Garage | Wallbox | Garden | actual, total |
- `tests/test_wp65_meter_family.py` — new regression tests (8):
  - `test_meter_device_model_literals` — model strings as literals.
  - `test_meter_sub_sensor_kwargs_literal` — full kwargs list as
    literals (uuids, names, formats, (device_class, state_class)
    pairs, parent link, shared device).
  - `test_meter_sub_sensor_kwargs_only_advertised_registers` — fewer
    registered registers yield fewer sub-sensors.
  - `test_meter_sub_sensor_kwargs_guards` — missing/non-dict
    `states`, non-string state uuids, missing `details`, a single
    missing format key, missing `uuidAction` (device fallback).
  - `test_meter_device_info_literals` — shared device identifier/name/
    model/room as literals, plus the `None` fallbacks.
  - `test_get_all_family_types` — the family type list resolves all
    five types and nothing else.
  - `test_meter_family_appears_and_updates` (HA harness, acceptance) —
    after setup: all 12 registers exist (4/3/3/2 per control, no
    phantoms for unadvertised registers); fed state events update the
    right register and do not leak across sibling controls; each
    control's registers sit on exactly **one** shared device with the
    right model.
  - `test_broken_wallbox_does_not_abort_platform` — a Wallbox with
    corrupted (`details` a string) still yields guarded kwargs, and a
    full fixture setup reaches LOADED.
- `tests/snapshots/entity_names.json` — regenerated; the diff against
  the pre-WP line is **exactly 12 added rows, nothing else** (attached
  below).
- `CHANGELOG.md` — Unreleased → Added line for the Meter family.
- `docs/review/LIVE-MINISERVER-CHECKS.md` — new item 16 (VERIFY below).

## VERIFY — live-Miniserver check required before merge

**Item 16 of `docs/review/LIVE-MINISERVER-CHECKS.md` (new):** the
assumption that `EnergyManager`/`EnergyManager2`/`PowerUnit`/`Wallbox`
advertise the same register states as the legacy `Meter` (`actual`,
`total`, `totalNeg`, `storage` + `*Format` details). Implemented behind
one named helper (`meter_sub_sensor_kwargs` + the three tables
`METER_STATE_CLASSES`/`METER_FORMAT_KEYS`/`METER_NAME_SUFFIX`), so a
real structure file with different register names is a one-table
correction. The tests assert the *intended* semantics (per-register
guard, naming, classes, shared device), which hold regardless of which
register names the Miniserver turns out to publish. **Do not merge
against upstream until item 16 has been run against a real Miniserver
or a real structure file with one of the four control types.**

## Definition of done evidence (run from the repo root)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
74 files already formatted

$ pytest -q
...
Required test coverage of 20% reached. Total coverage: 58.84%
558 passed, 1 deselected in 68.28s (0:01:08)
```

Before the change the suite was 550 passed / 1 deselected; the 8 new
tests are the delta. With `custom_components/loxone/sensor.py` stashed
reverted, `tests/test_wp65_meter_family.py` fails collection (it
imports the WP-6.5 helpers), i.e. the regression tests fail before the
change and pass after.

Entity-name snapshot diff (complete, 12 added rows):

```diff
+   sensor.garden_energy_manager_actual   | Energy Manager     | EnergyManager
+   sensor.garden_energy_manager_level    | Energy Manager     | EnergyManager
+   sensor.garden_energy_manager_total    | Energy Manager     | EnergyManager
+   sensor.garden_energy_manager_total_neg| Energy Manager     | EnergyManager
+   sensor.garden_power_unit_actual       | Power Unit         | PowerUnit
+   sensor.garden_power_unit_level        | Power Unit         | PowerUnit
+   sensor.garden_power_unit_total        | Power Unit         | PowerUnit
+   sensor.garden_wallbox_garage_actual   | Wallbox Garage     | Wallbox
+   sensor.garden_wallbox_garage_total    | Wallbox Garage     | Wallbox
+   sensor.hall_energy_manager_2_level    | Energy Manager 2   | EnergyManager2
+   sensor.hall_energy_manager_2_total    | Energy Manager 2   | EnergyManager2
+   sensor.hall_energy_manager_2_total_neg| Energy Manager 2   | EnergyManager2
```

(`unique_id` per row is the register's state uuid from the fixture, e.g.
`36333734-01e1-...` for `garden_energy_manager_actual`.)

## Follow-ups (outside this package's scope)

- Energy-dashboard wiring: the `total`/`totalNeg` registers are
  `energy`/`total_increasing` and thus energy-dashboard eligible, but
  routing them into an actual HA energy dashboard (grid/solar/battery
  flow configuration per device role) is a separate feature, not part
  of this WP.
- `details.type`-based model strings for the *newer* four types (if a
  real Miniserver turns out to carry one, `meter_device_model` is the
  one place to extend).
- If item 16 finds extra registers these controls advertise beyond the
  Meter set (e.g. an `actualPos`/`actualNeg` split), extend
  `METER_STATE_CLASSES`/`METER_FORMAT_KEYS`/`METER_NAME_SUFFIX` — one
  place.
