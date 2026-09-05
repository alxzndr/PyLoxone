# PyLoxone remediation plan (2026-09-03)

This plan turns the findings in `2026-09-findings.md` into work packages (WPs) that one agent can
complete in one session each. Finding IDs (`API-01`, `CORE-05`, `PS-02`, `PC-01`, `TOOL-07`) refer to
that catalogue; read the referenced entries before starting a WP.

## How to work a package

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

## Decisions taken for this plan (override only with maintainer input)

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

## Phase overview and dependencies

```
Phase 0  Foundations            WP-0.1 ──┬── WP-0.2 (needs 0.1 dev requirements)
                                        └── WP-0.3 (parallel-safe with 0.2)
Phase 1  Critical safety        WP-1.1 … WP-1.5                  (all parallel-safe; need 0.2 fixtures for tests)
Phase 2  Connection layer       WP-2.1 → WP-2.2 → WP-2.3         (sequential; one agent owns pyloxone_api/)
Phase 3  Lifecycle & identity   WP-3.1 → WP-3.2 → WP-3.3 → WP-3.4 (sequential; WP-2.3 lands before 3.1 is finalised)
Phase 4  Platform correctness   WP-4.1 … WP-4.5                  (parallel-safe by file; need 1.1 + 1.3)
Phase 5  Compliance & hygiene   WP-5.1 … WP-5.4                  (after Phase 3 and 4)
Phase 6  Feature gaps           WP-6.x                            (optional, after 5)
```

Phases 1 and 2 can run concurrently with Phase 0.2/0.3 once 0.1 is merged. Phase 4 packages can
start as soon as 1.1 and 1.3 are merged. Agents working Phase 4 must not touch `__init__.py`,
`coordinator.py`, `helpers.py` or `pyloxone_api/` (owned by Phases 2–3).

---

## Phase 0 — Foundations

### WP-0.1 CI, lint and test harness baseline
- **Findings**: TOOL-01, TOOL-04, TOOL-05, TOOL-06, TOOL-07, TOOL-09, TOOL-10, TOOL-11, TOOL-12, PS-01, API-03, CORE-12 (print only), PC-34.
- **Files**: `.github/workflows/ci.yaml` (new), `.github/workflows/hassfest.yaml`, `validate.yaml`, `stale.yaml`, `.github/dependabot.yml` (new), `.gitignore`, `ruff.toml`, `pytest.ini`, `requirements.txt`, `requirements-dev.txt` (new), `.pre-commit-config.yaml` (new), `.devcontainer.json`, `.vscode/settings.json`, `scripts/lint`, `tests/test_python_compatibilty.py` (delete), `custom_components/loxone/pyloxone_api/tests/test_run_alone.py` (delete), `switch.py` (import only), `fan.py` (imports only), `connection.py:282` (one line), `coordinator.py:86` (one line).
- **Steps**:
  1. Fix `.gitignore` first (TOOL-05) or the new YAML files will not stage.
  2. `requirements-dev.txt`: `pytest-homeassistant-custom-component==0.13.355`, `ruff==0.15.20`, `pre-commit`, `debugpy`, `colorlog`. `requirements.txt` mirrors `manifest.json` runtime deps only.
  3. `pytest.ini`: `testpaths = tests custom_components/loxone/pyloxone_api/tests`, `addopts = -m "not online" --cov=custom_components/loxone --cov-report=term-missing --cov-fail-under=15`, `asyncio_mode = auto`, `markers = online: requires a reachable Miniserver`.
  4. `ruff.toml`: keep `select = ["ALL"]`; add `[lint.per-file-ignores] "tests/**" = ["S101","ANN","D","PLR2004"]`. CI blocks on the defect subset only (see step 6).
  5. Fix the four one-line defects (PS-01 `from typing import Any`; PC-34 voluptuous annotations; API-03 logging placeholder; delete the `print`).
  6. `ci.yaml` jobs: `lint` (blocking `ruff check` over the **blocking set** defined below; full `ruff check --statistics` advisory with `continue-on-error`; `ruff format --check`), `test` (Python 3.14 only — HA 2026.8.1 refuses < 3.14.2), `hassfest`, `hacs` (`hacs/action@22.5.0`), `translations` (de.json key parity vs en.json), `manifest-version` (on tag push: manifest version == tag). Pin `actions/checkout@v7`, `actions/setup-python@v7`, `actions/stale@v11`. Re-enable the stale cron or delete the workflow.
  7. **Blocking set and the ratchet.** The blocking set is `F,E9,PLE,B,T20,S307,ASYNC,RUF006`. Getting it to zero is a WP-0.1 deliverable, but WP-0.1 must not implement behavioural fixes owned by later packages. Resolve the two rules that collide with other packages by *marking*, not fixing:
     - `S307` (6 `eval()` sites, PC-01, owned by **WP-1.2**) and `RUF006` (8 unstored `asyncio.create_task` calls, CORE-03/API-14, owned by **WP-2.2** and **WP-3.1**): add `# noqa: <RULE>  # TODO(WP-x.y)` at each site naming the owning package. Do not restructure the code.
     - Everything else in the blocking set is mechanical and behaviour-preserving: fix it repo-wide. Take `F401` from `ruff check --select F401 --fix` output only; never hand-delete an import you have not proved unused, and re-run `pytest -q` afterwards (HA re-exports such as `PLATFORM_SCHEMA` are assigned, so they are not flagged).
     Touching ~18 files for mechanical lint is expected and acceptable: WP-0.1 lands before any Phase 1 package starts, so the conflicts are with unwritten work.
  8. Do **not** widen the blocking set beyond those eight rules. `ruff.toml` keeps `select = ["ALL"]` for the advisory job; WP-5.3 ratchets additional rule groups in later.
  7. One separate commit `style: ruff format` and add its SHA to `.git-blame-ignore-revs`.
  8. Devcontainer image `python:3.14`; fix both `extraPaths`.
