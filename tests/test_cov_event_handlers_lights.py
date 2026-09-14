"""Pin the light-platform ``event_handler`` state decoding.

These handlers translate the Miniserver colour stream into HA light state.
They were entirely unexercised, so an inverted or mis-scaled mapping would
not have failed a single test.

Expected values are hand-derived from the documented conversions in
``custom_components/loxone/helpers.py``:

* ``lox_to_hass(v) = v / 100 * 255``  (Loxone 0-100 %  ->  HA 0-255)
* the colour-temperature branch instead rounds: ``round(255 * v / 100)``

so a Loxone brightness of ``40`` reads back as ``round(255 * 40 / 100) = 102``
on the ``temp(...)`` branch and as ``40 / 100 * 255 = 102.0`` on the
``hsv(...)`` branch (the latter is *not* rounded -- see the PR body).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.lights.colorpickers import (
    LumiTech,
    RGBColorPicker,
    TunableWhiteLight,
)
from custom_components.loxone.lights.switch import LoxoneLightSwitch
from homeassistant.components.light import ColorMode

COLOR_UUID = "0f86a2b1-0000-0000-ffff-504943000001"


class _Bus:
    """Records the outbound bus events ``LoxoneEntity._send`` falls back to."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def async_fire(self, topic: str, data: dict) -> None:
        self.fired.append((topic, dict(data)))


def _stub(cls, **kwargs):
    """Build a bare entity with a recording bus and no HA attachment."""
    kwargs.setdefault("name", "Picker")
    kwargs.setdefault("room", "Parlour")
    kwargs.setdefault("cat", "Lighting")
    kwargs.setdefault("states", {})
    kwargs.setdefault("details", {})
    kwargs["async_add_devices"] = lambda *a, **k: None
    entity = cls(**kwargs)
    entity.hass = SimpleNamespace(bus=_Bus())
    # LoxoneEntity.async_write_ha_state is a no-op while entity_id is None.
    entity.entity_id = None
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    return entity


# --------------------------------------------------------------------------- #
# RGBColorPicker.event_handler
# --------------------------------------------------------------------------- #


class TestRgbColorPickerEventHandler:
    def picker(self, **overrides):
        kwargs = {"uuidAction": "ctl-rgb-evt", "states": {"color": COLOR_UUID}}
        kwargs.update(overrides)
        return _stub(RGBColorPicker, **kwargs)

    def test_starts_unavailable_and_off(self):
        light = self.picker()
        assert light.available is False
        assert light.is_on is False
        assert light.color_mode is ColorMode.UNKNOWN
        assert light.brightness is None

    def test_hsv_sets_hue_saturation_and_brightness(self):
        light = self.picker()
        # hsv(210, 85, 40): hue 210, saturation 85, Loxone brightness 40 %.
        light.event_handler({COLOR_UUID: "hsv(210,85,40)"})
        assert light.color_mode is ColorMode.HS
        assert light.hs_color == (210, 85)
        # 40 / 100 * 255 = 102.0 (hand-derived from lox_to_hass)
        assert light.brightness == pytest.approx(102.0)
        assert light.is_on is True
        assert light.available is True

    def test_hsv_zero_is_off_but_still_available(self):
        light = self.picker()
        light.event_handler({COLOR_UUID: "hsv(0,0,0)"})
        assert light.hs_color == (0, 0)
        assert light.brightness == 0
        assert light.is_on is False
        # "off" is a *known* state: the entity must not stay unavailable.
        assert light.available is True

    def test_temp_sets_kelvin_and_clears_hs(self):
        light = self.picker()
        light.event_handler({COLOR_UUID: "hsv(210,85,40)"})
        light.event_handler({COLOR_UUID: "temp(40,2700)"})
        assert light.color_mode is ColorMode.COLOR_TEMP
        assert light.color_temp_kelvin == 2700
        # round(255 * 40 / 100) = round(102.0) = 102
        assert light.brightness == 102
        # the stale hue/saturation must not survive a colour-temp report
        assert light.hs_color is None
        assert light.is_on is True

    def test_temp_full_brightness(self):
        light = self.picker()
        light.event_handler({COLOR_UUID: "temp(100,6500)"})
        assert light.brightness == 255
        assert light.color_temp_kelvin == 6500

    def test_unparsable_payload_keeps_the_last_known_state(self):
        light = self.picker()
        light.event_handler({COLOR_UUID: "hsv(210,85,40)"})
        light.event_handler({COLOR_UUID: "hsv(oops"})  # literal_decoder -> None
        assert light.hs_color == (210, 85)
        assert light.brightness == pytest.approx(102.0)

    def test_unknown_command_is_logged_and_changes_nothing(self, caplog):
        light = self.picker()
        with caplog.at_level(logging.ERROR, logger="custom_components.loxone.lights.colorpickers"):
            light.event_handler({COLOR_UUID: "rgb(1,2,3)"})
        assert "Not handled command" in caplog.text
        assert light.brightness is None
        assert light.available is False

    def test_event_for_another_uuid_is_ignored(self):
        light = self.picker()
        light.event_handler({"some-other-uuid": "hsv(210,85,40)"})
        assert light.brightness is None
        assert light.available is False

    def test_state_uuids_is_the_color_stream(self):
        assert self.picker()._state_uuids() == frozenset({COLOR_UUID})
        assert _stub(RGBColorPicker, uuidAction="ctl-rgb-nostate")._state_uuids() == frozenset()


