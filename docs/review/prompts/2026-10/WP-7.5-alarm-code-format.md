# WP-7.5: Secured alarm code: text field or numeric keypad

**Before running:** the maintainer has to pick the default (see
"Decision"). If the prompt is started without an explicit choice, use the
recommended default and say so in the report.

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `feat/wp-7.5-alarm-code-format` cut
from `master` (`e2123b6` or later). The remote `upstream` is
`JoDehli/PyLoxone` and is already fetched. Read
`docs/review/2026-10-upstream-0.10.2-gaps.md` first. This WP touches
`alarm_control_panel.py`, `config_flow.py`, `const.py`, the translations
and their tests; it can run in parallel with the others.

## Goal

A secured Loxone `Alarm` asks for the user's visualisation password. The
fork shows Home Assistant's numeric keypad for it (PC-06, upstream #413,
where users asked for a PIN pad instead of a text field). Upstream
0.10.2 (`d13ef23`) went back to a text field, because the visualisation
password may contain letters, and a numeric keypad cannot enter them: such
a user cannot arm or disarm from the alarm card at all.

Make it a per-entry option, so both groups are served.

## Upstream commit to read

```
git show d13ef23   # fix(alarm): accept text codes for secured alarms again
```

## Current fork code

- `custom_components/loxone/alarm_control_panel.py:136-141`
  `_attr_code_format = CodeFormat.NUMBER if is_secured else None`;
  `:232-270` the arm/disarm paths send the code with the secured command.
- `custom_components/loxone/config_flow.py:447-470` options flow
  (`BooleanSelector` / `NumberSelector` pattern, stored defaults so saving
  the form never flips an old install, CORE-15).
- `custom_components/loxone/const.py:128-160` option constants.
- `custom_components/loxone/translations/{en,de,cs}.json` option labels.
- Tests: `tests/test_fan_alarm_media.py`, `tests/test_config_flow.py`,
  and the `#413` row of the regression sweep in
  `docs/review/LIVE-MINISERVER-CHECKS.md`.

## Facts

- `~/github/loxone-mac/docs/pyloxone/secured-controls.md` §3: the
  visualisation password is a per-user field `visupass` next to `pass`
  in Loxone Config's user record; nothing in Config's serializer limits it
  to digits (observed in the binary, not on a live Miniserver).
- The secured command hashes the code as a string (`jdev/sps/ios/...`), so
  letters are valid on the wire.
- Home Assistant's `CodeFormat.NUMBER` only changes the frontend keypad;
  the `alarm_control_panel` services still accept any string.
- The maintainer's alarm is **not** secured (`isSecured: false`), so the
  house cannot test either keypad; it can only confirm that the unsecured
  alarm still asks for no code.

## Decision

Option key, e.g. `alarm_code_format` with values `text` and `number`
(a `SelectSelector`), applied to every secured alarm of the entry.

- **Recommended default: `text`.** A text field can take digits; a numeric
  keypad cannot take letters, and a locked-out user has no hint why. Users
  who want the PIN pad of #413 switch the option once.
- Alternative: default `number` (keeps 1.0.x behaviour for existing
  installs, at the cost of locking out letter passwords until the option is
  found).

Whichever default is chosen, an existing entry without the option gets the
default, and saving the options form must not change an explicitly stored
value. Unsecured alarms keep `code_format = None` and
`code_arm_required = False` regardless of the option.

## Tests (real `hass` fixture)

Use `mock_connection` / `mock_entry` from `tests/conftest.py`. Both alarms
in `tests/fixtures/LoxAPP3.json` are unsecured; add a secured one through a
module-level override of the `loxapp3` fixture rather than editing the
shared fixture (pytest lets a module fixture request the conftest fixture of
the same name; `copy.deepcopy` it first, the conftest one is
session-scoped), so this WP stays file-disjoint from WP-7.1/7.2.

- `hass.states.get(<secured alarm>).attributes["code_format"]` is
  `"text"` / `"number"` per the option, and `None` for the unsecured alarm.
- The options flow stores the choice (drive it with
  `hass.config_entries.options.async_init` / `async_configure`), the entry
  reloads, the attribute follows.
- Arming a secured alarm with a code containing letters via
  `hass.services.async_call("alarm_control_panel", "alarm_arm_away",
  {"entity_id": ..., "code": "ab12"}, blocking=True)` reaches
  `mock_connection.sent` with `secured: True` and that code.
- **Prove the tests are not vacuous:** hard-code `CodeFormat.NUMBER` again,
  see the option test fail, restore.
- Translations: the existing translation-key tests must pass for `en`,
  `de`, `cs`.

## Documentation

- `CHANGELOG.md`, `## Unreleased`, prose voice; cite `upstream d13ef23`,
  `#413`, PC-06. If the default changes the keypad for existing installs,
  say so plainly (a "Changed" entry, not hidden under "Added").
- `README.md`: the option in the options table, and the `Alarm` row.
  `tests/test_docs_readme.py` pins the documented options; extend it for
  the new one instead of weakening it.
- `docs/review/LIVE-MINISERVER-CHECKS.md`: update the `#413` regression row
  to name the option, and add **#33 Secured alarm with a letter in the
  visualisation password** (needs a secured alarm; not available in the
  maintainer's house).
- `docs/fork-vs-upstream.md`: the #413 line, if the default changes.

## Out of scope

Arm modes and their direction (resolved live 2026-09-13), alarm level and
delay attributes, the NFC Code Touch, any change to the secured-command
transport.

## Rules

- Do not push, tag, open a PR or post anything on GitHub. Do not contact
  the live Miniserver or Home Assistant.
- Small commits, conventional subjects (`feat(alarm): ...`), bodies citing
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

Report: the default you used and whether the maintainer chose it, the
non-vacuity evidence, and the user-visible change for existing installs.
