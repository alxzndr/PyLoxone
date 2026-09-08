# Agent prompt — WP-4.1: Climate

You are implementing work package **WP-4.1** of the PyLoxone remediation plan.
Repository: **your current working directory**. It is a Home Assistant custom integration for
Loxone Miniservers. Every path in this document is relative to that directory.
Do not `cd` outside it. Do not search the filesystem for another copy of this project: other
checkouts exist, they belong to other people, and writing to one destroys their work.
Use `.venv/bin/python` for Python; it is present in your working directory.
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 4
- Prerequisite packages that must be merged first: WP-1.1, WP-1.3
- Parallel-safe with the other WP-4.x packages. Do not edit __init__.py, coordinator.py, helpers.py or pyloxone_api/.
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

### WP-4.1 Climate
- **Findings**: PC-02, PC-10, PC-12, PC-13, PC-16 (climate lines), PC-20, PC-23, PC-24, PC-25, PC-26 (**VERIFY**), PC-28, PC-32, PC-41 (climate lines).
- **Files**: `climate.py`, `sensor.py` (`LoxoneClimateController` only, `hass` kwarg removal), `tests/test_climate.py` (new).
- **Steps**: extract pure helpers first — `temperature_unit_from_format(fmt)`, `legacy_mode_to_hvac`/`hvac_to_legacy_mode`, `capabilities_to_hvac_modes(bits)`, `plan_set_temperature(op_mode, active, kwargs, state) -> list[str]` — then fix the call sites; `.get()` everywhere; stable preset literals; `hvac_action` fallback from the controller's own states when no demand event has arrived.
- **Acceptance**: parametrised tests over the planner covering every branch incl. `target_temp_low` alone in BUILDING_PROTECT (NameError regression); `"°C"` → Celsius; unknown `activeMode` value leaves the previous state and does not raise; `target_temperature` is never `None` while `TARGET_TEMPERATURE` is advertised; AcControl from the fixture without `fanspeeds` sets up and writes state.

## Findings you are fixing (verbatim from the catalogue)

### PC-02 [high] `NameError: comfort_cool` when only `target_temp_low` is set in building-protect mode
- Where: `climate.py:536-560` (fault at 556; `comfort_cool` only bound in the `target_temp_high` block at 512). Also semantically wrong — compare against `frostProtectTemperature`.
- Effort: S · Upstream: #416

### PC-10 [high] `AcControl.get_state_value` raises `KeyError` for missing states (#479 only partially fixed)
- Where: `climate.py:775-779` (`self._stateAttribUuids[name]`); used by `target_temperature` (887), `fan_mode` (898), `swing_mode` (930), `hvac_mode` (816). `LoxoneRoomControllerV2.get_state_value` (432-436) is the correct version.
- Fix: `.get(name)` with default; mirror the V2 signature.
- Effort: S · Upstream: #479 #398

### PC-12 [high] `RoomControllerV2.target_temperature` returns `None` in dual modes while `TARGET_TEMPERATURE` is advertised
- Where: `climate.py:580-591` (falls off the end for AUTO/MANUAL_HEAT_COOL with COMFORT/ECONOMY/BUILDING_PROTECT), `383-403` (`TARGET_TEMPERATURE` unconditional, `TARGET_TEMPERATURE_RANGE` gated on three conditions).
- Fix: final `return self.get_state_value("tempTarget")`; advertise TARGET **or** RANGE, never both.
- Effort: M · Upstream: #416

### PC-13 [high] Unknown Loxone mode values raise `ValueError` inside `event_handler`
- Where: `climate.py:51-56` (`ActiveMode(base_value)` for values outside `{0,1,2,3,4,14,112}`), `67-73`, `417-430` (exception aborts the loop; entity freezes).
- Fix: try/except, keep previous value, log once; or `_missing_` → `UNKNOWN`.
- Effort: S

