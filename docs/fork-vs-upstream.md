# This fork vs upstream PyLoxone

Fork: `alxzndr/PyLoxone`, branch `master`
Base: `JoDehli/PyLoxone` at `7561247` (release 0.9.23, the upstream HEAD when
this work began on 2026-09-03)
Last updated: 2026-10-28 (fork 1.0.1 plus the WP-7.x sync with upstream
0.10.2)

---

## The short version

Since branching, 183 commits (not counting merges) changed 151 files (39,754
lines added, 4,643 removed). The test suite went from 69 tests to 1,283 and
whole-integration coverage from 14 % to 91 %.

**Yes, you can run it**, with one caveat that is genuinely one-way. Read the
rollback section before you install.

---

## Version numbers

The fork released 0.10.0 to 0.10.9 between 2026-09-03 and 2026-09-16.
Upstream then released its own, different 0.10.0 and 0.10.1 in October 2026
and 0.10.2 on 2026-10-24. To keep the two apart, the fork continues at
**1.0.x**; a version number starting with `0.10.` in a bug report therefore
needs the repository it came from.

---

## Upstream 0.10.2

Upstream released 0.10.2 on 2026-10-24 and pushed a follow-up batch the same
evening (tip `ecf4d65`). The fork compared all 30 commits of
`38016e3..ecf4d65` against itself, one by one, in
[docs/review/2026-10-upstream-0.10.2-gaps.md](review/2026-10-upstream-0.10.2-gaps.md);
that file is the record, this section is the summary as merged.

The range went both ways: upstream is now porting from the fork. `6f90986`
copies the fork's smoke-alarm level state (PS-04), `d614882` the fork's
select locks and `parse_control_lock` (PS-19), `f61acd9` the fork's
auth-reply filter (the 1.0.0 security fix) and `4230276` the fork's
`scripts/pyloxone-cli` launcher unchanged. Counting those, 14 commits of the
range needed no fork change and 2 were already done better here: the entity
availability of `98704fd` (the fork sends a per-entry connection signal
since API-09, not a global one) and the temperature unit of `e9f0c71`
(under "differs" below). The rest of the range is the table and the lists
below.

What the fork took from 0.10.2, merged as the WP-7.x series, each doubtful
rule behind a named helper and a live-check entry:

| WP | In the fork now | Upstream commits | Live check |
|---|---|---|---|
| WP-7.1 + 7.1b | `LightControllerV2` state derived from moods, master value and circuits (`lcv2_is_on`); moods 0 and 778 read as off (`OFF_MOOD_IDS`), plain turn-on behind `lcv2_plain_turn_on_command` preferring the all-on mood 777; the switch circuit starts unknown | `e43301f`, `466ea0e`, `173d2ea`, `64ad11d` (LCV2 part) | #29 |
| WP-7.2 | ColorPickerV2 Kelvin range from `TWMin`/`TWMax` instead of a hard 2700-6500; plain TunableWhite turn-on sends `setBrightness`, not `On` (the spec lists no `On` for it) | `7cc7c77`, `5867889` (light parts) | #30 |
| WP-7.3 | `TextInput` reads its `text` state (PS-03); Meter `totalNeg` formats with `totalFormatNeg`; binary sensors stay unknown until the first value, and `digital_value_is_on` reads `"0"` as off | `6911e14`, `6441290`, `127f42a`, `64ad11d` (text part), the shared bug in `6f90986` | #31 |
| WP-7.4 | the "Schedule" preset sends `stopOverride` during an override again (a fork regression lost in WP-4.1); the temperature unit comes from whole unit tokens, not `"F" in fmt` | `cf51951` (context), `e9f0c71` (shared bug) | #32 |
| WP-7.5 | the secured-alarm code input is the `alarm_code_format` option, default `text` as upstream chose, `number` for the #413 keypad | `d13ef23` | #33 |
| WP-7.6 | CLI `--format pretty`, `--uuid` and `--format web`, with `lastcode` / `lasttag` / `events` redacted in every format | `4230276`, `adc411b`, `ecf4d65` | no |

Where the fork deliberately differs:

