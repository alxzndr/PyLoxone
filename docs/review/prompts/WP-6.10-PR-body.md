# WP-6.10 — `NfcCodeTouch` and `LightsceneRGB` controls (PS-27)

Branch: `fix/wp-6.10-nfc-lightscene` from `9d9a267` == the tip the harness
calls `origin/master` (all Phase 6 prerequisites are in, including the
prerequisite **WP-5.3** at `862f688`).

Catalogue entry behind this WP: **PS-27** ("`NfcCodeTouch` and
`LightsceneRGB` controls produce no entities", confirmed against a live
Gen 1 Miniserver, firmware 17.2.8.28, 2026-09-10). **Upstream:** the
catalogue records *none* — no issue, no upstream PR to port. Verification
of the pre-fix state: on `origin/master` no platform module contains the
strings `NfcCodeTouch` or `LightsceneRGB` (verified with
`git show origin/master:... | grep -c` → 0 in `sensor.py`, `light.py`,
`select.py`), so neither type matched any `get_all`/`iter_controls` scan.

The fixture additions carry the *real* uuids and state maps from the
catalogue's structure-file dump (names/rooms stripped there; rooms/cats
were pointed at existing fixture rooms; the NFC control was given the
"Entry" name, the two scene lights "Cinema Light 1/2").

## Decisions

### NFC reader: event over sensor (PS-27 hard requirement)

A `NfcCodeTouch` is an access-control reader: it is interesting at the
moment it authenticates, not as a polled "last user" string. The primary
exposure is therefore the `**loxone_nfc_auth**` bus event (constant
`EVENT_NFC_AUTH` in `const.py`, fired via `hass.bus.async_fire`), with a
`lastuser` sensor that accompanies — not replaces — it, per the plan.
The event payload carries the control identity, `user` (the
`lastuser` value) and `code_date` (parsed ISO-8601 UTC, falling back to
the raw string when unparseable) plus `entry_id` when the coordinator is
resolvable — the same shape `loxone_event` keeps.

**Credential rule (PS-27 hard requirement):** `lastcode` and `lasttag`
identify *how* someone authenticated. They are not consumed by any
entity, never appear in any state, never in any attribute, and never in
the event payload. The three NFC entities declare their subscription
uuids explicitly, and the new tests assert their absence from
`_state_uuids()`, `extra_state_attributes` and the fired payload
(`test_nfc_*..._no_credentials*`, plus the integration test that
*feeds* the `lastcode` stream and asserts nothing surfaces).

The timestamp sensor does not reinvent an epoch: `nfc_code_date` reuses
the tested `helpers.loxone_timestamp` (Loxone-epoch ms, the message
center's family) and then tries a datetime string. Because nothing we
hold documents the wire format, the format assumption sits in that one
named helper → **live check #21**, with the failure path written out
(split point: `nfc_code_date` for the format,
`LoxoneNfcCodeTouchSensor.event_handler` for the trigger).

### LightsceneRGB: the **channels** are authoritative

The control advertises both a combined `color` state and separate
`red`/`green`/`blue` channel states. **Chosen: the three channels
(0-100 % each) are authoritative** and drive a `light` entity in
`ColorMode.RGB`; the combined `color` stream is deliberately **not**
consumed (and is not subscribed to). Rationale:

1. the trio maps one-to-one onto `ColorMode.RGB` — no second colour
   parser, no coordinate math;
2. the `color` payload is not observable in anything we hold, so
   consuming it would be pure inference about an unseen format, while
   the channels are at least *named* value states of the block;
3. the failure mode of guessing wrong is *missing/incorrect colour*,
   not a broken entity — flipping to `color` later touches one helper.

On the second point: the internal consistency of the block (a
six-channel namespace, a `sceneList` that is *for scenes, not colours*,
`jLockable` ok on both) indicates `color` is the *rendered* colour of
the **active scene** — i.e. display summarization — while `red/green/blue`
are the *actuator* levels. Hence use the channels; the "flipping to
`color`" decision (and how to parse `hsv(...)`/`temp(...)` if we do —
reuse `lights/colorpickers.py` parsing, do not write a second parser)
is written into **live check #22** verbatim.

