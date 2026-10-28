# WP-7.1: LightControllerV2 on/off from moods, master and circuits; plain turn-on

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `fix/wp-7.1-lcv2-state` cut from
`master` (`e2123b6`, release 1.0.1, or later). The remote `upstream` is
`JoDehli/PyLoxone` and is already fetched. Read
`docs/review/2026-10-upstream-0.10.2-gaps.md` first; this WP is its first
row.

## Goal

The maintainer's house has 20 `LightControllerV2` blocks (7 with a
`masterValue` sub-control, 13 without). Make the controller entity's on/off
state and its plain `light.turn_on` (no brightness, no effect) correct and
honest:

1. `is_on` follows the active moods, the master dimmer and, when neither
   decides, the controller's own circuits.
2. A plain turn-on sends a command that is known to exist, chosen by one
   named helper.
3. A missing optional state no longer raises.
4. The LCV2 `Switch` circuit starts as `None`, not the string `"unknown"`.

## Upstream commits to read

```
git show e43301f   # derive LightControllerV2 state from moods and master value
git show 466ea0e   # stop hardcoding installation-specific mood ids
git show 64ad11d   # optional-structure states (only the lightcontroller.py hunks)
git show 173d2ea   # light switch state None instead of 'unknown'
```

Port the behaviour you can justify, not the code or the test harness
(upstream builds entities with `__new__` and `SimpleNamespace`; the fork
does not).

## Current fork code

- `custom_components/loxone/lights/lightcontroller.py`
  - `:10-15` `OFF_MOOD_ID = "0"`, `ON_MOOD_ID = "99"`,
    `ALL_OFF_ACTIVE_MOODS = [778]`
  - `:137-154` `async_turn_on`: a bare turn-on while not on sends
    `changeTo/99`
  - `:209-216` `event_handler`: on unless `activeMoods == [778]`, so `[0]`,
    `[]` and `[0, 778]` read as on
  - `:69, 165-167, 209-224` index `self.states["moodList"]`,
    `["activeMoods"]`, `["additionalMoods"]` directly, also in
    `_state_uuids`, which runs in `async_added_to_hass`
  - `:44-58` the master is the sub-control whose key contains `masterValue`
- `custom_components/loxone/lights/switch.py:22-23` `_attr_is_on =
  STATE_UNKNOWN` (a truthy string)
- `custom_components/loxone/light.py` builds the controller and, with
  `generate_lightcontroller_subcontrols`, its circuits
- `custom_components/loxone/scene.py` depends on the controller's mood list
  (do not break scene generation)

## Facts that decide the semantics

From the maintainer's real structure file (firmware 17.2.8.28; private,
quote shapes only, never copy names or uuids):

- Controller states: `activeMoods`, `activeMoodsNum`, `additionalMoods`,
  `circuitNames`, `daylightConfig`, `favoriteMoods`, `jLocked`, `moodList`,
  `presence`. No `active`, `bri`, `mood`, `state`.
- Sub-controls are keyed `<controller uuid>/AI<n>`, plus
  `<controller uuid>/masterValue` (a `Dimmer` with `position`, `min`, `max`,
  `step`) and `<controller uuid>/masterColor` (a `ColorPickerV2`) when the
  controller has them; `details.masterValue` / `details.masterColor` hold
  the matching sub-control key (`<uuid>/masterValue`, `<uuid>/masterColor`). Circuits are `Dimmer` (`position`,
  `min`, `max`, `step`), `Switch` (`active`, `jLocked`, `lockedOn`) and
  `ColorPickerV2` (`color`).
- 13 of 20 controllers have no `masterValue`.

From upstream (claims you must confirm in the Structure File, section
LightControllerV2; upstream cites pages 95-98 of its copy):

- `changeTo/{moodId}` plays a mood; mood id 0 is always off.
- 778 is the static "Aus" mood on upstream's installation; the runtime
  mood list is not part of the structure file.
- Upstream observed that moving the master clears `activeMoods` to `[]`
  ("Stimmung Manuell") while the circuits stay lit.

The Structure File v16 text was not available to the reviewer (the copy
expected at `~/Library/Caches/pyloxone/StructureFile-v16.txt (v17: StructureFile-v17.txt)`
was missing). If you cannot find it either, do not invent spec facts: put
every rule you could not confirm behind the helpers below and say so in the
live-check entry. Reverse-engineering notes are in
`~/github/loxone-mac/docs/pyloxone/` (`HANDOFF.md`, `pyloxone-gaps.md`): an
unknown command is answered with `Code 200` and value `"0"`, so a reply
never proves a command exists; command words are case-insensitive.

## What to build

1. **One named helper for the off moods**, e.g.
   `lcv2_off_mood_ids() -> frozenset[int]` or a module constant
   `OFF_MOOD_IDS = frozenset({0, 778})` with a docstring that says which id
   comes from where. Accept ints and numeric strings from the JSON.
2. **A pure function for the state**, e.g.
   `lcv2_is_on(active_moods, master_position, circuit_levels) -> bool | None`:
   - `active_moods` not yet received: `None` (unknown);
   - any id not in the off set: `True`;
   - only off ids: `False`;
   - empty list (manual): the master position if the controller has a
     master and it is known (`> 0` is on); otherwise any known circuit
     level above zero (dimmer `position > 0`, switch `active == 1`, picker
     brightness `> 0`); if nothing is known, `None`.
   Do **not** copy upstream's `master or 0` (that makes a masterless
   controller off). Do not add rules for ids such as `-1` unless the
   Structure File documents them.
3. **Feed the circuit levels.** The controller subscribes to its circuits'
   `position` / `active` / `color` state uuids in `_state_uuids` (whether or
   not `generate_lightcontroller_subcontrols` creates entities for them),
   and keeps the latest value per circuit. Parse picker `color` with the
   existing colour parsing in `lights/colorpickers.py` (do not write a
   second parser).
