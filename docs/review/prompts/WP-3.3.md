# Agent prompt — WP-3.3: Device registry and identity

You are implementing work package **WP-3.3** of the PyLoxone remediation plan.
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

- Phase: Phase 3
- Prerequisite packages that must be merged first: WP-3.2
- Sequential after WP-3.2.
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

### WP-3.3 Device registry and identity
- **Findings**: CORE-16, CORE-20, PC-04, PC-05, CORE-17, CORE-15, PS-14, PS-10/PC-35, PS-20 (device link), PS-17 (device link).
- **Files**: `miniserver.py`, `helpers.py` (`get_or_create_device` → `device_info_for(entry, uuid, name, model, room, via=serial)` returning a fresh `DeviceInfo`), `binary_sensor.py` (stop overwriting `uuidAction`), `fan.py` (parent-first), `sensor.py`, `lights/colorpickers.py:49-53,160-164`, `cover.py`, `lights/lightcontroller.py`, `__init__.py` (group creation), `const.py` (device-type constants), `tests/test_devices.py` (new).
- **Steps**: call `async_update_device_registry` from setup; identifiers `{(DOMAIN, serial)}` only, no fake MAC; type-aware `software_version`; fresh `DeviceInfo` per entity with `via_device`; parent constructed before sub-entities; `device_class` properties that return type strings deleted; group creation fixed and moved behind a new option `generate_groups` (default off for new entries, on for migrated ones); dead dispatcher wiring deleted.
- **Acceptance**: snapshot of the device registry after setup shows one Miniserver device with `sw_version`, and one device per control with the control's own name/model/area and `via_device` set; the Ventilation device is named after the fan (regression for PR #513); `group.loxone_dimmers` contains dimmers and `group.loxone_analog` is non-empty when the option is on; the WP-0.2 `device_type` contract test passes.

## Findings you are fixing (verbatim from the catalogue)

### CORE-16 [medium] Miniserver device is never created; identity fields are wrong
- Where: `miniserver.py:91-116` — `async_update_device_registry` has **no callers**; `CONNECTION_NETWORK_MAC = "mac"` (16) populated with the host IP (105); `software_version` (71-72) iterates characters if `softwareVersion` is a string (same construct in `sensor.py:220-221`); `miniserver_id` (75-77) returns `config_entry.unique_id` which is always `None` (config flow never sets one) so dispatcher signals collide across entries; `identifiers={(DOMAIN, self.serial)}` with `serial` possibly `None`. Unused `asyncio`/`traceback` imports.
- Fix: call it from `async_setup_entry`; drop the fake MAC; type-aware version join; set entry `unique_id` to the serial (CORE-19).
- Effort: M

