# WP-7.4: Climate: "Schedule" ends an override again; exact temperature unit detection

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `fix/wp-7.4-climate-schedule` cut from
`master` (`e2123b6` or later). The remote `upstream` is `JoDehli/PyLoxone`
and is already fetched. Read `docs/review/2026-10-upstream-0.10.2-gaps.md`
first. This WP touches only `climate.py` and its tests and can run in
parallel with the others.

## Goal

1. Selecting the "Schedule" preset of an `IRoomControllerV2` while the
   controller is overridden sends `stopOverride` again. The fork lost this
   branch in WP-4.1 (c7ad8c9); since then "Schedule" always sends
   `setOperatingMode/0`, which does not end the override and can switch a
   controller that was on automatic heating only to heating and cooling.
2. `temperature_unit_from_format` stops reading any capital `F` or `C` in a
   format as the unit. Today `"F" in fmt` makes a format such as
   `%.1f°C Fußboden` Fahrenheit.

## Upstream commits to read

```
git show cf51951   # setOperatingMode typo fix; shows the stopOverride branch that upstream kept
git show e9f0c71   # shared _temperature_unit_from_format (has the same substring test)
git show 7561247:custom_components/loxone/climate.py | sed -n 715,735p   # the 0.9.23 preset code
git show c7ad8c9 -- custom_components/loxone/climate.py | grep -n stopOverride   # where the fork dropped it
```

## Current fork code

- `custom_components/loxone/climate.py`
  - `:974-986` `async_set_preset_mode`: `mode_id == "stop"` always sends
    `setOperatingMode/0`
  - `:773-774` `is_overridden` (`overrideReason > 0`)
  - `:922-937` `async_set_hvac_mode` already sends `stopOverride` first when
    an auto mode is overridden (keep consistent with it)
  - `:66-85` `temperature_unit_from_format(fmt, default)`; used at `:571`,
    `:790`, `:1165`
- `custom_components/loxone/helpers.py:635-654` `clean_unit` already
  extracts the unit text from a Loxone format (handles `<v.1>` placeholders
  and the bare `°`); reuse it rather than writing another parser.
- Tests: `tests/test_climate.py:570-630` pin `["setOperatingMode/0"]` for
  the schedule preset (the not-overridden case); the PC-23 / unit tests in
  `tests/test_climate.py` and `tests/test_units_from_formats.py`.

## Facts that decide the semantics

- Structure File v16, `IRoomControllerV2` (confirm; upstream cites page
  77): `stopOverride` ends an active override; `setOperatingMode/{opMode}`
  with 0 = automatic, heating and cooling allowed. Upstream 0.10.2 sends
  `stopOverride` when `is_overridden and operating_mode`, else
  `setOperatingMode/0`.
- Decide and document the not-overridden case. Options: keep
  `setOperatingMode/0` (upstream, current fork), or send nothing because
  the controller already follows its schedule. Pick one, justify it from
  the Structure File in the docstring, and keep it behind a named helper
  (e.g. `schedule_preset_commands(is_overridden, operating_mode) ->
  list[str]`).
- The maintainer has no room controller and no AC block, so nothing here
  can be checked on the house; anything not in the Structure File goes
  into a live-check entry.
- Units: a format names a unit only in its unit text (`%.1f°F`, `%.1f °C`,
  `%.1f F`); the bare `°` (Loxone Config's default) means the Miniserver's
  own unit (`msInfo.tempUnit`, 1d4d03d). Anything else falls back to the
  default as today.
- The Structure File v16 text was not available to the reviewer (the copy
  expected at `~/Library/Caches/pyloxone/StructureFile-v16.txt (v17: StructureFile-v17.txt)`
  was missing). Command words are case-insensitive on the firmware, and an
  unknown command is answered 200 / `"0"`
  (`~/github/loxone-mac/docs/pyloxone/live/README.md`).

## What to build

1. The schedule helper above, used by `async_set_preset_mode`.
2. `temperature_unit_from_format` via `clean_unit`: `°F` / `F` →
   Fahrenheit, `°C` / `C` → Celsius, `°` → the default (the Miniserver's
   unit), anything else → the default. Exact matches on the extracted
   unit, no substring tests.

## Tests (real `hass` fixture)

Use `mock_connection` / `mock_entry` from `tests/conftest.py` and the
fixture's `Bedroom Controller` (`tests/test_climate_spec_commands.py` shows
the uuids and the pattern).

- Feed `overrideReason` > 0, call `hass.services.async_call("climate",
  "set_preset_mode", {"entity_id": "climate.bedroom_bedroom_controller",
  "preset_mode": PRESET_SCHEDULE}, blocking=True)`, assert
  `mock_connection.sent` holds `stopOverride` (and nothing that changes the
  operating mode). Then the not-overridden case per your decision.
- Unit: parametrised literals for `%.1f°F`, `%.1f °C`, `%.1f°`, `%.1f`,
  `%.1f°C Fußboden`, `%.1f F`, `<v.1>°F`, `None`, with both defaults.
- **Prove the tests are not vacuous:** restore the unconditional
  `setOperatingMode/0` and the substring test, see the new tests fail,
  restore. Record it in the commit message body.
- Expected existing-test changes: `tests/test_climate.py` around
  `:576-603` only if you change the not-overridden case; say why in the
  commit message.

## Documentation

- `CHANGELOG.md`, `## Unreleased`, prose voice; cite `upstream cf51951`,
  `e9f0c71`, `7561247` (0.9.23 behaviour), PC-20, PC-32, PC-23.
- `docs/review/LIVE-MINISERVER-CHECKS.md`: add **#32 "Schedule" preset
  during an override** (Test: on an `IRoomControllerV2`, start an override
  from the Loxone app, select "Schedule" in Home Assistant; Pass: the
  override ends and the operating mode is unchanged).

## Out of scope

The legacy `IRoomController` table (LIVE #4), AC fan/swing modes, preset
names, the ventilation redesign, any other climate behaviour.

## Rules

- Do not push, tag, open a PR or post anything on GitHub. Do not contact
  the live Miniserver or Home Assistant.
- Small commits, conventional subjects (`fix(climate): ...`), bodies citing
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
[ -x $D/pytest-ha2026.11b ] && $D/pytest-ha2026.11b -q     # HA 2026.11-dev (native_* climate API), if present
```

Report: the decision for the not-overridden case and its source, the
non-vacuity evidence, and any test you changed.
