# WP-7.2: ColorPickerV2 Kelvin range from TWMin/TWMax; TunableWhite plain turn-on

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `fix/wp-7.2-colorpicker-range` cut from
`master` **after WP-7.1 has been merged** (both edit
`tests/fixtures/LoxAPP3.json`, `tests/test_lights.py` and
`tests/snapshots/entity_names.json`). The remote `upstream` is
`JoDehli/PyLoxone` and is already fetched. Read
`docs/review/2026-10-upstream-0.10.2-gaps.md` first.

## Goal

1. Colour pickers take their colour-temperature range from the control's
   `details.TWMin` / `details.TWMax` instead of the hard-coded 2700-6500 K.
   All three pickers in the maintainer's house say 2200-6500, so Home
   Assistant currently clamps every request between 2200 and 2699 K up to
   2700.
2. A plain `light.turn_on` of a TunableWhite picker sends a command the
   Structure File documents.
3. The fixture gets a colour picker in the shape a Miniserver really sends.

## Upstream commits to read

```
git show 7cc7c77   # complete ColorPickerV2 commands and use TWMin/TWMax
git show 5867889   # only the lights/colorpickers.py hunks (TWMin null, plain turn-on brightness)
```

The fork already has the other parts of 7cc7c77: `temp()` never carries
`None` (PC-11) and a brightness-only RGB turn-on sends `setBrightness`
(PC-03, upstream PR #512). Do not port those again.

## Current fork code

- `custom_components/loxone/lights/colorpickers.py`
  - `:18-21` `DEFAULT_TURN_ON_KELVIN = 4000` (marked VERIFY, PC-39)
  - `:24-51` `plan_turn_on` (RGB), `:54-66` `plan_temp_turn_on`
    (TunableWhite; a bare turn-on returns `"On"`)
  - `:70-71`, `:177-178` class-level `_attr_min/max_color_temp_kelvin =
    2700 / 6500`
- `custom_components/loxone/light.py:43-67` `PICKER_TYPE_TO_CLASS` maps the
  integers 0/1/2 (VERIFY, live check #13) and the strings `Rgb`,
  `Lumitech`, `TunableWhite`; `:325-345` builds pickers. Pickers inside a
  `LightControllerV2` only become entities with the
  `generate_lightcontroller_subcontrols` option.
- Tests pinning today's values: `tests/test_lights.py`
  (`test_min_kelvin_is_the_documented_loxone_floor`,
  `test_bare_turn_on_sends_on`, `test_plan_temp_turn_on_defaults`, the
  `temp(100.0, 4000)` assertions), `tests/test_cov_event_handlers_lights.py`.

## Facts that decide the semantics

From the maintainer's real structure file (firmware 17.2.8.28; private,
quote shapes only): every `ColorPickerV2` has `details: {"pickerType":
"Rgb", "TWMin": 2200, "TWMax": 6500}` (a **string** picker type) and states
`color`, `sequence`, `sequenceColorIdx`. The `color` state carries
`hsv(h,s,v)` or `temp(brightness,kelvin)`. One of the three is a
controller's `<uuid>/masterColor`.

From upstream (confirm in the Structure File, section ColorPickerV2):
`TWMin` / `TWMax` are details with defaults 2700 / 6500; the Miniserver
never accepts `temp()` with a missing Kelvin part; upstream says `On` is not
a documented ColorPickerV2 command and sends `setBrightness/<last level>`
instead (falling back to 100).

The Structure File v16 text was not available to the reviewer (the copy
expected at `~/Library/Caches/pyloxone/StructureFile-v16.txt (v17: StructureFile-v17.txt)`
was missing). Look for the ColorPickerV2 command list. If the spec lists
`on`, keep `On` and close the question in live check #13; if it lists
`setBrightness` but no `on`, switch to upstream's choice; if you cannot
read the spec, keep `On` and write the open question into the live-check
entry. A `Code 200` reply never proves a command exists (unknown commands
answer 200 / `"0"`; `~/github/loxone-mac/docs/pyloxone/live/README.md`).

## What to build

1. A helper `color_temp_range(details) -> tuple[int, int]` in
   `lights/colorpickers.py`: `TWMin` / `TWMax` as ints, falling back to
   2700 / 6500 for a missing, `null`, non-numeric or non-positive value, and
   to the defaults as a pair when min >= max. Use it in the `__init__` of
   `TunableWhiteLight` and `RGBColorPicker` (and therefore `LumiTech`); keep
   the class attributes as the defaults.
2. `DEFAULT_TURN_ON_KELVIN` is clamped into the picker's range when it is
   used (a picker with 2200-3000 must not be sent 4000).
3. The TunableWhite plain turn-on per the decision above, in
   `plan_temp_turn_on` only.
4. No change to how `hsv()` / `temp()` replies are parsed.

## Tests (real `hass` fixture)

- Add to `tests/fixtures/LoxAPP3.json` a standalone `ColorPickerV2` in the
  real shape (string `pickerType: "Rgb"`, `TWMin` 2200, `TWMax` 6500, states
  `color` / `sequence` / `sequenceColorIdx`) with synthetic uuids and a
  name in the fixture's style, and one TunableWhite picker with a narrow
  range (e.g. 2700-4000). Keep the existing integer-type pickers. Never copy
  from the private device file.
- A new module, e.g. `tests/test_colorpicker_range.py`, with the
  `mock_connection` and `mock_entry` fixtures from `tests/conftest.py`:
  - `hass.states.get(...)` attributes `min_color_temp_kelvin` 2200 and
    `max_color_temp_kelvin` 6500 for the realistic picker;
  - `hass.services.async_call("light", "turn_on", {"entity_id": ...,
    "color_temp_kelvin": 2200}, blocking=True)` sends `temp(..., 2200)`
    (`mock_connection.sent`); before the fix Home Assistant clamps it to
    2700;
  - the narrow TunableWhite picker never receives a Kelvin outside its
    range on a turn-on without a known temperature;
  - the plain turn-on command of the TunableWhite picker;
  - `TWMin: null`, a missing range and min >= max fall back to 2700-6500
    (unit tests of the helper are enough here).
- **Prove the tests are not vacuous:** put the class-level 2700 back, run
  the new tests, see the 2200 test fail; same for the clamp. Record it in
  the commit message body.
- Expected changes in existing tests, each explained in the commit message:
  `test_min_kelvin_is_the_documented_loxone_floor` (the default stays 2700,
  the range now comes from details), and, if you change the plain turn-on,
  `test_bare_turn_on_sends_on` and `test_plan_temp_turn_on_defaults`.
  `tests/snapshots/entity_names.json` gains the new fixture pickers
  (regenerate with `LOXONE_ENTITY_SNAPSHOT_OUT=/tmp/x.json $V/bin/python -m
  pytest -q tests/test_entity_name_snapshot.py`, review, copy over).

## Documentation

- `CHANGELOG.md`, `## Unreleased`, in the file's prose voice; cite
  `upstream 7cc7c77`, `5867889`, PC-39, PC-11.
- `README.md` line ~183 (`ColorPickerV2` row): the range now follows the
  block (`TWMin`/`TWMax`), 2700-6500 K only as the default.
- `docs/review/LIVE-MINISERVER-CHECKS.md`: update **#13** (picker type
  mapping and default Kelvin; record that the real device sends the string
  `"Rgb"`) and add **#30 Colour temperature range from TWMin/TWMax**. Test
  on the maintainer's house (needs `generate_lightcontroller_subcontrols`
  on, since all three pickers sit in light controllers): set one RGB picker
  to 2200 K from Home Assistant, read the `color` state back
  (`temp(<b>,2200)`), and check the light. Pass: the light follows and the
  state reads back 2200. TunableWhite plain turn-on: no such picker in the
  house, stays open.

## Out of scope

LightControllerV2 state (WP-7.1), `LightsceneRGB`, the integer picker-type
mapping beyond documenting the real string, hsv brightness scaling, colour
sequences.

## Rules

- Do not push, tag, open a PR or post anything on GitHub. Do not contact
  the live Miniserver or Home Assistant.
- Small commits, conventional subjects (`fix(light): ...`), body citing
  upstream hashes and finding ids, ending with
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```
- `ruff.toml` targets py313 on purpose; leave it. Keep `scripts/lint
  --check` clean.

## Verification

Run from the worktree root with the main checkout's venv:

```
V=/Users/alexandergeeraerts/github/PyLoxone/.venv
$V/bin/python -m pytest -q                          # HA 2026.8.1
PATH="$V/bin:$PATH" scripts/lint --check
D=~/Library/Caches/pyloxone
[ -x $D/pytest-ha2026.9 ] && $D/pytest-ha2026.9 -q   # HA 2026.9.1, if present
[ -x $D/pytest-ha2026.11b ] && $D/pytest-ha2026.11b -q     # HA 2026.11.0b0, if present
```

Report: what changed, the non-vacuity evidence, what the Structure File
says about `on` / `setBrightness` (or that you could not read it), and the
live check the maintainer has to run.