`turn_off` sends `Off`, a plain power-on sends `On` (the word shapes
`LoxoneSwitch` / `TunableWhiteLight` already use), `turn_on(rgb_color=…)`
sends the three channel commands `red/<0-100>` / `green/<0-100>` /
`blue/<0-100>`. **All three are VERIFY items** — no wire capture exists —
each lives behind its own named helper (`lightscene_off_command`,
`lightscene_on_command`, `lightscene_channel_command`), so flipping the
write path means editing one function.

### Scene select: only when `sceneList` is populated

The live block's `sceneList` is `{}` — a select over it would be a
zero-option entity (the exact PS-19/empty-Radio class of bug WP-4.5
fixed). The select platform therefore builds options through
`lightscene_scene_lookup` and **skips the control with a debug log** when
the list is empty/absent/invalid; the fixture (both scene lights with
`sceneList: {}`) therefore produces *no* select, and the new tests
assert the absence. When populated — the assumed shapes are a list of
names or the Radio-style `{number: name}` dict — a
`select` per control is created, with dedupe/labels following the Radio
conventions (`_dedupe_label`) and the `activeScene` stream decoded by
`lightscene_active_scene_option` (scene name or zero-based index;
unknown values keep the last known option).

- `custom_components/loxone/const.py`
  - `EVENT_NFC_AUTH = "loxone_nfc_auth"` (the per-authentication bus
    event, documented with the credential rule).
- `custom_components/loxone/sensor.py`
  - Pure helpers: `nfc_code_date` (Loxone-epoch ms counter →, then
    datetime string →, aware UTC datetime; **VERIFY**),
    `nfc_auth_event_payload` (no credential fields by construction).
  - Entities (all on the control's device, `type`/`device_type` =
    `NfcCodeTouch`): `LoxoneNfcCodeTouchSensor` (primary, state =
    `lastuser`, fires the auth event on every *change* of `codeDate`),
    `LoxoneNfcCodeDateSensor` (diagnostic TIMESTAMP, name "Code Date"),
    `LoxoneNfcDeviceStateSensor` (diagnostic raw register, name
    "Device State").
  - Setup loop for `iter_controls(hass, config_entry, "NfcCodeTouch")`
    with the per-control `try/except` (PS-08 house pattern).
- `custom_components/loxone/light.py`
  - Pure helpers: `lightscene_channel_value` (0-100 % normalisation,
    guards → `None`), `lightscene_channel_command` /
    `lightscene_on_command` / `lightscene_off_command` (each a
    **VERIFY** assumption in one place).
  - `LoxoneLightsceneRGB(LightEntity)`: `ColorMode.RGB` light driven by
    the three channel states; the `color` stream is not consumed;
    `is_on` derives from the channels; commands above.
  - Setup loop for `iter_controls(hass, config_entry,
    "LightsceneRGB")`.
- `custom_components/loxone/select.py`
  - Pure helpers: `lightscene_scene_lookup` (sceneList → options,
    degrading to `([], [])` on empty/absent/invalid; **VERIFY** shapes),
    `lightscene_active_scene_option` (**VERIFY** decode),
    `lightscene_scene_command` (**VERIFY** `scene/<name>`).
  - `LoxoneLightsceneRGBScene(SelectEntity)` (name "Scene", unique id
    `<uuidAction>/scene` on the light's device) + setup loop that
    **skips** a control with an empty `sceneList`.
- `tests/fixtures/LoxAPP3.json`
  - +3 controls (76 lines): `Entry NFC` (`NfcCodeTouch`, `isSecured:
    true`, the full real state map **including** `lastcode`/`lasttag` —
    the test suite must not publish them), `Cinema Light 1` and
    `Cinema Light 2` (`LightsceneRGB`, `sceneList: {}`).
- `tests/snapshots/entity_names.json`
  - Regenerated (`LOXONE_ENTITY_SNAPSHOT_OUT=… pytest -q
    tests/test_entity_name_snapshot.py`) and reviewed: exactly +5 rows
    (2 lights, 3 NFC sensors), all on the right device names/models,
    short names for the sub-sensors, no "Device Device" duplicates (the
    no-duplicate test passes).
- `tests/test_wp610_nfc_lightscene.py` (new, 16 tests)
  - Hand-derived literals for every pure helper (`nfc_code_date`
    against the 2009 Loxone epoch, `lightscene_channel_value`,
    commands, scene lookup/decode).
  - Unit-style: NFC user sensor + event firing (change only, no
    re-fire on repeat, latest user), credential absence from
    attributes/subscription/payload; light channel → color math
    (100 % → 255, 50 % → 128, 25 % → 64; malformed feed keeps last
    colour; `color`-stream feed changes nothing); command shapes;
    scene select tracking + `scene/<name>` send + unknown-option
    rejection.
  - Integration (WP-0.2 fixtures): after setup the 3 NFC sensors + 2
    lights exist (no entity per credential stream, no select for the
    empty sceneList); fed `lastuser`/`codeDate`/`deviceState` update
    the entities and exactly one auth event fires with the right
    payload (no `lastcode`/`lasttag` — a fed credential stream
    surfaces nowhere); fed channel values update each light
    independently; `turn_off`/`turn_on(rgb)`/plain `turn_on` send
    `Off` / `red/100`,`green/25`,`blue/3` / `On` addressed at the
    control's `uuidAction`.
- `docs/review/LIVE-MINISERVER-CHECKS.md`
  - New non-blocking items **#21** (NFC `codeDate` format + event
    trigger; helpers `nfc_code_date`,
    `LoxoneNfcCodeTouchSensor.event_handler`) and **#22**
    (LightsceneRGB colour authority, write commands, scene list
    shape; the named helpers above) — both name the one-place fix
    point and the pass/fail acceptance.
