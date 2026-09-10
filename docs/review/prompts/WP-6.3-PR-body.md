# WP-6.3 — IntercomV2 (JoDehli/PyLoxone#466)

Branch: `fix/wp-6-3-intercom-v2`, from `origin/master` at `d632ebd`.
Prerequisite **WP-5.3 is present on master** (commit `862f688`, "widen
the blocking rule set and sweep dead code"). Optional Phase 6 feature
work; upstream PR check: no upstream PR exists for #466 — the issue
sits unimplemented with one user workaround (adding "V2" to the matched
type name worked for them), which is exactly what this package makes
permanent. Credit: reported in JoDehli/PyLoxone#466 by @mousator.

## What changed, by file

- `custom_components/loxone/switch.py`
  - `INTERCOM_TYPES = ("Intercom", "IntercomV2")` — the type match now
    lives in one constant, used by both the `iter_controls` filter and
    the dispatch `elif`. This is the functional fix for #466; a
    firmware-16.2+ Miniserver reports the same block as `IntercomV2`
    and the platform used to skip it entirely.
  - `intercom_sub_control_kwargs(control, config_entry, loxconfig)` —
    **pure helper** (Phase 4 platform pattern, cf. WP-6.1
    `presence_sub_sensor_kwargs`, WP-6.5 `meter_sub_sensor_kwargs`):
    one `LoxoneIntercomSubControl` kwargs dict per sub-control that
    advertises an `active` state stream. Each sub-control keeps its own
    name (WP-5.1 short-name convention), carries the parent's
    `uuidAction` as `parent_id`, and shares the parent's `device_info`
    so the device registry merges everything onto the intercom's one
    device. The device `model` is the actual block type
    (`Intercom` / `IntercomV2`) rather than a hard-coded `"Intercom"`.
    Every `states` / `subControls` lookup is guarded (missing /
    non-dict `states` or `subControls`, missing `uuidAction` → no
    entities, never an exception; the helper also works on *copies* of
    the sub-control dicts, so nothing leaks back into the cached
    structure file).
  - The old inline loop is deleted; the setup branch is now two lines
    that call the helper. The PS-05 skip-and-warn for sub-controls
    without `active` is preserved verbatim (same message string — the
    existing `test_setup_intercom_sub_control_without_active_skipped`
    still passes).

- `tests/fixtures/LoxAPP3.json`
  - New control `"Entrance Intercom"` (`IntercomV2`, hall room uuid
    `726f6f6d-0105…0005` = "Hall", security cat uuid
    `6361743a-0164…0100` = "Security") with one `subControls` entry,
    `"Door Lock"` (`IntercomSubControl`), each carrying one guarded
    `active` state stream. Modeled on the existing `Intercom` entry;
    deliberately conservative — only the `active` stream, which is
    the one the entity's handler can act on (PS-05), plus the names
    the upstream reporter named. No doorbell / camera / audio fields
    invented. All four new uuids are unique in the fixture
    (script-verified against every control/start-control/state uuid).

- `tests/test_wp63_intercom_v2.py` (new, 11 tests)
  - Pure helper: parent device linkage (identifiers / name / model /
    suggested area), legacy model stays `Intercom`, skip for missing /
    non-dict `states`, skip for missing `subControls`, skip for
    missing `uuidAction`, non-dict sub-control entries skipped, only
    actable sub-controls survive, and the helper does not mutate the
    structure file it was handed. All expectations are hand-derived
    literals ("Hall" / "Security" read from the fixture's
    rooms/cats tables, not computed in the test).
  - Setup (HA harness, WP-0.2 fixtures): the V2 sub-switch appears as
    `switch.hall_entrance_intercom_door_lock`, with the device
    "Entrance Intercom" / model "IntercomV2", stored name `null`
    (has_entity_name: inherit device name) and original name "Door
    Lock"; the legacy "Main Intercom" still produces nothing (its
    sub-control has no `active`); a V2 without `subControls` is inert
    and the rest of the switch platform still sets up.
  - State updates: the entity is `unavailable` before the first feed,
    `on` after feeding the sub-control's `active` stream 1, `off`
    after 0; feeding the *master* intercom's active stream does not
    move the sub-switch.