class TestLumiTechEventHandler:
    """LumiTech inherits the RGB decoding; only its device payload differs."""

    def test_decodes_like_an_rgb_picker(self):
        light = _stub(LumiTech, uuidAction="ctl-lumitech", states={"color": COLOR_UUID})
        assert light.type == "LumiTech"
        light.event_handler({COLOR_UUID: "hsv(120,50,20)"})
        assert light.color_mode is ColorMode.HS
        assert light.hs_color == (120, 50)
        # 20 / 100 * 255 = 51.0
        assert light.brightness == pytest.approx(51.0)

    def test_sub_light_of_a_controller_uses_the_controller_device(self):
        light = _stub(
            LumiTech,
            uuidAction="ctl-lumitech-sub",
            name="Ambient",
            states={"color": COLOR_UUID},
            lightcontroller_id="ctl-lcv2",
            lightcontroller_name="Living Light Controller",
        )
        assert light.type == "LightControllerV2"
        assert light._attr_name == "Ambient"
        assert light._attr_device_info["identifiers"] == {("loxone", "ctl-lcv2")}
        assert light._attr_device_info["name"] == "Living Light Controller"


# --------------------------------------------------------------------------- #
# TunableWhiteLight.event_handler
# --------------------------------------------------------------------------- #


class TestTunableWhiteEventHandler:
    def light(self, **overrides):
        kwargs = {"uuidAction": "ctl-tw-evt", "states": {"color": COLOR_UUID}}
        kwargs.update(overrides)
        return _stub(TunableWhiteLight, **kwargs)

    def test_temp_sets_kelvin_brightness_and_availability(self):
        light = self.light()
        assert light.available is False
        light.event_handler({COLOR_UUID: "temp(40,3000)"})
        assert light.color_mode is ColorMode.COLOR_TEMP
        assert light.color_temp_kelvin == 3000
        # round(255 * 40 / 100) = 102
        assert light.brightness == 102
        assert light.is_on is True
        assert light.available is True

    def test_hsv_zero_turns_it_off_without_publishing(self):
        """Pins today's behaviour: ``hsv(0,0,0)`` zeroes brightness but does
        *not* set ``request_update``, so availability is never flipped (see
        the PR body -- reported, not fixed here)."""
        light = self.light()
        light.event_handler({COLOR_UUID: "hsv(0,0,0)"})
        assert light.brightness == 0
        assert light.is_on is False
        assert light.available is False

    def test_non_zero_hsv_is_rejected_with_a_warning(self, caplog):
        light = self.light()
        light.event_handler({COLOR_UUID: "temp(40,3000)"})
        with caplog.at_level(logging.WARNING, logger="custom_components.loxone.lights.colorpickers"):
            light.event_handler({COLOR_UUID: "hsv(120,50,80)"})
        assert "hsv not supported for TunableWhiteLight" in caplog.text
        # the colour-temp state survives an unsupported hsv report
        assert light.brightness == 102
        assert light.color_temp_kelvin == 3000

    def test_unknown_command_is_logged(self, caplog):
        light = self.light()
        with caplog.at_level(logging.ERROR, logger="custom_components.loxone.lights.colorpickers"):
            light.event_handler({COLOR_UUID: "position(12)"})
        assert "Not handled command" in caplog.text
        assert light.brightness is None

    def test_unparsable_temp_keeps_the_last_known_state(self):
        light = self.light()
        light.event_handler({COLOR_UUID: "temp(40,3000)"})
        light.event_handler({COLOR_UUID: "temp(nope"})
        assert light.brightness == 102
        assert light.color_temp_kelvin == 3000


