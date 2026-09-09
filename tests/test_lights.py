"""WP-4.3 regression tests: light platform (pickers, dimmer, LCV2, setup).

Expected values are hand-derived literals.  E.g. for a dimmer with a
Loxone span of ``[10, 90]``: a server position of ``50`` sits at
``(50 - 10) / (90 - 10) = 1/2`` of the span, so it reads back as
``round(255 / 2) = 128``; writing HA brightness ``255`` maps to the
span maximum ``90``, and HA brightness ``1`` (the bottom of the HA
scale) maps to ``10`` — never ``0`` (off).

Items marked VERIFY in the catalogue are pinned here to their *intended*
semantics behind the named helpers (``PICKER_TYPE_TO_CLASS`` /
``picker_class_for`` for the numeric picker-type mapping); a
live-Miniserver check is required before relying on them (see the PR
body).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.helpers import (
    hass_to_lox,
    hass_to_lox_range,
    lox_to_hass,
    lox_to_hass_range,
)
from custom_components.loxone.lights.colorpickers import (
    RGBColorPicker,
    TunableWhiteLight,
    plan_temp_turn_on,
    plan_turn_on,
)
from custom_components.loxone.lights.dimmer import LoxoneDimmer
from custom_components.loxone.lights.lightcontroller import LoxoneLightControllerV2
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
)

# --------------------------------------------------------------------------- #
# helpers: brightness mapping (PC-17, PC-18)
# --------------------------------------------------------------------------- #


class TestRangeMapping:
    """``lox_to_hass_range``/``hass_to_lox_range`` with a [10, 90] span."""

    def test_full_span_maps_to_255(self):
        # (90, 10, 90) → 255 is the catalogue acceptance literal:
        # map_range(90, 10, 90, 0, 255) = 0 + 80/80 * 255 = 255
        assert lox_to_hass_range(90, 10, 90) == 255

    def test_mid_span(self):
        # map_range(50, 10, 90, 0, 255) = 40/80 * 255 = 127.5 → rounds to 128
        assert lox_to_hass_range(50, 10, 90) == 128

    def test_at_or_below_minimum_is_off(self):
        assert lox_to_hass_range(10, 10, 90) == 0
        assert lox_to_hass_range(5, 10, 90) == 0
        assert lox_to_hass_range(0, 0, 100) == 0
        assert lox_to_hass_range(None, 0, 100) == 0

    def test_just_above_minimum_is_one_not_zero(self):
        # (10.1 - 10)/80 * 255 ≈ 0.32 → round 0 → floors to 1 (PC-18)
        assert lox_to_hass_range(10.1, 10, 90) == 1

    def test_above_maximum_clamps_to_255(self):
        # map_range(101, 0, 100, 0, 255) = 257.55 → clamped to 255
        assert lox_to_hass_range(101, 0, 100) == 255

    def test_degenerate_span_at_the_edge_still_bright(self):
        assert lox_to_hass_range(10, 10, 10) == 255

    def test_write_full_scale_maps_to_maximum(self):
        # 255 → 90 is the catalogue acceptance literal:
        # max(1, round(map_range(255, 1, 255, 10, 90))) = max(1, 90) = 90
        assert hass_to_lox_range(255, 10, 90) == 90

    def test_write_mid_span(self):
        # map_range(128, 1, 255, 10, 90) = 10 + 127/254 * 80 = 10 + 40 = 50
        assert hass_to_lox_range(128, 10, 90) == 50

    def test_write_min_bright_with_minimum_not_zero(self):
        # map_range(1, 1, 255, 10, 90) = 10 + 0/254 * 80 = 10
        assert hass_to_lox_range(1, 10, 90) == 10

    def test_write_min_bright_full_span_never_zero(self):
        # map_range(1, 1, 255, 0, 100) = 0 → floors to 1 (PC-18: HA
        # brightness 1 must not become Loxone 0 = off)
        assert hass_to_lox_range(1, 0, 100) == 1

    def test_write_full_span_top(self):
        assert hass_to_lox_range(255, 0, 100) == 100
        # map_range(128, 1, 255, 0, 100) = 127/254 * 100 ≈ 50.39 → 50
        assert hass_to_lox_range(128, 0, 100) == 50

    def test_write_zero_is_off(self):
        assert hass_to_lox_range(0, 10, 90) == 0
        assert hass_to_lox_range(None, 10, 90) == 0

    def test_degenerate_span(self):
        assert hass_to_lox_range(128, 10, 10) == 10

    def test_plain_full_scale_helpers_unchanged(self):
        # The un-mapped helpers stay pure 0-255 ↔ 0-100 percent.
        assert hass_to_lox(255) == 100.0
        assert lox_to_hass(100) == 255.0
        # hand: 1 * 100 / 255 = 0.39… (a caller must floor / round, not assume)
        assert hass_to_lox(1) == pytest.approx(100 / 255)


# --------------------------------------------------------------------------- #
# entity plumbing for the stub tests
# --------------------------------------------------------------------------- #


class _Bus:
    def __init__(self):
        self.fired: list[dict] = []

    def async_fire(self, topic: str, data: dict) -> None:
        assert topic == SENDDOMAIN
        self.fired.append(dict(data))


def _stub(cls, **kwargs):
    """Construct an entity without a live HomeAssistant."""
    kwargs.setdefault("room", "R")
    kwargs.setdefault("cat", "C")
    kwargs.setdefault("states", {})
    kwargs.setdefault("isSecured", False)
    kwargs.setdefault("details", {})
    kwargs["async_add_devices"] = lambda *a, **k: None
    entity = cls(hass=object(), **kwargs)
    bus = _Bus()
    hass = SimpleNamespace(bus=bus)
    entity.hass = hass
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    return entity


def _event(data: dict):
    """CORE-27/PS-13: handlers now take the plain ``{uuid: value}`` dict the
    dispatcher delivers; identity for existing call sites."""
    return dict(data)


# --------------------------------------------------------------------------- #
# Dimmer (PC-17, PC-18)
# --------------------------------------------------------------------------- #


async def _feed_dimmer_states(dimmer: LoxoneDimmer, payload: dict) -> None:
    dimmer.event_handler(_event(payload))


class TestDimmer:
    async def test_mapped_read_rescales(self):
        # (pos, min, max) → hass
        dimmer = _stub(
            LoxoneDimmer,
            name="D",
            uuidAction="ctl-dimmer",
            states={"min": "st-min", "max": "st-max", "position": "st-pos"},
        )
        await _feed_dimmer_states(dimmer, {"st-min": "10", "st-max": "90"})
        await _feed_dimmer_states(dimmer, {"st-pos": "90"})
        assert dimmer._attr_brightness == 255
        await _feed_dimmer_states(dimmer, {"st-pos": "50"})
        assert dimmer._attr_brightness == 128
        await _feed_dimmer_states(dimmer, {"st-pos": "9.9"})
        assert dimmer._attr_brightness == 0
        await _feed_dimmer_states(dimmer, {"st-pos": "10.1"})
        assert dimmer._attr_brightness == 1

    async def test_unmapped_read_rounds(self):
        # No min/max states: raw 0-100 % scale (unchanged behaviour).
        dimmer = _stub(LoxoneDimmer, name="D", uuidAction="ctl-dimmer3", states={"position": "st-pos"})
        # hand: 50 * 255 / 100 = 127.5 → rounds to 128
        await _feed_dimmer_states(dimmer, {"st-pos": 50.0})
        assert dimmer._attr_brightness == 128
        # hand: 0.25 * 255 / 100 = 0.6375 → rounds to 1, never 0 (PC-18)
        await _feed_dimmer_states(dimmer, {"st-pos": 0.25})
        assert dimmer._attr_brightness == 1
        await _feed_dimmer_states(dimmer, {"st-pos": "unknown"})
        # non-numeric payloads must not corrupt the last known value
        assert dimmer._attr_brightness == 1

    async def test_turn_on_mapped_write(self):
        dimmer = _stub(LoxoneDimmer, name="D", uuidAction="ctl-dimmer4", states={"min": "st-min", "max": "st-max"})
        await _feed_dimmer_states(dimmer, {"st-min": "10", "st-max": "90"})
        await dimmer.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        assert dimmer.hass.bus.fired[-1] == {"uuid": "ctl-dimmer4", "value": 90}
        await dimmer.async_turn_on(**{ATTR_BRIGHTNESS: 1})
        assert dimmer.hass.bus.fired[-1] == {"uuid": "ctl-dimmer4", "value": 10}
        await dimmer.async_turn_on(**{ATTR_BRIGHTNESS: 128})
        assert dimmer.hass.bus.fired[-1] == {"uuid": "ctl-dimmer4", "value": 50}

    async def test_turn_on_unmapped_write_never_zero(self):
        dimmer = _stub(LoxoneDimmer, name="D", uuidAction="ctl-dimmer5", states={})
        await dimmer.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        assert dimmer.hass.bus.fired[-1] == {"uuid": "ctl-dimmer5", "value": 100}
        # Before the fix this was round(1 * 100 / 255) = 0 → the light went
        # off instead of dimming to the minimum (PC-18).
        await dimmer.async_turn_on(**{ATTR_BRIGHTNESS: 1})
        assert dimmer.hass.bus.fired[-1] == {"uuid": "ctl-dimmer5", "value": 1}

    async def test_turn_on_off_semantics_unchanged(self):
        dimmer = _stub(LoxoneDimmer, name="D", uuidAction="ctl-dimmer6", states={})
        await dimmer.async_turn_on()
        assert dimmer.hass.bus.fired[-1] == {"uuid": "ctl-dimmer6", "value": "On"}
        await dimmer.async_turn_off()
        assert dimmer.hass.bus.fired[-1] == {"uuid": "ctl-dimmer6", "value": "Off"}


# --------------------------------------------------------------------------- #
# RGB color picker (PC-03, PC-05, PC-38, PC-39, PC-40)
# --------------------------------------------------------------------------- #


class TestRgbColorPicker:
    def picker(self, **overrides):
        kwargs = dict(name="Solo RGB", uuidAction="ctl-pick-rgb")
        kwargs.update(overrides)
        return _stub(RGBColorPicker, **kwargs)

    async def test_brightness_only_with_unknown_color_mode_emits_set_brightness(self):
        # PR #512 regression (PC-03): before the fix this service call
        # sent *nothing* — `_attr_color_mode` was UNKNOWN, so neither the HS
        # nor the colour-temp branch matched and the fallthrough was
        # unreachable.  It must always emit exactly one command.
        light = self.picker()
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        value = light.hass.bus.fired[-1]["value"]
        assert value == "setBrightness/100.0"

    async def test_brightness_only_with_known_hs_mode_sends_hsv(self):
        light = self.picker()
        light._attr_color_mode = ColorMode.HS
        light._attr_hs_color = (120.0, 50.0)
        light._attr_brightness = 200
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})
        value = light.hass.bus.fired[-1]["value"]
        # hue/sat are kept; level = 128 * 100 / 255 ≈ 50.196
        assert value.startswith("hsv(120.0, 50.0, ")
        assert float(value.removeprefix("hsv(120.0, 50.0, ").rstrip(")")) == pytest.approx(128 * 100 / 255)

    async def test_brightness_only_with_known_ct_mode_sends_temp(self):
        light = self.picker()
        light._attr_color_mode = ColorMode.COLOR_TEMP
        light._attr_color_temp_kelvin = 3500
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 200})
        value = light.hass.bus.fired[-1]["value"]
        assert value.startswith("temp(")
        _, kelvin = value.rstrip(")").split(", ")
        assert kelvin == "3500"
        assert float(value.removeprefix("temp(").rstrip(")").split(", ")[0]) == pytest.approx(200 * 100 / 255)

    async def test_hs_kwargs_win_over_everything(self):
        light = self.picker()
        light._attr_color_mode = ColorMode.COLOR_TEMP
        light._attr_color_temp_kelvin = 3500
        await light.async_turn_on(**{ATTR_HS_COLOR: (30.0, 80.0), ATTR_BRIGHTNESS: 255})
        assert light.hass.bus.fired[-1]["value"] == "hsv(30.0, 80.0, 100.0)"

    async def test_kelvin_kwargs_win_over_hs_mode(self):
        light = self.picker()
        light._attr_color_mode = ColorMode.HS
        light._attr_hs_color = (30.0, 80.0)
        await light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 2700})
        # brightness unknown before any state → 255 → 100.0 (hand)
        assert light.hass.bus.fired[-1]["value"] == "temp(100.0, 2700)"

    async def test_no_kwargs_emits_default_brightness(self):
        light = self.picker()
        await light.async_turn_on()
        assert light.hass.bus.fired[-1]["value"] == "setBrightness/100.0"

    async def test_turn_off_accepts_kwargs(self):
        # PC-38: `async_turn_off(self)` without **kwargs raised TypeError
        # whenever HA passed service data through.
        light = self.picker(uuidAction="ctl-pick-off")
        await light.async_turn_off(some_service_data=1)
        assert light.hass.bus.fired[-1]["value"] == "setBrightness/0"

    def test_min_kelvin_is_the_documented_loxone_floor(self):
        # PC-39: Loxone's documented range is 2700-6500; the old 2000
        # floor advertised a capability the control does not have.
        assert self.picker()._attr_min_color_temp_kelvin == 2700
        assert self.picker()._attr_max_color_temp_kelvin == 6500

    def test_standalone_device_identifier_is_a_string(self):
        # PC-05: a standalone picker carries a real control uuidAction,
        # not the `lightcontroller_id` (which is `None` there).
        light = self.picker()
        ids = light._attr_device_info["identifiers"]
        assert {("loxone", "ctl-pick-rgb")} == ids

    def test_subcontrol_uses_the_light_controller_device(self):
        light = self.picker(lightcontroller_id="ctl-lcv2", lightcontroller_name="Hall")
        assert light._attr_device_info["identifiers"] == {("loxone", "ctl-lcv2")}
        # WP-5.1: the picker is a *light-controller sub-entity*.  It
        # carries a short name; the parent controller device contributes
        # the "Hall" part (the UI shows "Hall Solo RGB").
        assert light._attr_name == "Solo RGB"


class TestPlanTurnOn:
    """The pure `plan_turn_on` contract (PC-03 fix, testable without HA)."""

    def test_unknown_mode_brightness_only_falls_through_to_set_brightness(self):
        assert (
            plan_turn_on(
                color_mode=ColorMode.UNKNOWN,
                hs_color=None,
                color_temp_kelvin=None,
                brightness=None,
                kwargs={ATTR_BRIGHTNESS: 128},
            )
            == "setBrightness/50.19607843137255"
        )

    def test_hs_known_but_missing_values_still_set_brightness(self):
        # HS mode but no stored hs_color (unknown companion value)
        assert (
            plan_turn_on(color_mode=ColorMode.HS, hs_color=None, color_temp_kelvin=None, brightness=100, kwargs={})
            == "setBrightness/39.21568627450981"
        )

    def test_hs_mode_with_stored_colour(self):
        assert (
            plan_turn_on(
                color_mode=ColorMode.HS, hs_color=(180.0, 60.0), color_temp_kelvin=None, brightness=255, kwargs={}
            )
            == "hsv(180.0, 60.0, 100.0)"
        )

    def test_ct_mode_with_stored_kelvin(self):
        assert (
            plan_turn_on(
                color_mode=ColorMode.COLOR_TEMP, hs_color=None, color_temp_kelvin=4400, brightness=255, kwargs={}
            )
            == "temp(100.0, 4400)"
        )

    def test_default_brightness_when_never_seen(self):
        assert (
            plan_turn_on(
                color_mode=ColorMode.UNKNOWN, hs_color=None, color_temp_kelvin=None, brightness=None, kwargs={}
            )
            == "setBrightness/100.0"
        )


# --------------------------------------------------------------------------- #
# TunableWhite picker (PC-11, PC-38, PC-39)
# --------------------------------------------------------------------------- #


class TestTunableWhite:
    def light(self, **overrides):
        kwargs = dict(name="Solo TW", uuidAction="ctl-pick-tw")
        kwargs.update(overrides)
        return _stub(TunableWhiteLight, **kwargs)

    async def test_turn_on_before_any_state_does_not_raise(self):
        # PC-11: before the fix, the kelvin branch formatted
        # `hass_to_lox(None)` (TypeError) and the brightness branch produced
        # the literal string "temp(50.0, None)".
        light = self.light()
        assert light._attr_brightness is None
        assert light._attr_color_temp_kelvin is None
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        value = light.hass.bus.fired[-1]["value"]
        assert value == "temp(100.0, 4000)"  # hand: 255 → 100.0 %, default kelvin
        assert "None" not in value

    async def test_turn_on_with_kelvin_only(self):
        light = self.light(uuidAction="ctl-pick-tw2")
        await light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 2700})
        assert light.hass.bus.fired[-1]["value"] == "temp(100.0, 2700)"

    async def test_bare_turn_on_sends_on(self):
        light = self.light(uuidAction="ctl-pick-tw3")
        await light.async_turn_on()
        assert light.hass.bus.fired[-1]["value"] == "On"

    async def test_turn_off_accepts_kwargs(self):
        light = self.light(uuidAction="ctl-pick-tw4")
        await light.async_turn_off(foo=1)
        assert light.hass.bus.fired[-1]["value"] == "setBrightness/0"

    def test_min_kelvin_is_the_documented_loxone_floor(self):
        assert self.light()._attr_min_color_temp_kelvin == 2700
        assert self.light()._attr_max_color_temp_kelvin == 6500

    def test_standalone_device_identifier_is_a_string(self):
        light = self.light()
        assert light._attr_device_info["identifiers"] == {("loxone", "ctl-pick-tw")}

    async def test_temp_state_event_drives_mode(self):
        light = self.light(states={"color": "st-color"})
        light.event_handler(_event({"st-color": "temp[0.85, 2700]"}))
        assert light._attr_color_mode == ColorMode.COLOR_TEMP
        assert light._attr_color_temp_kelvin == 2700
        # hand: 0.85 * 2.55 = 2.1675 → round → 2
        assert light._attr_brightness == 2
        assert light._attr_available is True


def test_plan_temp_turn_on_defaults():
    # PC-11: no state yet → exact, non-raising defaults.
    assert plan_temp_turn_on(color_temp_kelvin=None, brightness=None, kwargs={}) == "On"
    assert (
        plan_temp_turn_on(color_temp_kelvin=None, brightness=None, kwargs={ATTR_BRIGHTNESS: 255}) == "temp(100.0, 4000)"
    )
    # a bare `turn_on` (no args) means "on, keep the setpoint" — the stored
    # values do not enter the payload
    assert plan_temp_turn_on(color_temp_kelvin=None, brightness=128, kwargs={}) == "On"
    assert plan_temp_turn_on(color_temp_kelvin=5200, brightness=128, kwargs={}) == "On"


# --------------------------------------------------------------------------- #
# LightControllerV2 (PC-33, PS-10, PC-40)
# --------------------------------------------------------------------------- #


def _lcv2(**overrides):
    kwargs = dict(
        name="Hall LCV2",
        uuidAction="ctl-lcv2-test",
        room="R",
        cat="C",
        details={},
        isSecured=False,
        states={
            "active": "st-active",
            "activeMoods": "st-moods",
            "moodList": "st-moodlist",
            "additionalMoods": "st-add",
        },
        subControls={},
    )
    kwargs.update(overrides)
    return _stub(LoxoneLightControllerV2, **kwargs)


class TestLightControllerV2:
    async def test_bare_turn_on_while_off_sends_on_mood(self):
        light = _lcv2()
        light._attr_is_on = False
        await light.async_turn_on()
        assert light.hass.bus.fired[-1] == {"uuid": "ctl-lcv2-test", "value": "changeTo/99"}

    async def test_bare_turn_on_while_on_sends_nothing(self):
        light = _lcv2(uuidAction="ctl-lcv2-test-on")
        light._attr_is_on = True
        await light.async_turn_on()
        assert light.hass.bus.fired == []

    async def test_brightness_with_master_writes_mapped_value(self):
        light = _lcv2(uuidAction="ctl-lcv2-test-m")
        light._master_value_uuid = "ctl-master"
        light._master_min = 10.0
        light._master_max = 90.0
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        assert light.hass.bus.fired[-1] == {"uuid": "ctl-master", "value": 90}

    async def test_brightness_without_master_sends_on(self):
        # PC-33: before the fix the elif chain fell through and the
        # service call did nothing.
        light = _lcv2(uuidAction="ctl-lcv2-test-nonm")
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})
        assert light.hass.bus.fired[-1] == {"uuid": "ctl-lcv2-test-nonm", "value": "on"}

    async def test_effect_and_brightness_both_sent(self):
        # PC-33 (and pre-existing effect path): the old elif chain dropped
        # the brightness whenever an effect was given.
        light = _lcv2(uuidAction="ctl-lcv2-test-ef")
        light._master_value_uuid = "ctl-master-ef"
        light._moodlist = [{"id": 1, "name": "Sunset"}]
        await light.async_turn_on(**{ATTR_EFFECT: "Sunset", ATTR_BRIGHTNESS: 255})
        fired = light.hass.bus.fired
        assert fired == [
            {"uuid": "ctl-lcv2-test-ef", "value": "changeTo/1"},
            {"uuid": "ctl-master-ef", "value": 100},
        ]

    async def test_turn_off_uses_named_off_mood(self):
        light = _lcv2(uuidAction="ctl-lcv2-test-off")
        await light.async_turn_off()
        assert light.hass.bus.fired[-1] == {"uuid": "ctl-lcv2-test-off", "value": "changeTo/0"}

    async def test_active_moods_flip_is_on(self):
        light = _lcv2(uuidAction="ctl-lcv2-test-moods")
        light.event_handler(_event({"st-moods": "[778]"}))
        assert light._attr_is_on is False
        light.event_handler(_event({"st-moods": "[1]"}))
        assert light._attr_is_on is True

    async def test_master_position_read_is_rescaled(self):
        # master min/max [10, 90]: 90 → 255, 50 → 128 (hand, as in the
        # dimmer tests)
        light = _lcv2(
            uuidAction="ctl-lcv2-test-pos",
            subControls={
                "sub-masterValue-test-0001": {
                    "name": "Master",
                    "type": "Dimmer",
                    "uuidAction": "ctl-master-pos",
                    "states": {
                        "position": "st-master-pos",
                        "min": "st-master-min",
                        "max": "st-master-max",
                    },
                }
            },
        )
        # a subControl key containing `masterValue` is adopted as the master
        assert light._master_value_uuid == "ctl-master-pos"
        assert light._master_min_uuid == "st-master-min"
        assert light._master_max_uuid == "st-master-max"
        light.event_handler(_event({"st-master-min": "10", "st-master-max": "90"}))
        light.event_handler(_event({"st-master-pos": "90"}))
        assert light._attr_brightness == 255
        light.event_handler(_event({"st-master-pos": "50"}))
        assert light._attr_brightness == 128
        assert light._attr_available is True

    def test_no_device_class_marker_ps10(self):
        # PS-10: the fake `device_class` (the Loxone control-type string
        # "LightControllerV2") is gone; the type lives in
        # extra_state_attributes["device_type"] for scene.py and friends.
        light = _lcv2(uuidAction="ctl-lcv2-test-ps10")
        assert getattr(light, "device_class", None) is None
        assert light.extra_state_attributes["device_type"] == "LightControllerV2"


# --------------------------------------------------------------------------- #
# platform setup: PC-43 (standalone pickers + numeric pickerType), PC-37
# --------------------------------------------------------------------------- #


_DROP = object()


def _json_safe_pruned(value):
    """Deep-copy a control catalogue to plain JSON values, dropping the
    non-JSON leftovers that full-setup tests from other files leave in the
    session fixture (platform setup mutates the shared control dicts in
    place: entity `hass`/`config_entry` back-references, `async_add_devices`
    bound methods) which would make a naive `deepcopy` fail with
    `cannot pickle 'mappingproxy'`.  Dropping them is safe: light.py
    re-resolves them during setup.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            pruned = _json_safe_pruned(v)
            if pruned is not _DROP:
                out[k] = pruned
        return out
    if isinstance(value, list):
        return [pruned for pruned in (_json_safe_pruned(v) for v in value) if pruned is not _DROP]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _DROP


