# This fork vs upstream PyLoxone — what changed, and how to deploy it

Fork: `alxzndr/PyLoxone`, branch `master`
Base: `JoDehli/PyLoxone` at `7561247` (release 0.9.23, the upstream HEAD when this began)
Date: 2026-09-10

---

## The short version

86 commits. 33,532 lines added, 4,107 removed, across 171 files:

| | Lines |
|---|---|
| Production code | 13,132 |
| Tests | 14,395 |
| Documentation | 10,112 |

The test suite went from **69 tests to 705**, coverage from **14% to 59%**, and 21 of 33
modules that had no tests at all now have them.

**Yes, you can run it** — with one caveat that is genuinely one-way. Read the rollback
section before you install.

---

## The one-way door: read this first

The fork migrates your config entry from **version 4 to version 5**, moving your
credentials out of `options` and into `data` where Home Assistant expects them.

Home Assistant refuses to load a config entry whose version is *higher* than the
integration's (`config_entries.py`, "has version %s which is higher than the current
version"). Upstream is version 4.

**So: once you run this fork, you cannot go back to upstream without deleting and
re-adding the integration.** Deleting the entry loses its entity customisations,
area assignments and any automation references to entity IDs that change.

Mitigations, in order of preference:

1. **Snapshot first.** Take a full Home Assistant backup before installing. Restoring the
   backup restores the version-4 entry, which upstream loads fine.
2. Note that a re-add is not catastrophic — entity IDs are derived from Loxone UUIDs and
   are stable (see "Entity IDs" below) — but customisations and area assignments live in
   the registry and would be lost.

---

## Requirements

| | Upstream | This fork |
|---|---|---|
| Minimum Home Assistant | `2025.2.4` (declared) | **`2026.7.0`** |
| Python | implied | **3.14.2+** (what HA 2026.7 requires) |

Upstream's declared minimum was wrong: its own `sensor.py` imports `UnitOfRatio`, which
only exists from HA 2026.7. A user on 2025.2 could install it via HACS and it would fail
on first setup. The fork's floor is honest.

The fork also uses `except A, B:` (PEP 758, unparenthesised) in 20 places, which is
**Python 3.14+ only**. That is inside the supported floor, but it means the code cannot
run on 3.13 even if a future HA allowed it.

---

## What actually gets better on your house

Verified against your Miniserver (Gen 1, firmware 17.2.8.28, 35 controls) on 2026-09-10:

