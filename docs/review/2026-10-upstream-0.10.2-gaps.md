# Upstream 0.10.2 against the fork (2026-10-28)

Range reviewed: `git log 38016e3..upstream/master` (upstream 0.10.1 to the
0.10.2 release of 2026-10-24 and the batch pushed the same evening, tip
`ecf4d65`). 30 commits plus 2 merges. Every diff was read with `git show`
and compared with the fork at `e2123b6` (release 1.0.1). The status columns
describe the fork at `e2123b6`; every work package below has since merged -
see "Aftermath" at the end.

Sources used to decide Loxone semantics:

- The maintainer's real structure file
  (`~/github/loxone-mac/Tests/LoxoneKitTests/Fixtures/LoxAPP3.device.json`,
  Gen 1, firmware 17.2.8.28, private: only shapes are quoted here, never
  contents). It has 20 `LightControllerV2` (7 with a `masterValue` sub-control,
  13 without), 36 `Dimmer` and 8 `Switch` circuits inside them, 3
  `ColorPickerV2` (`pickerType: "Rgb"`, `TWMin: 2200`, `TWMax: 6500`, one of
  them the `masterColor`), 7 `Meter` (`type: "legacy"`, states `actual`/`total`
  only), one each of `Gate`, `Jalousie` (publishes both `autoActive` and
  `autoState`, both `infoText` and `autoInfoText`), `Alarm` (not secured),
  `NfcCodeTouch` (secured), `Ventilation`, `Tracker`, 2 `Switch`, 2
  `LightsceneRGB`. No `TextInput`, `IRoomController(V2)`, `AcControl`,
  `SmokeAlarm`, `InfoOnlyDigital`, `Window`, `TunableWhite` picker.
- The reverse-engineering notes in `~/github/loxone-mac/docs/pyloxone/`
  (`HANDOFF.md`, `pyloxone-gaps.md`, `secured-controls.md`, `live/README.md`):
  unknown commands answer `Code 200` / `"0"`, so a 200 reply proves nothing;
  command words are case-insensitive; `visupass` is a free-form per-user
  password field.
- **The Structure File v16 text was not available for this review.** The
  expected copy in the review's scratch directory does not exist and no
  other copy was found under `~`. Where a row below says "upstream cites
  the spec", the claim is upstream's (they quote
  `docs/1700_Structure-File.txt` and page
  numbers) and has not been re-read here. Every WP prompt tells the agent to
  confirm those claims against the Structure File before changing behaviour,
  and to keep anything it cannot confirm behind a named helper with a
  `LIVE-MINISERVER-CHECKS.md` entry.

A notable thing about this range: upstream is now porting from the fork.
`6f90986` carries the fork's PS-04 comment verbatim, `d614882` says it
"reflects the fork's PS-19 select changes" and adds the fork's
`parse_control_lock`, `f61acd9` adds `_AUTH_COMMANDS` / `is_auth_response`
with the fork's names, and `4230276` adds the fork's `scripts/pyloxone-cli`
launcher unchanged.

## Status counts

| Status | Count | Commits |
|---|---|---|
| Fork already has it | 14 | e1aa4b7, e61e12e, c406497, c0b1a33, 7cfea6e, a802e1b, cf51951, 700e27a, 3b34a05, 7b52900, 049d39e, 6f90986, d614882, f61acd9 |
| Fork has something better | 2 | e9f0c71, 98704fd |
| Gap (fork lacks it) | 9 | 6911e14, 6441290, e43301f, 466ea0e, 173d2ea, 127f42a, 4230276, adc411b, ecf4d65 |
| Partial gap | 3 | 64ad11d, 7cc7c77, 5867889 |
| Fork deliberately differs | 1 | d13ef23 |
| Not applicable | 3 | 3db77b8, 4ad1a6f (merge), 37079d6 (merge) |

Two gaps were found that no upstream commit names, while checking the
neighbouring code: the fork's "Schedule" preset no longer ends an override
(a fork regression), and the fork's `TextInput` text entity never receives a
value (PS-03 was never fixed). Both are in the WP list.

## Per-commit table

Fork line numbers are at `e2123b6`. Effort: S under an hour or two, M half a
day, L more.

