# Agent prompt — WP-4.3: Lights

You are implementing work package **WP-4.3** of the PyLoxone remediation plan.
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
- Prerequisite packages that must be merged first: WP-1.1, WP-1.2, WP-1.3
- Parallel-safe with the other WP-4.x packages except that it owns the brightness helpers in helpers.py; do not edit __init__.py, coordinator.py or pyloxone_api/.
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

### WP-4.3 Lights
- **Findings**: PC-03, PC-05, PC-11, PC-17, PC-18, PC-33, PC-37, PC-38, PC-39, PC-43 (standalone `ColorPickerV2` only), PC-40 (light lines), PS-10 (lightcontroller `device_class`).
- **Files**: `light.py`, `lights/*.py`, `helpers.py` (brightness mapping functions only), `scene.py` (switch from the `device_class` marker to an explicit attribute), `tests/test_lights.py` (new).
- **Steps**: extract `plan_turn_on(kwargs, color_mode, hs, kelvin, brightness) -> str` per picker type; `lox_to_hass_range`/`hass_to_lox_range` using `map_range` with min/max, `max(1, …)` floor; create standalone `ColorPickerV2` entities via `get_all`.
- **Acceptance**: brightness-only `turn_on` on an RGB picker with unknown colour mode emits `setBrightness/…` (PR #512 regression); `(90, 10, 90) → 255` and `255 → 90`; brightness 1 never maps to 0; TunableWhite `turn_on` before any state does not raise; standalone picker gets a device with a string identifier.

## Findings you are fixing (verbatim from the catalogue)

### PC-03 [high] RGB colour picker: `turn_on` with brightness only sends nothing; `hs_color[0]` on `None`
- Where: `lights/colorpickers.py:181-238` (`_attr_color_mode` starts `UNKNOWN`, so neither branch at 213/225 matches and the `else` at 236 is unreachable when brightness is present; 219 dereferences `self.hs_color`).
- Fix: always emit exactly one command (hs → kelvin → mode-with-known-value → `setBrightness`).
- Effort: S · Upstream: PR #512

### PC-05 [high] Standalone colour pickers would register a device with identifier `None`
- Where: `lights/colorpickers.py:49-53, 160-164` (`else` branch passes `self._light_controller_id`, which is `None` there). `LumiTech` at 288 does it right. Latent only because standalone `ColorPickerV2` is never instantiated (PC-43).
- Fix: `self.unique_id`.
- Effort: S

### PC-11 [high] `TunableWhiteLight.async_turn_on` crashes before the first state; sends `temp(50.0,None)`
- Where: `lights/colorpickers.py:70-92` (`_attr_brightness` never initialised → `hass_to_lox(None)` `TypeError`; brightness-only branch formats `None`). `RGBColorPicker` has the `or 255` fallback at 185.
- Fix: default brightness/kelvin at the top of `turn_on`.
- Effort: S

### PC-17 [medium] `lox2hass_mapped` clamps but does not rescale; write path ignores min/max
- Where: `helpers.py:50-55` (`(90,10,90) → 229.5` not 255), `lights/dimmer.py:73,98-114`, `lights/lightcontroller.py:148,179-194`. Slider snaps back on every full-brightness command.
- Fix: real bidirectional mapping via the existing `map_range`.
- Effort: M

### PC-18 [medium] HA brightness 1–2 rounds to Loxone `0` (off); brightness stored as float
- Where: `lights/dimmer.py:73,105-109`; `lightcontroller.py:148`; `colorpickers.py:250` vs `106,258` (int).
- Fix: `max(1, round(...))` on write when `> 0`; `round` on read.
- Effort: S

### PC-33 [medium] LightControllerV2 `turn_on` with brightness but no master value sends nothing; effect+brightness drops brightness
- Where: `lights/lightcontroller.py:140-156`; magic `changeTo/99` and `[778]` all-off sentinel (199).
- Fix: final `else`; handle both; named constants; share scaling with dimmer.
- Effort: M

### PC-37 [low] `masterColor` sub-control filter uses `find(...) > 1` instead of `> -1`
- Where: `light.py:81-85`.
- Effort: S

### PC-38 [low] `async_turn_off(self)` without `**kwargs`
- Where: `lights/colorpickers.py:64, 175`.
- Effort: S

### PC-39 [low] `_attr_min_color_temp_kelvin = 2000` below the documented Loxone range (2700)
- Where: `lights/colorpickers.py:20-21, 129-130`; `helpers.py:69` docstring.
- Effort: S

### PC-43 [gap] Missing capabilities
- Standalone `ColorPickerV2` controls are never created (`light.py` only walks `LightControllerV2.subControls`); `AudioZoneV2` lacks sources/favourites/metadata/mute/on-off/shuffle/repeat/play_media; alarm lacks `ARM_NIGHT`/`ARM_VACATION`/`TRIGGER` and arming-delay surfacing (#323); Jalousie has no auto/shade select; Gate lacks `SET_POSITION` despite tracking `position`; Window lacks stop/tilt; `IRoomControllerV2` sub-controls not enumerated; Ventilation `temperatureIndoor` sensor commented out, no boost/timer entity; AcControl improvements (#398).

### PC-40 [low] Dead code in the complex platforms
- `helpers.py:42-47` (`lox2lox_mapped`), `68-83` (mired converters — confirmed unused; the lights are fully Kelvin-based), `cover.py:73-75, 118-121, 369-372`, `alarm_control_panel.py:158-175, 241-246` (`hidden`, `icon`, sync stubs, `_validate_code`), `alarm_control_panel.py:30-37`/`climate.py:75-79` (`PLATFORM_SCHEMA` for no-op YAML), `colorpickers.py:128`, `dimmer.py:129-142` (duplicate device-info block), `fan.py:109-122, 256-258, 273-277`, `lightcontroller.py:23,28,75-77,167-169`, `media_player.py:146-149` (`async_media_stop` without the `STOP` feature), unused imports (`DeviceInfo`/`DOMAIN` in `lights/*`, `DOMAIN` in alarm).
- Effort: M

### PS-10 [medium] `device_class` properties return Loxone control-type strings
- Where: `sensor.py:417-420` (`"TextInput"`), `lights/lightcontroller.py:70-73` (`"LightControllerV2"`, which `scene.py:66` depends on as a marker), `cover.py:144` (`"Gate"`), `fan.py:198-234` (dead property/setter pair).
- Fix: return `None`/delete; expose the type via `extra_state_attributes["device_type"]` or device `model`.
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
