"""Pin the binary-sensor and sensor ``event_handler`` state decoding.

Covers the handlers that were running unexercised:

* ``LoxoneCustomBinarySensor`` -- the YAML binary sensor's ``== 1.0`` mapping,
* ``LoxoneDigitalSensor``'s remaining value-type branches (bool / text /
  unusable payload; PS-04),
* ``LoxoneCustomSensor`` -- list/dict stringification and the 255-char cap,
* ``LoxoneKeepAliveSensor`` -- the 60 s throttle around the heartbeat,
* ``LoxoneClimateController`` -- the JSON control list, the per-room demand
  fan-out signal (PS-18) and the heating/cooling summary.

Expected values are hand-written literals derived from the source mapping and
from ``THROTTLE_KEEP_ALIVE_TIME = 60`` in ``const.py``; the dispatcher signal
name is spelled out by hand ("loxone_<entry id>_<uuid>_demand").
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from custom_components.loxone.binary_sensor import LoxoneCustomBinarySensor, LoxoneDigitalSensor
from custom_components.loxone.sensor import (
    LoxoneClimateController,
    LoxoneCustomSensor,
    LoxoneKeepAliveSensor,
)

CUSTOM_UUID = "0f86a2b1-0000-0000-ffff-53454e530001"
DIGITAL_UUID = "0f86a2b1-0000-0000-ffff-53454e530002"
CONTROLS_UUID = "0f86a2b1-0000-0000-ffff-53454e530003"
ROOM_A_UUID = "0f86a2b1-0000-0000-ffff-53454e530004"
ROOM_B_UUID = "0f86a2b1-0000-0000-ffff-53454e530005"


def _detach(entity):
    """Make the HA state writes no-ops on an unattached entity."""
    entity.entity_id = None  # LoxoneEntity.async_write_ha_state guard
    entity.schedule_update_ha_state = lambda *a, **k: None
    entity.async_schedule_update_ha_state = lambda *a, **k: None
    return entity


# --------------------------------------------------------------------------- #
# LoxoneCustomBinarySensor.event_handler
# --------------------------------------------------------------------------- #


class TestCustomBinarySensor:
    def sensor(self, **overrides):
        kwargs = {"name": "Garage Contact", "uuidAction": CUSTOM_UUID}
        kwargs.update(overrides)
        return _detach(LoxoneCustomBinarySensor(**kwargs))

    def test_starts_unknown(self):
        e = self.sensor()
        assert e._state == STATE_UNKNOWN
        assert e.is_on is False
        assert e.state == STATE_OFF

    def test_exactly_one_is_on(self):
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: 1.0})
        assert e.is_on is True
        assert e.state == STATE_ON

    def test_zero_is_off(self):
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: 1.0})
        e.event_handler({CUSTOM_UUID: 0.0})
        assert e.is_on is False
        assert e.state == STATE_OFF

    def test_any_other_value_is_off(self):
        # The YAML sensor compares against 1.0 exactly: 2.0 reads as "off".
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: 2.0})
        assert e.is_on is False

    def test_unrelated_event_is_ignored(self):
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: 1.0})
        e.event_handler({"unrelated": 0.0})
        assert e.is_on is True

    def test_without_a_uuid_the_entity_never_updates(self):
        e = _detach(LoxoneCustomBinarySensor(name="Nameless"))
        assert e.uuidAction == ""
        assert e._state_uuids() == frozenset()
        e.event_handler({CUSTOM_UUID: 1.0})
        assert e.is_on is False

    def test_state_uuids_is_the_action_uuid(self):
        assert self.sensor()._state_uuids() == frozenset({CUSTOM_UUID})


# --------------------------------------------------------------------------- #
# LoxoneDigitalSensor.event_handler -- remaining value-type branches (PS-04)
# --------------------------------------------------------------------------- #


class TestDigitalSensorValueTypes:
    def sensor(self):
        return _detach(
            LoxoneDigitalSensor(
                uuidAction=DIGITAL_UUID,
                name="Breaker",
                room="Cellar",
                cat="Energy",
                states={"active": DIGITAL_UUID},
                type="digital",
                details={},
            )
        )

    def test_true_and_false_booleans(self):
        e = self.sensor()
        e.event_handler({DIGITAL_UUID: True})
        assert e.is_on is True
        e.event_handler({DIGITAL_UUID: False})
        assert e.is_on is False

    def test_text_payloads_are_on_unless_they_say_off(self):
        e = self.sensor()
        e.event_handler({DIGITAL_UUID: "On"})
        assert e.is_on is True
        e.event_handler({DIGITAL_UUID: " OFF "})
        assert e.is_on is False
        # any other non-blank text is a *reported* state, i.e. "on"
        e.event_handler({DIGITAL_UUID: "Bewegung erkannt"})
        assert e.is_on is True

    def test_blank_and_unusable_payloads_are_off(self):
        e = self.sensor()
        e.event_handler({DIGITAL_UUID: "On"})
        e.event_handler({DIGITAL_UUID: "   "})
        assert e.is_on is False
        e.event_handler({DIGITAL_UUID: "On"})
        e.event_handler({DIGITAL_UUID: None})
        assert e.is_on is False


# --------------------------------------------------------------------------- #
# LoxoneCustomSensor.event_handler
# --------------------------------------------------------------------------- #


class TestCustomSensor:
    def sensor(self, **overrides):
        kwargs = {"name": "Raw Value", "uuidAction": CUSTOM_UUID}
        kwargs.update(overrides)
        return _detach(LoxoneCustomSensor(**kwargs))

    def test_scalars_pass_through(self):
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: 21.5})
        assert e.native_value == 21.5
        e.event_handler({CUSTOM_UUID: "text"})
        assert e.native_value == "text"

    def test_dicts_are_stringified(self):
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: {"a": 1}})
        assert e.native_value == "{'a': 1}"

    def test_short_lists_are_stringified_whole(self):
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: [1, 2, 3]})
        assert e.native_value == "[1, 2, 3]"

    def test_long_lists_are_capped_at_255_characters(self):
        # HA's state string limit: str(list(range(100))) is far longer than
        # 255 characters, so the value must be truncated to its first 255.
        payload = list(range(100))
        expected = str(payload)[:255]
        assert len(str(payload)) > 255  # guards the fixture, not the code
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: payload})
        assert e.native_value == expected
        assert len(e.native_value) == 255

    def test_unrelated_event_is_ignored(self):
        e = self.sensor()
        e.event_handler({CUSTOM_UUID: 1})
        e.event_handler({"unrelated": 2})
        assert e.native_value == 1

    def test_placeholder_units_read_as_no_unit(self):
        for placeholder in ("None", "none", "-"):
            e = self.sensor(unit_of_measurement=placeholder)
            assert e.native_unit_of_measurement is None
        e = self.sensor(unit_of_measurement="°C")
        assert e.native_unit_of_measurement == "°C"

    def test_extra_state_attributes_expose_the_loxone_identity(self):
        e = self.sensor(room="Cellar", cat="Energy")
        attrs = e.extra_state_attributes
        assert attrs["uuid"] == CUSTOM_UUID
        assert attrs["platform"] == "loxone"
        assert attrs["room"] == "Cellar"
        assert attrs["category"] == "Energy"


# --------------------------------------------------------------------------- #
# LoxoneKeepAliveSensor.event_handler (THROTTLE_KEEP_ALIVE_TIME = 60)
# --------------------------------------------------------------------------- #


class TestKeepAliveSensor:
    def sensor(self):
        return _detach(LoxoneKeepAliveSensor("SERIAL"))

    def test_subscribes_to_the_keep_alive_pseudo_stream(self):
        assert self.sensor()._state_uuids() == frozenset({"keep_alive"})

    def test_first_heartbeat_records_the_timestamp(self):
        e = self.sensor()
        assert e.native_value is None
        moment = dt_util.utcnow()
        with patch("custom_components.loxone.sensor.dt_util.utcnow", return_value=moment):
            e.event_handler({"keep_alive": "received"})
        assert e.native_value == moment

    def test_a_second_heartbeat_inside_the_window_is_throttled(self):
        e = self.sensor()
        first = dt_util.utcnow()
        with patch("custom_components.loxone.sensor.dt_util.utcnow", return_value=first):
            e.event_handler({"keep_alive": "received"})
        with patch(
            "custom_components.loxone.sensor.dt_util.utcnow",
            return_value=first + timedelta(seconds=59),
        ):
            e.event_handler({"keep_alive": "received"})
        assert e.native_value == first

    def test_a_heartbeat_past_the_window_updates(self):
        e = self.sensor()
        first = dt_util.utcnow()
        with patch("custom_components.loxone.sensor.dt_util.utcnow", return_value=first):
            e.event_handler({"keep_alive": "received"})
        later = first + timedelta(seconds=61)
        with patch("custom_components.loxone.sensor.dt_util.utcnow", return_value=later):
            e.event_handler({"keep_alive": "received"})
        assert e.native_value == later

    def test_other_payloads_are_ignored(self):
        e = self.sensor()
        e.event_handler({"keep_alive": "sent"})
        e.event_handler({"something_else": "received"})
        assert e.native_value is None

    def test_extra_state_attributes(self):
        assert self.sensor().extra_state_attributes == {"uuid": "", "platform": "loxone"}


# --------------------------------------------------------------------------- #
# LoxoneClimateController.event_handler (PS-18 per-room demand fan-out)
# --------------------------------------------------------------------------- #

ENTRY_ID = "entry-1"
# Hand-spelled from const.py: loxone_uuid_signal() + "_demand".
ROOM_A_SIGNAL = f"loxone_{ENTRY_ID}_{ROOM_A_UUID}_demand"
ROOM_B_SIGNAL = f"loxone_{ENTRY_ID}_{ROOM_B_UUID}_demand"


class TestClimateController:
    def controller(self, *, with_coordinator: bool = True, **overrides):
        kwargs = {
            "uuidAction": "ctl-climate",
            "name": "Climate Controller",
            "room": "House",
            "cat": "Climate",
            "states": {"controls": CONTROLS_UUID},
            "details": {},
        }
        kwargs.update(overrides)
        e = _detach(LoxoneClimateController(**kwargs))
        e.hass = SimpleNamespace()
        if with_coordinator:
            coordinator = SimpleNamespace(connected=True, config_entry=SimpleNamespace(entry_id=ENTRY_ID))
            e._connection_coordinator = lambda: coordinator  # type: ignore[method-assign]
        else:
            e._connection_coordinator = lambda: None  # type: ignore[method-assign]
        return e

    def test_starts_idle(self):
        e = self.controller()
        assert e.native_value == "Idle"
        assert e._state_uuids() == frozenset({CONTROLS_UUID})

    def test_demand_one_counts_as_heating_and_is_fanned_out_per_room(self):
        e = self.controller()
        payload = f'[{{"uuid": "{ROOM_A_UUID}", "demand": 1}}, {{"uuid": "{ROOM_B_UUID}", "demand": 0}}]'
        with patch("custom_components.loxone.sensor.async_dispatcher_send") as send:
            e.event_handler({CONTROLS_UUID: payload})
        assert e.native_value == "Heating (1)"
        assert e.extra_state_attributes["heat_demand"] == 1
        assert e.extra_state_attributes["cool_demand"] == 0
        assert e.extra_state_attributes["device_type"] == "ClimateController"
        # one signal per listed room, carrying that room's own demand
        assert [(c.args[1], c.args[2]) for c in send.call_args_list] == [
            (ROOM_A_SIGNAL, 1),
            (ROOM_B_SIGNAL, 0),
        ]

    def test_demand_minus_one_counts_as_cooling(self):
        e = self.controller()
        payload = f'[{{"uuid": "{ROOM_A_UUID}", "demand": -1}}, {{"uuid": "{ROOM_B_UUID}", "demand": -1}}]'
        with patch("custom_components.loxone.sensor.async_dispatcher_send") as send:
            e.event_handler({CONTROLS_UUID: payload})
        assert e.native_value == "Cooling (2)"
        assert e.extra_state_attributes["cool_demand"] == 2
        assert [c.args[2] for c in send.call_args_list] == [-1, -1]

    def test_heating_wins_the_summary_when_both_are_demanded(self):
        e = self.controller()
        payload = f'[{{"uuid": "{ROOM_A_UUID}", "demand": 1}}, {{"uuid": "{ROOM_B_UUID}", "demand": -1}}]'
        with patch("custom_components.loxone.sensor.async_dispatcher_send"):
            e.event_handler({CONTROLS_UUID: payload})
        assert e._heat_demand == 1
        assert e._cool_demand == 1
        assert e.native_value == "Heating (1)"

    def test_an_empty_control_list_is_idle(self):
        e = self.controller()
        with patch("custom_components.loxone.sensor.async_dispatcher_send") as send:
            e.event_handler({CONTROLS_UUID: "[]"})
        assert e.native_value == "Idle"
        assert send.call_args_list == []

    def test_entries_without_a_room_uuid_are_counted_but_not_signalled(self):
        e = self.controller()
        with patch("custom_components.loxone.sensor.async_dispatcher_send") as send:
            e.event_handler({CONTROLS_UUID: '[{"demand": 1}]'})
        assert e.native_value == "Heating (1)"
        assert send.call_args_list == []

    def test_without_a_coordinator_nothing_is_signalled(self):
        e = self.controller(with_coordinator=False)
        payload = f'[{{"uuid": "{ROOM_A_UUID}", "demand": 1}}]'
        with patch("custom_components.loxone.sensor.async_dispatcher_send") as send:
            e.event_handler({CONTROLS_UUID: payload})
        # the summary still updates; only the per-room fan-out is skipped
        assert e.native_value == "Heating (1)"
        assert send.call_args_list == []

    def test_malformed_json_leaves_the_previous_demand_intact(self):
        e = self.controller()
        payload = f'[{{"uuid": "{ROOM_A_UUID}", "demand": 1}}]'
        with patch("custom_components.loxone.sensor.async_dispatcher_send"):
            e.event_handler({CONTROLS_UUID: payload})
            e.event_handler({CONTROLS_UUID: '[{"uuid": '})
        assert e.native_value == "Heating (1)"

    def test_non_list_payloads_are_stored_verbatim(self):
        e = self.controller(states={"controls": CONTROLS_UUID, "temp": "temp-uuid"})
        e.event_handler({"temp-uuid": 21.5})
        assert e._stateAttribValues["temp-uuid"] == 21.5
        assert e.native_value == "Idle"

    def test_unrelated_event_does_not_schedule_an_update(self):
        e = self.controller()
        e.schedule_update_ha_state = Mock()
        e.event_handler({"unrelated": "[]"})
        assert e.schedule_update_ha_state.call_count == 0
