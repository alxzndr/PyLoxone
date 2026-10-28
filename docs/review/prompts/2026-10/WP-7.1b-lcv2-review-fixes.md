# WP-7.1b: LightControllerV2 review fixes (plain turn-on mood, manual-mode circuits)

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `fix/wp-7.1b-lcv2-review` cut from
`master` (`d0b4e8e`, WP-7.1 merged, or later). WP-7.1 introduced
`OFF_MOOD_IDS`, `lcv2_is_on` and `lcv2_plain_turn_on_command` in
`custom_components/loxone/lights/lightcontroller.py`, tests in
`tests/test_lcv2_state.py`, and live check #29 in
`docs/review/LIVE-MINISERVER-CHECKS.md`. Read those first. This package
applies two findings from the review of WP-7.1.

## Evidence from the maintainer's house (observed, not guessed)

The live Home Assistant registry of the maintainer's installation has scene
entities generated from the LightControllerV2 mood lists. Their unique ids
end in the mood id, and their names are the mood names:

| Mood id | Scene names (every controller) |
|---|---|
| `777` | "alles aan" / "bright" / "on" |
| `778` | "uit" / "off" |
| `1`, `2`, `3` | custom moods, e.g. "cinema", "nacht", "gewoon", "tv kijken", "gezellig", "dimmed" |

So on this Miniserver 777 is the static "all on" mood and 778 the static
"off" mood. 0 is "always off" per the Structure File
(`~/Library/Caches/pyloxone/StructureFile-v16.txt` / `-v17.txt`,
LightControllerV2 commands).

## Finding 1: plain turn-on should prefer mood 777

Today `lcv2_plain_turn_on_command` plays the first non-off mood of
`moodList`. Depending on the list order that can be a custom mood
("cinema", "nacht") instead of full light.

Change the helper to:

1. `changeTo/777` when the mood list contains a mood with id 777,
2. otherwise the first mood that is not an off mood,
3. otherwise `plus`.

Keep the mood id a named constant (e.g. `ALL_ON_MOOD_ID = 777`) with a
comment naming where it comes from: observed on the maintainer's
installation (scene names), not in the Structure File. Update the
`OFF_MOOD_IDS` comment: 778 is now also observed as "uit"/"off" on the
maintainer's installation, not only upstream's.

## Finding 2: in manual mode the circuits decide, the master is the fallback

Today `lcv2_is_on` with an empty `activeMoods` list returns
`master_position > 0` as soon as the master position is known, and ignores
the circuits. The circuits are the real outputs: a master at 0 with one
circuit switched on directly reads off while a lamp burns, and a master
above 0 with every circuit at 0 reads on.

Change the manual-mode branch to:

1. when any circuit level is known: on if any known circuit level is above 0;
2. otherwise, when the master position is known: `master_position > 0`;
3. otherwise `None` (unknown).

Moods still decide first, exactly as now.

## Minor

In the circuit loop of `event_handler`, only set `request_update = True` when
the parsed level is not `None`.

## Tests

- Update `tests/test_lcv2_state.py`: the parametrised `lcv2_is_on` cases
  (add master 0 with a lit circuit -> on, master > 0 with all circuits 0 ->
  off, master known and no circuits known -> master decides) and the
  `lcv2_plain_turn_on_command` cases (777 present but not first -> 777;
  no 777 -> first non-off; only off moods -> `plus`).
- Integration tests on the real `hass` fixture (`mock_connection.feed`,
  `hass.services.async_call("light", "turn_on", ..., blocking=True)`,
  assert `mock_connection.sent`). If the fixture controller's mood list has
  no 777, add it in the test by feeding a `moodList` that holds 777 after a
  custom mood.
- Prove non-vacuity: revert each fix once and show the new tests fail.

## Docs

- `CHANGELOG.md` under `## Unreleased`, in the file's prose voice; amend
  the WP-7.1 entries rather than adding contradicting ones.
- `docs/review/LIVE-MINISERVER-CHECKS.md` #29: plain turn-on now expects
  mood 777 ("alles aan"); manual mode now follows the circuits first.
- README LightControllerV2 row if it describes the turn-on.

## Out of scope

Anything outside `lights/lightcontroller.py`, its tests and the docs above.
No change to `turn_off` (`changeTo/0`), effects or brightness handling.

## Rules

- Commit in small logical commits (`fix(light): ...`), each ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Do not push, tag or post anything; do not contact the live Miniserver or
  Home Assistant.
- `ruff.toml` targets py313 on purpose; do not change it.

## Verification

Run from the worktree root (a worktree has no `.venv`; use the main
checkout's):

```
V=/Users/alexandergeeraerts/github/PyLoxone/.venv
$V/bin/python -m pytest -q                       # HA 2026.8.1
PATH="$V/bin:$PATH" scripts/lint --check
D=~/Library/Caches/pyloxone
[ -x $D/pytest-ha2026.9 ] && $D/pytest-ha2026.9 -q      # HA 2026.9.1
[ -x $D/pytest-ha2026.11b ] && $D/pytest-ha2026.11b -q  # HA 2026.11.0b0
```

On 2026.11.0b0, `tests/test_devices.py::test_device_registry_snapshot_after_setup`
already fails before your change (`DeviceEntry.suggested_area` was removed);
leave it alone.

Report: the commits, the non-vacuity evidence, and the updated live check
#29 text.
