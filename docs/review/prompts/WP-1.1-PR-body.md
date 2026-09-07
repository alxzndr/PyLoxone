# WP-1.1: `LoxoneEntity` base-class hardening

**Branch:** `fix/wp-1.1-loxone-entity` (from `master` @ WP-0.3 head)
**Findings:** CORE-01, CORE-02, CORE-32, PS-12, PS-22 (CORE-26 — see below).

## The headline fix (CORE-01)

The old `LoxoneEntity` stored the bus-subscription id in `self.listener` but
its `async_will_remove_from_hass` did `self.listener = None` **without calling
the unsubscriber** — a real leak. Every in-place `reload` kept the prior
listeners alive. Verified before/after against the WP-0.2 fixture:

```
                 loxone_event listeners after each of 3 reloads
  master (buggy): baseline=33 -> 58 -> 83 -> 108   (+25 every cycle, unbounded)
  this WP:        baseline=33 -> 27 -> 27 -> 27     (steady; no growth)
```

The fix registers the listener through `async_on_remove(...)` so HA detaches
it on entity removal, and the dead `async_will_remove_from_hass` +
`self.listener` field are deleted. Entity count is unchanged (35 on both
bases), so behaviour is identical.

## Other changes

- **CORE-02 / PS-17**: `LoxoneEntity.__init__`'s `except` no longer
  `sys.exit(-1)` (which *killed the whole HA process* on any read-only kwarg
  `setattr` failure). It now `_LOGGER.exception`s and continues; the
  `"Could set …"` message is corrected to `"Could not set %s=%r for %s"`.
- **CORE-32**: deleted the dead `LoxoneEntity._clean_unit` (a verbatim
  duplicate of `helpers.clean_unit`, zero call-sites). `_get_format` is
  *kept* — it IS used by `sensor.py:466`, `fan.py:149`, `binary_sensor.py:114`;
  deleting it was out of this package's file list.
- **PS-12 / PS-22**: `_attr_should_poll = False` on the base (so every
  subclass inherits it) and per-class `@property should_poll` overrides
  deleted from `switch.py`(×2), `number.py`, `text.py`, `select.py`,
  `cover.py`(×2). `_unrecorded_attributes = frozenset({"uuid",
  "platform", "room", "category", "state_uuid", "device_type"})` added to the
  base to keep the recorder from logging these high-churn / low-signal attrs.

## Decision: CORE-26 (`cached_property` removal) deferred

The plan called for replacing the `name`/`unique_id` `@cached_property`
overrides with `_attr_name`/`_attr_unique_id` set in `__init__`. I tried it:
removing the `name` `cached_property` changed the ctor's `setattr(self,
"name", …)` path (HA's `Entity.name` is a read-only property, so the loop's
else-branch now just logs instead of shadowing into the instance dict), broke
`LoxoneLightSwitch` (`no attribute __attr_name`), and dropped entity count 35 →
~22 (see the CLOSED diff trail). Reverting it (keep the two cached_properties +
`self._attr_name` handling in `__init__`) restores 35/35 with identical
behaviour — which is exactly what PC-04 ("keep behaviour identical") requires.
So CORE-26 is **deliberately deferred** to a follow-up that can land with its
own subclass propagation audit (every `self.name = …` / `self.unique_id`
read-sites), not silently here. This choice is recorded in
`docs/review/prompts/DECISIONS.md`.

## Acceptance tests (`tests/test_loxone_entity.py`)

1. `test_in_place_reload_does_not_leak_bus_listeners` — reloads the fixture
   entry three times and asserts `loxone_event` listener count is *steady*
   (no per-reload growth). On master this fails (33→58→83→108); on this
   branch it passes (33→27→27→27).
2. `test_readonly_kwarg_does_not_sys_exit` +
   `test_could_not_set_is_logged_not_fatal` — a subclass with a no-setter
   property constructs without a `SystemExit`; the ctor logs `"Could not
   set"` instead (LOW-02 / CORE-02).
3. `test_entity_count_and_unique_ids_stable` — the WP-0.2 fixture set-up makes
   a stable entity set (35 in isolation; lower-bound 25 to absorb the two
   `ColorPickerV2` "Not implemented" noise + the `fan.kitchen_ventilation_
   presence` unique-id collision, both pre-existing findings); all unique_ids
   non-empty.

## Three green commands (verbatim)

### `ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006`
```
All checks passed!
```

### `ruff format --check .`
```
50 files already formatted
```

### `python -m pytest -q`
```
100 passed, 1 deselected, 3 xfailed, 2 warnings
```

(The 3 remaining xfails are owned by WP-1.7 / WP-1.8 / WP-2.1.)

## Follow-ups
- CORE-26 cached_property removal (deferred; see Decision above).
- `fan.kitchen_ventilation_presence` unique-id collision (pre-existing; a
  fan-presence identity fix — land with WP-3.3 device-identity).