- **Acceptance**: CI green on the branch; `pytest -q` collects both test trees with `test_discover` deselected; `ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006` reports 0 (with the 14 marked sites carrying a `TODO(WP-x.y)` noqa); `ruff format --check` clean; `grep -rn "TODO(WP-" custom_components/` lists exactly the 6 S307 and 8 RUF006 sites and nothing else.

### WP-0.2 Test fixtures and contract tests
- **Findings**: TOOL-08 (foundation), TOOL-16, plus contract tests for CORE-07, CORE-23, CORE-24, PS-14.
- **Files**: `conftest.py`, `tests/conftest.py` (new), `tests/fixtures/LoxAPP3.json` (new), `tests/test_contracts.py` (new), `tests/test_message_parsing.py` (new).
- **Steps**:
  1. Build `tests/fixtures/LoxAPP3.json`: `msInfo` (fake serial, `miniserverType: 2`, list-form `softwareVersion`), `rooms`, `cats`, and one representative control of each type listed in the coverage tables of the findings catalogue: `InfoOnlyAnalog` (°C and kWh variants), `InfoOnlyDigital`, `TextInput`, `Meter`, `ClimateController`, `IRoomControllerV2` (with `IRCV2Daytimer` sub-control — seed from README lines 274-346), `IRoomController`, `AcControl`, `PresenceDetector`, `SmokeAlarm`, `Switch`, `TimedSwitch`, `Pushbutton`, `Intercom`, `Slider`, `Radio` (seed from `tests/test_select_mapping.py`), `Jalousie`, `Window`, `Gate`, `LightControllerV2` (sub-controls: `Dimmer`, `Switch`, `ColorPickerV2` ×3 picker types), `Dimmer`, `EIBDimmer`, `Alarm`, `Ventilation` (with presence), `AudioZoneV2`. Every uuid must be unique and fake.
  2. `tests/conftest.py` fixtures: `loxapp3` (session, loads the file), `mock_connection` (patches `LoxoneConnection.open` to set `structure_file` from the fixture and skip auth; exposes `feed(uuid, value)` that pushes a state update through whatever entry point the integration uses at the time — bus event now, dispatcher after WP-3.2), `mock_entry` (`MockConfigEntry(domain="loxone", version=4, options={...})`), and `enable_custom_integrations` (from phcc).
  3. `tests/test_contracts.py`: every `custom_components/loxone/*.py` defining `async_setup_entry` is in `LOXONE_PLATFORMS` and vice versa (fails today on `text.py`); every `manifest.requirements` name is imported somewhere (fails on `httpx`); `de.json` keys ⊇ `en.json` keys (fails on `sync_areas`); every `services.yaml` key has an `en.json` entry; every `device_type` literal in `__init__.py`'s grouping table is produced by some platform (fails today); `manifest.version` is valid semver; all 14 platform modules import cleanly.
  4. `tests/test_message_parsing.py`: `MessageHeader` valid/invalid/`UNKNOWN` (assert `payload_length` exists — fails today), `ValueStatesTable` with `struct.pack("<d", ...)` vectors, `TextStatesTable` padding for `text_length ∈ {0,1,3,4,5}`, `LLResponse` code/Code/nested value, `parse_message` dispatch, `LoxoneToken.seconds_to_expire`, `LxJsonKeySalt` with and without `hashAlg` (#498 regression).
- **Acceptance**: fixture loads; contract tests exist and the ones expected to fail are marked `xfail(strict=True)` with the finding ID in the reason, so later WPs flip them to pass; coverage floor can be raised to 20.

### WP-0.3 Packaging, metadata, translations, docs baseline
- **Findings**: TOOL-02, TOOL-03/CORE-25, TOOL-13 (logger name + version line), TOOL-14 (CHANGELOG, CONTRIBUTING, feature-request template), CORE-23, CORE-24, TOOL-15.
- **Files**: `custom_components/loxone/manifest.json`, `hacs.json`, `README.md`, `CHANGELOG.md` (new), `CONTRIBUTING.md` (new), `.github/ISSUE_TEMPLATE/*`, `.github/workflows/release.yaml` (new), `custom_components/loxone/translations/{en,de,cs}.json`, `custom_components/loxone/services.yaml`, `custom_components/loxone/__init__.py` (delete `REQUIREMENTS` list only).
- **Steps**: manifest → `iot_class: local_push`, drop `httpx`, `websockets>=14,<16`, `pycryptodome>=3.20`, `dependencies: ["group"]`, `integration_type: hub`, `loggers: ["custom_components.loxone"]`, `version: 0.9.23`; `hacs.json` → `homeassistant: "2026.7.0"`; README → version line, logger snippet `custom_components.loxone.pyloxone_api`; translations → add `services.sync_areas` to de, add `config.error`/`options.error` keys (`invalid_username_encoding`, `invalid_password_encoding`) to all three, remove `single_instance_allowed`, remove `re\-`; `release.yaml` → on tag push, verify (or write) manifest version = tag and create the GitHub release with generated notes; `CHANGELOG.md` seeded with an *Unreleased* section.
- **Acceptance**: hassfest and HACS jobs pass; translation parity test from WP-0.2 passes for de; `manifest-version` job passes on a test tag.

---

## Phase 1 — Critical safety fixes (parallel-safe)

### WP-1.1 `LoxoneEntity` base class hardening
- **Findings**: CORE-01, CORE-02, PS-12, PS-22, CORE-26 (only the `cached_property` removal; keep names identical), PS-23, CORE-32 (`_clean_unit` duplicate only).
- **Files**: `custom_components/loxone/__init__.py` (class `LoxoneEntity` only), plus the minimum edits in subclasses that assign `self.name = ...` (`switch.py:369`, `scene.py`) to use `_attr_name`, and deletion of per-class `should_poll` overrides (`sensor.py`, `switch.py`, `number.py`, `text.py`, `select.py`, `cover.py`).
- **Steps**: replace `sys.exit` with `_LOGGER.exception` + continue and fix the "Could set" message; `self.async_on_remove(self.hass.bus.async_listen(EVENT, self.event_handler))`, delete `async_will_remove_from_hass`; `_attr_should_poll = False`; `_unrecorded_attributes = frozenset({"uuid","platform","room","category","state_uuid","device_type"})`; replace the `name`/`unique_id` `cached_property` overrides with `self._attr_name`/`self._attr_unique_id = uuidAction` set in `__init__` — verify with a grep that no subclass still reads `self.uuidAction` *through* `unique_id` (PC-04 does; leave that to WP-3.3 but keep behaviour identical here); delete `LoxoneEntity._clean_unit`/`_get_format` in favour of `helpers.clean_unit`.
- **Acceptance**: HA-harness test that sets up the fixture entry, records `len(hass.bus.async_listeners()["loxone_event"])`, reloads the entry three times, and asserts the count is constant; test that constructing an entity with a kwarg whose `setattr` raises does not raise `SystemExit`; all entity ids and unique ids unchanged versus a snapshot taken before the change.

### WP-1.2 Remove `eval()` from the light platform
- **Findings**: PC-01, PC-21.
- **Files**: `lights/lightcontroller.py`, `lights/colorpickers.py`, `helpers.py` (add `parse_loxone_color(s) -> tuple[str, float, float, float | None]`), `tests/test_light_parsing.py` (new).
- **Steps**: `json.loads` for `activeMoods`/`moodList`/`additionalMoods` (no `replace` hacks, never write back into `event.data`); regex parser for `hsv(...)`/`temp(...)`; malformed input → log at debug and skip.
- **Acceptance**: unit tests for the parser (valid hsv, valid temp, garbage, injection string `__import__('os')` must not execute); `ruff check --select S307` reports 0 with **no** `# noqa` remaining, i.e. `grep -rn "TODO(WP-1.2)" custom_components/` returns nothing (WP-0.1 left six markers on the `eval()` sites).

### WP-1.3 Initial-state correctness (entity half of #475)
- **Findings**: PS-02, PS-11, PS-15 (sentinel only), PS-03 + CORE-07, PS-09 (`ERROR_VALUE`/`None`), PS-20 (`STATE_UNKNOWN` string only), PC-22, PS-13 (`@callback` on the handlers touched).
- **Files**: `switch.py`, `binary_sensor.py`, `number.py`, `text.py`, `const.py` (add `Platform.TEXT`), `sensor.py` (event handlers and the two diagnostic sensors), `lights/colorpickers.py` (color_mode init), `tests/test_entity_state.py` (new).
- **Steps**: no entity may report a concrete state before its first value: `_attr_is_on = None`, `is_on` → `None` while unknown, `native_value` → `None`; availability flips only when the *state* uuid (not `uuidAction`) has been seen; `ERROR_VALUE`/`None` → `None`; `text.py` writes `_attr_native_value`, listens on `states["text"]`, sets `native_max`; delete the `_attr_state`/`_attr_assumed_state` lines.
- **Acceptance**: for each touched class, a test constructs it from the fixture, asserts HA state is `unknown`/`unavailable` before any event, feeds a fake event with the real state uuid, asserts the correct state, and asserts a `uuidAction`-only event does *not* make it available; `text.*` entities appear after setup (flips the WP-0.2 xfail).

### WP-1.4 Secrets and privacy
- **Findings**: CORE-08, CORE-21, API-05.
- **Files**: `diagnostics.py`, `system_health.py`, `pyloxone_api/connection.py` (callback filter at 744-749 and the ERROR log at 1421-1426), `pyloxone_api/websocket_protocol.py:47`, `__init__.py:368-371` (debug log only), `tests/test_diagnostics.py` (new).
- **Steps**: diagnostics for the *requested* entry with `async_redact_data` over `{serialNr, localUrl, remoteUrl, projectName, mac, token, password, username, key, salt}` and redacted entry options; system health aggregates all entries with `.get()`; protocol/auth text responses never reach the external callback; token dicts redacted in logs; `Sent:` debug line truncated to the command name.
- **Acceptance**: diagnostics test asserts no fixture serial/URL appears in the output and the right entry is returned when two are loaded; a connection-layer test feeds a fake `gettoken` response and asserts the callback is not invoked.

### WP-1.5 Transient 401 during setup must retry, not die (incident 2026-09-02)
- **Findings**: CORE-09 (retry + close parts only; reauth stays in WP-3.4).
- **Files**: `custom_components/loxone/__init__.py` (the `try/except` block around `async_config_entry_first_refresh`, lines 257-298 only — WP-1.1 owns the `LoxoneEntity` class further down the same file; rebase carefully), `tests/test_setup_retry.py` (new). Read `2026-09-02-pyloxone-401-setup-error.md` at the repo root first.
- **Steps**: delete the `return False`; every failure branch awaits `coordinator.api.close()` (wrap the whole block so it cannot be missed); `LoxoneUnauthorisedError` → increment a per-entry consecutive-auth-failure counter with first-failure timestamp stored in `hass.data[DOMAIN]` under a key that survives the retry (HA re-creates the coordinator on each attempt, so do not store it on the coordinator), log at WARNING "Miniserver answered 401 during setup; retrying (attempt n)", and raise `ConfigEntryNotReady`; when the counter reaches 5 **and** at least 5 minutes have elapsed since the first failure, log at ERROR with guidance to check credentials and keep raising `ConfigEntryNotReady` (WP-3.4 later replaces this branch with `ConfigEntryAuthFailed`); reset the counter on successful setup. Keep the existing `LoxoneServiceUnAvailableError`/`OSError`/`TimeoutError` behaviour.
- **Acceptance**: harness test where the patched `LoxoneConnection.open` raises `LoxoneUnauthorisedError` twice then succeeds → entry reaches `LOADED` after HA's retries (use `async_fire_time_changed` to advance the retry timer) and `api.close` was awaited once per failed attempt; test where it raises 401 forever → entry stays `SETUP_RETRY`, never `SETUP_ERROR`, and after the fifth attempt an ERROR log record with the word "credentials" exists; the 503 path still raises `ConfigEntryNotReady`.

---

## Phase 2 — Connection layer (`pyloxone_api/`, one agent, sequential)

### WP-2.1 Connection correctness quick wins
- **Findings**: API-01, API-04, API-07, API-10, API-11, API-12, API-15, API-18, API-19, API-20, API-21, API-22, API-23, API-24, API-25 (comment), API-26.
- **Files**: `pyloxone_api/connection.py`, `message.py`, `websocket_protocol.py`, `loxone_token.py`, `const.py`, `exceptions.py`, `helper.py` (delete), `api.py` (delete), `__main__.py`, `coordinator.py` (only the `open()` call site), `tests/test_connection_unit.py` (new).
- **Steps** (in this order): store `self.connection`/`self._session` in `open()` and make `start_listening` reuse them; `send(command)` not `send([command])`; delete line 1319; guard-and-raise in `_send_text_command`; scheme/host/port from the redirected URL; `urllib.parse.quote` the username (keep a **VERIFY** note on UTF-8 vs latin-1); `_secured_queue` → `deque` of parameter dataclasses, cleared in `close()`; `MessageHeader` always sets `payload_length`; strict header→body reads (port the logic from the dead `recv_message`, then delete it); `json.loads` off-loop via an injectable `loads` callable (coordinator passes `hass.async_add_executor_job`); `"<d"`; simplify decoding; delete dead files/constants/exceptions; rename `send_secured__websocket_command` → `send_secured_websocket_command` with a deprecated alias; initialise `_session_key`.
- **Acceptance**: unit tests per the catalogue's API test-gap list — URL-building table incl. the Cloud-DNS `http` redirect case, encryption round-trip asserting `send` receives a `str`, `_hash_token` returns `None` on a non-hex key and the `getvisusalt` handler leaves `_key` untouched, `close()` idempotent; HA-harness test asserts exactly one `wslib.connect` call per setup.

### WP-2.2 Clean-close detection, auth failure handling, logging hygiene (#514)
- **Findings**: API-02, API-27, API-14, API-16, API-13, API-06, API-08, API-17.
- **Files**: `pyloxone_api/connection.py`, `pyloxone_api/exceptions.py` (add `LoxoneReconnectRequested`), `pyloxone_api/const.py`, `coordinator.py` (token-changed callback → `async_update_entry` with `**data`), `tests/test_connection_lifecycle.py` (new).
- **Steps**: detect the normal end of the `async for` and raise `LoxoneConnectionClosedOk`; `asyncio.wait(FIRST_COMPLETED)`; typed handlers: expected closes → INFO once, control-flow reconnect → DEBUG, real errors → WARNING with the first traceback at DEBUG; `_process_message` awaits each send; tracked task set with done-callback; `check_refresh_token` waits for an authenticated event, awaits the refresh, escalates after N failures; check `code` on every auth response and raise `LoxoneUnauthorisedError` for 401/4003; `ping_interval=None`; retry loop → 3 tries with backoff, all three GETs; token persisted via callback whenever it changes (incl. `unsecure_password`); `killtoken` on entry removal (`async_remove_entry` hook).
- **Acceptance**: with a stub connection whose `__aiter__` ends cleanly, `start_listening` raises `LoxoneConnectionClosedOk` within 1s (not 30s) and the log contains no ERROR records (`caplog`); wrong-password stub → `LoxoneUnauthorisedError` within the open timeout; token change invokes the persistence callback; `grep -rn "TODO(WP-2.2)" custom_components/` returns nothing (the tracked-task set replaces WP-0.1's `RUF006` markers in `pyloxone_api/`).

### WP-2.3 In-place reconnect with availability
- **Findings**: API-09, CORE-28, CORE-05 (final removal of reload-on-error).
- **Files**: `pyloxone_api/connection.py` (new `run()` supervisor or `start_listening` loop), `coordinator.py` (`connected` flag + `async_dispatcher_send` on change), `__init__.py` (delete `_reload_after_delay`/`handle_task_result` reload paths; `LoxoneEntity.available`), `tests/test_reconnect.py` (new).
- **Design**: `LoxoneConnection.run(on_state)` loops: `open` → auth → `enablebinstatusupdate` → listen; on any recoverable exception close the socket, call `on_state(False)`, sleep `min(2**n, 300)` s with jitter, retry; `on_state(True)` after `enablebinstatusupdate`; `LoxoneUnauthorisedError` is *not* recoverable → propagate so the integration can start reauth. Entities read `available` from the coordinator; existing per-entity `_attr_available` logic stays for "value not yet seen".
- **Acceptance**: harness test drops the stub socket mid-session and asserts entities go `unavailable`, no entity is removed from the registry, and after reconnect they return to their previous state; reload count of the config entry stays 0 through five simulated drops.

---

## Phase 3 — Lifecycle, multi-instance, identity, config flow (sequential)

### WP-3.1 Setup / unload / reload lifecycle
- **Findings**: CORE-03, CORE-06, CORE-10, CORE-12, CORE-13, CORE-14, CORE-29, CORE-31 (optional), CORE-05 (interim `async_schedule_reload(entry_id)` if WP-2.3 is not yet merged). CORE-09's retry/close fix lands in WP-1.5; keep its counter logic intact here.
- **Files**: `__init__.py`, `coordinator.py`, and deletion of the six stub `async_setup_platform` functions plus `PLATFORM_SCHEMA` in `alarm_control_panel.py`, `climate.py`; `tests/test_init.py` (new).
- **Steps**: `entry.async_create_background_task` for the listener; every listener via `entry.async_on_unload`; `async_at_started` for the startup hook; close the API on every setup-failure branch; spread `**entry.data`; coordinator gets `config_entry=`, drops the override of `async_config_entry_first_refresh` in favour of `_async_setup`, guards `api is None`, uses `.get()`; unload = `async_unload_platforms` first, cleanup only on success; delete the `async_load_platform` loop; add the options update listener; migrate to `entry.runtime_data` if time permits.
- **Acceptance**: setup/unload leaves `hass.bus.async_listeners()` counts for `loxone_event`, `EVENT_HOMEASSISTANT_STOP`, `EVENT_HOMEASSISTANT_STARTED` at baseline; five reloads leave no extra tasks (`asyncio.all_tasks()` diff); `LoxoneServiceUnAvailableError` on open → `ConfigEntryNotReady` **and** `api.close` awaited; changing an option triggers a reload; `grep -rn "TODO(WP-3.1)" custom_components/` returns nothing (background tasks are now stored via `entry.async_create_background_task`, clearing WP-0.1's `RUF006` markers in `__init__.py`).

### WP-3.2 Multi-instance isolation (#491)
- **Findings**: CORE-04, CORE-11, CORE-27, PS-13, PS-18 (dispatcher part).
- **Files**: `__init__.py`, `coordinator.py`, `const.py`, every platform's `event_handler` (mechanical: subscribe via `async_dispatcher_connect` to own uuids; `@callback`), `sensor.py` (`LoxoneClimateController` fan-out), `tests/test_multi_instance.py` (new).
- **Design**: coordinator owns one listener on the connection; for each `{uuid: value}` it calls `async_dispatcher_send(hass, f"loxone_{entry_id}_{uuid}", value)` and still fires the public `loxone_event` (unchanged payload, plus `entry_id`). `LoxoneEntity.async_added_to_hass` connects to each of its state uuids. Outbound: `LoxoneEntity._send(value, secured=False, code=None)` → `self.coordinator.api.send_...`. Domain services registered once in `async_setup`; per call, resolve the coordinator from the entity registry entry's `config_entry_id` (or, for a raw `uuid`, the entry whose structure file contains it); `vol.Schema` with exactly-one-of `uuid`/`device`; `ServiceValidationError` otherwise. `loxone.reload` reloads only the entries targeted (default: all, but each via `async_schedule_reload`). The `loxone_send*` bus listeners stay for external users and forward only uuids known to that entry.
- **Acceptance**: two fixture entries with distinct serials: both `LOADED`; unloading B leaves all seven services registered; a command from an entity of A reaches only A's `send_websocket_command` mock; a state update fed to B does not touch A's entities; per-event listener invocations for an entity equal 1 (measured with a counting stub).

### WP-3.3 Device registry and identity
- **Findings**: CORE-16, CORE-20, PC-04, PC-05, CORE-17, CORE-15, PS-14, PS-10/PC-35, PS-20 (device link), PS-17 (device link).
- **Files**: `miniserver.py`, `helpers.py` (`get_or_create_device` → `device_info_for(entry, uuid, name, model, room, via=serial)` returning a fresh `DeviceInfo`), `binary_sensor.py` (stop overwriting `uuidAction`), `fan.py` (parent-first), `sensor.py`, `lights/colorpickers.py:49-53,160-164`, `cover.py`, `lights/lightcontroller.py`, `__init__.py` (group creation), `const.py` (device-type constants), `tests/test_devices.py` (new).
- **Steps**: call `async_update_device_registry` from setup; identifiers `{(DOMAIN, serial)}` only, no fake MAC; type-aware `software_version`; fresh `DeviceInfo` per entity with `via_device`; parent constructed before sub-entities; `device_class` properties that return type strings deleted; group creation fixed and moved behind a new option `generate_groups` (default off for new entries, on for migrated ones); dead dispatcher wiring deleted.
- **Acceptance**: snapshot of the device registry after setup shows one Miniserver device with `sw_version`, and one device per control with the control's own name/model/area and `via_device` set; the Ventilation device is named after the fan (regression for PR #513); `group.loxone_dimmers` contains dimmers and `group.loxone_analog` is non-empty when the option is on; the WP-0.2 `device_type` contract test passes.

### WP-3.4 Config flow rewrite with reauth and unique id
- **Findings**: CORE-19, CORE-18, CORE-09 (reauth escalation only: replace WP-1.5's "keep raising NotReady after 5 attempts / 5 min" branch with `ConfigEntryAuthFailed`; a 401 inside the transient window must still retry), API-13 consumer side, CORE-30 (YAML repair issue).
- **Files**: `config_flow.py`, `__init__.py` (`async_migrate_entry` v5, `ConfigEntryAuthFailed`, delete `async_setup` import + `CONFIG_SCHEMA`), `coordinator.py` (read from `entry.data`), `translations/*`, `tests/test_config_flow.py` (new), `tests/test_config_entry_migration.py` (extend).
- **Design**: hand-written `ConfigFlow` v5: `user` step (host/port/user/password/verify_ssl) → test connection with `LoxoneConnection.open` → read `msInfo.serialNr` → `async_set_unique_id(serial)` + `_abort_if_unique_id_configured()` → create entry with data; errors `cannot_connect`, `invalid_auth`, `invalid_username_encoding`, `invalid_password_encoding`; `reauth`/`reauth_confirm` steps; optional `zeroconf` step using `discover.py` if the Miniserver advertises via mDNS (**VERIFY**; skip if not). `OptionsFlow` with only `generate_scenes`, `generate_scenes_delay`, `generate_lightcontroller_subcontrols`, `generate_groups`. Migration v4→v5 moves connection keys from options to data and sets `unique_id` from the stored serial if available (else leave `None` and set it on next successful setup).
- **Acceptance**: flow tests for success, cannot-connect, invalid-auth, duplicate serial abort, reauth success; migration test v1→v5; `ConfigEntryAuthFailed` from setup starts a reauth flow; an entry created under v4 still loads after migration.

---

## Phase 4 — Platform correctness (parallel-safe by file)

Prerequisites: WP-1.1 and WP-1.3 merged. Each package owns its files exclusively.

### WP-4.1 Climate
- **Findings**: PC-02, PC-10, PC-12, PC-13, PC-16 (climate lines), PC-20, PC-23, PC-24, PC-25, PC-26 (**VERIFY**), PC-28, PC-32, PC-41 (climate lines).
- **Files**: `climate.py`, `sensor.py` (`LoxoneClimateController` only, `hass` kwarg removal), `tests/test_climate.py` (new).
- **Steps**: extract pure helpers first — `temperature_unit_from_format(fmt)`, `legacy_mode_to_hvac`/`hvac_to_legacy_mode`, `capabilities_to_hvac_modes(bits)`, `plan_set_temperature(op_mode, active, kwargs, state) -> list[str]` — then fix the call sites; `.get()` everywhere; stable preset literals; `hvac_action` fallback from the controller's own states when no demand event has arrived.
- **Acceptance**: parametrised tests over the planner covering every branch incl. `target_temp_low` alone in BUILDING_PROTECT (NameError regression); `"°C"` → Celsius; unknown `activeMode` value leaves the previous state and does not raise; `target_temperature` is never `None` while `TARGET_TEMPERATURE` is advertised; AcControl from the fixture without `fanspeeds` sets up and writes state.

### WP-4.2 Cover
- **Findings**: PC-07 (Gate **VERIFY**), PC-14, PC-15, PC-16 (cover lines, incl. #501), PC-19, PC-27, PC-36, PC-40/PC-41 (cover lines).
- **Files**: `cover.py`, `const.py` (feature bits), `services.yaml` (target selector narrowed to Jalousie via `required_features` — keep `domain: cover`), `tests/test_cover.py` (new).
- **Acceptance**: Window `stop_cover` sends `stop`; calling `quick_shade` on a Gate raises `ServiceValidationError`/is filtered by `required_features` rather than `AttributeError`; a Window fixture without `targetPosition` sets up and updates; `animation → device_class` table test; position inversion identities; the structure dict is unchanged after entity construction (deep-compare).

### WP-4.3 Lights
- **Findings**: PC-03, PC-05, PC-11, PC-17, PC-18, PC-33, PC-37, PC-38, PC-39, PC-43 (standalone `ColorPickerV2` only), PC-40 (light lines), PS-10 (lightcontroller `device_class`).
- **Files**: `light.py`, `lights/*.py`, `helpers.py` (brightness mapping functions only), `scene.py` (switch from the `device_class` marker to an explicit attribute), `tests/test_lights.py` (new).
- **Steps**: extract `plan_turn_on(kwargs, color_mode, hs, kelvin, brightness) -> str` per picker type; `lox_to_hass_range`/`hass_to_lox_range` using `map_range` with min/max, `max(1, …)` floor; create standalone `ColorPickerV2` entities via `get_all`.
- **Acceptance**: brightness-only `turn_on` on an RGB picker with unknown colour mode emits `setBrightness/…` (PR #512 regression); `(90, 10, 90) → 255` and `255 → 90`; brightness 1 never maps to 0; TunableWhite `turn_on` before any state does not raise; standalone picker gets a device with a string identifier.

### WP-4.4 Fan, alarm, media player
- **Findings**: PC-08, PC-09 (**VERIFY** command), PC-29 (**VERIFY**), PC-30, PC-06, PC-31 (**VERIFY**), PC-16 (alarm/media lines), PC-40/PC-41 (fan/alarm/media lines), PC-43 (`STOP` feature only).
- **Files**: `fan.py`, `alarm_control_panel.py`, `media_player.py`, `tests/test_fan_alarm_media.py` (new).
- **Acceptance**: `supported_features` includes TURN_ON/TURN_OFF; `set_preset_mode("Auto")` sends a command; `percentage` is an `int` in 0..100; secured alarm reports `CodeFormat.NUMBER` and `code_arm_required is True` without evaluating properties in a particular order; `play_state_to_media_player_state` table test incl. unknown values; alarm fixture without `nextLevelAt` sets up.

### WP-4.5 Sensor, binary sensor, switch, select, number, button, scene
- **Findings**: PS-04, PS-05, PS-06, PS-07, PS-08, PS-09 (remainder), PS-15 (remainder), PS-16, PS-17, PS-19, PS-21, PS-24, PS-25, CORE-22, CORE-32 (`map_range`/`get_all` hardening from the fuzzing PR).
- **Files**: `sensor.py`, `binary_sensor.py`, `switch.py`, `select.py`, `number.py`, `button.py`, `scene.py`, `helpers.py` (`get_all`, `map_range`, `iter_controls`), `tests/test_simple_platforms.py` (new).
- **Acceptance**: a `SmokeAlarm` fixture reports `on` when `level > 0` and ignores `areAlarmSignalsOff`; a Meter without `storageFormat` still yields the other sub-sensors; Meter registers have the expected device/state classes; `override_reason` states are slugs and translate; a `Radio` without outputs is skipped with a log; scenes exist immediately after setup with a device link; YAML sensor without `name` has a unique id; `get_all({}, "Switch") == []`; `map_range` with equal bounds does not divide by zero.

---

## Phase 5 — Compliance and hygiene

### WP-5.1 `has_entity_name` and translation keys
- **Findings**: CORE-26 (remainder), CORE-33 (partial convergence).
- **Files**: `__init__.py` (`LoxoneEntity`), every platform's name construction, `translations/*`, `tests/` snapshots.
- **Steps**: `_attr_has_entity_name = True`; primary entity of a device gets `_attr_name = None`; sub-entities get short names ("Override Reason", "Total"); `translation_key` for enum sensors and presets; release note listing the entity-id changes and an entity-registry migration where the unique id is stable (it is — ids only change if the user never customised them).
- **Acceptance**: snapshot diff reviewed and attached to the PR; no duplicate "Device Device" names in the snapshot.

### WP-5.2 Repairs, availability polish, runtime_data
- **Findings**: CORE-30, CORE-31 (if not done in 3.1), API-17 (`killtoken` if not done in 2.2).
- **Acceptance**: repair issues for `yaml_config_present`, `auth_failed` (until reauth completes), `unsupported_firmware`; translations for each.

### WP-5.3 Lint ratchet and dead-code sweep
- **Findings**: TOOL-07 (ratchet), API-23, PC-40, PC-41, PS-25, CORE-32 remainder, API-26.
- **Steps**: enable rule groups one PR at a time (`F401`, `G004`, `ERA001`, `B006`, `BLE001`, `TRY400`, `ARG`), each PR mechanical; translate German comments; fix module docstrings; uncomment `ruff check` in `scripts/lint`; add `.pre-commit-config.yaml` mirroring CI.
- **Acceptance**: advisory ruff count trends down and the blocking set grows; no behaviour change (test suite unchanged and green).

### WP-5.4 Documentation
- **Findings**: TOOL-13, TOOL-14 remainder, CORE-24 docs side.
- **Steps**: README "Configuration options" table (option, default, effect, incl. `verify_ssl` security note and why `generate_scenes_delay` ≥ 3), "Services" table generated from `services.yaml`, entity/attribute reference per platform, event `loxone_event` payload documented (with the `entry_id` field), CONTRIBUTING with `scripts/setup`, `scripts/lint`, `pytest`, min Python 3.14.2; feature-request issue template; CI check that every `services.yaml` key appears in the README.

---

## Phase 6 — Feature gaps (optional, ordered by upstream demand)

Each is an independent WP following the platform pattern established in Phase 4. Check upstream PRs first (#512, #513, #515 already exist) and prefer porting them.

1. **WP-6.1** `PresenceDetector` illumination/noise sub-sensors (#461) — pattern `fan.py:81-135`.
2. **WP-6.2** Message center → repairs (#515) — port the upstream PR.
3. **WP-6.3** `IntercomV2` (#466) — extend `switch.py:48`.
4. **WP-6.4** `InfoOnlyDigital` device-class inference from `details.text` and category (#402).
5. **WP-6.5** Meter family: `EnergyManager`, `EnergyManager2`, `PowerUnit`, `Wallbox` — generalise the Meter sub-state loop.
6. **WP-6.6** `InfoOnlyText`, `UpDownDigital`, `Tracker`; recursive `get_all` over `subControls`.
7. **WP-6.7** AudioZoneV2 sources/favourites/metadata/mute/on-off.
8. **WP-6.8** Alarm `ARM_NIGHT`/`ARM_VACATION`, arming delay surfaced (#323); Gate `SET_POSITION`; Jalousie auto/shade select; AcControl polish (#398).
9. **WP-6.9** Zeroconf discovery via `discover.py` if not done in WP-3.4.

---

## Suggested release cut points

- **0.9.24** after Phase 0 + Phase 1: no behavioural redesign, only crash/security/initial-state fixes. Safe patch release; note the HA floor change.
- **0.10.0** after Phases 2–3: reconnect and multi-instance rewrite, credentials moved to `data`, config-flow rewrite. Minor bump; migration notes.
- **0.11.0** after Phases 4–5: platform fixes, `has_entity_name` rename. Release note must list entity-id changes.
