# WP-6.6 — `InfoOnlyText`, `UpDownDigital`, `Tracker`; recursive `get_all`

Branch: `fix/wp-6-6-text-updown-tracker` (started as `wp/WP-6.6` from
`de30674` == `origin/master`; renamed to the plan's `fix/wp-<id>-<slug>`
pattern).
Prerequisite **WP-5.3 is present on master** (commit `862f688`,
"widen the blocking rule set and sweep dead code").

Catalogue entry behind this WP: **PS-26** ("Control types that are cheap
to add with existing patterns") — the one and only finding with an
authoritative `API-/CORE-/PS-/PC-/TOOL-` prefix involved here. There is
**no upstream issue number** for these three control types.

## Scope, per the plan

Feature gaps (Phase 6, optional), following the Phase 4 platform
pattern: extract pure helpers, guard every `states[...]`/`details[...]`
lookup with `.get()`, add a fixture control to
`tests/fixtures/LoxAPP3.json`, add setup + state-update tests.

**Upstream check (done first, per the plan):** no upstream PR or
master-side implementation exists for `InfoOnlyText`, `UpDownDigital` or
`Tracker` (checked `JoDehli/PyLoxone`; the known upstream PRs
#512/#513/#515 cover light brightness, the device registry and the
message centre). The LoxApp protocol shapes were verified against the
openHAB Loxone binding, which ports the same Miniserver API:

- `InfoOnlyText` — read-only variant of `TextInput`; only a `text`
  state, no write command.
- `UpDownDigital` — a virtual up/down rocker input with **no states**,
  commands `UpOn`/`UpOff`/`DownOn`/`DownOff`.
- `Tracker` — reports its entries as a JSON list on the `entries` state
  (e.g. an `Alarm`'s `sensors` tracker, which a structure file *nests*
  in the alarm's `subControls`).

## What changed, by file

- `custom_components/loxone/helpers.py`
  - `get_all(json_data, name, recursive=False)`: an **opt-in** walk over
    controls' `subControls` dicts (any depth), with de-duplication on
    `uuidAction`. Off by default on purpose: enabling recursion for a
    widely-used type would surface nested sub-controls the parent
    platform already creates (the `Dimmer`/`Switch`/`ColorPickerV2`
    sub-controls of a `LightControllerV2`) and duplicate entities.
    Malformed controls (`subControls` not a dict, non-dict entries, no
    `type`) are skipped instead of raising (CORE-32 fuzzing posture).
  - `iter_controls(hass, config_entry, types, recursive=False)` passes
    the flag through.
- `custom_components/loxone/sensor.py`
  - The text-sensor loop now covers `["TextInput", "InfoOnlyText"]`.
    `LoxoneTextSensor` distinguishes the two: `async_set_value` on an
    `InfoOnlyText` is refused locally with a warning (a write would be
    rejected by the Miniserver anyway); wriable `TextInput` behaviour is
    unchanged. The device model string now reflects the real type.
  - `tracker_entries(raw)` — pure helper: normalises the raw `entries`
    stream (JSON-string form or already-parsed list) into a list of
    strings; `None` for missing/empty/unparseable payloads so the sensor
    keeps its last list; non-scalar entries dropped.
  - `LoxoneTrackerSensor` — new sensor entity on the pattern of
    `LoxoneClimateController`'s JSON-list handling: the `entries` stream
    (falling back to the control's own `uuidAction`) renders as a
    comma-joined `native_value` (empty list → `unknown`), with
    `entries`/`count`/`state_uuid` extra attributes.
  - Tracker setup loop uses `iter_controls(..., "Tracker",
    recursive=True)` — the recursive find that also reaches the alarm-nested
    tracker.
- `custom_components/loxone/button.py`
  - `LoxoneUpDownDigitalButton` + an `UpDownDigital` setup loop: one
    button per rocker side, short names `Up`/`Down` (device is named
    after the control, WP-5.1 pattern), stable per-side unique ids
    `<uuidAction>/up` and `<uuidAction>/down` (the structure file's own
    sub-control convention). A press sends `UpOn`/`DownOn`; the entity
    exposes no state and subscribes to no stream (there is no press-echo
    to track, PS-16 posture).
  - `async_press` (not sync `press`): HA dispatches a *sync* handler off
    the event loop, where `LoxoneEntity._send`'s coordinator path cannot
    create its send task — see Follow-up 1.
- `tests/fixtures/LoxAPP3.json`
  - New top-level controls: `Dev Status` (`InfoOnlyText`), `Stairwell`
    (`UpDownDigital`, no states), `Device Tracker` (`Tracker`).
  - `Home Alarm` gains a nested `Alarm Sensors` `Tracker` in
    `subControls` — the only fixture entity that proves the recursive
    `get_all` (it cannot be found by the top-level scan).
- `tests/test_wp66_controls.py` (new, 10 tests — every expected value a
  hand-derived literal)
  - `get_all`: top-level results unchanged; recursive find order;
    `uuidAction` de-dup (`Dup A` wins, `Dup B` skipped); copy isolation.
  - `tracker_entries`: all literal cases incl. malformed payloads.
  - Tracker sensor unit tests: update, keep-on-malformed, empty →
    unknown, `uuidAction` fallback.
  - `InfoOnlyText` writes refused, `TextInput` still sends.
  - UpDownDigital unit test: unique ids, names, empty `_state_uuids`,
    `UpOn`/`DownOn` on the outbound bus.
  - **Full fixture setup + fed state events** (the acceptance test):
    `Dev Status` appears then follows two fed text values; `Device
    Tracker` appears, renders `Front Door, Hall Motion` with
    `entries`/`count` attributes after fed JSON; the alarm-nested
    `Alarm Sensors` tracker appears (recursive find) and updates
    independently; `button.hall_stairwell_up`/`_down` press through the
    HA service and `UpOn`/`DownOn` are recorded on the mocked
    connection in order.
- `tests/snapshots/entity_names.json` — regenerated with
  `LOXONE_ENTITY_SNAPSHOT_OUT` and reviewed: exactly **five added rows**
  (button `hall_stairwell_up`/`_down`, sensor `dev_status`,
  `alarm_sensors`, `garden_device_tracker`), zero removed, zero changed.
- `CHANGELOG.md` — line under *Unreleased / Added*.
- `docs/review/LIVE-MINISERVER-CHECKS.md` — items 14 and 15.

## VERIFY (live-Miniserver check required before merge)

1. **`Tracker` entries payload shape** — `tracker_entries` assumes a
   JSON array of strings/numbers. If the Miniserver delivers objects or
   codes, one helper is the fix point.
2. **`UpDownDigital` rocker on-press semantics** — the control has no
   state, so a press emits `<side>On` only. The openHAB binding models
   these as *toggle switches* (latch on, second on to unlatch/latch-off).
   Whether a single `UpOn`/`DownOn` press is the intended momentary
   behaviour, or the buttons need real state and send `<side>Off` on
   toggle-off, must be confirmed on a real Miniserver. Notes added to
   `LIVE-MINISERVER-CHECKS.md` items 14/15.

## Design decisions (assumptions, no open upstream issue)

- UpDownDigital → **two buttons** (the catalogue's "two buttons");
  identity `<uuidAction>/{up,down}`.
- Tracker → **one sensor** per control (pattern `LoxoneClimateController`):
  comma-joined entry names as the state, full list as attributes.
- `get_all` recursion is **opt-in** (default off) to protect the
  platforms that already walk `subControls` themselves (light.py,
  switch.py/Intercom).

## Follow-ups (out of scope, not changed here)

1. **Off-loop device action dispatch is broken in production code
   paths too.** The existing sync `press` (`LoxoneButton`) and sync
   `turn_on` (switch platform) handlers are dispatched by HA off the
   event loop, where `LoxoneEntity._send`'s coordinator path calls
   `config_entry.async_create_background_task` and fails — the
   WP-6.6 fixture test reproduced this crash on a sync `press` before
   `LoxoneUpDownDigitalButton` started using `async_press`. Fixing it
   for all existing platforms is a separate WP (one-line
   `async_press`/`async_turn_on` overrides per class).
2. `InfoOnlyText` deliberately has no unit/device-class inference (it
   is raw text) and no `states.active` subscription — if a real
   `InfoOnlyText` control ever carries a second state, this is a
   follow-up.
3. `Tracker` entries that are not scalars are dropped by
   `tracker_entries` (see VERIFY 1) — if the real payload is
   richer, convert `native_value` to the human-readable field then.

## Verification (run from the repo root, on this branch)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!   (exit 0)

$ ruff format --check .
73 files already formatted   (exit 0)

$ pytest -q
550 passed, 1 deselected in 66.67s
```

Baseline before this WP: 540 passed. New tests: 10 (all in
`tests/test_wp66_controls.py`). `tests/test_contracts.py` still passes.
`grep -rn "TODO(WP-6.6)" custom_components/` returns nothing (WP-6.6
added no lint silencing; no markers to clear).
