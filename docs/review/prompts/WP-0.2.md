# Agent prompt — WP-0.2: Test fixtures and contract tests

You are implementing work package **WP-0.2** of the PyLoxone remediation plan.
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

- Phase: Phase 0
- Prerequisite packages that must be merged first: WP-0.1
- Parallel-safe with WP-0.3.
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

### WP-0.2 Test fixtures and contract tests
- **Findings**: TOOL-08 (foundation), TOOL-16, plus contract tests for CORE-07, CORE-23, CORE-24, PS-14.
- **Files**: `conftest.py`, `tests/conftest.py` (new), `tests/fixtures/LoxAPP3.json` (new), `tests/test_contracts.py` (new), `tests/test_message_parsing.py` (new).
- **Steps**:
  1. Build `tests/fixtures/LoxAPP3.json`: `msInfo` (fake serial, `miniserverType: 2`, list-form `softwareVersion`), `rooms`, `cats`, and one representative control of each type listed in the coverage tables of the findings catalogue: `InfoOnlyAnalog` (°C and kWh variants), `InfoOnlyDigital`, `TextInput`, `Meter`, `ClimateController`, `IRoomControllerV2` (with `IRCV2Daytimer` sub-control — seed from README lines 274-346), `IRoomController`, `AcControl`, `PresenceDetector`, `SmokeAlarm`, `Switch`, `TimedSwitch`, `Pushbutton`, `Intercom`, `Slider`, `Radio` (seed from `tests/test_select_mapping.py`), `Jalousie`, `Window`, `Gate`, `LightControllerV2` (sub-controls: `Dimmer`, `Switch`, `ColorPickerV2` ×3 picker types), `Dimmer`, `EIBDimmer`, `Alarm`, `Ventilation` (with presence), `AudioZoneV2`. Every uuid must be unique and fake.
  2. `tests/conftest.py` fixtures: `loxapp3` (session, loads the file), `mock_connection` (patches `LoxoneConnection.open` to set `structure_file` from the fixture and skip auth; exposes `feed(uuid, value)` that pushes a state update through whatever entry point the integration uses at the time — bus event now, dispatcher after WP-3.2), `mock_entry` (`MockConfigEntry(domain="loxone", version=4, options={...})`), and `enable_custom_integrations` (from phcc).
  3. `tests/test_contracts.py`: every `custom_components/loxone/*.py` defining `async_setup_entry` is in `LOXONE_PLATFORMS` and vice versa (fails today on `text.py`); every `manifest.requirements` name is imported somewhere (fails on `httpx`); `de.json` keys ⊇ `en.json` keys (fails on `sync_areas`); every `services.yaml` key has an `en.json` entry; every `device_type` literal in `__init__.py`'s grouping table is produced by some platform (fails today); `manifest.version` is valid semver; all 14 platform modules import cleanly.
  4. `tests/test_message_parsing.py`: `MessageHeader` valid/invalid/`UNKNOWN` (assert `payload_length` exists — fails today), `ValueStatesTable` with `struct.pack("<d", ...)` vectors, `TextStatesTable` padding for `text_length ∈ {0,1,3,4,5}`, `LLResponse` code/Code/nested value, `parse_message` dispatch, `LoxoneToken.seconds_to_expire`, `LxJsonKeySalt` with and without `hashAlg` (#498 regression).
- **Acceptance**: fixture loads; contract tests exist and the ones expected to fail are marked `xfail(strict=True)` with the finding ID in the reason, so later WPs flip them to pass; coverage floor can be raised to 20.

## Findings you are fixing (verbatim from the catalogue)

### TOOL-08 [high] 14% coverage; 21 of 33 modules at 0%
- `climate.py` (516 stmts), `cover.py` (353), `switch.py` (221), `lights/*` (456), `connection.py` (900 stmts, 13%) untested. Existing tests are good but narrow (sensor matching, select mapping, migration, TLS).

### TOOL-16 [low] `conftest.py` is a docstring only, and the documented setup is insufficient (needs `pytest-asyncio`, Python ≥ 3.14.2).


### CORE-07 [high] `Platform.TEXT` missing from `LOXONE_PLATFORMS` → `text.py` is dead code
- Where: `const.py:13-27`. README line 64 advertises TextInput. Users get only the read-only `LoxoneTextSensor` (`sensor.py:228-231`). See PS-03 for bugs inside `text.py` itself.
- Fix: add `Platform.TEXT`; decide whether the sensor mirror stays.
- Effort: S

### CORE-23 [medium] Translation drift and stray escapes
- `de.json` lacks `services.sync_areas.*` (4 keys); `cs.json` has only the `entity` block (51 keys missing); no `error` sections anywhere; `services.yaml:71` and `en.json:74` contain a literal `re\-synchronized`; `config.abort.single_instance_allowed` is unused.
- Fix: backfill de, add error keys, remove the backslash and the dead key, add a CI key-parity check.
- Effort: S

### CORE-24 [medium] `manifest.json` / legacy declarations
- `iot_class` is `local_polling` (integration is websocket push); `httpx` required but never imported (HTTP layer is `aiohttp`); `pycryptodome` unpinned, `websockets>=14` unbounded; `dependencies: []` while `homeassistant.components.group` is imported at module scope; missing `integration_type`, `loggers`; `__init__.py:51` `REQUIREMENTS = [..., "numpy"]` is an HA 0.x relic that modern HA ignores.
- Fix: `local_push`; drop `httpx`; pin; `dependencies: ["group"]` (if groups stay); `loggers: ["custom_components.loxone"]`; delete `REQUIREMENTS`.
- Effort: S

### PS-14 [medium] `device_type` attribute strings never match the group-generation table
- Where: `sensor.py:477,512` (`"Sensor analog_sensor"`), `binary_sensor.py:153` (`"digital"`/`"presence"`/`"smoke"`), `switch.py:114` (`"TimeSwitch"`) vs `__init__.py:459-467` (`"analog_sensor"`, `"digital_sensor"`, `"TimedSwitch"`). Three groups are always empty.
- Fix: constants in `const.py` used on both sides; contract test.
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