- A Window sends `stop` unconditionally. Upstream `a802e1b` keeps the old
  opposite-end heuristic as a fallback for structures without a
  `targetPosition` state, which drives the window to the other end - the
  very bug that commit set out to fix.
- A bare-degree temperature format follows `msInfo.tempUnit`; upstream
  `e9f0c71` reads it as Celsius even on a Fahrenheit Miniserver. The fork
  also fixed the substring test both used to share (a format labelled
  `%.1f°C Fußboden` is not Fahrenheit).
- `LightControllerV2` manual mode follows the circuits; the master position
  is the fallback. Upstream's "empty `activeMoods` follows the master"
  reads a controller without a `masterValue` as off while its circuits are
  lit - on the maintainer's installation 13 of 20 controllers.
- The alarm code field is a per-entry option, not upstream's unilateral
  switch back to text.
- The CLI and web view redact NFC credentials that upstream prints in clear
  text (PS-27: `lastcode` / `lasttag` never leave the connection).

Do not copy from this range (the full list, with evidence, is the
"do not copy" section of
[docs/review/2026-10-upstream-0.10.2-gaps.md](review/2026-10-upstream-0.10.2-gaps.md)):
the empty-`activeMoods` master fallback and the `_attr_is_on is not True`
plain-turn-on guard from `e43301f`; the Window `targetPosition` fallback to
`fullopen` / `fullclose` from `a802e1b`; `"F" in format_str` as a Fahrenheit
test from `e9f0c71`; the digital sensor reading `"0"` and `"0.0"` as on from
`6f90986`; smoke alarms from `areAlarmSignalsOff` in `64ad11d` (superseded
two commits later); a third entity per `TextInput`; printing NFC
credentials in the CLI; and upstream's test style of building entities with
`__new__` and `SimpleNamespace` instead of the real `hass` fixture - port
the cases, not the harness.

---

## The one-way door: read this first

The fork migrates the config entry from **version 4 to version 5**, moving the
credentials out of `options` into `data`, where Home Assistant expects them.

Home Assistant refuses to load a config entry whose version is *higher* than
the integration's. Upstream is version 4.

**Once you run this fork, you cannot go back to upstream without deleting and
re-adding the integration.** Deleting the entry loses its entity
customisations, area assignments and any automation references to entity ids
that change.

Mitigations, in order of preference:

1. **Snapshot first.** Take a full Home Assistant backup before installing.
   Restoring the backup restores the version-4 entry, which upstream loads.
2. A re-add is not catastrophic: entity ids derive from Loxone UUIDs and are
   stable (see "Entity ids" below), but customisations and area assignments
   live in the registry and would be lost.

---

## Requirements

| | Upstream | This fork |
|---|---|---|
| Minimum Home Assistant | `2025.2.4` (declared) | **`2026.7.0`** |
| Python | implied | **3.14.2+** (what Home Assistant 2026.7 requires) |

Upstream's declared minimum was wrong: its own `sensor.py` imports
`UnitOfRatio`, which only exists from Home Assistant 2026.7. A user on 2025.2
could install it via HACS and it would fail on first setup. The fork's floor
is honest.

---

## What gets better

Verified on a Gen 1 Miniserver (firmware 17.2.8.28, 35 controls) between
2026-09-10 and 2026-09-15:

