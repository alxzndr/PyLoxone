# Agent prompt — WP-1.3: Initial-state correctness (entity half of #475)

You are implementing work package **WP-1.3** of the PyLoxone remediation plan.
Repository: `/Users/alexandergeeraerts/github/PyLoxone` (Home Assistant custom integration for Loxone Miniservers).
Work autonomously. Do not ask questions unless genuinely blocked; state assumptions in the PR body instead.

Source documents (read them; they are authoritative):
- `docs/review/2026-09-remediation-plan.md` — the full plan (this package is one section of it)
- `docs/review/2026-09-findings.md` — the findings catalogue (the entries you need are inlined below)

## Ordering

- Phase: Phase 1
- Prerequisite packages that must be merged first: WP-0.2
- Parallel-safe with WP-1.1, WP-1.2, WP-1.4.
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

### WP-1.3 Initial-state correctness (entity half of #475)
- **Findings**: PS-02, PS-11, PS-15 (sentinel only), PS-03 + CORE-07, PS-09 (`ERROR_VALUE`/`None`), PS-20 (`STATE_UNKNOWN` string only), PC-22, PS-13 (`@callback` on the handlers touched).
- **Files**: `switch.py`, `binary_sensor.py`, `number.py`, `text.py`, `const.py` (add `Platform.TEXT`), `sensor.py` (event handlers and the two diagnostic sensors), `lights/colorpickers.py` (color_mode init), `tests/test_entity_state.py` (new).
- **Steps**: no entity may report a concrete state before its first value: `_attr_is_on = None`, `is_on` → `None` while unknown, `native_value` → `None`; availability flips only when the *state* uuid (not `uuidAction`) has been seen; `ERROR_VALUE`/`None` → `None`; `text.py` writes `_attr_native_value`, listens on `states["text"]`, sets `native_max`; delete the `_attr_state`/`_attr_assumed_state` lines.
- **Acceptance**: for each touched class, a test constructs it from the fixture, asserts HA state is `unknown`/`unavailable` before any event, feeds a fake event with the real state uuid, asserts the correct state, and asserts a `uuidAction`-only event does *not* make it available; `text.*` entities appear after setup (flips the WP-0.2 xfail).

## Findings you are fixing (verbatim from the catalogue)

### PS-02 [high] Switches report `on` before their real state arrives; `turn_on` guard is dead
- Where: `switch.py:98-99, 203-204` (`self._attr_is_on = STATE_UNKNOWN` — the truthy string `"unknown"`), `239-248` (state written while unavailable, then `available` flipped on `uuidAction` alone), `225-232` (`if not self._attr_is_on` never true). Same in `LoxoneTimedSwitch`.
- Fix: `_attr_is_on = None`; write state once after the value is assigned; only mark available when `active` is known; remove the guard; coerce with `bool(float(v))`. Delete `_attr_state`/`_attr_assumed_state` annotations (93-94, 198-199).
- Effort: S · Upstream: #475

### PS-11 [medium] Binary sensors publish `off` before any value has arrived
- Where: `binary_sensor.py:100-131` (`_attr_available = True` unconditionally in `__init__`; `_state = STATE_UNKNOWN`), `182-185` (`is_on` returns `self._state == self._on_state` → `False`). Window/motion/alarm sensors claim "closed/no motion" until the first burst.
- Fix: `is_on` → `None` while unknown; keep unavailable until the first event.
- Effort: S · Upstream: #475

### PS-15 [medium] `number.py`: string sentinel as `native_value`, wrong state uuid, unguarded details, no unit/device class
- Where: `number.py:57-70` (`_state = STATE_UNKNOWN` returned as `native_value`; `details["min"/"max"/"step"]` unguarded), `102-119` (listens on `uuidAction` while advertising `states["value"]`; `schedule_update_ha_state` from the loop), `details["format"]` ignored.
- Fix: `None`; `states.get("value", uuidAction)`; `.get()`; reuse `clean_unit` + `match_sensor_description` for unit/device class/precision.
- Effort: M · Upstream: #492 #481

### PS-03 [high] `text.py` writes `_state` but reads `_native_value`; listens on the wrong uuid; no `native_max`
- Where: `text.py:66-69, 87-89, 96-108, 118`. Would show `""` forever even if the platform were loaded (CORE-07). `async_set_value` is not optimistic; `HA TextEntity` caps at 100 chars by default.
- Fix: assign `_attr_native_value`; listen on `states["text"]`; set `native_max` from the control.
- Effort: S

### CORE-07 [high] `Platform.TEXT` missing from `LOXONE_PLATFORMS` → `text.py` is dead code
- Where: `const.py:13-27`. README line 64 advertises TextInput. Users get only the read-only `LoxoneTextSensor` (`sensor.py:228-231`). See PS-03 for bugs inside `text.py` itself.
- Fix: add `Platform.TEXT`; decide whether the sensor mirror stays.
- Effort: S

### PS-09 [medium] `ERROR_VALUE`/`None`/non-numeric values unhandled; rounding helper dead; blanket `MEASUREMENT`
- Where: `sensor.py:463-471` (unmatched units get `MEASUREMENT` even for text values → HA `ValueError`), `491-505` (`_get_lox_rounded_value` and `self._format` never used; `available` re-runs the `state` pipeline). `const.py:31 ERROR_VALUE = -1` is used nowhere, so `-1` is published as a real reading.
- Fix: map `ERROR_VALUE`/`None` to `None`; set `state_class` only for numeric values; drop the `available` override; use or delete the rounding helper.
- Effort: S · Upstream: #402 #492 #481

### PS-20 [medium] Keep-alive and version sensors have no device and no `entity_category`; version sensor stores `"unknown"` string
- Where: `sensor.py:350-403`. Both float outside the Miniserver device; `LoxoneVersionSensor` swallows exceptions into `STATE_UNKNOWN`.
- Fix: `DeviceInfo(identifiers={(DOMAIN, serial)})`, `EntityCategory.DIAGNOSTIC`, `None`; consider `entity_registry_enabled_default=False` for keep-alive.
- Effort: S

### PC-22 [medium] `_attr_color_mode = ColorMode.UNKNOWN` is not in `supported_color_modes`
- Where: `lights/colorpickers.py:32, 142`. HA logs an error on every write until the first colour event.
- Fix: leave unset, or initialise to the single implied mode.
- Effort: S

### PS-13 [medium] `async def event_handler` without any `await` → a task per entity per event
- Where: every `event_handler` in these files (`sensor.py:323,366,412,502,559,585,615`; `binary_sensor.py:163,213`; `switch.py:141,239,338,383`; `button.py:86`; `number.py:107`; `text.py:96`; `select.py:152`). `sensor.py:618` allocates a `set` per event.
- Fix: `@callback def`; precompute `frozenset` of state uuids. Superseded by CORE-27.
- Effort: S


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