| Change | Effect for you |
|---|---|
| **Dimmer brightness range (PC-17)** | Your Bureau dimmer is `min=50,max=100`. Upstream ignored that range, so HA brightness 1 mapped to Loxone 0 (off) and roughly the **bottom half of every brightness slider did nothing**. Confirmed live: HA 128 now drives it to 75. |
| **Energy meter classification (PS-21)** | Your 7 meters (solar, grid in/out, car charger, pool) now report `actual` as power/measurement and `total` as energy/total_increasing. Upstream put resetting counters into the energy dashboard as `total_increasing`, which shows resets as **spikes**. |
| **Log noise (#514)** | A normal session now produces **zero WARNING or ERROR** records. Upstream logged expected token expiry and clean disconnects as ERROR with full tracebacks. |
| **Reconnect behaviour (#475, #486, #491)** | A dropped connection now reconnects in place with backoff. Upstream reloaded the whole integration, which destroyed and recreated every entity — the cause of entities flicking to a wrong state after a Miniserver restart. |
| **New entities on your setup** | Your `NfcCodeTouch` and two `LightsceneRGB` controls produced **nothing** on upstream. They now produce entities. |

---

## Security and privacy fixes

These are the ones that would matter if you ever paste a diagnostics dump into a GitHub
issue, or if something on your network could reach the Miniserver.

- **`eval()` removed** from the light platform. Upstream called `eval()` on six strings
  received over the websocket — arbitrary code execution in the Home Assistant process
  from anything that could spoof or compromise the Miniserver.
- **Bearer token no longer broadcast.** Upstream fired the Loxone auth token, the user
  salt and the visual-password salt onto the Home Assistant event bus, readable by any
  automation, and logged the token at ERROR level in the refresh path.
- **Diagnostics redacted.** Upstream dumped the entire `LoxAPP3.json` unredacted,
  including your serial, project name and cloud DNS URL — into a file people routinely
  attach to public issues.
- **NFC credentials never exposed.** The new NFC support publishes *who* authenticated
  and *when*, never `lastcode` or `lasttag`.
- **`sys.exit()` removed** from the entity constructor. One malformed control could
  terminate your entire Home Assistant process.

---

## Upstream issues addressed

18 referenced by commit: #292, #398, #402, #413, #416, #457, #461, #466, #475, #479,
#481, #486, #491, #492, #501, #506, #514, #515.

Notable ones: #413 (alarm PIN pad showed a text field), #501 (Window without
`targetPosition`), #506 (non-ASCII usernames), #512-adjacent RGB brightness, and #515
(message centre as repairs, ported with credit to the original author).

---

## New control types supported

Nine types that upstream ignores entirely:

`InfoOnlyText`, `UpDownDigital`, `Tracker`, `EnergyManager`, `EnergyManager2`,
`PowerUnit`, `Wallbox`, `IntercomV2`, `NfcCodeTouch`, `LightsceneRGB`

Plus a `text` platform that upstream shipped but never loaded (`Platform.TEXT` was
missing from its platform list, so `text.py` was dead code), and richer
`PresenceDetector` support (illuminance and noise sub-sensors).

Platform count: 13 → 14.

---

## Entity IDs: unchanged

The fork adopts `has_entity_name`, which normally renames entities. It does not here.
Verified by dumping the entity registry before and after:

```
master: 38 entities | fork: 38 entities
unique_ids removed: NONE   added: NONE
ENTITY_ID RENAMES:  0      stored-name changes: 0
```

Your automations and dashboards keep working. What changes is the *display* name — the
duplicated "Kitchen Temp Kitchen Temp" labels are gone.

Caveat: that was measured against the test fixture and your control set. A house with
control types neither of us has could differ.

---

## Manifest differences

| Field | Upstream | Fork |
|---|---|---|
| `iot_class` | `local_polling` | `local_push` (it is a websocket subscriber) |
| `dependencies` | `[]` | `["group"]` (it imports the group component) |
| `requirements` | `websockets>=14`, `pycryptodome`, `httpx` | `websockets>=14,<16`, `pycryptodome>=3.20` (`httpx` was never imported) |
| `integration_type` | absent | `hub` |
| `loggers` | absent | declared, so HA's debug-logging button covers the protocol layer |

---

## How to install

Your fork is already on GitHub with everything pushed.

**Via HACS (recommended):**
1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/alxzndr/PyLoxone`, category *Integration*
3. Install it — it will replace the upstream copy
4. Restart Home Assistant

**Manually:** copy `custom_components/loxone/` over your existing one and restart.

**Before you do either: take a full backup.** See the one-way door section.

After restarting, check Settings → Devices & Services → PyLoxone loads without error, then
confirm your energy dashboard still shows sensible figures and a light dims across its
full range.

---

## What is still unverified

Five items in `LIVE-MINISERVER-CHECKS.md` could not be settled, four because your house
lacks the hardware:

| Item | Why it is open |
|---|---|
| Alarm arm-home/away parameter | No Alarm block here. **I suspect it is inverted** — the write path and the state mapping contradict each other. |
| Ventilation mode command | No Ventilation block. |
| Ventilation speed timer | No Ventilation block. |
| Legacy `IRoomController` mode table | You have none; the V1 and V2 tables in the code disagree. |
| `LightsceneRGB` write path | Attempted live; four candidate commands produced no reaction, but both your blocks have an empty `sceneList`, so the test was inconclusive rather than negative. |

None of these affect your installation, because you have none of that hardware. They do
matter before this is proposed upstream.

---

## Honest assessment

**Deploy it.** The failure modes it fixes are ones you are actually exposed to: half your
brightness sliders are inert, your energy dashboard is being fed resetting counters as
monotonic ones, and every reconnect churns your entities.

The risks are the one-way migration, which a backup covers, and that 705 passing tests
prove internal consistency rather than agreement with the Loxone protocol. The parts of
that protocol your house exercises have been checked against your actual hardware. The
parts it does not exercise have not been checked by anyone.