# --------------------------------------------------------------------------- #
# LoxoneLightSwitch (lights/switch.py)
# --------------------------------------------------------------------------- #

ACTIVE_UUID = "0f86a2b1-0000-0000-ffff-504943000002"


class TestLightSwitchEventHandler:
    def switch(self, **overrides):
        kwargs = {
            "uuidAction": "ctl-light-switch",
            "name": "Hall Light",
            "states": {"active": ACTIVE_UUID},
        }
        kwargs.update(overrides)
        return _stub(LoxoneLightSwitch, **kwargs)

    def test_standalone_light_builds_its_own_device(self):
        light = self.switch()
        assert light.type == "Light"
        assert light._attr_device_info["identifiers"] == {("loxone", "ctl-light-switch")}
        assert light._attr_extra_state_attributes["device_type"] == "Light"
        assert light.color_mode is ColorMode.ONOFF

    def test_active_one_is_on_and_flips_availability(self):
        light = self.switch()
        assert light.available is False
        light.event_handler({ACTIVE_UUID: 1.0})
        assert light.is_on is True
        assert light.available is True

    def test_active_zero_is_off(self):
        light = self.switch()
        light.event_handler({ACTIVE_UUID: 1.0})
        light.event_handler({ACTIVE_UUID: 0.0})
        assert light.is_on is False
        assert light.available is True

    def test_repeated_identical_value_is_a_no_op(self):
        light = self.switch()
        light.event_handler({ACTIVE_UUID: 0.0})
        assert light.is_on is False
        assert light.available is True
        # A second identical report must not re-publish (request_update stays
        # False); the state itself is unchanged.
        light.event_handler({ACTIVE_UUID: 0.0})
        assert light.is_on is False

    def test_event_without_the_active_uuid_is_ignored(self):
        light = self.switch()
        light.event_handler({"unrelated": 1.0})
        assert light.available is False

    def test_control_without_an_active_state_never_updates(self):
        light = self.switch(uuidAction="ctl-light-switch-bare", states={})
        light.event_handler({ACTIVE_UUID: 1.0})
        assert light.available is False
        assert light._state_uuids() == frozenset()

    async def test_turn_on_and_off_commands(self):
        light = self.switch(uuidAction="ctl-light-switch-cmd")
        await light.async_turn_on()
        assert light.hass.bus.fired[-1] == (SENDDOMAIN, {"uuid": "ctl-light-switch-cmd", "value": "on"})
        await light.async_turn_off()
        assert light.hass.bus.fired[-1] == (SENDDOMAIN, {"uuid": "ctl-light-switch-cmd", "value": "off"})
