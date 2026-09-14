"""Tests for LoxoneAlarm.code_format / code_arm_required (#413)."""

from homeassistant.components.alarm_control_panel.const import CodeFormat

from custom_components.loxone.alarm_control_panel import LoxoneAlarm


def _alarm(*, secured, code):
    # Minimal Burglar Alarm block as it comes out of LoxAPP3.json, after
    # the room/category names have been resolved.
    return LoxoneAlarm(
        name="Alarm",
        uuidAction="0f1a2b3c-00d4-e5f6-ffff112233445566",
        room="Hallway",
        cat="Security",
        isSecured=secured,
        code=code,
    )


class TestCodeFormat:
    """code_format must reflect the configured code, not a sentinel."""

    def test_secured_without_code_prompts_for_text(self):
        alarm = _alarm(secured=True, code=None)
        assert alarm.code_arm_required is True
        assert alarm.code_format == CodeFormat.TEXT

    def test_secured_with_numeric_code_prompts_for_number(self):
        # The reported bug: this returned TEXT because the digit check ran on
        # the sentinel "required" instead of the configured code.
        alarm = _alarm(secured=True, code="1234")
        assert alarm.code_format == CodeFormat.NUMBER

    def test_secured_with_alphanumeric_code_prompts_for_text(self):
        alarm = _alarm(secured=True, code="abc1")
        assert alarm.code_format == CodeFormat.TEXT

    def test_unsecured_block_needs_no_code(self):
        alarm = _alarm(secured=False, code="1234")
        assert alarm.code_arm_required is False
        assert alarm.code_format is None


class TestCodeArmRequiredHasNoSideEffects:
    """Reading a property must not rewrite the configured code."""

    def test_configured_code_survives_reading_code_arm_required(self):
        alarm = _alarm(secured=True, code="1234")
        assert alarm.code_arm_required is True
        assert alarm._code == "1234"
        assert alarm.code_format == CodeFormat.NUMBER

    def test_absent_code_stays_none(self):
        alarm = _alarm(secured=True, code=None)
        assert alarm.code_arm_required is True
        assert alarm._code is None
