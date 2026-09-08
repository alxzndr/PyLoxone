# Remediation plan v2 — actual `PyLoxone` repo (monolithic)

> **Why this exists:** the original
> `docs/review/2026-09-remediation-plan.md` was generated against a *modular*
> Loxone integration (one file per control type: `mode_helper.py`,
> `temperature_sensor.py`, `valve_sensor.py`, `fan_mode_class.py`,
> `scene_*.py`, …). **Those files have never existed in this repo** (verified
> via `git log --all`) — this repo is the older *monolithic* integration where
> all of that lives inside `climate.py` / `fan.py` / `sensor.py` / `scene.py` /
> `switch.py`. The original line numbers are therefore wrong. This plan is the
> re-audit: it grades each finding and points it at the **actual file:line** in
> this repo. Phase 0 (0.1/0.2/0.3) + Phase 1 (WP-1.1) are already done
> and their file lists matched; this plan only reconciles the module WPs.

## Verdict per module (what actually carries over here)

### WP-1.2 · `climate.py` (all HVAC: RoomController, RoomControllerV2, AcControl)
The plan's "split into mode_helper.py / temperature_sensor.py / …" is **moot**
(single file). The real, verified issues:
- **CORE-43/CORE-11 (KeyError on missing state):** `LoxoneAcControl.get_state_value`
  (climate.py:780) does `self._stateAttribUuids[name]` — **KeyError** if a
  control lacks that state key (e.g. an AcControl without `mode`). Make it
  `.get(name)`-default `None` (like `RoomControllerV2.get_state_value`
  already does at :405). Same class of bug: `hvac_mode` reads
  `get_state_value("mode")`.
- **PF-19 (degree-symbol zero/falsy):** `temperature_unit` (climate.py:880)
  `self.details["format"].find("°")` — a format beginning in `"°"` returns `0`
  (falsy) → mislabeled as F. Rewrite with `"°C" in s / "°F" in s` (like the
  V2's `temperature_unit` at :467 already does correctly). Also `.find` on a
  missing `format` key → KeyError (guard it).
- **C-31 (dead line + KeyError):** `climate_handler` (:400-401) sets
  `self._demand = event.data.get("value", 0)` then **overwrites** with
  `self._demand = event.data["value"]` (KeyError if absent). Delete the second line.
- **C-43 (mode → ValueError):** `OperatingMode.from_mode` raises
  `ValueError` on unknown value; wrap in `event_handler` so one bad state
  can't kill the update.
- **Low-pp (duplicated field set):** `_attr_min_temp`/`_attr_max_temp` hardcoded
  on both controllers; the fixture's `details.temperatureRange` is unused.
- **Action/fan (PS-19/PF-05):** `sum()` output on the feature set + `hvac_action`
  — verify the AC `hvac_action` (not defined on AcControl at all) and
  RoomController `hvac_action` render. Target: no "no hvac_action" attribute.
- **No `PLATFORM` string:** the plan's "3× `Platform.CLIMATE` loops" → in this
  repo it's `get_all(loxconfig, "IRoomControllerV2")` etc. at :112-139. Keep as
  a `CLIMATE_TYPES` tuple.
- `mode.py` / preset `Mode` track + `PRESET_PAUSED_WINDOW` (:731) — present;
  wave exactly.
- **Acceptance test:** WP-0.2 harness, `mock_entry` + `generate_scenes=False`,
  fixture's `IRoomControllerV2`/`IRoomController`/`AcControl` → zero
  `KeyError`/`ValueError` during set-up + a mode round-trip.

### WP-1.5 · `fan.py` (monolithic — no `fan_mode_class.py`)
- All fans in `fan.py` (SensorFan, MiniFanV2, Ventilation, …). Plan's split
  moot. Verify `fan_speed_list`/`percentage` math + `[key]` KeyError in
  `get_state_value` (fan.py:232, same as AcControl).

### WP-1.6 · valve → `sensor.py` (no `valve_sensor.py`)
- `LoxImi` etc. inside `sensor.py`. Same `states["value"]` KeyError class.

### WP-1.3 · scenes → `scene.py` + `light.py` + `lights/`
- `scene.py` (NONE ACCOUNTING / SceneCT) is monolithic; plan's
  `scene_accounting.py`/`scene_clut3.py` moot. `light.py`/`lights/*.py` do
  exist (WP-1.3's `lightcontroller.py`/`colorpickers.py`/`switch.py`/`dimmer.py`
  are real). The issues (Phase Array KeyError, colorpicker str-enum switch,
  SceneCT `self.name` lowering (:91 — still present, LOW-02 legacy)) apply
  directly.

### WP-1.4 · sensor → `sensor.py` (monolithic — no `lightstype.py`/`lowbat_sensor.py`)
- `states["value"]` KeyError (sensor.py:466 `details["format"]`),
  `_parse_digits_after_decimal`, refresh.

### WP-1.7 · text — verified `platformText` exists in `text.py` (do the
  `Platform.TEXT` registration + sensory, same as the plan).

### WP-1.8 · `helpers.py` power-sensitive — verified (the sensors) applies.

### WP-2.x coordinate: `coordinator.py` + `connection.py` (real paths)
- PC-5 tmode insert, cookie id, O-30 fresh copy, coords KM, per-user access —
  the monolithic `coordinator.py`/`connection.py` contain the named bugs.

## Moot (findings that don't apply to this snapshot)
- All "REF-08/REF-09 split file X" wording → target file absent (single file).
- `ALARM-0n`/`LOW-25`/`OV-1n`/`Light-XX` (color seasons not in this tree)
  and `text-xx` beyond the verified `platformText`.

## Execution note
Each WP below is done against the **actual** file above (not the plan's file
list). Split-file refactor is OPTIONAL and deferred unless a finding
specifically needs it; the safe default is a monolithic-section fix + a
`tests/test_<platform>_lo.go` regression (merging the platform's keys/key
presence assertions into the WP-0.2 xfail set already in
`tests/test_contracts.py` where applicable).
