# Agent prompt — WP-4.5: Sensor, binary sensor, switch, select, number, button, scene

You are implementing work package **WP-4.5** of the PyLoxone remediation plan.
Repository: `/Users/alexandergeeraerts/github/PyLoxone` (Home Assistant custom integration for Loxone Miniservers).
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 4
- Prerequisite packages that must be merged first: WP-1.1, WP-1.3
- Parallel-safe with the other WP-4.x packages except that it owns get_all/map_range/iter_controls in helpers.py; do not edit __init__.py, coordinator.py or pyloxone_api/.
- Before starting, run `git log --oneline -20` and check whether prerequisite packages have landed. If a prerequisite is missing, stop and report; do not re-implement it.

## Rules (from the plan)

1. **Branch per WP** from `master`: `fix/wp-<id>-<slug>`. Do not mix WPs in one branch.
2. **Read the finding entries** listed under the WP, then the code they point at. Line numbers are
   for commit `7561247`; re-locate if the file has moved on.
3. **Stay inside the WP's file list.** If a fix needs a change elsewhere, note it in the PR body as a
   follow-up rather than expanding scope. Two WPs marked *parallel-safe* never touch the same file.
4. **Tests are part of the WP.** Every behavioural fix ships with a regression test named in the
   package's acceptance criteria. Pure-function tests go in `tests/`; HA-harness tests use the
   fixtures from WP-0.2.
5. **Before finishing** run, from the repo root, and paste the results into the PR body:
   ```
   ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
   ruff format --check .
   pytest -q
   ```
   A WP is not done while any of these fail.
6. **Commit messages** reference the finding IDs (`fix(cover): send stop on Window stop_cover (PC-07)`)
   and the upstream issue where one exists (`Fixes JoDehli/PyLoxone#413`). Add a line to
   `CHANGELOG.md` (created in WP-0.3) under *Unreleased*.
7. **Items marked VERIFY** in the catalogue must not change behaviour on assumption. Implement the
   fix behind a clearly named helper, write the test for the *intended* semantics, and flag in the
   PR that a live-Miniserver check is required before merge.
8. **Do not bump `manifest.json` version** inside a WP; releases are cut separately (WP-0.3 sets up
   the mechanism).
9. **No new `# noqa` to make a check pass.** Silencing a lint rule to turn CI green is forbidden
   for every package. The single exception is the set of markers **WP-0.1** lays down — 6×`S307` →
   WP-1.2, 6×`RUF006` → WP-2.2, 2×`RUF006` → WP-3.1 — each written as
   `# noqa: <RULE>  # TODO(WP-x.y)` with the owning package id. No other `# noqa` may be added.
   For the owning package, clearing is part of its definition of done: removing the marker
   *and* the code that caused it, such that
   `grep -rn "TODO(WP-<your id>)" custom_components/` returns nothing when it finishes. If a rule
   genuinely cannot be satisfied, say so in the PR body instead of silencing it.

## Decisions already taken (do not re-litigate)

