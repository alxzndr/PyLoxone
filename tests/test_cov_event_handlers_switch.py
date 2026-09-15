"""Pin the switch-platform ``event_handler`` decoding and the on/off commands.

The switch classes each speak a *different* command vocabulary ("pulse",
"On"/"Off", "on", "override/1"/"stopOverride") and decode different state
streams; none of that was exercised.  With no coordinator attached,
``LoxoneEntity._send`` falls back to firing ``SENDDOMAIN`` on the bus, so the
recording bus below captures the exact ``{uuid, value}`` payload.

Expected values are hand-derived from the source mapping, e.g. the presence
switch's ``int(active) & 2``: bit 1 (value 2) is "presence detected", so 2 and
3 are on while 0 and 1 are off.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.loxone.const import SENDDOMAIN
from custom_components.loxone.switch import (
    LoxoneIntercomSubControl,
    LoxoneLightPresenceSwitch,
    LoxoneRoomControllerOverride,
    LoxoneSwitch,
    LoxoneTimedSwitch,
)

DELAY_UUID = "0f86a2b1-0000-0000-ffff-535749430001"
DELAY_TOTAL_UUID = "0f86a2b1-0000-0000-ffff-535749430002"
ACTIVE_UUID = "0f86a2b1-0000-0000-ffff-535749430003"
PRESENCE_UUID = "0f86a2b1-0000-0000-ffff-535749430004"
OVERRIDE_UUID = "0f86a2b1-0000-0000-ffff-535749430005"


class _Bus:
    """Records the outbound bus events ``LoxoneEntity._send`` falls back to."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def async_fire(self, topic: str, data: dict) -> None:
        self.fired.append((topic, dict(data)))


def _stub(cls, **kwargs):
    """Build a bare switch entity with a recording bus and no HA attachment."""
    kwargs.setdefault("name", "Switch")
    kwargs.setdefault("room", "Parlour")
    kwargs.setdefault("cat", "Comfort")
    kwargs.setdefault("states", {})
    kwargs.setdefault("details", {})
    entity = cls(**kwargs)
    entity.hass = SimpleNamespace(bus=_Bus())
    # LoxoneEntity.async_write_ha_state is a no-op while entity_id is None;
    # the scheduling variants need explicit stubs (they reach into HA).
    entity.entity_id = None
    entity.schedule_update_ha_state = lambda *a, **k: None
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    return entity


def _last(entity) -> tuple[str, dict]:
    return entity.hass.bus.fired[-1]


# --------------------------------------------------------------------------- #
# LoxoneTimedSwitch
# --------------------------------------------------------------------------- #


class TestTimedSwitch:
    def switch(self, **overrides):
        kwargs = {
            "uuidAction": "ctl-timed",
            "name": "Stairwell Timer",
            "states": {
                "deactivationDelay": DELAY_UUID,
                "deactivationDelayTotal": DELAY_TOTAL_UUID,
            },
        }
        kwargs.update(overrides)
        return _stub(LoxoneTimedSwitch, **kwargs)

    def test_remaining_delay_above_zero_means_on(self):
        s = self.switch()
        assert s._attr_is_on is None
        assert s.available is False
        s.event_handler({DELAY_UUID: 42.7})
        assert s._attr_is_on is True
        # the remaining delay is truncated to whole seconds: int(42.7) == 42
        assert s._delay_remain == 42
        assert s.available is True

    def test_zero_remaining_delay_means_off(self):
        s = self.switch()
        s.event_handler({DELAY_UUID: 0.0})
        assert s._attr_is_on is False
        assert s._delay_remain == 0
        assert s.available is True

    def test_total_delay_is_tracked_separately(self):
        s = self.switch()
        s.event_handler({DELAY_TOTAL_UUID: 300.0})
        # the total alone says nothing about on/off
        assert s._attr_is_on is None
        assert s._delay_time_total == 300
        assert s.available is True

    def test_both_streams_in_one_event(self):
        s = self.switch()
        s.event_handler({DELAY_UUID: 12.0, DELAY_TOTAL_UUID: 300.0})
        assert s._attr_is_on is True
        assert s._delay_remain == 12
        assert s._delay_time_total == 300

    def test_unrelated_event_changes_nothing(self):
        s = self.switch()
        s.event_handler({"unrelated": 1.0})
        assert s._attr_is_on is None
        assert s.available is False

    def test_control_without_delay_states_ignores_everything(self):
        s = self.switch(uuidAction="ctl-timed-bare", states={})
        assert s._state_uuids() == frozenset()
        s.event_handler({DELAY_UUID: 5.0})
        assert s._attr_is_on is None

    def test_state_uuids_are_both_delay_streams(self):
        assert self.switch()._state_uuids() == frozenset({DELAY_UUID, DELAY_TOTAL_UUID})

    def test_attributes_expose_the_remaining_delay_only_while_on(self):
        s = self.switch()
        s.event_handler({DELAY_UUID: 0.0, DELAY_TOTAL_UUID: 300.0})
        off_attrs = s.extra_state_attributes
        assert off_attrs["delay_time_total"] == "300"
        assert "delay" not in off_attrs
        s.event_handler({DELAY_UUID: 12.0})
        on_attrs = s.extra_state_attributes
        assert on_attrs["delay"] == "12"
        assert on_attrs["delay_time_total"] == "300"
        assert on_attrs["device_type"] == "TimeSwitch"

    async def test_turn_on_pulses_once(self):
        s = self.switch(uuidAction="ctl-timed-cmd")
        await s.async_turn_on()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-timed-cmd", "value": "pulse"})
        assert s._attr_is_on is True
        # Already on: a second pulse would restart the timer, so it is skipped.
        await s.async_turn_on()
        assert len(s.hass.bus.fired) == 1

    async def test_turn_off_always_sends_off(self):
        s = self.switch(uuidAction="ctl-timed-off")
        await s.async_turn_off()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-timed-off", "value": "off"})
        assert s._attr_is_on is False
        # unconditional: off while already off still sends
        await s.async_turn_off()
        assert len(s.hass.bus.fired) == 2

    async def test_service_entrypoints_send_commands(self):
        s = self.switch(uuidAction="ctl-timed-sync")
        await s.async_turn_on()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-timed-sync", "value": "pulse"})
        await s.async_turn_off()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-timed-sync", "value": "off"})


