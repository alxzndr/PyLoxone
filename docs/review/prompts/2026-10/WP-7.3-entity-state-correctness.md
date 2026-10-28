# WP-7.3: TextInput text state, Meter totalFormatNeg, binary sensors unknown until the first value

You work in a git worktree of `alxzndr/PyLoxone` (a maintained fork of
`JoDehli/PyLoxone`), on a new branch `fix/wp-7.3-entity-state` cut from
`master` (`e2123b6` or later). The remote `upstream` is `JoDehli/PyLoxone`
and is already fetched. Read `docs/review/2026-10-upstream-0.10.2-gaps.md`
first.

This WP can run in parallel with WP-7.1, 7.2, 7.4, 7.5 and 7.6 **if it does
not edit `tests/fixtures/LoxAPP3.json`**: override the `loxapp3` fixture in
your own test module instead. pytest lets a module fixture request the
conftest fixture of the same name, and `mock_connection` then uses yours:

```python
@pytest.fixture
def loxapp3(loxapp3):
    data = copy.deepcopy(loxapp3)   # the conftest one is session-scoped: never mutate it
    data["controls"][BIDI_METER_UUID] = {...}
    return data
```

## Goal

Three small correctness fixes, none of which the maintainer's house can
exercise:

1. The writable `text` entity of a `TextInput` shows the block's value. It
   listens on the control's `uuidAction`, but the value arrives on the
   `text` state, which has its own uuid; the entity has never shown
   anything. PS-03 named this in 2026-09 and it was never fixed.
2. A bidirectional `Meter`'s `Total Neg` sensor uses `totalFormatNeg`.
3. Digital binary sensors report `unknown` until their first value instead
   of `off`, and stop overriding Home Assistant's final `state` property.
   Fix the shared string bug while there: `"0"` must not read as on.

## Upstream commits to read

```
git show 6911e14   # TextInput text entity reads the "text" state
git show 64ad11d   # only the text.py hunk (states.get("text") in attributes)
git show 6441290   # Meter totalNeg with totalFormatNeg
git show 127f42a   # binary_sensor unknown until the first event
git show 6f90986   # smoke level (the fork already has this; it shows the shared string bug)
```

Port behaviour, not the test harness: upstream builds entities with
`__new__` / `SimpleNamespace`; the fork tests through the real `hass`.

## Current fork code

- `custom_components/loxone/text.py:75-91` `_state_uuids` and
  `event_handler` read `self.uuidAction` (the comment there, from WP-3.2,
  claims TextInput publishes on `uuidAction`; that is wrong); `:103`
  `states["text"]` unguarded; `:107-110` `async_set_value` sends the raw
  value.
- `custom_components/loxone/sensor.py:144-149` `METER_FORMAT_KEYS`
  (`"totalNeg": "totalFormat"`), `:565-605` `meter_sub_sensor_kwargs`
  (missing format falls back to `"%.1f"`, keep that).
- `custom_components/loxone/binary_sensor.py:256-279` (`_state` starts as
  `STATE_UNKNOWN`, entity available from setup), `:347-366` event handler
  (string branch `value.strip().lower() != "off"` at `:361`), `:370-380`
  `@final state` override and `is_on`, `:388-416` `LoxoneCustomBinarySensor`
  with the same pattern.
- Existing tests that pin today's behaviour:
  `tests/test_entity_state.py:61-73` (feeds the text entity on
  `uuidAction`), `tests/test_cov_event_handlers_sensors.py` (string values
  such as `"On"`), `tests/test_window_monitor.py` (binary sensors after a
  value), `tests/test_digital_device_class.py`,
  `tests/test_presence_sensors.py`.

## Facts that decide the semantics