| Change | Effect |
|---|---|
| **Dimmer brightness range** (PC-17) | A dimmer configured with `min=50,max=100` was driven as if its range were 0-100, so roughly the bottom half of every brightness slider did nothing. Confirmed live: HA brightness 128 now drives it to 75. |
| **Energy meter classification** (PS-21) | Meter `actual` registers report as power / measurement and `total` as energy / total_increasing. Upstream fed resetting counters into the energy dashboard as `total_increasing`, which shows resets as spikes. |
| **Log noise** (#514) | A normal session produces zero WARNING or ERROR records. Upstream logged expected token expiry and clean disconnects as ERROR with full tracebacks. |
| **Reconnect behaviour** (#475, #486, #491) | A dropped connection reconnects in place with backoff. Upstream reloaded the whole integration, which destroyed and recreated every entity and left entities in a wrong state after a Miniserver restart. |
| **Setup survives a Miniserver reboot** (CORE-09) | A transient 401 while the Miniserver boots is retried. Upstream treated it as bad credentials and left the entry dead until a manual reload (see `docs/incidents/`). |
| **Token persistence** (0.10.3) | The Loxone token is actually stored between restarts. Upstream (and 0.10.0 to 0.10.2) re-authenticated with the password every restart because the persist call never worked. |
| **Commands from the executor thread** (0.10.5 to 0.10.7) | Cover, climate, fan and button commands work. Upstream defines these handlers as plain `def`, which Home Assistant runs off the event loop; the fork's stricter send path exposed it and the handlers are now coroutines. |
| **New entities** | `NfcCodeTouch` and `LightsceneRGB` controls produced nothing on upstream; they now produce entities. |

---

## Security and privacy fixes

These matter if you ever paste a diagnostics dump into a GitHub issue, or if
something on your network could reach the Miniserver.

- **`eval()` removed** from the light platform. Upstream called `eval()` on
  six strings received over the websocket: arbitrary code execution in the
  Home Assistant process from anything that could spoof or compromise the
  Miniserver.
- **Bearer token no longer broadcast.** Upstream fired the Loxone auth token,
  the user salt and the visual-password salt onto the Home Assistant event
  bus, readable by any automation, and logged the token at ERROR level in the
  refresh path.
- **Diagnostics redacted.** Upstream dumped the entire `LoxAPP3.json`
  unredacted, including the serial, project name and cloud DNS URL, into a
  file people routinely attach to public issues.
- **NFC credentials never exposed.** The NFC support publishes *who*
  authenticated and *when*, never `lastcode` or `lasttag`.
- **`sys.exit()` removed** from the entity constructor. One malformed control
  could terminate the entire Home Assistant process.

---

## Upstream issues addressed

Referenced by commit: #292, #323, #398, #402, #413, #416, #457, #461, #466,
#475, #479, #481, #486, #491, #492, #501, #506, #514, #515, #517.

Notable ones: #413 (secured-alarm code entry: the fork now follows upstream
`d13ef23`'s text field by default, with the numeric PIN pad of #413 available
through the `alarm_code_format` option), #501 (Window without
`targetPosition`), #506 (non-ASCII usernames), #512-adjacent RGB brightness,
#515 (message centre as repairs, ported with credit to the original author)
and #517 (control characters in notification texts).

---

## New control types supported

Twelve types that upstream ignores entirely:

`InfoOnlyText`, `UpDownDigital`, `Tracker`, `EnergyManager`, `EnergyManager2`,
`PowerUnit`, `Wallbox`, `IntercomV2`, `NfcCodeTouch`, `LightsceneRGB`,
`TextState` (the Status block), `WindowMonitor`

Plus a `text` platform that upstream shipped but never loaded
(`Platform.TEXT` was missing from its platform list, so `text.py` was dead
code; upstream enabled it in 0.10.2 with `6911e14`, which the fork took in
WP-7.3 with the value-state fix), Message Center entries as repair issues,
and diagnostic sensors for the Miniserver version and websocket traffic.