STANDALONE_PICKER = {
    "name": "Solo Picker",
    "type": "ColorPickerV2",
    "uuidAction": "ctl-solo-picker-0001",
    "room": "726f6f6d-0101-9617-ffff-c6f7572726f6000001",
    "cat": "6361743a-0182-96d6-ffff-f72746361743000130",
    "details": {"pickerType": 0},  # integer RGB — the old `if picker_type:` check skipped 0
    "states": {"color": "36333734-solo-color-ffff-000000000000000001"},
    "defaultRating": 0,
    "isFavorite": False,
    "isSecured": False,
}

MASTER_COLOR_SUB_CONTROL = {
    "name": "Master Colour",
    "type": "Dimmer",
    "uuidAction": "masterColor-0001",
    "details": {},
    "states": {
        "active": "36333734-mc-active-ffff-000000000000000001",
        "brightness": "36333734-mc-bri-ffff-000000000000000002",
    },
    "defaultRating": 0,
    "isFavorite": False,
    "isSecured": False,
}


async def test_standalone_picker_and_master_color_filter(hass, loxapp3, mock_connection, mock_entry) -> None:
    """Full entry setup against the fixture, with:

    * a standalone `ColorPickerV2` added to the control catalogue
      (PC-43: these were never created before), and
    * a `masterColor` subControl added to the fixture LightControllerV2
      (PC-37: `find('masterColor') > 1` did not skip keys that start with
      `masterColor`; `> -1` must).

    Expected (hand-derived from the fixture):

    * a `light.parlour_solo_picker` entity exists (integer pickerType 0
      must resolve to the RGB picker; the room "Parlour" prefixes the
      name, as for every other fixture control), and
    * its device registry entry carries the string identifier
      `("loxone", "ctl-solo-picker-0001")` — never `None`, and
    * no entity for the `masterColor` dimmer is generated (its subControl
      key is *exactly* `masterColor-0001`, so the broken
      ``find("masterColor") > 1`` filter fails to skip it: index 0 is not
      greater than 1).
    """
    from homeassistant.helpers import device_registry as dr
    from homeassistant.config_entries import ConfigEntryState
    from unittest.mock import Mock, patch

    config = _json_safe_pruned(loxapp3)
    config["controls"]["ctl-solo-picker-0001"] = STANDALONE_PICKER
    lcv2 = config["controls"]["63746c3a-0183-9766-ffff-e67204c69676000131"]
    lcv2["subControls"]["masterColor-0001"] = MASTER_COLOR_SUB_CONTROL

    # mock_connection seeded `structure_file` from the pristine session
    # fixture before entry setup; re-script `LoxoneConnection.open` with a
    # patched view (same pattern as conftest).
    from custom_components.loxone.pyloxone_api.connection import LoxoneConnection

    namespace = mock_connection
    fake_connection = Mock()
    fake_connection.protocol.state.name = "OPEN"

    async def _fake_open_with_config(self, session=None):
        self.structure_file = config
        self.miniserver_version = config.get("softwareVersion")
        self.connected = True
        self.connection = fake_connection
        self._session_key = b"\x00" * 32
        self.event_bus = namespace
        return self

    entry = mock_entry
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "generate_lightcontroller_subcontrols": True}
    )
    with patch.object(LoxoneConnection, "open", new=_fake_open_with_config):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(entry.entry_id).state is ConfigEntryState.LOADED

    light_ids = hass.states.async_entity_ids("light")
    assert "light.parlour_solo_picker" in light_ids
    # PC-37: the masterColor subControl must not become an entity.
    assert "light.living_light_controller_master_colour" not in light_ids

    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    solo_device = [d for d in devices if ("loxone", "ctl-solo-picker-0001") in d.identifiers]
    assert len(solo_device) == 1
    for identifier in solo_device[0].identifiers:
        assert all(isinstance(part, str) and part for part in identifier)
