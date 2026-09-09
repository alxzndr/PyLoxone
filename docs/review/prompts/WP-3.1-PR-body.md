# WP-3.1 — Setup / unload / reload lifecycle

**Branch:** `fix/wp-3.1-setup-lifecycle`
**Prerequisites:** WP-1.1 (merged, `8b2ab6e`), WP-2.3 (merged, `4f9c0bf` — the
in-place reconnect is in place, so this package only finishes the *interim* CORE-05
work that WP-2.3 left: the `loxone.reload` service now targets per-entry and, with the
new optional `entry_id`, a single Miniserver).
**Findings:** CORE-03, CORE-05 (interim tail), CORE-06, CORE-10 (setter side; the
coordinator's token check), CORE-12, CORE-13, CORE-14, CORE-29. CORE-09's retry/close
logic from WP-1.5 is kept byte-for-byte semantically intact (helper names, thresholds,
log wording, close-on-every-failure branch); CORE-31 is **deferred** (see Follow-ups).

## Changes by file

### `custom_components/loxone/__init__.py`
- **CORE-03** — the AP-9 session supervisor is now created with
  `config_entry.async_create_background_task(hass, run_loxone_session(), name="loxone-session")`
  and stored as `coordinator.listening_task`. `async_unload_entry` cancels and awaits it
  *before* closing the connection. The two untracked
  ``_ = asyncio.create_task(...)`` sends in the `loxone_send` bus listener (the last
  two `# noqa: RUF006 # TODO(WP-3.1)` markers of WP-0.1) are now entry-tracked
  background tasks (`loxone-send-command` / `loxone-send-secured-command`), so
  `grep -rn "TODO(WP-3.1)" custom_components/` returns nothing.
- **CORE-05** — `handle_reload` keeps per-entry `async_schedule_reload` (already the
  interim fix) and gains an optional `entry_id` service field that restricts the reload
  to the named Miniserver instead of fanning out over every loxone entry.
- **CORE-06** — all four listeners register through `config_entry.async_on_unload`:
  the STOP one-shot, the two send subscriptions and the auto-group hook. The STARTED
  subscription is replaced by `homeassistant.helpers.start.async_at_started(hass,
  create_groups)` (its previous `event.data["component"] == DOMAIN` test never matched
  the STARTED event payload, so group creation was doubly dead on a reload; the new
  hook runs once per HA start, or immediately if HA is already running, and an
  idempotence guard (`group.loxone_group` already present) prevents re-running it per
  reload). The STOP handler moved to module scope
  (`_persist_token_and_close`) — `EventBus` listen-once wrappers are incompatible with
  coroutine-bound locally defined functions (observed: the nested closure's unsubscribe
  silently stopped working on reload).
- **CORE-10** — the STOP persistence spreads `**config_entry.data` (already the case
  since WP-1.5) and now skips the update entirely when `get_token_dict()` returned the
  empty-dict from a missing token.
- **CORE-13** — `async_unload_entry` runs `async_unload_platforms` *first*; a `False`
  result is returned as such (HA marks `FAILED_UNLOAD`) and **no** cleanup happens
  (connection, listeners, `hass.data`, services are all kept so the unload can be
  retried). On success the session task is cancelled, the connection closed via
  `coordinator.async_cleanup()`, the domain data + auth-failure bookkeeping is dropped,
  and the services are removed. The `except Exception as e: raise e` is gone.
- **CORE-14** — the `async_load_platform` loop (which passed a `ConfigEntry` as YAML
  config and created no entities) and its import are deleted.
- **CORE-29** — the dead `async_config_entry_updated` stub is deleted; setup now
  registers an options update listener (a coroutine, as HA fires update listeners as
  tasks) that schedules a reload of *that* entry, and the registration is held with
  `config_entry.async_on_unload`.

### `custom_components/loxone/coordinator.py`
- **CORE-12** — `config_entry=config_entry` is passed to `super().__init__` (no more
  ContextVar deprecation report); the override of `async_config_entry_first_refresh`
  is dropped in favour of `_async_setup()` (the stock first refresh now sets
  `data`/`last_update_success` and applies the entry-state check); the debug print is
  gone; `async_cleanup` guards `self.api is None` and nulls the handle after closing;
  options are read with `.get()` and `DEFAULT_PORT`.
- **CORE-10** — `LoxoneConnection` only receives `token=config_entry.data` when
  `config_entry.data.get("token")` is *truthy*; an empty-string token is treated as
  absent (the old `"token" in config_entry.data` passed an empty token to the API).
- **CORE-03** — the task attribute is `listening_task` (public, set from
  `__init__.py`); the obsolete `listeners` list and `_listening_task` are removed.
- The connection open/`MiniServer` construction moved into `_async_setup`; every
  setup exception still propagates (wrapping `ConfigEntryNotReady.__cause__` in
  `async_setup_entry`, which classifies 401 / 503 / other before re-raising — the
  retry/escalation counter of CORE-09 is untouched and its `finally`-close stays the
  single close-on-failure point).

### Platform stubs (CORE-14)
- Deleted: `async_setup_platform` in `switch.py`, `button.py`, `number.py`, `text.py`,
  `select.py`, `scene.py`, and the vestigial empty one in `alarm_control_panel.py`
  (its `PLATFORM_SCHEMA` was already removed in WP-4.4; only the function and the
  typing imports survived); deleted `PLATFORM_SCHEMA` and the commented-out stub in
  `climate.py` (plus its now-unused `voluptuous` imports). Unused
  `ConfigType`/`DiscoveryInfoType` imports removed where the stub was the only user.
  `sensor.py`'s real YAML platform (and its `PLATFORM_SCHEMA`) is untouched.
- Side effect (intended by the finding): a YAML platform declaration for one of these
  domains now produces the standard "incomplete platform" setup error instead of
  silently doing nothing.

### Other touched files (documentation only)
- `custom_components/loxone/services.yaml` + `translations/{en,de}.json`: optional
  `entry_id` field for `loxone.reload` (satisfies the contract tests
  `test_services_yaml_keys_have_en_entries` / `test_de_translations_superset_of_en`).
- `CHANGELOG.md`: entry under *Unreleased → Fixed*.

## Regression tests (`tests/test_init.py`, new)

Verified **red before / green after** by stashing the implementation and re-running:
9 of 10 fail on the pre-change code, all 10 pass after it (the one pre-passing test is
the 503/close acceptance that WP-1.5 already satisfied and which this package must
keep intact):

| Test | Finding |
|---|---|
| `test_setup_and_unload_leave_listener_counts_at_baseline` | CORE-06/13 (STOP one-shot return-to-baseline delta; send/event counts flat) |
| `test_stop_and_started_listeners_do_not_accumulate_across_reloads` | CORE-06 (all tracked counts flat across 3 reloads; pre-fix STOP & STARTED grew +1 per reload) |
| `test_five_reloads_leave_no_extra_tasks` | CORE-03/13 (`asyncio.all_tasks()` diff vs pre-setup snapshot; exactly the one live session task left) |
| `test_unavailable_on_open_raises_not_ready_and_closes_api` | CORE-09/10 (`open` raises 503 → `SETUP_RETRY` + `close` awaited once; kept from WP-1.5) |
| `test_options_change_schedules_reload_of_its_entry` | CORE-29 (options update schedules exactly the owning entry's reload) |
| `test_stub_platforms_and_dead_schemas_are_deleted` | CORE-14 / CORE-12 (no stubs, no dead `PLATFORM_SCHEMA`, no `async_load_platform(` call, no first-refresh override) |
| `test_coordinator_uses_stock_first_refresh` | CORE-12 (`data`/`last_update_success` set by the stock first refresh) |
| `test_coordinator_passes_config_entry_to_super` | CORE-12 (no `frame.report_usage` deprecation report from the ctor) |
| `test_empty_stored_token_is_not_sent_to_connection` | CORE-10 (`""` token → no `token=` kwarg; real token → forwards the whole data dict) |
| `test_failed_platform_unload_keeps_entry_resources` | CORE-13 (failed `async_unload_platforms` → `FAILED_UNLOAD`, coordinator/listening task/services all preserved) |

All expected values are hand-derived literals (listener deltas, task diffs, `SETUP_RETRY`
states, `close_calls == [1]`, `scheduled == [entry_id]`, `() / ["token"]` kwarg
membership) — no test derives its expectation from the code under test.

## Commands run and observed output (from the repo root)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
61 files already formatted

$ pytest -q
364 passed, 1 deselected, 1 xfailed, 2 warnings in 41.54s
```

(the full suite before this package was `354 passed` — the delta is exactly the 10 new
tests in `tests/test_init.py`)

```
$ grep -rn "TODO(WP-3.1)" custom_components/
(no output — the two WP-0.1 RUF006 markers in __init__.py are cleared along with the code they marked)
```

No `# noqa` was added anywhere; the `manifest.json` version was not bumped.

## VERIFY items

- **No new live-Miniserver VERIFY item in this package.**
- Carried over from WP-1.5 (still live): the 401 boot-window escalation thresholds
  (5 attempts / 5 min) in `_should_escalate_auth_failure` remain guidance until a
  live Miniserver's boot behaviour is confirmed; the WP-3.4 reauth flow will make the
  escalation terminal. This package keeps that logic byte-for-byte semantically
  intact, so the carry-over applies unchanged.

## Related upstream issues

- JoDehli/PyLoxone#491, JoDehli/PyLoxone#475 (CORE-05, interim)

## Follow-ups (out of scope for this WP, noted per plan rule 3)

1. **CORE-31**: `hass.data[DOMAIN]` should become `entry.runtime_data` (marked
   optional in the plan): the migration also touches `system_health.py` and
   `miniserver.py`, which are not in this WP's file list, so it was deliberately
   deferred. Note it would also resolve the per-retry coordinator stacking
   stacking created by setup retries (a fresh coordinator — with its on_unload
   registrations — is made on every failed `async_setup_entry` attempt; master had the
   same behaviour). The `auth_failures` key in `hass.data[DOMAIN]` lives alongside the
   coordinators and is why the `_hass_data()` helper and its removal discipline stay.
2. **Multi-instance services**: the domain services (`event_websocket_command`,
   `event_secured_websocket_command`, `sync_areas`, `reload`) are registered per entry
   in `async_setup_entry`; a second Miniserver entry fails setup with
   `ServiceAlreadyRegisteredError` (pre-existing, not touched here). Belongs to the
   outbound-commands work package from the decision table.
3. **Auto-groups**: kept and made idempotent, but still ungated. Per the decision
   table they should later sit behind an option defaulting to off for new installs
   (CORE-15/PS-14, another package).
4. **YAML `loxone:` block**: `async_setup`'s import-flow attempt and `CONFIG_SCHEMA`
   deliberately untouched (CORE-19 owns them; the decided repair-issue path is a
   separate package). This WP fixes none of that and changes none of it.
5. **Event schema hygiene**: `EVENT`/`SENDDOMAIN` payload constants and the
   `LoxoneEntity` class are unchanged; the WP-3.2 per-uuid dispatcher migration
   remains upstream of those handlers.