- `CHANGELOG.md` — Unreleased: one Added entry covering both control
  types.

No `manifest.json` version bump (rule 8); no YML changes; nothing else
touched.

## VERIFY items (live-Miniserver checks required before merge)

Both are recorded in `docs/review/LIVE-MINISERVER-CHECKS.md` and every
assumption in the package sits behind a named helper so the flip is one
place:

1. **#21 — NfcCodeTouch:** wire format of `codeDate` (helper
   `nfc_code_date`), and the event trigger = *change of the
   `codeDate` stream* (helper
   `LoxoneNfcCodeTouchSensor.event_handler`) — the first push after
   (re)connecting also fires once, and a re-auth with the same code
   date does not.
2. **#22 — LightsceneRGB:** (a) channels authoritative over the
   consolidated `color` stream (entity `LoxoneLightsceneRGB`); (b) the
   write-path word shapes: `red/<l>`/`green/<l>`/`blue/<l>`, `On`, `Off`
   (helpers `lightscene_channel_command` / `lightscene_on_command` /
   `lightscene_off_command`); (c) the shape of `sceneList` when
   populated (list of names vs `{number: name}` dict — both accepted by
   `lightscene_scene_lookup`), the decode of `activeScene` (name or
   index — `lightscene_active_scene_option`), and the scene activation
   command `scene/<name>` (`lightscene_scene_command`).

## Follow-ups

1. (Creative-coop/external) If the scene select becomes usable in the
   field, also display the currently active scene on the *light* entity
   as an attribute (`activeScene` is consumed only by the select).
2. The NFC `events` state was not consumed; if a live check (item #21)
   shows it to be the authoritative authentication marker (rather than
   `codeDate`), switch the trigger to it (one place).
3. `jLocked` states on both controls (lockable controls) are not
   surfaced anywhere. If live usage shows a lock is meaningful here
   (intercom, alarm …), the binary/platform handling would come in a
   separate WP.
4. `nfcLearnResult`, `historyDate`, `keyPadAuthType` are deliberately
   not exposed (learning mode / audit data); decide per field use.

## Definition-of-done checks

Ran from the repo root on this branch, immediately before the docs
commit:

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
80 files already formatted

$ pytest -q
TOTAL                                                           1635    666    59%
Required test coverage of 20% reached. Total coverage: 59.27%
705 passed, 1 deselected in 71.24s (0:01:11)
```

Baseline on `origin/master` before the change: `689 passed, 1
deselected`. Delta 16 = the new `tests/test_wp610_nfc_lightscene.py`;
the only other test-tree change is the hand-reviewed, regenerated
`tests/snapshots/entity_names.json` (data for the existing
`test_entity_registry_snapshot`), which failed with exactly the +5
expected rows until reviewed and adopted.

Contract tests (`tests/test_contracts.py`, 7) pass — the PS-14 group
table was not widened, and the new `device_type` values
(`NfcCodeTouch`, `LightsceneRGB`) are emitted only (not table-referenced),
which the extractor test tolerates.

No new `# noqa` markers; `grep -rn "TODO(WP-6.10)" custom_components/`
returns nothing (this WP owns no WP-0.1 marker).