- `tests/snapshots/entity_names.json`
  - Regenerated via the documented
    `LOXONE_ENTITY_SNAPSHOT_OUT=… pytest -q
    tests/test_entity_name_snapshot.py` path and reviewed by hand:
    the diff is exactly one new row
    (`switch.hall_entrance_intercom_door_lock`, unique id
    `7375623a‑9c9d‑9696‑ffff‑c74c6f6c6b3276000026`, device
    "Entrance Intercom" / "IntercomV2", name `null`, original name
    "Door Lock"). No existing row changed — nothing is renamed.

- `CHANGELOG.md` — one bullet under *Unreleased* → *Added*.

- `docs/review/LIVE-MINISERVER-CHECKS.md` — new item **17** (see
  VERIFY below) and one new row in the regression-sweep table (#466).

## Acceptance and evidence

- **Entity appears after setup from the fixture** —
  `test_setup_creates_intercom_v2_sub_switch`.
- **Updates on a fed state event** —
  `test_intercom_v2_sub_switch_updates_on_fed_state_event`
  (unavailable → on → off via `mock_connection.feed` on the sub's
  `active` stream).
- **Contract tests still pass** — `tests/test_contracts.py` green in
  the full run below.
- **Fails before / passes after**: with `switch.py` and the fixture
  stashed to master, `pytest -q tests/test_wp63_intercom_v2.py` errors
  at collection (`ImportError: cannot import name 'INTERCOM_TYPES'`)
  and the setup/fixture tests have nothing to find (master's fixture
  has zero `IntercomV2` entries — `grep -c IntercomV2` was 0 pre-change);
  with the change in, all 11 pass.

### Gate (full output, run from the repo root)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
77 files already formatted

$ pytest -q
604 passed, 1 deselected in 70.72s
```

The single "deselected" is the opt-in snapshot-regeneration test
(`LOXONE_ENTITY_SNAPSHOT_OUT`), absent in the master run too. Baseline
suite was 593 passed / 1 deselected; +11 = this package.

## VERIFY — live-Miniserver check required before merge

Per plan rule 7, the assumptions are implemented behind named
helpers and tested for the *intended* semantics; the following need a
real Miniserver (documented as item 17 in
`docs/review/LIVE-MINISERVER-CHECKS.md`):

- **`IntercomV2` block shape / sub-control command names** —
  helpers: `INTERCOM_TYPES` and `intercom_sub_control_kwargs` in
  `custom_components/loxone/switch.py`. The V2 fixture entry (one
  `active` stream per sub-control, shared parent device, `on`/`Off`
  send on the sub-control's action uuid) is inferred from the
  reporter's statement that the V2-suffix workaround worked, not from
  a live structure file. If a real V2 uses different state names or
  commands, the fix is in that one helper (or an explicit
  `LoxoneIntercomSubControl.turn_off` if the off command is
  case-sensitive).

## Follow-ups (out of scope, noted only)

- The `LoxoneIntercomSubControl` turn-on / turn-off command-case
  asymmetry (`on` vs inherited `Off` from `LoxoneSwitch`) pre-dates
  this WP and affects legacy `Intercom` too; it is called out inside
  live-check item 17 but deliberately not "fixed" here (no
  authoritative command table for intercom sub-controls to fix
  against — guessing at command names is exactly what #466's
  conservative scope rules out).
- The legacy "Main Intercom" sub-control in the fixture ("Micro")
  still advertises only an `on` stream, so it remains (correctly, per
  PS-05) skipped; if a real `Intercom` sub-control without `active`
  should still be controllable, that's a follow-up with its own
  protocol evidence.
