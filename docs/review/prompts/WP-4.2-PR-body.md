# WP-4.2 — Cover

Findings fixed: **PC-07 (Gate **VERIFY), PC-14, PC-15, PC-16 (cover lines,
incl. #501), PC-19, PC-27, PC-36, PC-40/PC-41 (cover lines)**.
Upstream issue: **JoDehli/PyLoxone#501** (Window position handling; the
`stop_cover` behaviour fix is directly related).

Prerequisites landed on `master` before this branch was cut (verified via
`git log --oneline -20` at the start):
WP-1.1 (`8b2ab6e`/`dfecbc6`) and WP-1.3 (`947b0be`).

## What changed (by file)

### `custom_components/loxone/cover.py`
- **PC-07**: `LoxoneWindow.stop_cover` now sends a real `stop`
  regardless of `is_closing`/`is_opening` (previously: closing →
  `fullopen`, opening → `fullclose` — the window was driven to the
  opposite end, JoDehli/PyLoxone#501). `LoxoneGate.stop_cover` no longer
  re-sends the **opposite** direction; it now sends `stop`, implemented
  behind the named helper `gate_stop_command()` — **VERIFY** (Gates keep
  the other direction vs. stop; Jalousie already sends `stop`).
- **PC-14**: `enable_sun_automation`/`disable_sun_automation`/
  `quick_shade` are `async def` + `hass.bus.async_fire` (previously sync
  defs calling `hass.bus.fire`, which must not run where HA invokes
  entity-service methods off the loop). The three registrations gained
  `required_features=[SUPPORT_SUN_AUTOMATION]` /
  `required_features=[SUPPORT_QUICK_SHADE]`, so a Gate/Window (or a
  Jalousie without slats/automation) targeted by the service gets a
  `ServiceValidationError`, not `AttributeError`.
- **PC-15**: `animation` is read once in `__init__` into `_animation`
  (defensive); the `animation` property and both `device_class`
  implementations use it, and the table logic moved to the pure,
  importable `gate_device_class(animation)` /
  `jalousie_device_class(animation)`. Gate's old `return self.type`
  fallback (a bare string `"Gate"` where a `CoverDeviceClass` is
  expected) is gone (`None` for unknown values).
- **PC-16 (#501)**: the three cover classes' event handlers and
  `__init__` read
  state uuids through the shared module-level `_state_uuid(states, name)`
  helper (`.get()`) with an `is not None and … in data` guard in the
  handlers. A Window without `targetPosition`/`direction`, a Jalousie
  without `shadePosition`/`up/down`/`autoInfoText`/`autoState`, or a Gate
  without `position` no longer `KeyError`s on construction or on the
  first event.
- **PC-27**: the services' feature gating now uses the moved-out bits
  (see `const.py`); the OR-ing itself was unchanged (`= int | int` stays
  within `CoverEntityFeature`).
- **PC-36**: `LoxoneJalousie.__init__` no longer injects
  `""` sentinel keys (`autoInfoText`/`autoState`) into the shared
  structure dict; the event handler skips absent state uuids.
- **PC-19**: the three `manualLamelle` commands go through
  `_lamelle_command(base)`, an **explicit `:.3f` format** (instead of
  Python's full-precision `repr`), plus the jitter documented on the
  named constants `_LAMELLE_JITTER_MIN`/`_LAMELLE_JITTER_MAX`
  (a sub-percent delta that keeps Loxone from discarding a command that
  equals the value it already has).
- **PC-40**: removed the dead `@callback async_add_covers` wrapper, the
  dead `_position is None` branches in `LoxoneGate.__init__` and
  `LoxoneJalousie.__init__`.
- **PC-41**: `shade_postion_as_text` → `shade_position_as_text` (also in
  `extra_state_attributes`); `async_setup_platform` is annotated
  `-> bool` (it returns `True`).

### `custom_components/loxone/const.py`
- **PC-27**: `SUPPORT_SUN_AUTOMATION`/`SUPPORT_QUICK_SHADE` moved from
  `1024`/`2048` to `1 << 12` (4096) / `1 << 13` (8192), far above HA's
  cover-feature range (highest bit is 128 as of 2026.8), with the
  reasoning in the comment. Only `cover.py` references them (verified by
  grep).

### `custom_components/loxone/services.yaml`
- The three service targets are narrowed with
  `integration: loxone` (kept `domain: cover`, per the decision table);
  descriptions now say "Jalousie" explicitly. (The effective Jalousie
  filtering is done by `required_features` in `cover.py`; the yaml can
  only restrict the UI selector to this integration.)

### `tests/test_cover.py` (new, 45 tests)
Acceptance-criteria coverage (all expected values hand-derived
literals):
- Window `stop_cover` sends `stop` — fresh, closing **and** opening
  (feeds `_direction` -1 and 1, expects two `{"value": "stop"}`).
- `gate_stop_command()` is pinned to `"stop"` and the Gate entity is
  checked in both directions (VERIFY — see below).
- `quick_shade` / sun-automation services on Gate, Window and a
  slat-less SHUTTER Jalousie raise `ServiceValidationError` (full
  HA-harness setup with the `LoxAPP3.json` fixture); the matching
  Jalousie *does* receive the service (`enable_sun_automation` on an
  automatic Jalousie fires `auto`).
- Window **without `targetPosition`** sets up (`ConfigEntryState.LOADED`)
  and updates: feed `0.25` → `current_position 25.0`, state `open`;
  feed `0.0` → `closed` (JoDehli/PyLoxone#501 regression).
- `animation → device_class` table tests for both Gates and Jalousies,
  incl. unknown/`None` values.
- Position inversion identities: `map_range(p, 0, 100, 100, 0) == 100 - p`
  (as an involution) and end-to-end: server 0.4 → hass 60.0 →
  `set_cover_position(60.0)` sends `manualPosition/40.0`.
- The structure dict is unchanged after entity construction
  (deep-compare of `states`/`details` for the fixture Jalousie, both
  standalone and across a full `async_setup_entry`).
- `manualLamelle` payload: fixed 3-decimal format, jitter within
  `[0, 0.0091]` / `[100, 100.0091]`.
- `KeyError`-free construction: Jalousie with `details={}`, Gate with
  `states={}`, Window missing `direction`/`targetPosition`, and the
  event-handler variants feed foreign uuids.

## Verification (repo root, `.venv/bin/python`)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
57 files already formatted

$ pytest -q
252 passed, 1 deselected, 1 xfailed, 2 warnings in 39.14s
```
(the `1 xfailed` and the two `DeprecationWarning`s about
`aiohttp.BasicAuth` are pre-existing and unrelated; `1 deselected` is the
`online` marker)

```
$ pytest tests/test_cover.py -q
45 passed in 0.68s
```

### Pre-fix behaviour (observed before the change)
Single-execution reproductions against the pre-change tree confirmed
every bug the tests pin:
- Window `stop_cover` while closing fired `fullopen` (not `stop`).
- Gate `stop_cover` while closing fired `open` (the reversal).
- `LoxoneJalousie.__init__` added `autoInfoText: ''` and `autoState: ''`
  to the shared structure dict.
- Jalousie `device_class` with `details={}` raised `KeyError: 'animation'`.
- The new test module does not even import against the old tree
  (`gate_device_class` etc. missing), i.e. the whole suite fails
  pre-change.

## VERIFY items (require a live-Miniserver check before merge)
1. **PC-07 Gate `stop_cover`**: `gate_stop_command()` → `"stop"`.
   The old logic was *certainly* a reversal, but whether the Loxone
   gate control honours a plain `stop` (vs. needing "keep the current
   direction") has to be confirmed on a real Miniserver.
2. **PC-14 threading**: the three service methods are now `async def` +
   `async_fire`. Whether HA's entity-service dispatcher invokes the old
   sync defs from an executor (where `hass.bus.fire` would raise)
   depends on the HA version; pinning them to the loop is correct
   either way, but the *observable* difference (was it actually raising
   in production) is worth a sanity check on a live server.

## Follow-ups (outside this WP's scope, listed per the rules)
- `switch.py:220` (and `268`): `self.states["active"]` is still indexed
  unguarded — a fixture Switch without an `active` state logs `KeyError: 'active'`
  on every `loxone_event`. This is the WP-4.5 (switch) side of PC-16.
- Fabricated finding IDs `CVE-1.10` / `CVE-43` in
  `tests/test_climate_loxone.py:1,13` (and in commit `0ccffb8`'s
  subject/plan reference). The catalogue defines only
  `API-/CORE-/PS-/PC-/TOOL-` prefixes; those strings are not in
  `2026-09-findings.md` and should be corrected — but that file belongs
  to WP-4.1.
- `climate.py:364` (`details["timerModes"]`), `alarm_control_panel.py`
  and `media_player.py` unguarded-indexing leftovers are PC-16 lines on
  files owned by other WPs; the `_state_uuid` pattern here is portable.
- `LoxoneJalousie.details` in the `LoxAPP3.json` fixture uses
  `"sunAutoma"`/`"quickShade"` keys, but the code reads
  `"isAutomatic"`; the fixture Jalousie therefore never advertises sun
  automation. Not a finding in the catalogue — flagging for the
  WP-0.2-ownership or the next fixture revision.
- The remaining Jalousie `open_cover`/`close_cover`/`stop_cover`/
  `set_cover_position` methods still call sync `hass.bus.fire`; whether
  to move *all* entity command methods to `async_fire` (or to the
  coordinator call per the "Outbound commands" decision) is a
  cross-platform question outside the cover WP.
- During finalization two transient scratch test files existed in the
  working tree (a service-domain inspector and a before/after
  reproducer); both were deleted before the final gate runs, and one of
  them was moved by the sandbox to
  `../WP-4.2._scratch_service_domain.py.bak` (outside the repo, not
  part of the PR). The before/after evidence is preserved above in the
  "Pre-fix behaviour" section instead.