| Topic | Decision | Rationale |
|---|---|---|
| Minimum HA version | Raise `hacs.json` floor to **2026.7.0**; README says the same | `sensor.py` already needs `UnitOfRatio` (CORE-25); a shim buys little since requirements pin 2026.8.1 |
| Reconnect architecture | In-place reconnect inside `LoxoneConnection` with a connection-state callback; entities stay alive and flip `available` | Reload-everything is the root of #475/#491 (API-09, CORE-05) |
| State fan-out | Coordinator holds one bus listener and dispatches per uuid via `async_dispatcher_send`; the public `loxone_event` bus event keeps firing for user automations | CORE-27; keeps README's recorder advice valid |
| Outbound commands | Entities call their coordinator directly; the `loxone_send`/`loxone_send_secured` bus listeners stay but filter on "uuid belongs to this Miniserver" | Multi-instance correctness (#491) without breaking external users |
| Credentials location | Move host/port/user/password/verify_ssl to `ConfigEntry.data` in entry version 5; options keep only preferences | HA convention; needed for reauth and diagnostics redaction |
| Auto-groups | Keep, but fix (CORE-15, PS-14) and gate behind an option defaulting to **off** for new installs | Existing users rely on them; groups are a legacy idiom |
| YAML `loxone:` block | Delete `async_setup` import attempt and `CONFIG_SCHEMA`; register a repair issue if the key is present | Import step never existed (CORE-19); YAML *sensors* (`platform: loxone`) stay |
| `has_entity_name` | Adopt in Phase 5 only, with a release note, because entity IDs will change | User-visible rename; keep it out of bug-fix releases |
| Dead `pyloxone_api` files | Delete `api.py`, `helper.py`, `__main__.py` CLI stays but fixed | Nothing imports them (API-23) |

## Your work package (verbatim from the plan)

### WP-4.5 Sensor, binary sensor, switch, select, number, button, scene
- **Findings**: PS-04, PS-05, PS-06, PS-07, PS-08, PS-09 (remainder), PS-15 (remainder), PS-16, PS-17, PS-19, PS-21, PS-24, PS-25, CORE-22, CORE-32 (`map_range`/`get_all` hardening from the fuzzing PR).
- **Files**: `sensor.py`, `binary_sensor.py`, `switch.py`, `select.py`, `number.py`, `button.py`, `scene.py`, `helpers.py` (`get_all`, `map_range`, `iter_controls`), `tests/test_simple_platforms.py` (new).
- **Acceptance**: a `SmokeAlarm` fixture reports `on` when `level > 0` and ignores `areAlarmSignalsOff`; a Meter without `storageFormat` still yields the other sub-sensors; Meter registers have the expected device/state classes; `override_reason` states are slugs and translate; a `Radio` without outputs is skipped with a log; scenes exist immediately after setup with a device link; YAML sensor without `name` has a unique id; `get_all({}, "Switch") == []`; `map_range` with equal bounds does not divide by zero.

## Findings you are fixing (verbatim from the catalogue)

### PS-04 [high] Smoke alarm reads "signals muted" instead of alarm level; broken `if`/`elif`; unguarded `KeyError`
- Where: `binary_sensor.py:110-124` (`areAlarmSignalsOff` used for `SMOKE`; second `if` should be `elif`; `self.states["areAlarmSignalsOff"]` unguarded aborts the whole platform; `InfoOnlyDigital` uses `uuidAction` instead of `states["active"]`).
- Fix: `elif` chain, `.get()`, use `level` for smoke, per-control try/except in setup.
- Effort: S

### PS-05 [high] Unguarded `self.states[...]` in `event_handler`/`extra_state_attributes`
- Where: `switch.py:240, 290`; `select.py:153, 183`; `button.py:115`; `number.py:129`; `text.py:118`. `LoxoneIntercomSubControl` is built from unvalidated `subControls` (`switch.py:59-75`) — a missing `active` raises on every event.
- Fix: resolve state uuids once in `__init__` with `.get()`; skip the entity with a log when missing.
- Effort: S

### PS-06 [high] LightControllerV2 presence switch: guard and constructor read different keys
- Where: `switch.py:81-84` (`switch_entity.get("presence")` top-level) vs `364-373` (`kwargs["states"]["presence"]`). Either dead code or a `KeyError` that aborts the platform. `self.name = ...` at 368 assigns over a `cached_property`.
- Fix: `if "presence" in switch_entity.get("states", {})`; use `_attr_name`.
- Effort: S

### PS-07 [high] YAML sensor: `unique_id` raises `TypeError` without `name`; `value_template` is broken
- Where: `sensor.py:46-54` (`CONF_NAME` optional), `318-321` (`self.uuidAction + self._attr_name`), `191-206` (`CONF_VALUE_TEMPLATE` not in schema → arrives as `str` → `value_template.hass = hass` raises; template never applied anyway).
- Fix: fall back to `uuidAction` for the unique id; either add `cv.template` and render it, or delete the three lines.
- Effort: S

### PS-08 [high] One Meter without `storageFormat`/`totalFormat` aborts the entire sensor platform
- Where: `sensor.py:237-256` (`sensor["details"][format_key]` unguarded), `448` (`self.details["format"]`).
- Fix: `.get(format_key, "%.1f")`; per-control try/except in `async_setup_entry`.
- Effort: S

### PS-09 [medium] `ERROR_VALUE`/`None`/non-numeric values unhandled; rounding helper dead; blanket `MEASUREMENT`
- Where: `sensor.py:463-471` (unmatched units get `MEASUREMENT` even for text values → HA `ValueError`), `491-505` (`_get_lox_rounded_value` and `self._format` never used; `available` re-runs the `state` pipeline). `const.py:31 ERROR_VALUE = -1` is used nowhere, so `-1` is published as a real reading.
- Fix: map `ERROR_VALUE`/`None` to `None`; set `state_class` only for numeric values; drop the `available` override; use or delete the rounding helper.
- Effort: S · Upstream: #402 #492 #481

### PS-15 [medium] `number.py`: string sentinel as `native_value`, wrong state uuid, unguarded details, no unit/device class
- Where: `number.py:57-70` (`_state = STATE_UNKNOWN` returned as `native_value`; `details["min"/"max"/"step"]` unguarded), `102-119` (listens on `uuidAction` while advertising `states["value"]`; `schedule_update_ha_state` from the loop), `details["format"]` ignored.
- Fix: `None`; `states.get("value", uuidAction)`; `.get()`; reuse `clean_unit` + `match_sensor_description` for unit/device class/precision.
- Effort: M · Upstream: #492 #481

### PS-16 [medium] `button.py` overrides the `@final` `ButtonEntity.state`
- Where: `button.py:58, 73-84, 105-108` (`# noinspection PyFinal`); press timestamp does not move until the Miniserver echoes `active`; bypasses `RestoreEntity`. Bespoke `DeviceInfo` at 121-130 diverges from other platforms.
- Fix: delete the override; expose the echo as an attribute if wanted.
- Effort: S

### PS-17 [medium] Scene generation is time-delayed, untracked, and scrapes HA internals
- Where: `scene.py:32-106` (`hass.loop.call_later` never cancelled → callback after unload; `hass.data["light"].get_entity`; depends on the fake `device_class` marker; no `_attr_device_info`; `self.name = name`; returns `True` from `-> None`). Moods are already in `states["moodList"]` in the structure file.
- Fix: build scenes directly from `get_all(loxconfig, "LightControllerV2")` with no timer; if a delay stays, `async_call_later` + `async_on_unload`.
- Effort: M

### PS-19 [medium] `select.py`: empty options accepted, `locked` not enforced, double state write
- Where: `select.py:118-130, 142-164, 186`. A `Radio` with no outputs yields `options == []` (HA rejects); `async_select_option` sends while locked; two writes per event; redundant property overrides.
- Fix: skip empty Radios; raise `HomeAssistantError` when locked; single write.
- Effort: S

### PS-21 [medium] Blanket `TOTAL_INCREASING` for every kWh/L-formatted value
- Where: `sensor.py:97-127`. "Consumption today" values that reset, and the Meter `totalNeg` register, get `TOTAL_INCREASING` → spurious spikes in the energy dashboard.
- Fix: explicit per-register descriptions for Meter (`actual`→POWER/MEASUREMENT, `total`/`totalNeg`→ENERGY/TOTAL_INCREASING, `storage`→MEASUREMENT); `TOTAL` or `MEASUREMENT` for plain `InfoOnlyAnalog` unless name/category indicates a meter.
- Effort: M

### PS-24 [low] Duplicated setup boilerplate across all platforms; duplicated unit helper
- Every platform repeats `get_miniserver_from_hass` → `lox_config.json` → `get_all` → `add_room_and_cat_to_value_values`. `LoxoneEntity._clean_unit` duplicates `helpers.clean_unit`.
- Fix: shared `iter_controls(hass, entry, types)` helper; delete the duplicate.
- Effort: M

### PS-25 [low] Dead code and small defects
- `binary_sensor.py:8-23` unused imports (`cv`, `vol`, `CONF_*`, `DOMAIN`, `SENDDOMAIN`); `switch.py:64-65` `_` used as a real variable; `sensor.py:588` magic `14`; `const.py:31 ERROR_VALUE` unused; `sensor.py:454` `if precision:` treats `0` as "none".
- Effort: S

### CORE-22 [medium] `override_reason` sensor translations are dead
- Where: `sensor.py:56-67` (`OVERRIDE_REASONS` values are display strings), `569-591` (no `_attr_translation_key`; runtime `_attr_options.append("Unknown (n)")`; magic clamp to 14 at 588 makes the fallback unreachable). Translations in en/de/cs use slug keys.
- Fix: `_attr_translation_key = "override_reason"`, slug values, a single `unknown` option.
- Effort: S

### CORE-32 [low] Code hygiene in the core files
- f-string logging on the hot path (`__init__.py:370` runs for every message), German comments (`126, 340, 346, 352, 361`), unused imports (`EVENT_COMPONENT_LOADED`, `Platform`, `ATTR_COMMAND`, `DOMAIN_DEVICES`, `ERROR_VALUE`, `MiniServer`, `LoxoneConnection`, `LoxoneException`, `get_miniserver_type`), `_UNDEF` (77), mutable default `data={}` (398), `LoxoneEntity._clean_unit` duplicates `helpers.clean_unit` with a different `%%` fix, commented-out numpy helpers (`helpers.py:58-65`), `map_range` divides by zero when `in_min == in_max`, `get_all` crashes on missing `controls`/`type` (both reported by the Uni Ulm fuzzing PR #292).
- Effort: S


## Definition of done

1. Every acceptance criterion above is met and backed by a test that fails before your change and passes after it.
2. From the repo root, all of these pass and their output is pasted into the PR/commit body:
   ```
   ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
   ruff format --check .
   pytest -q
   ```
3. The commit message(s) cite the finding IDs and upstream issue numbers listed above.
4. Items marked **VERIFY** are implemented behind a clearly named helper with tests for the intended semantics, and the PR body lists them as requiring a live-Miniserver check before merge.
5. A line is added under *Unreleased* in `CHANGELOG.md` (if the file does not exist yet because WP-0.3 has not landed, add the note to the PR body instead).
6. Anything you discovered outside this package's scope is listed under "Follow-ups" in the PR body, not fixed.
7. Finish with a short report: what changed (by file), test results, VERIFY items, follow-ups.