### CORE-20 [medium] `helpers.device_registry` is a module-level global; first writer wins, dicts are shared by reference
- Where: `helpers.py:12-25`. Never cleared on reload (renames in Loxone Config need an HA restart), shared across config entries, the *same dict object* is handed to sibling entities as `_attr_device_info`, no `via_device`. See PC-04 for the concrete fan/presence symptom (upstream PR #513).
- Fix: build a fresh `DeviceInfo(...)` each time (registry de-duplicates on identifiers) or cache per entry; add `via_device=(DOMAIN, serial)`.
- Effort: M · Upstream: PR #513

### PC-04 [high] Ventilation device is named/typed after its presence sub-sensor
- Where: `fan.py:80` constructs `LoxoneDigitalSensor` *before* `LoxoneVentilation` (138); `binary_sensor.py:137-147` overwrites `self.uuidAction` with the parent id so `unique_id` collides, then seeds `get_or_create_device` (CORE-20) with name `"<Fan> - Presence"`, model `"presence"`, area `""`. Meter sub-sensors have the same first-writer hazard (`sensor.py:473-480` vs `517-521`).
- Fix: parent-first construction; do not overwrite `uuidAction` (keep state uuid as unique id, parent only for `device_info`); see CORE-20.
- Effort: M · Upstream: PR #513

### PC-05 [high] Standalone colour pickers would register a device with identifier `None`
- Where: `lights/colorpickers.py:49-53, 160-164` (`else` branch passes `self._light_controller_id`, which is `None` there). `LumiTech` at 288 does it right. Latent only because standalone `ColorPickerV2` is never instantiated (PC-43).
- Fix: `self.unique_id`.
- Effort: S

### CORE-17 [medium] Dead dispatcher wiring that also leaks
- Where: `cover.py:77-81`, `sensor.py:301-303`, `binary_sensor.py:87-92` subscribe to `async_signal_new_device` signals that nothing ever sends (`async_dispatcher_send` has zero callers); unsubs are appended to `MiniServer.listeners`, which is never iterated. `binary_sensor.py:29` defines `NEW_SENSOR = "binairy_sensors"` (typo, unused) and registers the same signal name as `sensor.py`. `cover.py:73-75` defines an unused `async_add_covers`.
- Fix: delete all of it (plus `MiniServer.listeners`/`async_signal_new_device`), or implement runtime discovery properly with `async_on_unload`.
- Effort: S

### CORE-15 [medium] Auto-group creation is broken in five ways
- Where: `__init__.py:433-566`: `lights` passed for the "Loxone Dimmer" group (520-522) while `dimmers` is unused; master group omits dimmers/climates/accontrollers (546-557); gated on `miniserver_type < 2` so Gen-2/Compact users get nothing (436); the comparison sits outside the `try` and raises `TypeError` when `miniserverType` is absent; runtime `async_setup_component(hass, "group", {})` (498) and test-only `hass.async_block_till_done()` (544). Also see PS-14 (device_type strings never match).
- Fix: fix the list, drop the gate, move inside `try`, declare `"dependencies": ["group"]`, remove `async_block_till_done`. Consider replacing groups with labels.
- Effort: M

### PS-14 [medium] `device_type` attribute strings never match the group-generation table
- Where: `sensor.py:477,512` (`"Sensor analog_sensor"`), `binary_sensor.py:153` (`"digital"`/`"presence"`/`"smoke"`), `switch.py:114` (`"TimeSwitch"`) vs `__init__.py:459-467` (`"analog_sensor"`, `"digital_sensor"`, `"TimedSwitch"`). Three groups are always empty.
- Fix: constants in `const.py` used on both sides; contract test.
- Effort: S

### PS-10 [medium] `device_class` properties return Loxone control-type strings
- Where: `sensor.py:417-420` (`"TextInput"`), `lights/lightcontroller.py:70-73` (`"LightControllerV2"`, which `scene.py:66` depends on as a marker), `cover.py:144` (`"Gate"`), `fan.py:198-234` (dead property/setter pair).
- Fix: return `None`/delete; expose the type via `extra_state_attributes["device_type"]` or device `model`.
- Effort: S

### PC-35 [medium] `device_class` returning non-enum strings (see PS-10)
- Where: `cover.py:136-144` (`"Gate"`), `lights/lightcontroller.py:70-73`, `fan.py:198-234`.
- Effort: S

### PS-20 [medium] Keep-alive and version sensors have no device and no `entity_category`; version sensor stores `"unknown"` string
- Where: `sensor.py:350-403`. Both float outside the Miniserver device; `LoxoneVersionSensor` swallows exceptions into `STATE_UNKNOWN`.
- Fix: `DeviceInfo(identifiers={(DOMAIN, serial)})`, `EntityCategory.DIAGNOSTIC`, `None`; consider `entity_registry_enabled_default=False` for keep-alive.
- Effort: S

### PS-17 [medium] Scene generation is time-delayed, untracked, and scrapes HA internals
- Where: `scene.py:32-106` (`hass.loop.call_later` never cancelled → callback after unload; `hass.data["light"].get_entity`; depends on the fake `device_class` marker; no `_attr_device_info`; `self.name = name`; returns `True` from `-> None`). Moods are already in `states["moodList"]` in the structure file.
- Fix: build scenes directly from `get_all(loxconfig, "LightControllerV2")` with no timer; if a delay stays, `async_call_later` + `async_on_unload`.
- Effort: M


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