# --------------------------------------------------------------------------- #
# LoxoneSwitch / LoxoneIntercomSubControl commands
# --------------------------------------------------------------------------- #


class TestPlainSwitchCommands:
    def switch(self, **overrides):
        kwargs = {
            "uuidAction": "ctl-switch",
            "name": "Pump",
            "states": {"active": ACTIVE_UUID},
        }
        kwargs.update(overrides)
        return _stub(LoxoneSwitch, **kwargs)

    async def test_on_is_capital_on_and_only_sent_when_off(self):
        s = self.switch(uuidAction="ctl-switch-on")
        await s.async_turn_on()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-switch-on", "value": "On"})
        await s.async_turn_on()
        assert len(s.hass.bus.fired) == 1

    async def test_off_is_capital_off_and_only_sent_when_on(self):
        s = self.switch(uuidAction="ctl-switch-off")
        # never turned on -> nothing to switch off
        await s.async_turn_off()
        assert s.hass.bus.fired == []
        await s.async_turn_on()
        await s.async_turn_off()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-switch-off", "value": "Off"})
        assert s._attr_is_on is False

    async def test_service_entrypoints_send_commands(self):
        s = self.switch(uuidAction="ctl-switch-sync")
        await s.async_turn_on()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-switch-sync", "value": "On"})
        await s.async_turn_off()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-switch-sync", "value": "Off"})


class TestIntercomSubControl:
    def sub(self, **overrides):
        kwargs = {
            "uuidAction": "ctl-intercom-sub",
            "name": "Bell",
            "states": {"active": ACTIVE_UUID},
        }
        kwargs.update(overrides)
        return _stub(LoxoneIntercomSubControl, **kwargs)

    async def test_turn_on_uses_lowercase_on(self):
        # The intercom sub-control takes "on", not the plain switch's "On".
        s = self.sub()
        await s.async_turn_on()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-intercom-sub", "value": "on"})
        assert s._attr_is_on is True
        await s.async_turn_on()
        assert len(s.hass.bus.fired) == 1

    async def test_turn_on_service_entrypoint_sends_command(self):
        s = self.sub(uuidAction="ctl-intercom-sync")
        await s.async_turn_on()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-intercom-sync", "value": "on"})

    def test_type_and_attributes(self):
        s = self.sub()
        assert s.type == "IntercomSubControl"
        assert s._attr_name == "Bell"
        assert s.extra_state_attributes["state_uuid"] == ACTIVE_UUID


# --------------------------------------------------------------------------- #
# LoxoneRoomControllerOverride.event_handler
# --------------------------------------------------------------------------- #


