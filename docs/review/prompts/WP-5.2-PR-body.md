# WP-5.2 — Repairs, availability polish, runtime_data

Branch: `wp/WP-5.2` (created from master at `95031b6`, the WP-3.4 merge; the
prerequisite WP-3.4 is present).

## Scope, per the plan

- **CORE-30** — repair issues: `yaml_config_present` (already created in
  WP-3.4; its translations verified by a new test), plus the two missing
  ones: `auth_failed` (present while reauth is pending) and
  `unsupported_firmware`. Translations in en/de/cs for all three.
- **CORE-31** — not done in WP-3.1; done here. The coordinator moves from
  `hass.data[DOMAIN][entry_id]` to `config_entry.runtime_data`, and the
  stale empty `hass.data["loxone"]` dict no longer survives the last
  unload.
- **API-17** (`killtoken` "if not done in 2.2") — **already done on
  master** (persists the token on every change via
  `token_change_callback`, incl. `unsecure_password`;
  `api.kill_token()` runs in `coordinator.async_cleanup()` before close,
  i.e. on unload/removal). This package verified it instead of
  re-implementing it; the stale `_persist_token_and_close` handler at HA
  stop remains as the shutdown-time safety net but is no longer the only
  persistence path (its duplicate was the API-10 removal candidate and is
  out of scope here, see Follow-ups).

## What changed, by file

- `custom_components/loxone/helpers.py` — new
  `MINIMUM_SUPPORTED_FIRMWARE = (7, 0, 0)`, `parse_firmware_version()`
  (both structure-file forms: string and list of parts) and
  `meets_minimum_firmware()` (True for unparseable/unknown; False only
  when a version is confirmed below the floor). **VERIFY**: see below.
- `custom_components/loxone/__init__.py`
  - CORE-30: `_async_report_auth_failure()` /
    `_async_clear_auth_failure_issue()` (per-entry id
    `auth_failed_<entry_id>`) called from the bounded-401 escalation
    branch of `async_setup_entry` **and** the live-session
    `LoxoneUnauthorisedError` handler; cleared on every successful setup
    (that is, when reauth completes). `_async_reconcile_firmware_issue()`
    creates/removes `unsupported_firmware_<entry_id>` from
    `coordinator.miniserver.software_version` on every successful setup.
  - CORE-31: the coordinator is stored on
    `config_entry.runtime_data`; the uuid- and device-based outbound
    lookups (`_loxone_coordinator_by_uuid`, `_resolve_outbound_target`),
    `LoxoneEntity._connection_coordinator` and `async_unload_entry` all
    read it from the entry. `_clear_auth_failure` now drops the whole
    `hass.data["loxone"]` key once the last auth-failure counter is
    cleared, so no stale dict survives (only that key ever lived there
    since coordinators moved).
- `custom_components/loxone/miniserver.py` —
  `get_miniserver_from_hass()` resolves via `config_entry.runtime_data`
  (returns `None` when the attribute is unset/None; signature kept).
- `custom_components/loxone/system_health.py` — iterates config entries'
  `runtime_data` instead of `hass.data[DOMAIN]` (the old code raised
  `KeyError` while no entry was loaded and only reported whatever dict
  entry happened to come first).
- `translations/en.json`, `de.json`, `cs.json` — `issues.auth_failed` and
  `issues.unsupported_firmware` (on top of the existing
  `issues.yaml_config_present`).
- `tests/test_repairs.py` (new) — firmware-floor pure-function tests
  (hand-derived literal versions), translation-coverage test for all
  three keys in all three languages, the yaml-block issue, the full
  escalation → issue → reauth-flow → working-credentials → issue-gone
  lifecycle, the live-session-401 path, and the below/above-floor
  firmware creation/removal.
- `tests/test_runtime_data.py` (new) — coordinator on `runtime_data`
  with no `hass.data` twin; `runtime_data` and the domain dict gone
  after unload; `get_miniserver_from_hass` degrades without the
  attribute; the auth-failure bookkeeping drops the whole
  `hass.data["loxone"]` key when the last counter clears (single- and
  two-entry variants).
- `tests/conftest.py`, `tests/test_init.py`, `tests/test_reconnect.py`,
  `tests/test_multi_instance.py`, `tests/test_connection_unit.py` —
  coordinators are now reached via `entry.runtime_data` (the
  `hass.data[DOMAIN]` reads were removed/made negative; the multi-
  instance and unload tests additionally assert `runtime_data` is gone).
- `CHANGELOG.md` — line added under *Unreleased*.

## Acceptance

- repair issues for `yaml_config_present`, `auth_failed` (until reauth
  completes), `unsupported_firmware` — **yes**, each with en/de/cs
  translations (test `test_repair_translations_in_every_language` proves
  all three keys exist in all three shipped languages).
- Every acceptance behaviour has a test that fails before the change
  (verified by stashing the `custom_components/` change and running the
  new tests: collection/import error for `test_repairs.py`, 4 failed /
  1 passed for `test_runtime_data.py`) and passes after.

## Verification results

`ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006`

```
All checks passed!
```

`ruff format --check .`

```
67 files already formatted
```

`pytest -q`

```
TOTAL                                                           1620    674    58%
Required test coverage of 20% reached. Total coverage: 58.40%
496 passed, 1 deselected, 4 warnings in 65.66s (0:01:05)
```

(the 1 deselected is the `online`-marked test, as usual)

## VERIFY items (live-Miniserver check required before merge)

- **`MINIMUM_SUPPORTED_FIRMWARE = (7, 0, 0)`** (helpers.py): the floor
  for the `unsupported_firmware` repair issue is *assumed* to be 7.0.0 —
  the JSON websocket API with public-key token auth and the
  `jdev/sys/killtoken` / keep-alive commands appeared with Miniserver
  firmware 7.x, but the exact floor (e.g. that 7.0.x actually serves the
  files the connection layer downloads) has not been checked against a
  real Miniserver. The helper is named and unit-tested for its intended
  semantics; confirm the constant on hardware before relying on the
  issue.
- **`kill_token()` command form** (inherited from the earlier API-17
  work, kept intact here): the `jdev/sys/killtoken/<token>/<user>` form
  is taken from Loxone's legacy 32-char-token docs and must be confirmed
  to also cancel JSON-web (SHA256) tokens.

## Follow-ups (out of scope for this package, not fixed)

- `__init__.py::_persist_token_and_close` (the STOP-event token writer)
  is now redundant with the coordinator's on-change persistence; it
  still writes `token`/`hash_alg`/`valid_until` without `unsecure_password`
  at shutdown. Removing/simplifying it is a separate cleanup (API-10
  removal of `stop_event` is its natural ticket).
- The `yaml_config_present` repair issue (WP-3.4's) is `is_persistent`
  and is never *deleted*: if the user removes the YAML block, the issue
  stays until HA is restarted or the issue is dismissed. Consider
  deleting it in `async_setup` when the key is absent.
- `system_health.py` still reports a single Miniserver (the first loaded
  one) — a per-entry output format is a separate decision.
- `except TypeError, ValueError:` (unparenthesised) occurs in
  `__init__.py::async_migrate_entry` (line ~416) and `helpers.py`
  (`json_decoder`); Python 3.14.2 accepts it (verified empirically) but
  ruff/teachers flag it as obsolete syntax — a WP-5.3 hygiene candidate.

## No `# noqa` added; `manifest.json` untouched; no assumption-based
behaviour changes (the one new threshold is behind the named helper with
a VERIFY flag).