### PC-16 [medium] Unguarded `states[...]`/`details[...]` indexing across five platforms
- Where: `cover.py:195, 241-243` (Window `targetPosition` — #501 — and `direction`), `407-413` (Jalousie `shadePosition`), `343-346` (mutates the shared structure dict to inject `autoInfoText`/`autoState`, see PC-36); `alarm_control_panel.py:103,107,119,123,127`; `media_player.py:106,110`; `climate.py:151, 364` (`details["timerModes"]` aborts the whole climate platform), `776`.
- Fix: shared `_state_uuid(name)` helper returning `.get()`; `if (u := ...) and u in e.data`.
- Effort: M · Upstream: #501

### PC-20 [medium] `setOperationMode/0` typo
- Where: `climate.py:727-730` vs `690` (`setOperatingMode`). Selecting the "schedule" preset sends an unknown command.
- Effort: S

### PC-23 [medium] `AcControl.temperature_unit` returns Fahrenheit when `°` is at index 0
- Where: `climate.py:874-881` (`find("°")` truthiness). Two other correct copies exist at 292-305 and 469-486.
- Fix: one shared `_temperature_unit_from_format(fmt)`.
- Effort: S · Upstream: #398

### PC-24 [medium] `AcControl` declares FAN_MODE/SWING_MODE unconditionally; `json.loads(None)`
- Where: `climate.py:742-748, 907-924, 939-957` (`fan_modes`/`swing_modes` return `None`; `set_*` parse `None`; send `setFan/None` on unknown names; JSON parsed three times per property read).
- Fix: compute features from present states; `[]` not `None`; parse once in `event_handler`.
- Effort: M · Upstream: #398

### PC-25 [medium] `AcControl.set_hvac_mode(OFF)` sends `off` then `setMode/1`
- Where: `climate.py:829-857`.
- Fix: return after `off`.
- Effort: S

### PC-26 [medium] Legacy `IRoomController` mode table contradicts the V2 enum in the same file — **VERIFY**
- Where: `climate.py:263-289, 322-338` (`0=Auto,1=Heat,2=Cool,3=Heat/Cool,4=Off`) vs `58-65` (`3=MANUAL_HEAT_COOL, 4=MANUAL_HEAT, 5=MANUAL_COOL`). If V2 is right, `set_hvac_mode(OFF)` sends manual heating. `hvac_modes` hardcodes all five.
- Fix: confirm against a real V1 structure file; single shared table.
- Effort: M

### PC-28 [medium] `RoomControllerV2.hvac_action` is permanently IDLE without a `ClimateController` control (regression from `7561247`)
- Where: `climate.py:361, 405-410, 635-646` (`_demand` fed only by `CLIMATE_EVENT` from `sensor.py:634-637`; line 409 duplicates 408 without the default).
- Fix: fall back to the room controller's own states; delete 409.
- Effort: M

### PC-32 [medium] `preset_mode` can return a value not in `preset_modes`; `preset_modes` is dynamic
- Where: `climate.py:412-415, 695-713` (FIXED=14 / FIXED_DYNAMIC=112 are not in `timerModes` → `None`; `PRESET_SCHEDULE` removed dynamically; condition at 708 simplifies to `not (is_auto and is_overridden)` and `is_auto` includes `MANUAL_HEAT_COOL`).
- Fix: stable literals for fixed modes; constant `preset_modes`; add the extra names to translations.
- Effort: M

### PC-41 [low] Docstrings, comments, formatting
- `fan.py:1`/`alarm_control_panel.py:1` say "Interfaces with Alarm.com alarm control panels"; f-string logging (`light.py:108,130,150`, `climate.py:751`, `media_player.py:83,90`); `climate.py:408-409` duplicate assignment; `cover.py:527` `shade_postion_as_text`; `climate.py:567` nested same-quote f-string (3.12+) with a suspicious `//`; `-> None` functions returning `True` (`climate.py:95`, `cover.py:43`, `light.py:48`, `alarm_control_panel.py:47,66`, `media_player.py:47`).
- Effort: S


## Definition of done

1. Every acceptance criterion above is met and backed by a test that fails before your change and passes after it.
2. From your working directory, all of these pass and their output is pasted into the PR/commit body:
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