4. **One named helper for the plain turn-on**, e.g.
   `lcv2_plain_turn_on_command(mood_list) -> str`. Use upstream's rule (the
   first mood of `moodList` whose id is not an off id; `plus` when there is
   none) unless the Structure File gives a documented "on" command, in which
   case use that and say so. Remove `ON_MOOD_ID = "99"`: no source documents
   99. Keep the existing behaviour that a bare turn-on of a controller that
   is on sends nothing, and decide explicitly what an unknown state does
   (recommendation: send the turn-on, as today, and write the decision in
   the docstring).
5. `.get()` for every optional controller state, also in `_state_uuids`
   and `mood_list_uuid` (`None` must not end up in the subscription set).
6. `lights/switch.py`: `_attr_is_on = None`, drop the `STATE_UNKNOWN`
   initialisation and its import if unused.
7. Keep `turn_off` as `changeTo/0`.

## Tests (real `hass` fixture)

- Add to `tests/fixtures/LoxAPP3.json` one realistic controller **with** a
  master and one **without**, in the real shape described above (states,
  `<uuid>/AI<n>` and `<uuid>/masterValue` sub-control keys,
  `details.masterValue`), with synthetic uuids and names in the fixture's
  existing style and existing rooms/categories. Keep the old fictional
  controller so existing tests stay meaningful; do not copy anything from
  the private device file.
- New test module, e.g. `tests/test_lcv2_state.py`, using the
  `mock_connection` and `mock_entry` fixtures from `tests/conftest.py`
  (`mock_connection.feed(uuid, value)` drives the real coordinator path,
  `mock_connection.sent` records commands). Cover, through
  `hass.states.get(...)`:
  - `[0]`, `[778]`, `[0, 778]` read off; `[3]`, `[0, 3]` read on;
  - `[]` with the master at 0 and at 57 (controller with master);
  - `[]` on the masterless controller with all circuits at 0, then one
    dimmer circuit at 40, then a switch circuit on;
  - unknown before the first `activeMoods`;
  - plain `light.turn_on` via `hass.services.async_call("light", "turn_on",
    {...}, blocking=True)` sends the first non-off mood, and `plus` when the
    mood list holds only off moods; nothing when already on;
  - a controller without `additionalMoods` / `moodList` in its states sets
    up without an exception in the log.
- Unit tests for the two pure helpers (parametrised literals).
- **Prove the tests are not vacuous:** for each behaviour, temporarily
  reintroduce the old code (the `[778]` rule, `changeTo/99`,
  `master or 0`, direct `states[...]`), run the new tests, confirm they
  fail, restore. Record the commands and the failing test names in the
  commit message body.
- Expected changes in existing tests, each explained in the commit message:
  `tests/test_lights.py::TestLightControllerV2::test_bare_turn_on_while_off_sends_on_mood`
  (pins `changeTo/99`) and `test_active_moods_flip_is_on`;
  `tests/snapshots/entity_names.json` gains the new fixture controllers
  (regenerate with `LOXONE_ENTITY_SNAPSHOT_OUT=/tmp/x.json $V/bin/python
  -m pytest -q tests/test_entity_name_snapshot.py`, review the diff by
  hand, then copy it over); `tests/test_safe_decoders.py` is unaffected.

## Documentation

- `CHANGELOG.md`, `## Unreleased`, in the file's prose voice (what the user
  sees, then why; cite `upstream e43301f`, `466ea0e`, `173d2ea` and PC-33).
  Call out the behaviour change of the plain turn-on.
- `README.md` line ~181 (`LightControllerV2` row): one sentence on how
  on/off is derived.
- `docs/review/LIVE-MINISERVER-CHECKS.md`: add **#29 LightControllerV2
  off moods, manual mode and plain turn-on** in the format of the existing
  entries (Where / The doubt / Test / Pass / Fail). The test, on the
  maintainer's house, read-only first: watch `activeMoods` and
  `activeMoodsNum` while (a) selecting a mood in the Loxone app, (b)
  switching off, (c) changing one circuit directly, (d) moving the master;
  then from Home Assistant a plain turn-on of a controller with and one
  without a master. Pass: the entity matches the lights in every step and
  the plain turn-on lights the room. Name the helpers to change on failure.

## Out of scope

Colour pickers (WP-7.2), scene generation, the `effect` list, brightness
mapping (PC-17 is verified live), `LightsceneRGB`, any change to
`light.py` beyond passing circuit state uuids, sub-control entity naming.

## Rules

- Do not push, tag, open a PR or post anything on GitHub. Do not contact
  the live Miniserver or Home Assistant.
- Small commits, conventional subjects as in `git log` (`fix(light): ...`),
  body citing upstream hashes, issue numbers and finding ids, ending with
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```
- `ruff.toml` targets py313 on purpose; do not change it. Keep
  `scripts/lint --check` clean.

## Verification

Run from the worktree root. A worktree has no `.venv` of its own; use the
main checkout's:

```
V=/Users/alexandergeeraerts/github/PyLoxone/.venv
$V/bin/python -m pytest -q                          # HA 2026.8.1, ~25 s
PATH="$V/bin:$PATH" scripts/lint --check
D=~/Library/Caches/pyloxone
[ -x $D/pytest-ha2026.9 ] && $D/pytest-ha2026.9 -q   # HA 2026.9.1, if present
[ -x $D/pytest-ha2026.11b ] && $D/pytest-ha2026.11b -q     # HA 2026.11.0b0, if present
```

The snapshot regeneration above uses `$V/bin/python` too.

Report: what changed, the non-vacuity evidence, any Structure File fact you
could or could not confirm, and the exact live check the maintainer has to
run before release.