- `TextInput` (Structure File v16, confirm): state `text` (the value),
  details `maxLength`, `retain`; the command is the text itself on the
  control. The test fixture's TextInput has `states.text` different from
  `uuidAction`. On the maintainer's real structure file no control
  publishes a state under its own `uuidAction` (the only exception is the
  Alarm's `sensors`), so a value stream on `uuidAction` is not a thing to
  rely on. Keep a fallback to `uuidAction` only for a TextInput without a
  `text` state.
- The command is sent as `jdev/sps/io/<uuid>/<value>`
  (`pyloxone_api/connection.py:2068`); a value with `/`, `?`, `#` or spaces
  breaks the path. If the Structure File says the text must be
  URI-encoded, encode it in a named helper (e.g. `text_input_command`);
  if you cannot confirm it, still add the helper (encoding only the
  characters that break the path, `urllib.parse.quote(value, safe="")`),
  and describe the doubt in the live-check entry.
- `Meter` (upstream cites the spec): `totalFormatNeg` exists since 13.01
  and is missing for unidirectional meters; fall back to `totalFormat`,
  then to `"%.1f"`. The maintainer's 7 meters are `type: "legacy"` with
  only `actual` / `total`; their sensors must not change (check the
  snapshot).
- Binary sensors: Home Assistant's `BinarySensorEntity.state` is final and
  derives `on` / `off` / `None` from `is_on`. `is_on` must be `None` until a
  value arrives. Numbers: `> 0` is on. Strings: a numeric string is a
  number (`"0"`, `"0.0"` off, `"1"` on); otherwise `"on"` / `"off"` (any
  case); an empty string or anything else keeps the previous state.
  The Structure File was not available to the reviewer
  (`~/Library/Caches/pyloxone/StructureFile-v16.txt`).

## What to build

1. `text.py`: subscribe to and read `states.get("text")` (fallback
   `uuidAction`), write `_attr_native_value` (unknown until the first value,
   cut to 255 characters as today), `.get()` in the attributes,
   `native_max` from `details.maxLength` when it is a positive int (cap at
   255). Send through the encoding helper.
2. `sensor.py`: `"totalNeg": "totalFormatNeg"` with the fallback chain
   above, in one place.
3. `binary_sensor.py`: one value parser (a small pure function), `is_on`
   `None` until the first value, remove both `state` overrides. Do not
   change availability handling beyond what the unknown state needs.

## Tests (real `hass` fixture)

Use `mock_connection` / `mock_entry` from `tests/conftest.py`;
`mock_connection.feed(uuid, value)` drives the real coordinator, and
`mock_connection.sent` records commands.

- TextInput: after setup the `text.*` entity is `unknown`; feeding the
  `text` state uuid sets it; feeding `uuidAction` does not;
  `hass.services.async_call("text", "set_value", {"entity_id": ...,
  "value": "a/b c"}, blocking=True)` sends the encoded value.
- Meter: a bidirectional meter (added through your module's own `loxapp3`
  override) with `totalFormat: "%.2fkWh"` and `totalFormatNeg: "%.3fkWh"`;
  the `Total Neg` sensor uses the latter (the fork derives
  `suggested_display_precision` from the format, `sensor.py:1612-1616`;
  Home Assistant stores it in the entity registry entry's
  `options["sensor"]["suggested_display_precision"]`), and a meter without
  `totalFormatNeg` falls back.
- Binary sensors: the `InfoOnlyDigital` and `SmokeAlarm` entities read
  `unknown` before the first value, then `on` / `off`; `"0"` reads off.
- **Prove the tests are not vacuous:** reintroduce `uuidAction` in
  `text.py`, `"totalFormat"` for `totalNeg`, the old `_state ==
  _on_state` / string rule, run the new tests, confirm each fails, restore.
  Put the commands and failing test names in the commit message bodies.
- Expected existing-test changes: `tests/test_entity_state.py::test_text_reports_value_after_event`
  (it feeds `uuidAction`; it must now feed the `text` uuid), any test that
  expects a binary sensor to read `off` before its first value. Explain
  each in the commit message. `tests/snapshots/entity_names.json` must not
  change.

## Documentation

- `CHANGELOG.md`, `## Unreleased`, prose voice; cite `upstream 6911e14`,
  `6441290`, `127f42a`, and PS-03, PS-02, PS-04. Mention that a binary
  sensor now reads unknown instead of off until the Miniserver reports it.
- `README.md` line ~193 (`TextInput` row) if the behaviour description
  changes.
- `docs/review/LIVE-MINISERVER-CHECKS.md`: add **#31 TextInput value state
  and command encoding** (no TextInput in the maintainer's house; describe
  the test for someone who has one: type a value with a slash and a space
  in Home Assistant, read the block's `text` state back).

## Out of scope

Removing the read-only TextInput sensor (it stays next to the writable
entity), meter state classes, `InfoOnlyDigital` device-class inference,
availability rework, `TextState`.

## Rules

- Do not push, tag, open a PR or post anything on GitHub. Do not contact
  the live Miniserver or Home Assistant.
- Small commits, conventional subjects (`fix(text): ...`,
  `fix(sensor): ...`, `fix(binary_sensor): ...`), bodies citing upstream
  hashes and finding ids, ending with
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

Report: what changed, the non-vacuity evidence, what you could confirm in
the Structure File, and which existing tests changed and why.
