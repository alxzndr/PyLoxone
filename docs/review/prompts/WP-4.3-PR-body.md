# WP-4.3 Lights — PR body

## What changed (by file)

- **`custom_components/loxone/helpers.py`**
  - New `lox_to_hass_range(lox_val, min_v, max_v)` / `hass_to_lox_range(hass_level, min_v, max_v)`: real bidirectional `map_range`-based mapping between a Loxone value in `[min_v, max_v]` and HA brightness 0-255 (PC-17), with a `max(1, round(...))` floor on any non-zero result (PC-18).
  - Deleted dead code: `lox2lox_mapped`, the mired converters `to_hass_color_temp`/`to_loxone_color_temp` and their commented `np.interp` block (PC-40, confirmed unused).
- **`custom_components/loxone/lights/colorpickers.py`**
  - `RGBColorPicker.async_turn_on` now always emits exactly one command via the pure `plan_turn_on(...)`: explicit kwargs → current colour mode with a *known* companion value → `setBrightness`. A brightness-only `turn_on` with unknown colour mode now sends `setBrightness/…` instead of nothing (PC-03, PR #512 regression).
  - `TunableWhiteLight.async_turn_on` no longer raises (or emits `temp(50.0, None)`) before the first state: brightness defaults to 255 and kelvin to `DEFAULT_TURN_ON_KELVIN = 4000` *before* formatting, via the pure `plan_temp_turn_on(...)` (PC-11).
  - Standalone pickers' device identifier is now `self.unique_id` — a string — instead of `lightcontroller_id` (`None`) (PC-05).
  - `async_turn_off(self, **kwargs)` (PC-38); `_attr_min_color_temp_kelvin` 2000 → 2700, the documented Loxone floor (PC-39); dead `__color_mode_reported`, `STATE_UNKNOWN` initial state lines removed (PC-40 lines; the attribute no longer exists in the HA 2026 light component).
- **`custom_components/loxone/lights/dimmer.py`**
  - `turn_on` and `event_handler` use the min/max-rescaled writers/readers (`_hass_to_master` / `lox_to_hass_range`); the slider no longer reads back below full on full-brightness commands (PC-17); brightness 1 can no longer become 0 (off) on write, reads are rounded (PC-18). Min/max/position payloads are coerced to `float` and non-numeric values no longer corrupt state.
- **`custom_components/loxone/lights/lightcontroller.py`**
  - Magic values are named: `OFF_MOOD_ID = "0"`, `ON_MOOD_ID = "99"`, `ALL_OFF_ACTIVE_MOODS = [778]` (PC-33).
  - `turn_on`: effect + brightness are both sent (the old `elif` chain dropped the brightness); a brightness-only call without a master dimmer now sends `on` instead of nothing; a bare call while off still sends `changeTo/99` (PC-33). Master-dimmer writes honour min/max and share their scaling with `LoxoneDimmer` (PC-17/18).
  - Removed the fake `device_class` property (PS-10 — the type is already surfaced via `extra_state_attributes["device_type"]`), the dead `_attr_state` lines, `self.kwargs`/`self._uuid_dict`, and the unused `mood_list_uuid` property (PC-40 lines).
- **`custom_components/loxone/light.py`**
  - `masterColor` subControl filter: `find(...) > 1` → `> -1` (PC-37).
  - Standalone `ColorPickerV2` controls are now created via `get_all(loxconfig, "ColorPickerV2")` (PC-43, light half), resolved through `PICKER_TYPE_TO_CLASS` / `picker_class_for(...)` which accepts both the integer and the string `pickerType` representations (and fixes the old `if picker_type:` check that skipped `0` = RGB).
- **`custom_components/loxone/scene.py`**
  - Scene generation now keys off the explicit `device_type` state attribute (`att.get("device_type") != "LightControllerV2"`) instead of the removed `entity.device_class` marker (PS-10).
- **`tests/test_lights.py`** (new, 52 tests): range-mapping literals, dimmer/LCV2 command payloads, picker plan functions, turn-on/off kwargs acceptance, kelvin floor, standalone device identifiers, and a full HA-harness setup test (WP-0.2 fixtures) asserting the standalone picker entity + string device identifier and the `masterColor` filter.
- **`CHANGELOG.md`**: entry under *Unreleased → Fixed*.

## Gate output (repo root)

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
59 files already formatted

$ pytest -q
313 passed, 1 deselected, 1 xfailed, 2 warnings in 40.02s
```

(The `1 deselected` / `1 xfailed` predate this WP — identical counts to the
pre-WP baseline of 261 passed.)

## Acceptance

| Criterion | Where pinned |
|---|---|
| brightness-only `turn_on`, RGB picker, unknown colour mode → `setBrightness/…` (#512) | `TestRgbColorPicker::test_brightness_only_with_unknown_color_mode_emits_set_brightness`, `TestPlanTurnOn::test_unknown_mode_brightness_only_falls_through_to_set_brightness` |
| `(90, 10, 90) → 255` and `255 → 90` | `TestRangeMapping::test_full_span_maps_to_255`, `test_write_full_scale_maps_to_maximum` (+ dimmer/LCV2 level: `90 → 255`, `255 → 90`) |
| brightness 1 never maps to 0 | `test_write_min_bright_full_span_never_zero`, `test_write_min_bright_with_minimum_not_zero`, `TestDimmer::test_turn_on_unmapped_write_never_zero` |
| TunableWhite `turn_on` before any state does not raise | `TestTunableWhite::test_turn_on_before_any_state_does_not_raise`, `test_plan_temp_turn_on_defaults` |
| Standalone picker gets a device with a string identifier | `test_standalone_picker_and_master_color_filter` (harness), plus per-class identifier tests |

Pre-fix behaviour: the new file fails to import against the old code
(the helpers/plans did not exist), and the old command paths were
manually exercised (old RGB `turn_on` with unknown colour mode fired
nothing; `round(hass_to_lox(1)) == 0`; `lox2hass_mapped(90, 10, 90) ==
229.5`).

## VERIFY items (live-Miniserver check required before merge)

1. **`PICKER_TYPE_TO_CLASS` integer mapping** (`light.py`): `0 = RGB`,
   `1 = kelvin`, `2 = kelvin full-range`. The logic is behind the named
   helper `picker_class_for()` and pinned to these *intended* semantics;
   confirm the live `details.pickerType` values (especially `2`, and the
   LumiTech representation) on a real Miniserver.
2. **`DEFAULT_TURN_ON_KELVIN = 4000`** (`lights/colorpickers.py`, PC-11):
   the default for a `turn_on` before any reported colour temperature.
   Any value inside the documented 2700-6500 works; sanity-check the
   rendered result on a live light.

## Follow-ups (discovered, out of scope for this WP)

- **Shared-fixture mutation during entry setup**: several platforms
  mutate the session-wide `LoxAPP3.json` control dicts in place (`hass` /
  `config_entry` back-references, `async_add_devices` bound methods,
  resolved `room`/`cat` names). `light.py` does this for its own
  LightControllerV2 dict — I kept the pattern (other platforms need the
  same fix) but it makes the `LoxAPP3` fixture not re-deepcopyable after
  a full-setup test; `tests/test_lights.py` scrubs to JSON-safe values
  before deep-copying. A WP-touching-fixture or conftest change belongs
  in a separate package.
- **PC-40 `dimmer.py:129-142` "duplicate device-info block"**: the only
  remaining EIBDimmer block now differs in type label ("EIBDimmer" vs
  "Dimmer") and re-registers the device under that label; treated as
  intentional, not dead. Flagging in case the catalogue points at
  something else there.
- Other PC-40 lines (cover/alarm/fan/media_player) belong to WP-4.1/4.2/4.4.
- The catalogue's remaining PC-43 gaps (AudioZoneV2, alarm arming,
  Jalousie, Gate, Window, IRoomControllerV2, Ventilation, AcControl) are
  out of this WP's file scope.
