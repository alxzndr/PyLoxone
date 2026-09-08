# Agent prompt — WP-3.1: Setup / unload / reload lifecycle

You are implementing work package **WP-3.1** of the PyLoxone remediation plan.
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
- Prerequisite packages that must be merged first: WP-1.1, WP-2.3
- If WP-2.3 is not merged, apply the interim reload fix described in the package.
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

### WP-3.1 Setup / unload / reload lifecycle
- **Findings**: CORE-03, CORE-06, CORE-10, CORE-12, CORE-13, CORE-14, CORE-29, CORE-31 (optional), CORE-05 (interim `async_schedule_reload(entry_id)` if WP-2.3 is not yet merged). CORE-09's retry/close fix lands in WP-1.5; keep its counter logic intact here.
- **Files**: `__init__.py`, `coordinator.py`, and deletion of the six stub `async_setup_platform` functions plus `PLATFORM_SCHEMA` in `alarm_control_panel.py`, `climate.py`; `tests/test_init.py` (new).
- **Steps**: `entry.async_create_background_task` for the listener; every listener via `entry.async_on_unload`; `async_at_started` for the startup hook; close the API on every setup-failure branch; spread `**entry.data`; coordinator gets `config_entry=`, drops the override of `async_config_entry_first_refresh` in favour of `_async_setup`, guards `api is None`, uses `.get()`; unload = `async_unload_platforms` first, cleanup only on success; delete the `async_load_platform` loop; add the options update listener; migrate to `entry.runtime_data` if time permits.
- **Acceptance**: setup/unload leaves `hass.bus.async_listeners()` counts for `loxone_event`, `EVENT_HOMEASSISTANT_STOP`, `EVENT_HOMEASSISTANT_STARTED` at baseline; five reloads leave no extra tasks (`asyncio.all_tasks()` diff); `LoxoneServiceUnAvailableError` on open → `ConfigEntryNotReady` **and** `api.close` awaited; changing an option triggers a reload; `grep -rn "TODO(WP-3.1)" custom_components/` returns nothing (background tasks are now stored via `entry.async_create_background_task`, clearing WP-0.1's `RUF006` markers in `__init__.py`).

## Findings you are fixing (verbatim from the catalogue)

### CORE-03 [critical] The websocket listening task is never stored, never registered with HA, and can be garbage-collected
- Where: `__init__.py:568-576` (`listening_task` is a local), `102-112` (unload reads `coordinator._listening_task`, which nothing ever sets — dead code). Same class of bug at `602`, `616` (`_ = asyncio.create_task(...)`).
- Fix: `coordinator.listening_task = config_entry.async_create_background_task(hass, ..., name="loxone-listener")`; cancel on unload.
- Effort: M

### CORE-06 [high] `EVENT_HOMEASSISTANT_STOP`/`STARTED` listeners accumulate across reloads; stale `stop_event` can overwrite a good token
- Where: `__init__.py:635-636` (unsubs discarded). `STARTED` never fires again, so group creation silently never runs after a reload.
- Fix: `config_entry.async_on_unload(...)` for all four listeners; `homeassistant.helpers.start.async_at_started` for the startup hook.
- Effort: S

### CORE-10 [high] `handle_task_result` replaces the entire `ConfigEntry.data` dict; coordinator checks key presence not truthiness
- Where: `__init__.py:327-341` (`data={"token": "", ...}` without `**config_entry.data`), `coordinator.py:47` (`if "token" in self.config_entry.data` — an empty-string token is still passed to `LoxoneConnection`).
- Fix: spread existing data; test `.get("token")`.
- Effort: S

### CORE-12 [high] `DataUpdateCoordinator` misused
- Where: `coordinator.py:20-45` (`config_entry` not passed to `super().__init__` → HA deprecation report; `async_config_entry_first_refresh` overridden without calling super, so `data`/`last_update_success` never set), `86` (`print("_async_update_data")`), `99-100` (`async_cleanup` calls `self.api.close()` on `None` if unload precedes first refresh), `29-32` (`options[...]` with `[]` → `KeyError` reported as a connection error).
- Fix: pass `config_entry=`; drop the print; guard `api is None`; either use `_async_setup()` or stop subclassing `DataUpdateCoordinator`.
- Effort: M

### CORE-13 [high] Unload tears down the coordinator before unloading platforms and ignores the unload result
- Where: `__init__.py:82-139` (`async_cleanup`, `hass.data` pop and service removal all happen before `async_unload_platforms`; a `False` result leaves a zombie LOADED entry; `except Exception as e: raise e` at 123-124).
- Fix: `unload_ok = await async_unload_platforms(...)`, then cleanup only if ok; register everything with `config_entry.async_on_unload`.
- Effort: M

### CORE-14 [medium] Redundant `async_load_platform` loop passes a `ConfigEntry` as `hass_config`
- Where: `__init__.py:307-317`. Verified: it creates no entities (every `async_setup_platform` is a stub except `sensor.py`, whose body is guarded by `if config:` and receives `{}`); YAML `platform: loxone` sensors are set up by the `sensor` component independently. It only survives because `async_forward_entry_setups` already registered the components. The discovery `EntityPlatform` it creates is never unloaded.
- Fix: delete the loop; delete the six stub `async_setup_platform` functions (`switch.py:28-35`, `button.py:28-35`, `number.py:26-33`, `text.py:26-33`, `select.py:84-91`, `scene.py:22-29`) and the `PLATFORM_SCHEMA` extensions that feed nothing (`alarm_control_panel.py:30-37`, `climate.py:75-79`).
- Effort: S

### CORE-29 [low] No options update listener; `async_config_entry_updated` stub is dead
- Where: `__init__.py:200-206`. Changing host/password/scene options has no effect until a manual reload.
- Fix: `config_entry.async_on_unload(config_entry.add_update_listener(...async_schedule_reload...))`.
- Effort: S

### CORE-31 [low] `hass.data[DOMAIN]` instead of `entry.runtime_data`; stale empty dict after last unload
- Where: `__init__.py:242-243, 305`; `miniserver.py:26-28`.
- Effort: M

### CORE-05 [critical] Reconnect strategy reloads *every* config entry via a service call from a task done-callback
- Where: `__init__.py:319-366` (`_reload_after_delay` → `hass.services.async_call("loxone", "reload")`), `421-431` (`handle_reload` unloads all entries, then `async_reload` unloads them again). `except Exception as e: raise e` at 365-366 raises inside a done-callback. No backoff.
- Fix: interim — `hass.config_entries.async_schedule_reload(config_entry.entry_id)` with backoff; final — API-09 in-place reconnect and no reload at all. `loxone.reload` service should reload only the targeted entry.
- Effort: L · Upstream: #491 #475

### CORE-09 [critical] A transient 401 during setup permanently kills the integration (`return False`, no retry, no reauth); connection leaked on two error branches
- Where: `__init__.py:257-269`. `LoxoneUnauthorisedError` is the *only* setup failure that neither raises `ConfigEntryNotReady` nor closes the API; it does `return False`, which parks the entry in `setup_error` until a human reloads it. `LoxoneServiceUnAvailableError` also skips `await coordinator.api.close()`. No `async_step_reauth` exists (CORE-19).
- Incident: documented in `2026-09-02-pyloxone-401-setup-error.md` (repo root). A Miniserver firmware update (17.0.3.31) rebooted the device; its HTTP server came back before auth was initialised and answered `401` to `GET /data/LoxAPP3.json` at 21:56:11. The reconnect path (CORE-05) re-ran `async_setup_entry`, hit this branch, and the integration stayed dead for 10.5 hours with valid credentials (verified with `curl` and a second client during the outage). The 503/timeout branches retried correctly minutes earlier — only 401 is treated as fatal, and a booting Miniserver emits exactly that.
- Fix: never `return False`. Treat 401 during setup as retryable: raise `ConfigEntryNotReady` and count consecutive auth failures per entry (e.g. in `hass.data`/runtime data with a timestamp); only after N attempts spanning at least several minutes (suggest 5 attempts / 5 min) escalate to `ConfigEntryAuthFailed` (once WP-3.4 provides reauth) and a repair issue. Close the API in every failure branch. Same rule must apply inside the in-place reconnect loop (API-09): a 401 immediately after a reconnect is a boot artefact, not a credential change.
- Effort: S (retry + close) / L (reauth, see CORE-19)


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
