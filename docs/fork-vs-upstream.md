# This fork vs upstream PyLoxone

Fork: `alxzndr/PyLoxone`, branch `master`
Base: `JoDehli/PyLoxone` at `7561247` (release 0.9.23, the upstream HEAD when
this work began on 2026-09-03)
Last updated: 2026-09-16 (release 0.10.9)

---

## The short version

Since branching, 131 commits changed 129 files (31,146 lines added, 4,528
removed). The test suite went from 69 tests to 962 and whole-integration
coverage from 14 % to about 89 %.

**Yes, you can run it**, with one caveat that is genuinely one-way. Read the
rollback section before you install.

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

Notable ones: #413 (alarm PIN pad showed a text field), #501 (Window without
`targetPosition`), #506 (non-ASCII usernames), #512-adjacent RGB brightness,
#515 (message centre as repairs, ported with credit to the original author)
and #517 (control characters in notification texts).

---

## New control types supported

Ten types that upstream ignores entirely:

`InfoOnlyText`, `UpDownDigital`, `Tracker`, `EnergyManager`, `EnergyManager2`,
`PowerUnit`, `Wallbox`, `IntercomV2`, `NfcCodeTouch`, `LightsceneRGB`

Plus a `text` platform that upstream shipped but never loaded (`Platform.TEXT`
was missing from its platform list, so `text.py` was dead code), richer
`PresenceDetector` support (illuminance and noise sub-sensors), Message Center
entries as repair issues, and diagnostic sensors for the Miniserver version
and websocket traffic.

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
its status. As of 2026-09-15:

| Item | Status |
|---|---|
| Alarm arm-home / arm-away parameter | **Resolved 2026-09-13.** The 0.10.0 change was inverted; 0.10.1 restores the direction upstream sent, verified on hardware. |
| Ventilation mode command and speed timer | **Confirmed wrong 2026-09-13.** `setMode/<id>` does nothing and `setTimer` is a self-reverting override. Needs a redesign (the "Ventilation fan model rework" follow-up in the remediation plan); the correct command verbs are still unknown. |
| Legacy `IRoomController` mode table | Open. No V1 controller available; the V1 and V2 tables in the code disagree. |
| `LightsceneRGB` write path | Inconclusive. Four candidate commands produced no reaction, but both available blocks have an empty `sceneList`. |
| Audio zone power / mute / source commands, `Tracker` and `UpDownDigital` shapes, `IntercomV2` sub-controls | Open. No such hardware available. |

None of these affect an installation without that hardware. They do matter
before any of this is proposed upstream.

---

## Assessment

The failure modes the fork fixes are ones a typical installation is exposed
to: inert brightness ranges, resetting counters fed to the energy dashboard as
monotonic ones, entity churn on every reconnect, and a dead integration after
a firmware update.

The risks are the one-way migration, which a backup covers, and that 962
passing tests prove internal consistency rather than agreement with the
Loxone protocol. The parts of that protocol the maintainer's house exercises
have been checked against real hardware. The parts it does not exercise have
not been checked by anyone.
