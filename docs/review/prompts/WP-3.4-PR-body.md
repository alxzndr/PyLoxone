# WP-3.4 — Config flow rewrite with reauth and unique id

Branch: `fix/wp-3.4-config-flow` (off `origin/master` @ b5d0c43).
Prerequisite: WP-3.3 merged (`81ddc3e`, `b5d0c43`).

Fixes: **CORE-19**, **CORE-18**, **CORE-09** (reauth escalation only),
**API-13** (consumer side), **CORE-30** (YAML repair issue).

## What changed (by file)

### `custom_components/loxone/config_flow.py` (rewritten)
- Hand-written `ConfigFlow` (v5) replacing `SchemaConfigFlowHandler`:
  - `user` step (host/port/username/password/verify_ssl) ->
    `test_loxone_connection()` (module-level, testable helper) runs the
    full `LoxoneConnection` bootstrap, reads the serial (`msInfo.serialNr`,
    fallback: API-key serial) and **always closes the test connection**;
  - `async_set_unique_id(serial)` + `_abort_if_unique_id_configured()` —
    the same Miniserver can no longer be added twice (the `single_instance
    allowed` dead-key era ends);
  - entry **data** carries the connection keys; options carry only the
    four preference keys; new installs are stamped
    `generate_groups: false` (CORE-15) plus the pre-flow preference
    defaults, so behaviour matches old installs;
  - `reauth` / `reauth_confirm` steps: the connection description is
    re-taken (host/port pre-filled; password is **not** pre-filled),
    verified against the live Miniserver, then
    `async_update_reload_and_abort(entry, data_updates=…)`;
  - form errors are translation keys, never free text (CORE-18):
    `cannot_connect`, `invalid_auth`, `invalid_host` (one key beyond the
    plan's four — empty host/username/password and unparseable hosts are
    an in-form input problem, not a network problem),
    `invalid_username_encoding`, `invalid_password_encoding`;
  - `OptionsFlow` (preference-only): `generate_groups`, `generate_scenes`,
    `generate_scenes_delay`, `generate_lightcontroller_subcontrols`;
    `generate_groups`'s default is the stored value (or pre-option
    `True`), so a save can never flip an old install's group behaviour
    (CORE-15 preserved);
  - the old plaintext password no longer round-trips to the frontend as a
    suggested option value.
- **VERIFY (live Miniserver)**: `user`/`reauth_confirm` run a *full*
  bootstrap (API key -> structure -> public key -> websocket) before
  creating/updating an entry — semantics intended, but that bootstrap
  cost/latency has not been measured against a real device.

### `custom_components/loxone/__init__.py`
- `async_migrate_entry`: v4→v5 moves host/port/username/password and
  `verify_ssl` from `options` to `data` (defaults: port 8080,
  `verify_ssl` true, empty strings); preferences and any existing data
  (tokens) are preserved; **no `unique_id` guessing** — it is stamped from
  `msInfo.serialNr` at setup (existing CORE-16 code) and an unmigrated
  serial simply stays `None` until the next successful setup.
- CORE-09 reauth escalation: after the bounded window (5 consecutive 401s
  **and** ≥ 300 s since the first), setup now raises
  `ConfigEntryAuthFailed` (HA parks the entry in `setup_error` and starts
  the reauth flow, whose `config_entry_reauth` repair issue is raised by
  HA itself). 401s **inside** the window still raise
  `ConfigEntryNotReady` and retry. The API handle is closed on every
  failure path (unchanged from WP-1.5/WP-3.1).
- `run_loxone_session`: a `LoxoneUnauthorisedError` from the live session
  now calls `config_entry.async_start_reauth(hass)` instead of logging and
  dying silently.
- **Deleted**: `CONFIG_SCHEMA` and the `async_setup` import-flow call;
  `async_set_options`.
- **CORE-30**: a leftover YAML `loxone:` block in `configuration.yaml`
  now registers a persistent, non-fixable repair issue
  (`yaml_config_present`, `async_setup` keeps registering the four domain
  services); `async_setup` never fired an `import` flow anyway, because
  no `async_step_import` ever existed (CORE-19) — the traceback at every
  startup for YAML users is gone.

### `custom_components/loxone/coordinator.py`
- Reads host/port/username/password/verify_ssl from `entry.data` (v5),
  with an `options` fallback for the migration window only.

### `custom_components/loxone/translations/{en,de,cs}.json`
- `config.step.user` (connection fields), `config.step.reauth_confirm`,
  `config.error.{cannot_connect,invalid_auth,invalid_host,
  invalid_username_encoding,invalid_password_encoding}` (CORE-18),
  preference-only `options.step.init`, and the `issues`
  section (`yaml_config_present`).

### Tests
- `tests/test_config_flow.py` (new, 8 tests): user flow success
  (v5 entry, data-vs-options split, serial unique_id, entry loads on the
  mocked connection), cannot-connect, invalid-auth, duplicate-serial
  abort, encoding errors (no connection attempted), reauth success
  (data updated in place, `reauth_successful`), reauth invalid-auth
  (stored credentials untouched), options flow (preferences only).
- `tests/test_config_entry_migration.py` (rewritten): v4→v5 data move,
  partial options get documented defaults, v1→v5 full chain in one update
  call, v5 is a no-op, migration never guesses `unique_id`,
  preferences + stored tokens survive.
- `tests/test_setup_retry.py`: `test_persistent_401_escalates_to_reauth` —
  attempts 1..4 keep the entry in `SETUP_RETRY`; the 5th (≥ 300 s out)
  lands the entry in `SETUP_ERROR`, with a reauth flow in progress and
  the `config_entry_reauth` repair issue raised. The retry/close
  regression (2 failures -> LOADED, `api.close` once per failed attempt)
  and the 503 test are unchanged.

## Verification (run on this branch)

```text
$ .venv/bin/python -m ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!
```

```text
$ .venv/bin/python -m ruff format --check .
65 files already formatted
```

```text
$ .venv/bin/python -m pytest -q
...
476 passed, 1 deselected, 4 warnings in 43.33s
```

No new `# noqa` added; `grep -rn "TODO(WP-3.4)" custom_components/`
returns nothing (WP-3.4 owns no marker set).

## VERIFY — requires a live-Miniserver check before merge

1. `test_loxone_connection` (user + reauth steps) performs a full
   bootstrap incl. the websocket, then closes it — confirm latency/cost
   on a real Miniserver (and that a Miniserver tolerates this
   open/close cadence; the Miniserver has a websocket connection cap).
2. A 401 from the live in-place reconnect loop now starts the reauth
   flow (log + `async_start_reauth`). Intended per CORE-09's "same rule
   inside the reconnect loop"; confirm with a booted-into-auth-window
   device (or a credential rotation) that no reauth flow is spawned
   spuriously for transient boot 401s.

## follow-ups (out of scope, not touched)

- `pyloxone_api/discover.py` has a `SyntaxError`
  (`except socket.timeout, TimeoutError, OSError:` line 55) and is UDP
  broadcast, not mDNS — the optional `zeroconf` step was therefore
  **skipped** as the plan allows; fix/replace it for WP-6.9.
- API-13 producer side (`_check_auth_response` in
  `pyloxone_api/connection.py`) is already in-tree from WP-2.x; this WP
  implemented the consumer side only (config-flow + setup + reconnect),
  as scoped.
- The reauth form does not re-take `verify_ssl` (it keeps the stored
  value); the options page is preference-only by design, so
  host-credential pairs are edited from `reauth_confirm`.
- README/hacs minimum-HA-floor and option tables: WP-5.4.