| Commit | Subject | Fork status | Evidence (fork) | Port | Effort |
|---|---|---|---|---|---|
| d13ef23 | fix(alarm): accept text codes for secured alarms again | **Deliberately differs.** The fork shows a numeric keypad for a secured alarm (PC-06, #413); upstream goes back to a text field because the code is the user's visualisation password, which may contain letters. The RE notes back upstream: `visupass` is a free-form per-user password (`secured-controls.md` §3). A numeric pad locks a user with a letter in the password out of the alarm card; a text field only inconveniences the others. | `alarm_control_panel.py:136-141` | maybe (WP-7.5, maintainer decides) | S |
| e1aa4b7 | fix(climate): send the absolute comfort temperature with setComfortModeTemp | Has it (ported in 9dd02f0). | `climate.py:284-293` | no | - |
| e61e12e | fix(climate): use the legacy IRoomController modes and commands from the spec | Has it (9dd02f0; LIVE check #4 still open). | `climate.py:152, 553, 601-609` | no | - |
| c406497 | fix(climate): no TypeError for the economy range while states are missing | Has it. | `climate.py:843-848, 862-866` | no | - |
| e9f0c71 | fix(climate): report Fahrenheit for AC controls formatted in °F | **Fork better**: one shared `temperature_unit_from_format` that also falls back to `msInfo.tempUnit` for bare-degree formats (1d4d03d); upstream treats `%.1f°` as Celsius on a °F Miniserver. Both share a bug: `"F" in fmt` makes any format containing a capital F (unit text such as "Fußboden") Fahrenheit, and `"C"` likewise. | `climate.py:66-85, 781-792, 1163-1167` | no; fix the shared bug (WP-7.4) | S |
| c0b1a33 | fix(sensor): label all room controller override reasons from the spec | Has it (e29fbf6, with slugs and translations; LIVE #26). | `sensor.py:110-133` | no | - |
| 7cfea6e | fix(cover): read the Jalousie automatic state from autoActive / infoText | Has it (cd5074a, shared helpers). The real device publishes both name pairs, so the spec names win there too. | `helpers.py:251-264`, `cover.py:474-478`, `select.py:17` | no | - |
| 98704fd | fix: update entity availability through a dispatcher signal | **Fork better**: per-entry connection signal since API-09/CORE-28, sent by the coordinator, every entity subscribed. | `coordinator.py:247-259`, `__init__.py:1354, 1452-1454` | no | - |
| 6911e14 | feat(text): fix the TextInput text entity and enable the text platform | **Partial gap.** The platform is forwarded, but `LoxoneText` listens on `uuidAction`, not on `states.text`; the Structure File's `text` state has its own uuid (the fixture's does, and on the real device no control publishes a state on its `uuidAction` except the Alarm's `sensors`). The writable entity therefore never shows a value. PS-03 named this in 2026-09 and was never fixed; WP-3.2 even documented `uuidAction` as the value stream. | `text.py:75-91, 103`, `tests/test_entity_state.py:66-73` (feeds `uuidAction`) | yes (WP-7.3) | S |
| 6441290 | fix(sensor): format the Meter totalNeg value with totalFormatNeg | **Gap.** `totalNeg` still uses `totalFormat`. Upstream cites the spec: `totalFormatNeg`, since 13.01, absent for unidirectional meters. Not on the maintainer's meters (legacy, no `totalNeg`). | `sensor.py:144-149, 601` | yes (WP-7.3) | S |
| a802e1b | fix(cover): send the spec stop command instead of opposite-end heuristics | Has it (PC-07): Gate and Window send `stop`. The fork's Window sends `stop` unconditionally; upstream keeps the old opposite-end heuristic for structures without `targetPosition`, which is the bug it set out to fix (see "do not copy"). `gate_stop_command` still says VERIFY but has no LIVE-MINISERVER-CHECKS entry; the maintainer has a Gate. | `cover.py:75-85, 287-290, 440-448` | no; add live check (WP-7.7) | S |
| cf51951 | fix(climate): send the spec command setOperatingMode in the stop fallback | Has the typo fix (PC-20). **But** while re-checking: the fork dropped the branch that sends `stopOverride` when the controller is overridden (0.9.23 had it, `7561247:climate.py:722-727`; removed in c7ad8c9, WP-4.1). Selecting "Schedule" during an override now sends `setOperatingMode/0`, which does not end the override and can switch an auto-heat-only controller to heat and cool. | `climate.py:974-986` | yes, the stopOverride branch (WP-7.4) | S |
| 64ad11d | fix: stop optional-structure states from crashing the entity setup | **Mostly has it** (PS-04, PS-05: `.get()` in binary_sensor, select, button, number, switch, cover). Left: `LightControllerV2` indexes `moodList`/`activeMoods`/`additionalMoods` directly (also in `_state_uuids`, so a missing state raises in `async_added_to_hass`), and `text.py` attributes index `states["text"]`. Upstream's intermediate smoke-alarm change in this commit is superseded by 6f90986. | `lights/lightcontroller.py:69, 165-167, 209-224`, `text.py:103` | yes, the remainder (WP-7.1, WP-7.3) | S |
| 700e27a | fix(cover): tolerate optional structure states for Gate, Window, Jalousie | Has it (`_state_uuid` helper for every optional cover state). | `cover.py:56-60, 343, 478` | no | - |
| e43301f | fix(light): derive LightControllerV2 state from moods and master value | **Gap, with semantics to settle.** Fork: off only when `activeMoods == [778]`, anything else (including `[]` and `[0]`) is on. Upstream: ids 0 and 778 are off, `[]` is manual mode and follows the master position. Upstream's `[]` rule turns every manual-mode controller without a `masterValue` (13 of the maintainer's 20) off while its circuits are lit (see "do not copy"). Plain turn-on: fork sends `changeTo/99`, upstream the first mood of `moodList`; neither is verified, and a 200 reply cannot tell. | `lights/lightcontroller.py:10-15, 137-157, 209-216` | yes, redesigned (WP-7.1, live check) | M |
| 7cc7c77 | fix(light): complete ColorPickerV2 commands and use TWMin/TWMax | **Partial gap.** Has: `temp()` never carries `None` (PC-11), brightness-only RGB turn-on sends `setBrightness` (PC-03). Lacks: the Kelvin range from `details.TWMin`/`TWMax`; the fork hard-codes 2700-6500 while all 3 of the maintainer's pickers say 2200-6500, so Home Assistant clamps 2200-2699 K away. Differs: a plain TunableWhite turn-on sends `On`, which upstream says the spec does not list. | `lights/colorpickers.py:18-21, 54-66, 70-71, 177-178` | yes (WP-7.2) | S |
| 3b34a05 | refactor(websocket): delete dead recv_message() with broken keepalive framing | Has it (removed earlier, comment left at the reader). | `pyloxone_api/connection.py:1592` | no | - |
| 4ad1a6f | Merge branch 'master' into fix/entity-fixes | n/a (merge) | - | - | - |
| 7b52900 | docs(climate): document service/0 restore semantics per structure spec | Has it (named constants `LEGACY_SERVICE_NORMAL` / `LEGACY_SERVICE_HEATING_COOLING_OFF`). | `climate.py:601-609` | no | - |
| 466ea0e | fix(light): stop hardcoding installation-specific mood ids | **Gap**, part of WP-7.1: one `OFF_MOOD_IDS` constant, and plain turn-on skips off moods. Upstream states that 778 is only observed on its installation and the runtime mood list is not in the structure file. | `lights/lightcontroller.py:10-15` | yes (WP-7.1) | S |
| 5867889 | fix: harden Meter format lookup, color temp range, and plain turn-on brightness | **Partial.** Meter: fork better (`details.get(key, "%.1f")`, PS-08). `TWMin: null`: gap, comes with WP-7.2. TunableWhite plain turn-on with the last brightness: gap, WP-7.2. | `sensor.py:601`; `lights/colorpickers.py:62-63` | yes, the light parts (WP-7.2) | S |
| 37079d6 | Merge pull request #530 from JoDehli/fix/entity-fixes | n/a (merge) | - | - | - |
| 3db77b8 | chore: bump version to 0.10.2 | n/a | - | - | - |
| 173d2ea | fix(light): start light switch state as None instead of the string 'unknown' | **Gap** (PS-02 was fixed for switches, not for the LCV2 switch circuit). Invisible today because the entity is unavailable until its first value, but `"unknown"` is truthy. | `lights/switch.py:22-23` | yes (WP-7.1) | S |
| 049d39e | fix(connection): accept sync message callbacks | Has it (`inspect.isawaitable` at every callback site). | `pyloxone_api/connection.py:333, 672, 1585, 1752` | no | - |
| 4230276 | feat(cli): pyloxone-cli with raw/pretty output and uuid filter | **Gap** (nice to have). The launcher is the fork's own (84efbb8); the fork's `__main__.py` is 52 lines with a raw print only. | `pyloxone_api/__main__.py:1-52`, `scripts/pyloxone-cli` | yes (WP-7.6) | M |
| 127f42a | fix(binary_sensor): report unknown until the first event, stop overriding state | **Gap.** The fork's digital sensor is available from setup and reads `off` until the first value (`_state` starts as `STATE_UNKNOWN`, `is_on` compares with `on`); both binary sensor classes override HA's `@final` `state`. | `binary_sensor.py:256-279, 370-380, 392, 406-416` | yes (WP-7.3) | S |
| 6f90986 | fix(binary_sensor): report smoke alarms from the level state, not the muted-signal flag | Has it: this is the fork's PS-04 code, copied by upstream. Both share the string bug listed under "do not copy". | `binary_sensor.py:264-275, 350-366` | no; fix the shared bug (WP-7.3) | - |
| d614882 | fix(select): decode jLocked as JSON, skip optionless Radio blocks, enforce locks | Has it (PS-19, 78475ce; upstream ported the fork's helper). | `helpers.py:591`, `select.py:125-127, 356, 367` | no | - |
| f61acd9 | fix(connection): do not forward auth command responses to the message callback | Has it (1.0.0 security fix, same names). | `pyloxone_api/connection.py:163-187, 1698` | no | - |
| adc411b | fix(cli): print command replies in full in pretty output | **Gap**, with WP-7.6. Safe in the fork only because auth replies never reach the callback. | - | yes (WP-7.6) | S |
| ecf4d65 | feat(cli): add --format web with a live, server-rendered structure tree | **Gap** (nice to have), with WP-7.6. Binds 127.0.0.1, no JavaScript, escapes names. Shows every state value, including the NFC reader's `lastcode`/`lasttag`, which the fork never exposes. | - | yes, with redaction (WP-7.6) | M |

## Who is right where the two disagree

| Topic | Upstream | Fork | Verdict |
|---|---|---|---|
| Secured alarm code field | text | numeric (#413) | Upstream is safer: `visupass` is a free-form password, and a numeric pad cannot enter a letter. Settle with an option (WP-7.5). The maintainer's alarm is not secured, so the house cannot test it. |
| ColorPickerV2 Kelvin range | `details.TWMin/TWMax`, default 2700/6500 | fixed 2700-6500 | Upstream. The real device has `TWMin: 2200` on all three pickers. |
| TunableWhite plain turn-on | `setBrightness/<last level>` | `On` | Undecided without the Structure File. `On` may be an unknown word that the Miniserver acknowledges with 200 and ignores. WP-7.2 decides from the spec; no TunableWhite in the house. |
| LCV2 off moods | 0 and 778 | 778 only | Upstream cites the spec for "0 is always off"; the fork itself sends `changeTo/0` to turn off. Treat both as off. |
| LCV2 empty `activeMoods` | manual mode, follows the master | on | Neither is right for a controller without a master. WP-7.1 derives it from the circuits and needs a live check. |
| LCV2 plain turn-on | first non-off mood of `moodList` | `changeTo/99` | Neither is verified; 99 is in no source and would be acknowledged even if unknown. WP-7.1 puts the choice behind a helper and checks it on the house. |
| Window stop on old firmware | old opposite-end heuristic without `targetPosition` | always `stop` | Fork. The heuristic drives the window to the other end, the bug upstream's own commit describes. |
| Temperature unit of a bare `%.1f°` | Celsius | `msInfo.tempUnit` | Fork (Loxone Config writes bare-degree formats; 1d4d03d). |
| TextInput value stream | `states.text` | `uuidAction` | Upstream (and the fork's own PS-03). |
| Binary sensor before the first value | unknown | off | Upstream (and the fork's own PS-02 rule). |
| "Schedule" preset during an override | `stopOverride` | `setOperatingMode/0` | Upstream; the fork lost the branch in WP-4.1. |

## Do not copy

1. **`e43301f` / `466ea0e`, empty `activeMoods` reads as "follows the master".**
   `self._master_position_loxone or 0` is 0 for a controller without a
   `masterValue`, so every manual-mode controller without a master (13 of
   20 in the maintainer's house) reads off while its circuits are lit. The
   "empty list means manual" observation is upstream's alone, from one
   installation.
2. **`e43301f`, `_attr_is_on is not True` as the plain turn-on guard.**
   While the state is unknown (before the first `activeMoods`), a plain
   turn-on switches the room to a mood. The fork has the same guard (`not
   self._attr_is_on`); WP-7.1 decides it explicitly.
3. **`a802e1b`, Window `stop` gated on `targetPosition`.** The fallback
   still sends `fullopen` / `fullclose` and drives the window to the other
   end. The fork's unconditional `stop` stays.
4. **`e9f0c71`, `"F" in format_str`.** Any capital F anywhere in the format
   (unit text, a label) means Fahrenheit; `"C"` likewise. The fork has the
   same test (`climate.py:79-82`); fix it, do not re-import it.
5. **`6f90986`, string values in the digital sensor.** `value.strip().lower()
   != "off"` reads the strings `"0"` and `"0.0"` as on. The fork has the
   identical code (`binary_sensor.py:361`).
6. **`64ad11d`, smoke alarm on `areAlarmSignalsOff`.** An intermediate
   state, superseded two commits later.
7. **`6911e14`, a writable text entity *and* a read-only sensor for every
   TextInput.** The fork does the same (`sensor.py:987`, README line 193).
   Not a bug, but do not add a third entity; WP-7.3 keeps the pair as is.
8. **`ecf4d65` / `4230276` / `adc411b`, printing every state.** The CLI and
   web view show `lastcode` / `lasttag` of an NFC reader in clear text. The
   fork's rule is that these never leave the connection; WP-7.6 redacts
   them.
9. **The upstream test style.** All new upstream tests build entities with
   `__new__` and `SimpleNamespace` buses. The fork's rule is the real `hass`
   fixture and real service calls; port the cases, not the harness.

## Test fixture reality check

While mapping the commits to the real device it turned out that the
fixture shapes these WPs touch are not the shapes a Miniserver sends:

| Block | `tests/fixtures/LoxAPP3.json` | Real device (firmware 17.2.8.28) |
|---|---|---|
| `LightControllerV2` | states `active`, `bri`, `control`, `mood`, `state`; no sub-controls keyed `<uuid>/masterValue` | states `activeMoods`, `activeMoodsNum`, `additionalMoods`, `circuitNames`, `daylightConfig`, `favoriteMoods`, `jLocked`, `moodList`, `presence`; sub-controls keyed `<uuid>/AI1`, `<uuid>/masterValue`, `<uuid>/masterColor`; `details.masterValue` points at the sub-control |
| `ColorPickerV2` | `pickerType` 0 / 1 / 2, states `brightness`, `hue`, `saturation`, `kelvin` | `pickerType: "Rgb"`, `TWMin`, `TWMax`, states `color`, `sequence`, `sequenceColorIdx` |
| `Meter` | details `format`, `powerFormat`, ...; states without `actual` | `type: "legacy"`, `actualFormat`, `totalFormat`; states `actual`, `total` |

So the integration-level tests of these blocks run against fiction. WP-7.1,
7.2 and 7.3 each add a realistic block (synthetic uuids and names, never the
private file's values) and keep the old ones.

## Work packages

Ordered by impact on the maintainer's house first, then general
correctness, then nice to have. Prompts are in
`docs/review/prompts/2026-10/`.

| WP | Title | Upstream | Why | Live check | Hardware in the house |
|---|---|---|---|---|---|
| WP-7.1 | LightControllerV2 on/off from moods, master and circuits; plain turn-on | e43301f, 466ea0e, 64ad11d (LCV2 part), 173d2ea | 20 controllers. Today `[0]` and `[]` read as on and the plain turn-on sends a mood id no source documents. | **Yes, before release** (#29) | yes: 20 LCV2, 7 with master |
| WP-7.2 | ColorPickerV2 Kelvin range from TWMin/TWMax; TunableWhite plain turn-on | 7cc7c77, 5867889 (light parts) | 3 RGB pickers whose 2200-2699 K range is unreachable from HA. | **Yes** for RGB (#30); TunableWhite cannot be checked | yes: 3 RGB pickers; no TunableWhite |
| WP-7.3 | State correctness: TextInput text state, Meter totalFormatNeg, binary sensors unknown until the first value | 6911e14, 6441290, 127f42a, 64ad11d (text part), shared bug in 6f90986 | General correctness; a dead writable entity, a wrong format, a sensor that says off when it does not know. | Entry only (#31); no hardware | no TextInput, no bidirectional meter, no binary sensor |
| WP-7.4 | Climate: "Schedule" ends an override again; temperature unit detection | cf51951 (context), e9f0c71 (shared bug) | Fork regression from WP-4.1 and a substring bug. | Entry only (#32); no hardware | no room controller, no AC |
| WP-7.5 | Secured alarm code: text or numeric keypad | d13ef23 | Lockout for letter passwords vs. #413. **Needs the maintainer's decision first.** | Entry (#33); house alarm is not secured | partial |
| WP-7.6 | CLI: pretty output, uuid filter, web view, with credential redaction | 4230276, adc411b, ecf4d65 | Debugging aid for live checks; upstream parity. | No (it is the tool for live checks) | n/a |
| WP-7.7 | Docs: fork-vs-upstream for 0.10.2, Gate stop live check, do-not-copy notes | a802e1b (Gate), the whole range | Keep the comparison honest; Gate `stop` is still VERIFY without an entry. | Adds #34 (Gate stop) | yes: Gate |

Not a WP: everything marked "has it", "fork better" and "n/a" above.

## Aftermath (2026-10-28, all merged into master)

All seven work packages shipped, so the "Gap" and "Partial gap" rows above
describe the fork at `e2123b6`, not current master:

- **WP-7.1 + WP-7.1b** (`e43301f`, `466ea0e`, `64ad11d` LCV2 part,
  `173d2ea`): `lcv2_is_on` derives the state from moods, master and
  circuits with circuits first; `OFF_MOOD_IDS = {0, 778}`; the mood ids
  777 ("alles aan") and 778 ("uit") are now *observed* in the
  maintainer's scene registry, not guessed; plain turn-on behind
  `lcv2_plain_turn_on_command` prefers `ALL_ON_MOOD_ID` 777, then the
  first non-off mood, then `plus`; the LCV2 switch circuit starts `None`.
  Live check #29 (amended by WP-7.1b). Do-not-copy items 1 and 2 fixed
  fork-side.
- **WP-7.2** (`7cc7c77`, `5867889` light parts): Kelvin range from
  `TWMin`/`TWMax`; plain TunableWhite turn-on sends `setBrightness` - the
  Structure File (v16) lists no `On` command for TunableWhite pickers, so
  upstream's reading won. Live check #30; no TunableWhite in the house.
- **WP-7.3** (`6911e14`, `6441290`, `127f42a`, `64ad11d` text part, the
  shared bug in `6f90986`): `TextInput` reads its `text` state and sends
  percent-encoded; Meter `totalNeg` uses `totalFormatNeg`; binary sensors
  read `unknown` until the first value and `digital_value_is_on` decodes
  `"0"` / `"0.0"` as off. Live check #31 (entry only). Do-not-copy items
  5 and 7 handled fork-side.
- **WP-7.4** (`cf51951` context, `e9f0c71` shared bug): the "Schedule"
  preset sends `stopOverride` during an override again; the temperature
  unit is read from whole unit tokens, bare `°` from `msInfo.tempUnit`.
  Live check #32 (entry only). Do-not-copy item 4 fixed fork-side.
- **WP-7.5** (`d13ef23`): the maintainer chose upstream's text field as
  the new default, with the #413 keypad kept as the `alarm_code_format:
  number` option. Live check #33 (house alarm is not secured).
- **WP-7.6** (`4230276`, `adc411b`, `ecf4d65`): CLI `--format pretty`,
  `--uuid`, `--format web`; `lastcode` / `lasttag` / `events` redacted in
  every format. Do-not-copy item 8 enforced by redaction.
- **WP-7.7**: this file and `docs/fork-vs-upstream.md` synced to the
  merged state; the Gate `stop` VERIFY became live check **#34** (the
  maintainer has a Gate). Do-not-copy items 3 (Window `stop` stays
  unconditional) and 9 (test harness) remain fork policy.
