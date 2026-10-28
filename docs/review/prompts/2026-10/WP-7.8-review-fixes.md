# WP-7.8: review fixes for WP-7.2 to WP-7.6

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `fix/wp-7.8-review` cut from `master`
(`3f41661` or later; WP-7.1 to 7.6 are merged). A review of the merged
packages found the issues below. Fix each in its own commit. Read the
named files first; line numbers are against `3f41661`.

## 1. TextInput writes: do not percent-encode the whole value (medium)

`custom_components/loxone/text.py:39`, `text_input_command`, now sends
`quote(value, safe="")`, which also encodes spaces, `(`, `)`, `;`, `,` and
`'`. Before WP-7.3 values went out raw and worked: the README recipe
(around README.md:452-475) sends `SET(Ap;Fav;4)` raw through
`loxone.event_websocket_command`. Upstream `6911e14` does not encode. The
Structure File (`~/Library/Caches/pyloxone/StructureFile-v16.txt`) says
"uriEncoded" explicitly where it wants encoding (Alarm `{uriEncodedReason}`,
Wallbox names), and TextInput's command is plain `{text}`.

Failure: `text.set_value` with `SET(Ap;Fav;4)` reaches the block as
`SET%28Ap%3BFav%3B4%29` if the Miniserver does not URL-decode it.

Fix: encode only what breaks the command path (`%`, `/`, `?`, `#`), e.g.
`quote(value, safe=" ()[];,:'\"!*+=@&$")`, behind the existing helper.
Test both: `SET(Ap;Fav;4)` goes out unchanged, and `a/b%c` is encoded. Update
live check #31 and the README TextInput row (around README.md:194).

## 2. TextInput: truncate incoming values to the entity's max (low-medium)

`text.py:81-85` sets `native_max` from the `maxLength` detail, but
`event_handler` (`text.py:129-135`) only cuts incoming values at 255. Home
Assistant's `TextEntity.state` raises `ValueError` for a value longer than
`max`. A probe on the real hass fixture confirmed it: feeding 25 characters
to the fixture TextInput (maxLength 20) raises "too long (maximum length
20)" from `async_write_ha_state`, and the entity stays stale.

Fix: truncate incoming values to `self.max` (not 255). Add a test on the
real hass fixture that feeds a value longer than `maxLength` and checks the
state is the truncated value and no error is logged.

## 3. CLI web view: refuse foreign Host headers (low)

`custom_components/loxone/pyloxone_api/__main__.py:486-500`: the web view
binds 127.0.0.1 but never checks the `Host` header, so a malicious website
in the user's browser can use DNS rebinding to read the tree (names, alarm
state, `lastuser`).

Fix: answer 400 unless `Host` is `127.0.0.1:<port>` or `localhost:<port>`.
Test both cases.

## 4. CLI raw/pretty: redact fail-closed (low)

`__main__.py:549-567`: `sink.redact` is only filled in `on_state(True)`.
Nothing guarantees that runs before the initial state dump. `--uuid` mode
already drops messages while `wanted is None`; the default path has no such
guard.

Fix: drop dict messages until the structure-derived redaction set exists.

## 5. CLI test gap (low)

`tests/test_cli_formatters.py:254`, the only end-to-end `main()` test,
filters the NFC reader out with `--uuid`, so the `sink.redact` wiring in
`main()` is untested for raw/pretty. Add a `main()` run without `--uuid`
that asserts the NFC `lastcode`/`lasttag` test values never appear in the
output. Prove it fails when the `sink.redact = ...` line is removed.

## 6. ColorPickerV2: also read `minKelvin`/`maxKelvin` (minor)

`custom_components/loxone/lights/colorpickers.py`, `color_temp_range`:
Structure File v17 names the details `TWMin`/`TWMax` ("available since
15.0"), v16 names them `minKelvin`/`maxKelvin`. The maintainer's firmware
17.2 sends `TWMin`/`TWMax`. Read `minKelvin`/`maxKelvin` as a fallback
when the `TW*` pair is missing, fix the docstring (it says v16 documents
"the same two details", which is true but under the other names), and add
a test case.

## 7. Test that breaks on Home Assistant 2026.11 (minor)

`tests/test_devices.py`, `test_device_registry_snapshot_after_setup`, reads
`DeviceEntry.suggested_area`, which 2026.11 removed. Assert the device's
area instead: resolve `device.area_id` through the area registry
(`homeassistant.helpers.area_registry`) and compare its name. Must pass on
2026.8.1, 2026.9.1 and 2026.11.0b0. Do not change integration code for
this; it only passes `suggested_area` in device info, which is still
supported.

## Docs

`CHANGELOG.md` under `## Unreleased`, in the file's prose voice: amend the
WP-7.3 TextInput entry rather than contradicting it. Update live checks
#31 (TextInput) and #30 (picker range) where the behaviour changed.

## Out of scope

Anything not listed above.

## Rules

- Commit in small logical commits, each ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Tests use the real `hass` fixture and real service calls where an entity
  is involved; prove each new test fails without its fix.
- Do not push, tag or post anything; do not contact the live Miniserver or
  Home Assistant. `ruff.toml` targets py313 on purpose.

## Verification

From the worktree root:

```
V=/Users/alexandergeeraerts/github/PyLoxone/.venv
$V/bin/python -m pytest -q                       # HA 2026.8.1
PATH="$V/bin:$PATH" scripts/lint --check
D=~/Library/Caches/pyloxone
[ -x $D/pytest-ha2026.9 ] && $D/pytest-ha2026.9 -q      # HA 2026.9.1
[ -x $D/pytest-ha2026.11b ] && $D/pytest-ha2026.11b -q  # HA 2026.11.0b0, must be fully green after item 7
```

Report: commits, non-vacuity evidence per item, and the updated live-check
text.
