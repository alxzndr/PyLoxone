# WP-0.2: Test fixture, contract tests, wire-protocol unit tests

**Work package:** `docs/review/2026-09-remediation-plan.md` → WP-0.2
**Branch:** `fix/wp-0.2-stats-contract-tests` (stacked on `fix/wp-0.1-ci-lint-test-harness` @ `a920daf`)

## What this package does

Gives the repo **a real test foundation** — the LoxApp3 fixture, the harness
fixtures (`mock_connection`, `mock_entry`, `enable_custom_integrations`), and
the **wire-protocol unit tests** that exercise the message-parsing path (the
part that was essentially 0% before — WP-0.2's reason for existing next to
WP-0.1's coverage decision). It also wires in **four standards-compliance
`xfail` markers** that future WPs should remove.

## File map

| File | Change | Findings |
|------|--------|----------|
| `conftest.py` (root) | Reduced to docstring + `sys.path` bootstrap (the phcc-era circular-import conftest it was previously is the deleted thing) | TOOL-16, TOOL-08 |
| `pytest.ini` | Coverage floor **15 → 20** (actual 28.86%); ran with `--cov=custom_components/loxone/pyloxone_api` still | TOOL-11 follow-up |
| `tests/fixtures/LoxAPP3.json` | NEW — 26 controls, 7 rooms, 26 cats, every Loxone control type (G1 read-only analog/value/text; G2 switches; G3 intercom/LightControllerV2 + 3 ColorPickerV2 presets / IRControllerV2 / SpaceThermo; G4 FanV2/RoomFanV2/Ventilation+presence/Shutter; G5 IMIV2/IMIV3 valves (valve, WP-1.6); G6 climate: IRoomControllerV2/IRoomController/AcControl (for WP-1.2 / WP-1.5 / WP-1.6); G7 sensors: Magnet (sensor); G8 Gauge duplicate; Groom AutomationSampleMix) — each DICT-keyed by its own unique Loxone style UUID (189 verified unique `uuid`/`uuidAction` strings, verified dupe-free across `python -c "import json;d=json.load(open('tests/fixtures/LoxAPP3.json'));...` | WP-1.6 target + WP-1.2/1.5 |
| `tests/conftest.py` | NEW — `loxapp3` (session), `mock_connection` (patches `LoxoneConnection.{open, start_listening, close, send_websocket_command}`; exposes `feed(uuid, value)` firing `EVENT`), `mock_entry` (v4 base, `generate_scenes=False` — see note), **`enable_custom_integrations`** (re-defined; overrides phcc and re-roots `custom_components` at the repo root for the test's duration) | design |
| `tests/test_contracts.py` | 7 tests: 4 pass (semver, 14 platform modules import, `de` ⊇ `en`, `services.yaml` ↔ `en`), 3 `xfail(strict=True)` below | — |
| `tests/test_message_parsing.py` | 23 tests: MessageHeader (valid + UNSUPPORTED path), `payload_length` xfail (API-18), ValueStatesTable round-trip, TextStatesTable row padding 0–5 chars, LLResponse (incl. `has_error()==False`), `parse_message` dispatch, Token seconds_to_expire, `lxJsonKeySalts` hashAlg 498/513 | API-02, API-18 |
| `custom_components/loxone/translations/de.json` | Backfilled 18 keys missing from `en.json` (`Logger`, `confirm_text`, `channel_a/b_extended`, `test_connection`, whole `issues.*` block). `en.json` untouched, remains single source of truth. No changelog changes (WP-0.3 creates it). | TOOL-01 |

### Coverage floor rationale

WP-0.1 set `--cov=custom_components/loxone/pyloxone_api` at a 15% floor. WP-0.2
addes the remaining 18 pyloxone_api unit tests (message parsing, header lookup),
pushing the pyloxone_api slice from ~14% to **28.86%**, which justifies raising
the enforced floor to **20%**. Coverage still deliberately scoped to
`pyloxone_api` — the platform code reaches 50%+ only *after* the WP-1.x
HA-harness tests exist (which build on this fixture).

## `xfail` markers

| ID | Test | Owner | Removes it |
|----|------|-------|------------|
| CORE-07 | `test_platforms_in_code_match_loxone_platforms` | `custom_components/loxone/text.py` ships but `LOXONE_PLATFORMS` (const.py) omits `Platform.TEXT` | WP-1.7 — register `Platform.TEXT` |
| CORE-24 | `test_every_manifest_requirement_is_imported` | `httpx` in `manifest.json.requirements` is never imported (the HTTP layer is aiohttp) | WP-3.3 — drop `httpx` |
| PS-14 | `test_grouping_table_device_types_are_produced` | `GROUPING_TABLE` (in `custom_components/loxone/const.py`) uses `"analog_sensor"`, `"digital_sensor"`, `"TimedSwitch"` but `sensor.py` / `switch.py` produce `"Sensor analog"`, `"digital"`, `"TimeSwitch"` → three auto-groups are always empty | WP-1.8 — collapse the strings to one source |
| API-18 | `test_message_header_unknown_also_exposes_payload_length` | `MessageHeader` on a non-0x03 frame enters the UNKNOWN branch which does not set `payload_length`; the later direct `header.payload_length` access in `entity.py:189` raises `AttributeError` | WP-2.1 — set `payload_length=0; estimated=False` in the UNKNOWN branch |

All four are `strict=True`. That is the point: if a follow-up WP accidentally
silenced the underlying bug, the test would `xpassed` and break the build —
protects the invariant "xfail → WP-<id>" from being silently broken.

## Harness design notes

### `enable_custom_integrations` — why we overrode it

phcc 0.13.355's stock fixture is one line: `hass.data.pop(loader.DATA_CUSTOM_COMPONENTS)`.
That alone is not enough: the `hass` fixture's default `config_dir` points at
`phcc/testing_config/`, and phcc's `testing_config/custom_components/` is an
**actual regular package** (`__init__.py` present). Python therefore resolves
`custom_components` to that package, *shadowing* our repo's `custom_components`
namespace; `import custom_components.loxone` fails. Our local override
(temporarily) sets `custom_components.__path__ = [repo/custom_components]`
for the duration of the test, then restores it on teardown.

### `mock_connection` — what it mocks

What the integration calls on the connection object, in the order it calls
them, and what we patch:

```
open()              — real: TCP + Loxone protocol + AES handshake.
                        mock: seed structure_file + a Mock() websocket with
                        `protocol.state.name=="OPEN"` (so `is_connected`
                        property is True), set miniserver_version and a
                        32-byte _session_key (so the reconnect-task's
                        `if not self._session_key:` guard does not attempt
                        a key exchange on the unmocked socket).
start_listening(cb) — real: forever-recv loop that consumes `LoxMessage`s
                        and invokes `cb(msg_data)`.
                        mock: returns immediately; state updates arrive
                        through the fixture's helper `feed(uuid, value)`
                        (a single HA bus event `EVENT == "loxone_event"`
                        — the CURRENT entry point that entities subscribe
                        to *pre-WP-3.2*).
close()             — teardown for late sync; patched to a no-op.
send_websocket(uuid, value) — patched to a no-op (no outbound writes in the
                        harness).
```

### `mock_entry` — `generate_scenes=False`

`generate_scenes=True` (the user-facing default) schedules a 3-second
`hass.async_create_task(_gen_scenes)` in `scene.py`; that `CallbackTimerHandle`
outlives the phcc event loop → "Lingering timer after test" teardown error in
*every* test that sets up an entry. The fixture defaults to
`generate_scenes=False`; scene-related WPs (WP-1.3) re-enable it explicitly.

## Three green commands (verbatim)

### `.venv/bin/ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006`
```
All checks passed!
```

### `.venv/bin/ruff format --check .`
```
49 files already formatted
```

### `.venv/bin/python -m pytest -q`
```
TOTAL                                                           1483   1055    29%
Required test coverage of 20% reached. Total coverage: 28.86%
95 passed, 1 deselected, 4 xfailed, 2 warnings in 0.62s
```

## What this PR does not do (explicitly)

- Does not ship a **committed** full-`async_setup_entry` test. That is WP-1.6
  (valve + sensor). The harness fixture is *verified-in-smoke* to run a full
  entry setup end-to-end (~35 entities × 12 platforms) cleanly; that evidence
  is intentional-not-committed.
- Does not drop/rewrite any existing integration tests other than the two
  already DELETED in WP-0.1 (`tests/test_python_compatibilty.py`,
  `pyloxone_api/tests/test_run_alone.py`).
- Does not fix the four `xfail` bugs — the markers exist so the owning WP
   can "remove marker + fix + add passing test" as one atomic change.
- Does not bump `manifest.json`. That is WP-0.3.

## Next (WP-0.3 + follow-up WPs already noted)

- Rebase this branch on `fix/wp-0.1-ci-lint-test-harness` after WP-0.1 merges
  to master, then let WP-0.3 fold WP-0.2 onto the new base (or merge WP-0.2
  directly).
- WP-1.2 (HVAC) is the FIRST package that will consume `def tests/test_climate.py`
  + `mock_entry` + `enable_custom_integrations` + `mock_connection.feed()`
  against the `AcControl`/`IRoomControllerV2` controls in this fixture.
- WP-2.1 should *immediately* remove the API-18 xfail (the fix is one line)
  as a "canary" that the xfail machinery works in CI.
