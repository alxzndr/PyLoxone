"""Tests for the night arming mode of LoxoneAlarm (#323)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
)

from custom_components.loxone.alarm_control_panel import LoxoneAlarm
from custom_components.loxone.const import SECUREDSENDDOMAIN, SENDDOMAIN

UUID = "0f1a2b3c-00d4-e5f6-ffff112233445566"


def _alarm(*, secured):
    alarm = LoxoneAlarm(
        name="Alarm",
        uuidAction=UUID,
        room="Hallway",
        cat="Security",
        isSecured=secured,
        code=None,
    )
    # Just enough of Home Assistant to observe the outgoing command.
    alarm.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
    alarm.async_schedule_update_ha_state = Mock()
    return alarm


class TestSupportedFeatures:
    def test_night_is_advertised_next_to_home_and_away(self):
        features = _alarm(secured=False).supported_features
        assert features & AlarmControlPanelEntityFeature.ARM_NIGHT
        assert features & AlarmControlPanelEntityFeature.ARM_HOME
        assert features & AlarmControlPanelEntityFeature.ARM_AWAY


class TestArmNight:
    """Night arms like home: delayed on, movement detection suppressed."""

    def test_unsecured_block_sends_delayedon_0(self):
        alarm = _alarm(secured=False)
        asyncio.run(alarm.async_alarm_arm_night())
        alarm.hass.bus.async_fire.assert_called_once_with(
            SENDDOMAIN, {"uuid": UUID, "value": "delayedon/0"}
        )
        alarm.async_schedule_update_ha_state.assert_called_once()

    def test_secured_block_sends_secured_command_with_code(self):
        alarm = _alarm(secured=True)
        asyncio.run(alarm.async_alarm_arm_night(code="1234"))
        alarm.hass.bus.async_fire.assert_called_once_with(
            SECUREDSENDDOMAIN, {"uuid": UUID, "value": "delayedon/0", "code": "1234"}
        )

    def test_night_and_home_send_the_same_command(self):
        night, home = _alarm(secured=False), _alarm(secured=False)
        asyncio.run(night.async_alarm_arm_night())
        asyncio.run(home.async_alarm_arm_home())
        assert night.hass.bus.async_fire.call_args == home.hass.bus.async_fire.call_args
