# WP-6.2 — Message Center → repairs (#515)

Branch: `wp/WP-6.2` (from `origin/master`). Prerequisite **WP-5.3 is
present on master** (commit `862f688`, "widen the blocking rule set and
sweep dead code").

Ports the approach from **JoDehli/PyLoxone#515 by @mpcaddy**,
re-implemented against the post-WP-3.2 / WP-5.x architecture. **No
catalogue finding IDs apply to this package** (the plan lists none — pure
feature work).

## Scope, per the plan

The Miniserver's Message Center entry list (top-level `messageCenter`
block of the structure file; entries synced with a `getEntries` command on
the control's `uuidAction`) is now:

1. **a diagnostic sensor per message-center control** — state is the
   highest *active* severity class (0 when none), `status` attribute the
   per-severity counts; the sensor lands on the control's own device,
   linked to the Miniserver host device; and
2. **HA repair issues** — one persistent, non-fixable issue per active
   entry (issue id `message_center_<entry_id>_<entryUuid>`):
   - severity mapping (upstream, verbatim): Miniserver severity `> 3` →
     HA `critical`, `> 2` → `error`, everything else (`1` warning, `0` /
     absent) → `warning`;
   - `translation_key` = `loxone_device_status` when at least one affected
     uuid resolves to an entity of this entry (title `{title}: {name}`,
     `more-info-entity-id` link in the description), `loxone_status`
     otherwise (`{title}` / `{description}`);
   - `learn_more_url` = the entry's `helpLink`;
   - resolved (historic) entries delete their issue; entries that simply
     disappear from the authoritative list are stale-cleaned on the next
     sync;
3. **a `Notifications` diagnostic text sensor** on the Miniserver host
   device for `globalStates.notifications` (also part of #515).

## Re-implementation vs the upstream PR (per the WP-6.2 instructions)

- **No global listener, no `hass.data[DOMAIN]`**: the upstream PR read one
  global bus event. Here the sensor subscribes to (a) the standard
  per-uuid entry signal for its `uuidAction` + `states.changed` stream
  (the normal `LoxoneEntity` mechanism, CORE-27), and (b) a new
  **entry-scoped full-message signal** `loxone_message_<entry_id>` — the
  `getEntries` reply arrives as `{"control": {...}, "value": "<json>"}`
  which the per-uuid fan-out cannot express (its keys are not stream
  uuids). The coordinator already fans out one dispatcher signal per
  message; the new signal is additive and entry-scoped. Nothing is added
  to the public `loxone_event` bus event, so user automations are
  unchanged.
- **`hass.data[DOMAIN]` → `entry.runtime_data`** (WP-5.2): the sensor
  resolves the live platform config entry (`platform.config_entry`, with
  the construction-time reference as fallback, same rule as
  `LoxoneEntity._connection_coordinator`) and namespaces every issue id
  with `entry.entry_id`, so two Miniservers on one HA instance never
  share an issue.
- **`has_entity_name` + translation keys** (WP-5.1): the message-center
  sensor is a primary entity (stored name `null` — the device named
  "Message Center" is the display name); the notifications sensor carries
  the short name "Notifications" on the host device.
- **Device linkage via `device_info_for`** (WP-3.3): the sensor builds a
  fresh `DeviceInfo` with the control's `uuidAction` as identifier and
  `via_device` to the Miniserver host device; no `helpers.device_registry`
  cache.
- **Upstream entity classes, minus the defects**: the PR's
  `LoxoneNotificationsSensor` stored a fixed `unique_id` that collided
  across installs and called `schedule_update_ha_state` from a callback;
  neither is ported (unique id is the stream uuid, `async_write_ha_state`
  is used).

## What changed, by file

- `custom_components/loxone/sensor.py`
  - `LoxoneMessageCenterSensor(LoxoneEntity, SensorEntity)`: one per
    top-level `messageCenter` control. On the first, and on every strictly
    newer, value of the `changed` stream it sends `getEntries/2` through
    `self._send` (this entry's own coordinator, CORE-27 outbound); identical
    / older values are deduped; corrupt values are ignored. When a full
    message's `control` field acks a `getEntries`, its `value` JSON is
    parsed and the repair issues + state are reconciled in the same task
    (tracked on the config entry). `native_value` = max active severity;
    `extra_state_attributes.status` = `{str(severity): count}`.
  - `LoxoneNotificationsSensor(LoxoneEntity, SensorEntity)`: mirrors the
    `globalStates.notifications` text stream on the Miniserver host device.
  - Pure helpers (tested with hand-derived literals):
    `message_center_summary(entries)` → `({str(severity): count}, max)`,
    skipping historic entries and corrupt entries;
    `message_center_issue_severity(n)` (the upstream `>3`/`>2`/else
    mapping); `message_center_entry_timestamp(list)` (entry `timestamps`
    are Unix epoch seconds, verbatim upstream — VERIFY below);
    `_is_get_entries_response(control)` (accepts a dict of uuid →
    command-list *or* a raw command string, requires the `getEntries`
    token); `message_center_issue_id(entry_id, entry_uuid)`;
    `_as_int_severity(v)` (non-numeric junk → 0).
  - Every `states[...]` / structure-file lookup uses guarded `.get()` +
    `isinstance` (a malformed block creates no entity and cannot abort
    the platform).
- `custom_components/loxone/coordinator.py` — emits
  `loxone_message_<entry_id>` once per received message alongside the
  existing per-uuid signals (semantic 2 lines + import).
- `custom_components/loxone/const.py` — `loxone_message_signal(entry_id)`.
- `custom_components/loxone/helpers.py` — `LOXONE_EPOCH_SECONDS = 1230768000`
  (2009-01-01T00:00:00Z in Unix seconds, hand-derived — and deliberately
  *not* parsed from a string, since `dt_util.parse_datetime` leaves the
  datetime naive and `.timestamp()` would then shift with the host
  timezone) and `loxone_timestamp(ms) → datetime | None`.
- `custom_components/loxone/translations/{en,de,cs}.json` — `issues`
  blocks `loxone_status` and `loxone_device_status` in all three shipped
  languages (placeholder-only, exactly as upstream; the
  `test_de_translations_superset_of_en` contract passes).
- `tests/fixtures/LoxAPP3.json` — new top-level blocks
  `globalStates.notifications` and `messageCenter.MessageCenter` (one
  control: uuidAction, `states.changed`, name "Message Center"). The
  block *names* follow the upstream PR's reads of the structure file
  (VERIFY item 4 below).
- `tests/test_message_center.py` (new, 17 tests)
  - **pure-helper tests, hand-derived literals**: `loxone_timestamp(0) ==
    2009-01-01T00:00:00Z`; `86400000 == 2009-01-02T00:00:00Z`;
    `486825600000 == 2024-06-05T13:20:00Z` (486 825 600 s = 5634 d + 13 h
    20 m on top of the epoch); `1700000000 == 2023-11-14T22:13:20Z`.
    Severity mapping `1/2 → warning, 3 → error, 4 → critical, 5 →
    critical`. Summary counts skip historic + corrupt entries.
    `getEntries` ack truth table. Issue-id namespacing.
  - **setup from the fixture** (`mock_connection`): `sensor.message_center`
    at unique id = the control's `uuidAction`, diagnostic category, state
    `0`, `status == {}`, on the "Message Center" device;
    `sensor.pyloxone_test_miniserver_notifications` on the "PyLoxone Test
    Miniserver" device.
  - **state update + issues**: a `changed` feed sends exactly one
    `getEntries/2` to the control uuid; same value → deduped; older value
    → no send; junk value → no crash, no send; newer value → sends again.
    The response with two active entries (3 = device-linked, 2 = unlinked)
    + one historic produces state `3`, `status {"3": 1, "2": 1}`, an ERROR
    issue with the device translation key, `more-info-entity-id` link for
    the affected switch, `learn_more_url` and the hand-computed
    "occurred at" line — and a WARNING issue with the status key; the
    historic entry creates no issue. A follow-up list where one entry is
    historic and the other disappears removes *both* issues and returns
    the state to `0`. An unrelated command ack does not touch state or
    issues; a malformed `value` keeps the previous state and issues.
  - **guard**: a structure file without the blocks sets up fine and
    creates no message-center entities (no KeyError).
- `tests/snapshots/entity_names.json` — regenerated with
  `LOXONE_ENTITY_SNAPSHOT_OUT`; the diff was hand-reviewed: exactly the
  two new sensor rows and nothing else.

## Evidence: tests fail before, pass after

Pre-change (integration source stashed to its pre-WP state, test file
left in place):

```
$ pytest -q tests/test_message_center.py --no-cov
ERROR tests/test_message_center.py
ImportError: cannot import name 'loxone_timestamp'
    from 'custom_components.loxone.helpers'
!!! Interrupted: 1 error during collection !!!
1 error in 0.18s
```

Post-change:

```
$ pytest -q tests/test_message_center.py
17 passed in 1.02s
```

## Gate results (repo root)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
73 files already formatted

$ pytest -q
557 passed, 1 deselected in 68.29s (0:01:08)
   (baseline before this WP: 540 passed — the +17 are the package's new
    tests; existing 540 all still pass, test_contracts.py included)

$ grep -rn "TODO(WP-6.2)" custom_components/
(no matches)
```

No `# noqa` added. The ignored
`docs/review/2026-09-remediation-plan-PyLoxone.md` was not consulted; no
fabricated finding-ID prefix appears in this package's scope (WP-6.2
references the upstream issue #515, not a catalogue finding).

## VERIFY / live-Miniserver checks required before merge

No catalogue entry for WP-6.2 is marked VERIFY, but these assumptions are
carried over from the upstream PR and must be confirmed on a real
Miniserver before relying on the feature:

1. **`timestamps` unit**: the upstream code reads entry
   `timestamps[0]` as **Unix epoch seconds** while the `changed` counter
   uses the Loxone epoch in *milliseconds*. Confirm the two units really
   differ (if `timestamps` are also Loxone-epoch ms, the "occurred at"
   line and nothing else is affected).
2. **`getEntries/2`**: the `/2` suffix is ported verbatim from upstream
   (it may be a server-side version/version-2 marker or a "start index").
   Confirm the command value on a real device and that `2` is correct for
   the first sync after an HA restart.
3. **`globalStates.notifications` shape**: assumed to be a stream uuid
   string (as every other structure-file state). Confirm the key exists
   and its type on real firmware; if absent, the code simply creates no
   sensor (guarded).
4. **Top-level block names**: the fixture uses `messageCenter` (map of
   named controls) and `globalStates` exactly as the upstream PR reads
   them. Confirm the current LoxAPP3.json layout (older firmwares may not
   ship a `messageCenter` block at all — the code handles that).
5. **Message-center `state`**: the PR's `LoxoneMessageCenterSensor` stored
   the raw entry list as `_active_entries` but never used it as state (its
   state came from `process_entries`). This port follows the refined
   behaviour (`native_value` = max severity, `status` attribute with the
   per-severity counts) — validate the UI presentation on a server that
   actually has entries.

## Follow-ups (out of scope, not fixed here)

- **README/Docs**: the three new capabilities (sensor(s) per control,
  notifications sensor, repair issues) are not documented yet (WP-5.4
  territory).
- **Multi-Miniserver fixture**: a second, independently-running
  message center in `LoxAPP3.json` (or a second fixture file) would let a
  test assert the `entry_id` namespacing end-to-end; the helper is
  unit-tested instead.
- **Localized issue titles**: both translation keys are
  placeholder-only (`{title}` / `{description}`), so issue titles render
  in the Miniserver's language. A later i18n pass could map well-known
  severities to localized keys.
- **"Mark as read" action**: no fixable repair action exists upstream
  (entries can only be resolved on the Miniserver side). Offering a
  `getEntries`-side dismiss from HA would need a new outbound protocol
  question — a product decision, not a port.
