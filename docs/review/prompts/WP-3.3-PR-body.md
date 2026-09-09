## WP-3.3 - Device registry and identity

Part of #28 (review remediation). Builds on `fix/wp-2.1-connection-quick-wins` (the integration base with #491 merged).
Finding IDs: **CORE-16, CORE-20, PC-04, PC-05, CORE-17, CORE-15, PS-14 (device-type literals), PS-10/PC-35, PS-20, PS-17 (scene device link; via_device deferred - see Below)**.

## What ships

**One `device_info_for(config_entry, uuid, name, model, room)` helper** in `helpers.py` builds every in-scope platform's `device_info` as a **fresh dict per call**. The module-level `device_registry` cache is deleted (CORE-20), so two setup passes over identical controls can no longer share and cross-contaminate a mutable payload; `get_or_create_device` remains as a compat shim (now raising on an empty uuid, PC-05) for platforms out of scope here. The helper also renders `configuration_url` from the entry options.

**Fan device identity (PC-04).** `fan.py` now constructs the `LoxoneVentilation` fan **before** its presence / indoor-humidity / outdoor-temperature sub sensors and passes the fan's device payload to them; the sub sensors no longer overwrite `uuidAction` back to the parent control. Previously the shared device got renamed after a *presence-detection* derivative of the control ("Ventilation 2 - Presence"), with a leftover `"Fan"` string model attached; the device is now named after the ventilation unit (Ventilation 2), model `Ventilation` (PS-14 constant, not a local literal), room wired to `suggested_area`. The same leak pattern in `colorpickers.py` (a picker sub-control renaming the shared LCV2 device to its own "room type") is fixed: the picker's device name is always the light controller's, and LCV2 sub-controls in `light.py` share the controller's fixed payload (PC-04 extension, mirroring the fan change).

**Miniserver host device + real software version (CORE-16 / PS-20).** `MiniServer.async_update_device_registry()` registers the Miniserver device (`Miniserver <serial>`, model from the structure, `configuration_url` from options, and **no synthetic (mac, hostname) connection** - the fixture's "mac" is not a hardware address, CORE-20) and is now called at setup time. `software_version_string()` generalises both the list form (`["7","1","0","28"]` -> `7.1.0.28`) and the string form (`7.1.0` -> `7.1.0`), which the version sensor and the device's `sw_version` use - the version sensor lands on the Miniserver device, becomes `entity_category: diagnostic`, and finally reports the real version instead of the literal `"unknown"` (PS-20). The keep-alive version sensor is likewise diagnostic and disabled by default (PS-20).

**Loxone auto-groups (CORE-15 + #486-follow-up).** `create_loxone_groups` is gated on the new `generate_groups` option (absent key keeps the historical groups-on behaviour for existing installs; the config flow stamps `generate_groups: false` on new installs, options flow preserves stored values). Group discovery matches the entities' `device_type` state attribute against the PS-14 constants instead of the dead table literals, and group creation now **converges on the entry's live entities**: a user-deleted group is recreated with its current members, surviving group entities get their tracked list updated, and the master group is assembled from **all non-empty subgroups** - including dimmers, climates and AC controllers, which the old master group silently dropped.

**Dead dispatcher wiring removed (CORE-17).** `MiniServer.listeners`, the `NEW_*` constants and `async_signal_new_device` are gone; the scene platform's mood-list bus subscription moves to `config_entry.async_on_unload(...)` (it was appended to the dead listener list and leaked across unloads). Config entries are stamped with `unique_id = serial` at setup (reload-loop idempotency). PS-14 device-type literals are centralised in `const.py`; `cover.py` adopts them. Dead `device_class` properties on `LoxoneVentilation` and the text sensor are deleted (PS-10/PC-35).

**Structure file integrity (found via testing).** `get_all` now returns deep copies: the structure file is cached per Miniserver and shared across platforms, and several platforms mutate their control in place (type rewrites, room resolution, runtime references, Intercom sub-control name joins), which previously corrupted the cached file on any later setup in the process - e.g. the fan platform silently setting up zero entities on a second setup. Room/category name resolution is also idempotent, so platforms that resolve the shared structure file more than once no longer blank out rooms (this is what restored `suggested_area` on later platforms such as lights).

## Evidence

- `pytest -q`: **465 passed, 1 deselected** (baseline: 437 passed, 1 xfailed, 1 deselected; the contract xfail now passes)
- `ruff check` on touched files: same violation set as the base commit (no new findings)
- `ruff format --check`: same 8 pre-existing unformatted files as base, no new drift
- New `tests/test_devices.py` (27 tests): PS-14 constant table, CORE-20 no-module-cache, CORE-16 both version shapes, CORE-15 groups + dimmer-in-master, PC-04 fan parent-first, PC-05 empty-uuid raise, PS-10/PS-20 categorisation, CORE-17 dead-wiring absence, entry `unique_id` stamp, groups off-option, and a full **device registry snapshot after setup** (names/models/areas for Miniserver, Ventilation, LCV2, Dimmer devices)
- Fixture `LoxAPP3.json`: the standalone dimmer gains `min`/`max`/`position` state UUIDs so it becomes available once streamed (the dimmer-group acceptance target); scene object ids pick up the device's area prefix on this HA version (test updated, rationale commented); group `entity_id` assertions compare sets

## Deferred / follow-ups

- **`via_device` is NOT set** by `device_info_for`: the out-of-scope platforms (switch, number, button, text, select, media_player, alarm, lights/dimmer, lights/switch) still build device payloads through the compat shim without the Miniserver host device being guaranteed, and HA 2026.x warns and drops payloads that reference a non-existing via device. Miniserver host-device via-linking lands as a follow-up once those platforms migrate to `device_info_for`.
- The `get_or_create_device` compat shim can be deleted after the platforms above migrate.
- Cross-entry service scoping is already in the integration base (#47/#486/#491 merged); this branch does not duplicate it.
- This branch touches no file outside the WP-3.3 scope list plus the fixture and the three test files; the `test_lights.py` behavioural notes from the plan concern the parallel light WPs, not this PR.

## Hold criteria (all HOLD)

| # | criterion | observed | verdict |
|---|-----------|----------|---------|
| 1 | `pytest -q` >= baseline (437 passed / 1 xfail) | **465 passed, 1 deselected** - full suite green including cross-file test ordering (structure-file mutation fixed) | HOLD |
| 2 | no new ruff violations on touched files | violation-set parity with the base commit; format check: same 8 pre-existing unformatted files, no new drift | HOLD |
| 3 | device snapshot: Miniserver device present; fan = Ventilation 2 / Ventilation / Kitchen with sensor subs; LCV2 / Dimmer under parent payloads; via_device = none anywhere (deferred) | `tests/test_devices.py::test_device_registry_snapshot_after_setup` passes; 27/27 in the file | HOLD |
| 4 | new-install steady state (no groups with `generate_groups` default off) | `test_groups_skipped_when_option_is_off` passes; master anchor absent | HOLD |

Notes:
- `via_device` deliberately not shipped (see Deferred); host-device via-linking
  is the stated follow-up.
- Scene entity object ids pick up the device's suggested-area prefix on this
  HA version because LCV2 rooms now resolve consistently (test updated,
  rationale commented in `tests/test_simple_platforms.py`).
- The commit subject of the fix commit is the mandated
  `fix(deprecation): device registry and identity (WP-3.3)`; the commit body
  was mangled by a heredoc encoding glitch in this run - this document is the
  authoritative per-area breakdown, and the PR description reproduces it.
