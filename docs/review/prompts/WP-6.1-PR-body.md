# WP-6.1 — `PresenceDetector` illumination/noise sub-sensors

Branch: `fix/wp-6.1-presence-sensors` (started as `wp/WP-6.1` from
`e79cc12`; renamed to the plan's `fix/wp-<id>-<slug>` pattern).
Prerequisite **WP-5.3 is present on master** (commit `862f688`, "widen the
blocking rule set and sweep dead code").

## Scope, per the plan

PresenceDetector controls publish an `illuminance` (lux) and a `noise`
(dB) state alongside the presence signal (upstream
JoDehli/PyLoxone#461). Both advertised sub-states are now surfaced as
analog sub-sensors (`Illuminance`, `Noise`) on the sensor platform,
following the Phase 4 platform pattern: pure helper, guarded
`.get()` lookups, fixture control already present since WP-0.2, setup
+ state-update tests.

**Upstream check (done first, per the plan):** no upstream PR or
master-side implementation exists for #461 (searched
`JoDehli/PyLoxone` for `illuminance`/`PresenceDetector` sub-sensor code);
this is a genuine feature gap, implemented here from the in-repo
pattern.

### Where the loop lives — deliberate deviation from
`fan.py:81-135` as written

`fan.py`'s pattern builds the sub-sensors with the *fan platform's*
`async_add_entities`. HA pins the domain to the platform, so a
`LoxoneSensor` added there would register as a `fan.*` entity. The
fixture never exercises that path (the Ventilation control
advertises only `presence`), so the defect is dormant but real — see
Follow-ups. For PresenceDetector the analog readings belong to the
**sensor platform**; the loop therefore lives in
`sensor.py::async_setup_entry` alongside the Meter and
IRoomControllerV2 sub-sensor loops, and the sub-sensors carry the
parent control's device identifiers so the device registry keeps them
on the presence device (the PC-04/CORE-20 shared-device linkage).

## What changed, by file

- `custom_components/loxone/sensor.py`
  - `PRESENCE_SUB_SENSOR_SPECS`: `state key -> (entity name, Loxone
    format)`; `illuminance -> ("Illuminance", "%.0f lx")`,
    `noise -> ("Noise", "%.0f dB")`. The fixed format fixes the unit,
    which drives device class via the existing unit table (lux →
    `illuminance`; dB → plain numeric measurement).
  - `PRESENCE_DEVICE_MODEL = "presence"` — the same model string the
    binary_sensor platform stamps on the presence device
    (`self.type`), so both platforms contribute to one merged device.
  - `presence_sub_sensor_kwargs(control, config_entry)` — **pure
    helper**, one `LoxoneSensor` kwargs dict per advertised sub-state;
    short entity name, `parent_id` = parent uuid, `device_info` built
    with `device_info_for(config_entry, uuidAction, name, "presence",
    room)`. Every `states`/`details` lookup is guarded with `.get()`
    and an `isinstance` check (missing/non-dict `states`, missing or
    empty state uuid, missing `uuidAction` → fewer/no entities, never
    an abort).
  - Setup loop for `iter_controls(hass, config_entry,
    "PresenceDetector")` with the per-control skip-and-log guard used
    by every other control loop (PS-08).
- `tests/test_presence_sensors.py` (new)
  - pure-helper tests with **hand-derived literals** (transcribed from
    the fixture): both-state control → 2 dicts in spec order
    (illuminance: `%.0f lx`, noise: `%.0f dB`), parent/unique uuids,
    short names, shared device identifiers/name/model; `states=None`
    → `[]`; no `states` key → `[]`; empty `uuidAction` → `[]`;
    noise-only detector → 1 dict; empty/`None` state uuids → `[]`;
  - `test_setup_creates_illuminance_noise_sub_sensors` — after fixture
    setup the entities exist as
    `sensor.hall_hallway_presence_illuminance` / `..._noise`, on the
    device `Hallway Presence` (model `presence`), the *same* device
    object as the presence binary sensor; `device_class == "illuminance"`
    with unit `lx`, noise has no device class and unit `dB`;
  - `test_setup_presence_detector_without_sub_states_creates_no_sub_sensors`
    — a detector with only `active` yields the binary sensor and no
    crash;
  - `test_sub_sensors_update_on_fed_state_event` — fed
    `illuminance=13` / `noise=42` arrive as the literals `13` / `42`;
    feeding the parent presence signal does not touch the sub-sensors.
- `tests/snapshots/entity_names.json` — +2 rows
  (`sensor.hall_hallway_presence_illuminance`,
  `sensor.hall_hallway_presence_noise`; regenerating under
  `LOXONE_ENTITY_SNAPSHOT_OUT` and hand-reviewing the diff → exactly
  these two rows, both short-named on the Hallway Presence device).
- `CHANGELOG.md` — Unreleased / Added line for #461.

## Evidence: tests fail before, pass after

Pre-change (with `sensor.py` stashed to its pre-WP state):

```
$ pytest -q tests/test_presence_sensors.py --no-cov
ERROR tests/test_presence_sensors.py
ImportError: cannot import name 'presence_sub_sensor_kwargs'
    from 'custom_components.loxone.sensor'
!!! Interrupted: 1 error during collection !!!
```

(i.e. the module under test does not even expose the helper; the
setup/feed behaviour tests could not exist before.) Post-change:

```
$ pytest -q tests/test_presence_sensors.py
10 passed in 0.59s
```

## Gate results (repo root, post-commit)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
71 files already formatted

$ pytest -q
532 passed, 1 deselected in 66.20s (0:01:06)

$ pytest -q tests/test_contracts.py
7 passed in 0.32s

$ grep -rn "TODO(WP-6.1)" custom_components/
(no matches)
```

No `# noqa` added. The ignored document
`docs/review/2026-09-remediation-plan-PyLoxone.md` was not read, and no
finding ID with a non-authoritative prefix appeared in the changed
scope.

## VERIFY / assumptions

The plan carries **no VERIFY mark for WP-6.1**, but two choices
depend on the state *name* rather than a documented format string
(the structure file does not publish per-state formats for these
readings), both fixed conservatively:

1. `illuminance` ⇒ lux (`%.0f lx`) — the Illuminance reading of Loxone
   presence detectors is in lux, and `lx` is already the unit table's
   illuminance key;
2. `noise` ⇒ decibel (`%.0f dB`) — dB for a noise-sensor has no
   device class in HA's unit table, so it stays a plain numeric
   measurement.

**Recommended live-Miniserver check before relying on
real-device readings** (see also the e79cc12 "Miniserver
checklist"): on a device that actually has Light + acoustics
hardware, confirm (a) the Miniserver really publishes
`illuminance` and `noise` under these exact state keys,
(b) values are plain numbers (no string sentinels), (c) the units
match lx/dB so an in-place format tweak is not needed.

## Follow-ups (out of scope, not fixed here)

- **fan.py sub-sensors are domain-bound**: `fan.py:119-191` adds the
  `LoxoneSensor` sub-sensors (Humidity, Air Quality, Temperature)
  through the fan platform's `async_add_entities`, which would pin
  them to the `fan` domain (wrong platform). Dormant in the fixture
  (the Ventilation control advertises only `presence`;
  `hasIndoorHumidity`/`hasAirQuality` are false and no
  `temperatureOutdoor` state exists), so no user-visible bug today —
  should be moved to `sensor.py` with the same pattern used here.
- README/Docs do not yet mention the two new sub-sensors (WP-5.4
territory).
- The PresenceDetector `time` state is deliberately not
  exposed (out of the requested illuminance/noise scope).