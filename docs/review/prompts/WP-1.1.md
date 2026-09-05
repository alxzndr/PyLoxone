# Agent prompt — WP-1.1: `LoxoneEntity` base class hardening

You are implementing work package **WP-1.1** of the PyLoxone remediation plan.
Repository: `/Users/alexandergeeraerts/github/PyLoxone` (Home Assistant custom integration for Loxone Miniservers).
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 1
- Prerequisite packages that must be merged first: WP-0.2
- Parallel-safe with WP-1.2, WP-1.3, WP-1.4.
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

### WP-1.1 `LoxoneEntity` base class hardening
- **Findings**: CORE-01, CORE-02, PS-12, PS-22, CORE-26 (only the `cached_property` removal; keep names identical), PS-23, CORE-32 (`_clean_unit` duplicate only).
- **Files**: `custom_components/loxone/__init__.py` (class `LoxoneEntity` only), plus the minimum edits in subclasses that assign `self.name = ...` (`switch.py:369`, `scene.py`) to use `_attr_name`, and deletion of per-class `should_poll` overrides (`sensor.py`, `switch.py`, `number.py`, `text.py`, `select.py`, `cover.py`).
- **Steps**: replace `sys.exit` with `_LOGGER.exception` + continue and fix the "Could set" message; `self.async_on_remove(self.hass.bus.async_listen(EVENT, self.event_handler))`, delete `async_will_remove_from_hass`; `_attr_should_poll = False`; `_unrecorded_attributes = frozenset({"uuid","platform","room","category","state_uuid","device_type"})`; replace the `name`/`unique_id` `cached_property` overrides with `self._attr_name`/`self._attr_unique_id = uuidAction` set in `__init__` — verify with a grep that no subclass still reads `self.uuidAction` *through* `unique_id` (PC-04 does; leave that to WP-3.3 but keep behaviour identical here); delete `LoxoneEntity._clean_unit`/`_get_format` in favour of `helpers.clean_unit`.
- **Acceptance**: HA-harness test that sets up the fixture entry, records `len(hass.bus.async_listeners()["loxone_event"])`, reloads the entry three times, and asserts the count is constant; test that constructing an entity with a kwarg whose `setattr` raises does not raise `SystemExit`; all entity ids and unique ids unchanged versus a snapshot taken before the change.

## Findings you are fixing (verbatim from the catalogue)

### CORE-01 [critical] `LoxoneEntity.__init__` calls `sys.exit(-1)` inside the event loop
- Where: `__init__.py:668-675`. `SystemExit` is a `BaseException`; HA's `except Exception` guards do not catch it. The `AttributeError` branch logs `"Could set ..."` (typo) using `self.name`, which can itself raise. `fan.py:76,88,101,130` passes `device_class` kwargs that hit the read-only property and trigger this branch at every startup.
- Fix: `_LOGGER.exception("Could not set %s on %s", key, type(self).__name__)` and continue; longer term, stop `setattr`-ing raw JSON onto entities.
- Effort: S

### CORE-02 [critical] Entity bus listeners are never unsubscribed → unbounded leak across every reload
- Where: `__init__.py:691-697` (`self.listener = None` drops the unsubscribe callable without calling it). Correct pattern already exists at `sensor.py:555-558` and `climate.py:377-381`.
- Fix: `self.async_on_remove(self.hass.bus.async_listen(EVENT, self.event_handler))`; delete `async_will_remove_from_hass`.
- Effort: S · Upstream: #491 #514 #475

### PS-12 [medium] `should_poll` left at the default `True` on most entity classes; `update_before_add=True`
- Where: no `_attr_should_poll` on `LoxoneEntity`; set only in `sensor.py:387,449`, `switch.py:120,216`, `number.py:73`, `text.py:77`, `select.py:133`, `cover.py:131,454`. Climate, fan, lights, media player, alarm, `LoxoneWindow`, keep-alive/text/custom/climate-controller sensors all poll for nothing. `sensor.py:299,305` requests `update_before_add`.
- Fix: `_attr_should_poll = False` on `LoxoneEntity`; delete the per-class overrides; drop `update_before_add`.
- Effort: S

### PS-22 [low] Static metadata (`uuid`, `platform`, `room`, `category`, `state_uuid`, `device_type`) is written to the recorder on every state change
- Where: `__init__.py:679-689` and every platform's `extra_state_attributes`.
- Fix: `_unrecorded_attributes = frozenset({...})`; move room/category to the device registry.
- Effort: S

### CORE-26 [medium] No `has_entity_name`; `name`/`unique_id` overridden with `functools.cached_property`
- Where: `__init__.py:702-704, 728-731`. `_attr_has_entity_name` appears nowhere, so the UI shows "Living Room Light Living Room Light". The `name` override bypasses HA's translation/device-name logic and relies on `_attr_name` having no class default (line 663's `hasattr` routing). `switch.py:369` and `scene.py` assign `self.name = ...` directly.
- Fix: delete both overrides; set `_attr_name`/`_attr_unique_id` in `__init__`; adopt `_attr_has_entity_name = True` behind a release note (user-visible renames).
- Effort: M

### PS-23 [low] `_attr_state: None = None` / `_attr_assumed_state: None = None` nonsense annotations; `self._assumed` never read
- Where: `switch.py:93-94, 98-99, 198-199, 203-204, 208, 362`; `binary_sensor.py:101, 106`.
- Effort: S

### CORE-32 [low] Code hygiene in the core files
- f-string logging on the hot path (`__init__.py:370` runs for every message), German comments (`126, 340, 346, 352, 361`), unused imports (`EVENT_COMPONENT_LOADED`, `Platform`, `ATTR_COMMAND`, `DOMAIN_DEVICES`, `ERROR_VALUE`, `MiniServer`, `LoxoneConnection`, `LoxoneException`, `get_miniserver_type`), `_UNDEF` (77), mutable default `data={}` (398), `LoxoneEntity._clean_unit` duplicates `helpers.clean_unit` with a different `%%` fix, commented-out numpy helpers (`helpers.py:58-65`), `map_range` divides by zero when `in_min == in_max`, `get_all` crashes on missing `controls`/`type` (both reported by the Uni Ulm fuzzing PR #292).
- Effort: S

### PC-04 [high] Ventilation device is named/typed after its presence sub-sensor
- Where: `fan.py:80` constructs `LoxoneDigitalSensor` *before* `LoxoneVentilation` (138); `binary_sensor.py:137-147` overwrites `self.uuidAction` with the parent id so `unique_id` collides, then seeds `get_or_create_device` (CORE-20) with name `"<Fan> - Presence"`, model `"presence"`, area `""`. Meter sub-sensors have the same first-writer hazard (`sensor.py:473-480` vs `517-521`).
- Fix: parent-first construction; do not overwrite `uuidAction` (keep state uuid as unique id, parent only for `device_info`); see CORE-20.
- Effort: M · Upstream: PR #513


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