Correction (2026-10): earlier versions of this page also listed
`PresenceDetector` illuminance and noise sub-sensors (#461), and the README
described `AudioZoneV2` power, mute, source and metadata support and
`IntercomV2` sub-controls as switches like the legacy intercom's. Those
claims were tested only against the synthetic test fixture. The Loxone
structure file (v16) documents no brightness or noise state on a
`PresenceDetector` (they become `InfoOnlyAnalog` sensors once "Use" is
ticked on the outputs), no `active`, `mute`, `source` or `metadata` state
and no on/off, mute or source command on an `AudioZoneV2` (its power state
is `power`), and describes intercom sub-controls as pushbuttons. The code
paths are guarded and stay until a live check settles them. From 1.0.0 the
intercom outputs are buttons that send `pulse`, as the structure file
describes them.

---

## Entity ids: unchanged

The fork adopts `has_entity_name`, which normally renames entities. It does
not here. Verified by dumping the entity registry before and after:

```
master: 38 entities | fork: 38 entities
unique_ids removed: NONE   added: NONE
ENTITY_ID RENAMES:  0      stored-name changes: 0
```

Automations and dashboards keep working. What changes is the *display* name:
the duplicated "Kitchen Temp Kitchen Temp" labels are gone.

Caveat: that was measured against the test fixture and one real control set.
A house with control types neither covers could differ.

---

## Manifest differences

| Field | Upstream | Fork |
|---|---|---|
| `iot_class` | `local_polling` | `local_push` (it is a websocket subscriber) |
| `dependencies` | `[]` | `["group"]` (it imports the group component) |
| `requirements` | `websockets>=14`, `pycryptodome`, `httpx` | `websockets>=14,<16`, `pycryptodome>=3.20` (`httpx` was never imported) |
| `integration_type` | absent | `hub` |
| `loggers` | absent | declared, so Home Assistant's debug-logging button covers the protocol layer |

---

## How to install

**Via HACS (recommended):**

1. HACS > Integrations > three-dot menu > Custom repositories
2. Add `https://github.com/alxzndr/PyLoxone`, category *Integration*
3. Install it; it replaces the upstream copy
4. Restart Home Assistant

**Manually:** copy `custom_components/loxone/` over your existing one and
restart.

**Before you do either: take a full backup.** See the one-way door section.

After restarting, check that Settings > Devices & Services > PyLoxone loads
without error, then confirm the energy dashboard still shows sensible figures
and a light dims across its full range.

---

## What is still unverified

`docs/review/LIVE-MINISERVER-CHECKS.md` lists every protocol assumption and
its status. As of 2026-10-28:

| Item | Status |
|---|---|
| Alarm arm-home / arm-away parameter | **Resolved 2026-09-13.** The 0.10.0 change was inverted; 0.10.1 restores the direction upstream sent, verified on hardware. |
| Ventilation mode command and speed timer | **Confirmed wrong 2026-09-13.** `setMode/<id>` does nothing and `setTimer` is a self-reverting override. Needs a redesign (the "Ventilation fan model rework" follow-up in the remediation plan); the correct command verbs are still unknown. |
| Legacy `IRoomController` mode table | Open. No V1 controller available; the V1 and V2 tables in the code disagree. |
| `LightsceneRGB` write path | Inconclusive. Four candidate commands produced no reaction, but both available blocks have an empty `sceneList`. |
| Audio zone power / mute / source commands, `Tracker` and `UpDownDigital` shapes, `IntercomV2` sub-controls | Open. No such hardware available. The structure file (v16) contradicts the audio zone states and commands the code assumes, and describes intercom sub-controls as pushbuttons, not switches; the code follows that from 1.0.0, still unchecked on hardware. |
| `PresenceDetector` illuminance / noise sub-sensors | Most likely dead code: the structure file lists no such states. Open until checked on a real detector. |
| LightControllerV2 off moods, manual mode, plain turn-on | Open, wanted **before release** (#29). The fork's rule (WP-7.1/7.1b) differs from upstream 0.10.2's on purpose; the 777/778 mood ids are observed in the maintainer's scene registry, the behaviour is not yet seen on the device. 20 controllers in the house. |
| ColorPickerV2 Kelvin range from TWMin/TWMax | Open (#30). The settable range only widens on hardware if the fix works; 3 RGB pickers in the house. |
| Gate `stop` command | Open (#34). PC-07 replaced the opposite-direction heuristic with `stop`; upstream `a802e1b` cites the structure file ("stops a moving gate"), never checked here. The maintainer has a Gate. |

None of these affect an installation without that hardware. They do matter
before any of this is proposed upstream.

---

## Assessment

The failure modes the fork fixes are ones a typical installation is exposed
to: inert brightness ranges, resetting counters fed to the energy dashboard as
monotonic ones, entity churn on every reconnect, and a dead integration after
a firmware update.

The risks are the one-way migration, which a backup covers, and that 1,283
passing tests prove internal consistency rather than agreement with the
Loxone protocol. The parts of that protocol the maintainer's house exercises
have been checked against real hardware. The parts it does not exercise have
not been checked by anyone.