class TestRoomControllerOverride:
    def override(self, **overrides):
        kwargs = {
            "uuidAction": "ctl-irc",
            "name": "Parlour Room Controller",
            "states": {"overrideEntries": OVERRIDE_UUID},
        }
        kwargs.update(overrides)
        return _stub(LoxoneRoomControllerOverride, **kwargs)

    def test_a_non_empty_entry_list_is_on(self):
        e = self.override()
        assert e.is_on is False
        e.event_handler({OVERRIDE_UUID: '[{"reason": 1, "until": 123}]'})
        assert e.is_on is True

    def test_an_empty_entry_list_is_off(self):
        e = self.override()
        e.event_handler({OVERRIDE_UUID: '[{"reason": 1}]'})
        e.event_handler({OVERRIDE_UUID: "[]"})
        assert e.is_on is False

    def test_an_already_decoded_list_is_accepted(self):
        e = self.override()
        e.event_handler({OVERRIDE_UUID: [{"reason": 1}]})
        assert e.is_on is True

    def test_malformed_json_reads_as_off(self):
        e = self.override()
        e.event_handler({OVERRIDE_UUID: '[{"reason":'})
        assert e.is_on is False

    def test_a_non_list_payload_reads_as_off(self):
        e = self.override()
        e.event_handler({OVERRIDE_UUID: '{"reason": 1}'})
        assert e.is_on is False

    def test_unrelated_event_is_ignored(self):
        e = self.override()
        e.event_handler({OVERRIDE_UUID: '[{"reason": 1}]'})
        e.event_handler({"unrelated": "[]"})
        assert e.is_on is True

    def test_state_uuids_and_identity(self):
        e = self.override()
        assert e._state_uuids() == frozenset({OVERRIDE_UUID})
        assert e.unique_id == "ctl-irc_override"
        assert e._attr_name == "Comfort Override"
        # groups with the climate entity: the device is the room controller's
        assert e._attr_device_info["identifiers"] == {("loxone", "ctl-irc")}
        assert e.extra_state_attributes["device_type"] == "RoomControllerOverride"

    def test_control_without_the_override_state_never_updates(self):
        e = self.override(uuidAction="ctl-irc-bare", states={})
        assert e._state_uuids() == frozenset()
        e.event_handler({OVERRIDE_UUID: '[{"reason": 1}]'})
        assert e.is_on is False

    async def test_commands(self):
        e = self.override(uuidAction="ctl-irc-cmd")
        await e.async_turn_on()
        assert _last(e) == (SENDDOMAIN, {"uuid": "ctl-irc-cmd", "value": "override/1"})
        assert e.is_on is True
        await e.async_turn_off()
        assert _last(e) == (SENDDOMAIN, {"uuid": "ctl-irc-cmd", "value": "stopOverride"})
        assert e.is_on is False

    async def test_service_entrypoints_send_commands(self):
        e = self.override(uuidAction="ctl-irc-sync")
        await e.async_turn_on()
        assert _last(e) == (SENDDOMAIN, {"uuid": "ctl-irc-sync", "value": "override/1"})
        await e.async_turn_off()
        assert _last(e) == (SENDDOMAIN, {"uuid": "ctl-irc-sync", "value": "stopOverride"})


# --------------------------------------------------------------------------- #
# LoxoneLightPresenceSwitch.event_handler
# --------------------------------------------------------------------------- #


class TestLightPresenceSwitch:
    def presence(self, **overrides):
        kwargs = {
            "uuidAction": "ctl-lcv2",
            "name": "Living Light Controller",
            "states": {"presence": PRESENCE_UUID, "active": ACTIVE_UUID},
        }
        kwargs.update(overrides)
        return _stub(LoxoneLightPresenceSwitch, **kwargs)

    def test_bit_one_set_means_presence_detection_is_on(self):
        # int(active) & 2 -> bit 1 (value 2) is the presence-detection flag.
        s = self.presence()
        assert s._attr_is_on is None
        assert s.available is False
        s.event_handler({PRESENCE_UUID: 2})
        assert s.is_on is True
        assert s.available is True

    def test_bit_one_set_alongside_other_bits(self):
        s = self.presence()
        s.event_handler({PRESENCE_UUID: 3})  # 3 & 2 == 2
        assert s.is_on is True

    def test_bit_zero_alone_is_off(self):
        s = self.presence()
        s.event_handler({PRESENCE_UUID: 1})  # 1 & 2 == 0
        assert s.is_on is False
        assert s.available is True

    def test_zero_is_off(self):
        s = self.presence()
        s.event_handler({PRESENCE_UUID: 0})
        assert s.is_on is False

    def test_float_values_are_truncated_before_masking(self):
        s = self.presence()
        s.event_handler({PRESENCE_UUID: 2.0})
        assert s.is_on is True

    def test_repeated_value_keeps_the_state(self):
        s = self.presence()
        s.event_handler({PRESENCE_UUID: 2})
        s.event_handler({PRESENCE_UUID: 2})
        assert s.is_on is True
        assert s.available is True

    def test_unrelated_event_is_ignored(self):
        s = self.presence()
        s.event_handler({ACTIVE_UUID: 1.0})
        assert s._attr_is_on is None
        assert s.available is False

    def test_state_uuids_is_the_presence_stream(self):
        assert self.presence()._state_uuids() == frozenset({PRESENCE_UUID})

    async def test_turn_off_targets_the_presence_sub_address(self):
        s = self.presence(uuidAction="ctl-lcv2-cmd")
        await s.async_turn_off()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-lcv2-cmd/presence", "value": "off"})

    async def test_turn_on_targets_the_presence_sub_address(self):
        s = self.presence(uuidAction="ctl-lcv2-cmd-on")
        await s.async_turn_on()
        assert _last(s) == (SENDDOMAIN, {"uuid": "ctl-lcv2-cmd-on/presence", "value": "on"})
