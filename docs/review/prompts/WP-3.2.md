# Agent prompt — WP-3.2: Multi-instance isolation (#491)

You are implementing work package **WP-3.2** of the PyLoxone remediation plan.
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
- Prerequisite packages that must be merged first: WP-3.1
- Sequential after WP-3.1.
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

### WP-3.2 Multi-instance isolation (#491)
- **Findings**: CORE-04, CORE-11, CORE-27, PS-13, PS-18 (dispatcher part).
- **Files**: `__init__.py`, `coordinator.py`, `const.py`, every platform's `event_handler` (mechanical: subscribe via `async_dispatcher_connect` to own uuids; `@callback`), `sensor.py` (`LoxoneClimateController` fan-out), `tests/test_multi_instance.py` (new).
- **Design**: coordinator owns one listener on the connection; for each `{uuid: value}` it calls `async_dispatcher_send(hass, f"loxone_{entry_id}_{uuid}", value)` and still fires the public `loxone_event` (unchanged payload, plus `entry_id`). `LoxoneEntity.async_added_to_hass` connects to each of its state uuids. Outbound: `LoxoneEntity._send(value, secured=False, code=None)` → `self.coordinator.api.send_...`. Domain services registered once in `async_setup`; per call, resolve the coordinator from the entity registry entry's `config_entry_id` (or, for a raw `uuid`, the entry whose structure file contains it); `vol.Schema` with exactly-one-of `uuid`/`device`; `ServiceValidationError` otherwise. `loxone.reload` reloads only the entries targeted (default: all, but each via `async_schedule_reload`). The `loxone_send*` bus listeners stay for external users and forward only uuids known to that entry.
- **Acceptance**: two fixture entries with distinct serials: both `LOADED`; unloading B leaves all seven services registered; a command from an entity of A reaches only A's `send_websocket_command` mock; a state update fed to B does not touch A's entities; per-event listener invocations for an entity equal 1 (measured with a counting stub).

## Findings you are fixing (verbatim from the catalogue)

### CORE-04 [critical] Services are registered per entry with a closure over one coordinator and removed unconditionally on unload
- Where: `__init__.py:625-633` (register), `126-133` (remove). `quick_shade`/`enable_sun_automation`/`disable_sun_automation` are cover *entity* services registered in `cover.py:84-99`; removing them here kills them for every other entry and logs "Unable to remove unknown service" on the second unload.
- Fix: register domain services once (guard with `hass.services.has_service`), resolve the coordinator per call from the target entity's `config_entry_id`; never remove entity services here; remove domain services only when the last entry unloads.
- Effort: M · Upstream: #491

### CORE-11 [high] `handle_websocket_command` dereferences a possibly-`None` registry entry; no service schema
- Where: `__init__.py:381-382`, `394-395`. Missing `uuid` and `device` silently sends `""` as UUID; entity not checked to belong to this integration.
- Fix: `vol.Schema` requiring exactly one of `uuid`/`device`; `ServiceValidationError` on unknown entity.
- Effort: S

### CORE-27 [medium] One global bus event per Miniserver message, one listener per entity, no per-entry namespacing
- Where: `__init__.py:368-371` (`hass.bus.async_fire(EVENT, message)`), `691-693` (every entity subscribes), `639-642` (`loxone_send` listener per entry on the same global event → with two Miniservers every command is sent to both; every state update reaches both entries' entities). All `event_handler`s are `async def` with no `await`, so HA creates a task per entity per event.
- Fix: one listener in the coordinator; `async_dispatcher_send(hass, f"loxone_{entry_id}_{uuid}", value)` per changed uuid; entities connect only to their own uuids with `@callback`; keep firing `loxone_event` for user automations (README documents it for the recorder). Route outbound commands through the entity's own coordinator.
- Effort: L · Upstream: #491

### PS-13 [medium] `async def event_handler` without any `await` → a task per entity per event
- Where: every `event_handler` in these files (`sensor.py:323,366,412,502,559,585,615`; `binary_sensor.py:163,213`; `switch.py:141,239,338,383`; `button.py:86`; `number.py:107`; `text.py:96`; `select.py:152`). `sensor.py:618` allocates a `set` per event.
- Fix: `@callback def`; precompute `frozenset` of state uuids. Superseded by CORE-27.
- Effort: S

### PS-18 [medium] `LoxoneClimateController` sets `self.hass` in `__init__` and fans out bus events from inside a listener
- Where: `sensor.py:262` (`"hass": hass` kwarg), `602-647` (one `CLIMATE_EVENT` per linked room → O(rooms²); `schedule_update_ha_state()` from the loop; set allocated per event).
- Fix: drop the kwarg; `async_dispatcher_send` per uuid; `async_write_ha_state`.
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
