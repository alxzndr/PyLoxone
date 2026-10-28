"""WP-7.5: the secured-alarm code input is the ``alarm_code_format`` option.

Upstream ``d13ef23`` (fix(alarm): accept text codes for secured alarms
again) went back to a text field because the code a secured Loxone ``Alarm``
demands is the user's visualisation password, which Loxone Config does not
limit to digits: Home Assistant's ``CodeFormat.NUMBER`` keypad cannot type
letters, so such a user could not arm or disarm at all.  The fork had
deliberately chosen the keypad (PC-06, upstream #413).  The fork now offers
both per config entry: ``alarm_code_format`` selects ``text`` (default) or
``number``.

Real ``hass`` fixture, real alarm services through ``mock_connection`` /
``mock_entry``.  Both alarms of ``tests/fixtures/LoxAPP3.json`` are
unsecured; the module-level ``loxapp3`` override below deep-copies the
session-scoped conftest fixture and secures *one* alarm, so the shared
fixture stays untouched.
"""

from __future__ import annotations

import copy

import pytest
from homeassistant.components.alarm_control_panel.const import CodeFormat
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.loxone.alarm_control_panel import alarm_code_format
from custom_components.loxone.const import (
    ALARM_CODE_FORMAT_NUMBER,
    ALARM_CODE_FORMAT_TEXT,
    CONF_ALARM_CODE_FORMAT,
    DOMAIN,
)

# The two alarms of the shared fixture (both unsecured there).  "Home Alarm"
# lacks the optional states; "Entrance Alarm" carries the full set.
SECURED_UUID = "63746c3a-019c-96d6-ffff-0416c61726d30001c1"  # Entrance Alarm
UNSECURED_UUID = "63746c3a-01a7-96d6-ffff-0416c61726d3000167"  # Home Alarm


@pytest.fixture
def loxapp3(loxapp3) -> dict:
    """The shared fixture with *Entrance Alarm* secured.

    A module fixture may request the conftest fixture of the same name; the
    conftest one is session-scoped, so it is deep-copied before the override
    mutates it (``test_fan_alarm_media.py`` mutates the same dict and
    restores it -- a shared instance must not see this edit).
    """
    data = copy.deepcopy(loxapp3)
    assert data["controls"][SECURED_UUID]["isSecured"] is False, "the shared fixture changed"
    data["controls"][SECURED_UUID]["isSecured"] = True
    return data


def _alarm_entity_id(hass, unique_id: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("alarm_control_panel", DOMAIN, unique_id)
    assert entity_id, f"no alarm entity for {unique_id}"
    return entity_id


async def _setup(hass, mock_connection, mock_entry, **option):
    mock_entry.add_to_hass(hass)
    if option:
        hass.config_entries.async_update_entry(mock_entry, options={**mock_entry.options, **option})
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED
    return mock_entry


# ---------------------------------------------------------------------- #
# the pure mapping, hand-derived from the WP decision (no option value is
# ever handed back unexamined)
# ---------------------------------------------------------------------- #


def test_alarm_code_format_table() -> None:
    """Secured: option picks the input, anything but ``number`` is text.

    Unsecured: no code input at all, whatever the option says."""
    assert alarm_code_format(True, ALARM_CODE_FORMAT_TEXT) is CodeFormat.TEXT
    assert alarm_code_format(True, ALARM_CODE_FORMAT_NUMBER) is CodeFormat.NUMBER
    assert alarm_code_format(True, None) is CodeFormat.TEXT
    assert alarm_code_format(True, "nonsense") is CodeFormat.TEXT
    assert alarm_code_format(False, ALARM_CODE_FORMAT_TEXT) is None
    assert alarm_code_format(False, ALARM_CODE_FORMAT_NUMBER) is None


# ---------------------------------------------------------------------- #
# entity state attributes per option (secured alarm secured by the fixture
# override above, unsecured alarm untouched)
# ---------------------------------------------------------------------- #


async def test_secured_alarm_defaults_to_text_without_the_option(hass, mock_connection, mock_entry) -> None:
    """An entry that predates the option (key absent) gets the text field.

    The keypad is no longer the fallback: a letter password must stay usable
    without anyone finding the option (upstream d13ef23)."""
    assert CONF_ALARM_CODE_FORMAT not in mock_entry.options
    await _setup(hass, mock_connection, mock_entry)

    state = hass.states.get(_alarm_entity_id(hass, SECURED_UUID))
    assert state is not None
    assert state.attributes["code_format"] == "text"
    assert state.attributes["code_arm_required"] is True


async def test_option_number_selects_the_keypad_and_text_the_field(hass, mock_connection, mock_entry) -> None:
    """The state attribute follows the stored option both ways."""
    await _setup(hass, mock_connection, mock_entry, **{CONF_ALARM_CODE_FORMAT: ALARM_CODE_FORMAT_NUMBER})
    secured = _alarm_entity_id(hass, SECURED_UUID)
    assert hass.states.get(secured).attributes["code_format"] == "number"

    hass.config_entries.async_update_entry(
        mock_entry, options={**mock_entry.options, CONF_ALARM_CODE_FORMAT: ALARM_CODE_FORMAT_TEXT}
    )
    await hass.async_block_till_done()  # the update listener reloads the entry
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED
    assert hass.states.get(secured).attributes["code_format"] == "text"


async def test_unsecured_alarm_asks_no_code_whatever_the_option(hass, mock_connection, mock_entry) -> None:
    """PC-06 stands regardless: unsecured keeps ``None`` / not required."""
    await _setup(hass, mock_connection, mock_entry)
    unsecured = _alarm_entity_id(hass, UNSECURED_UUID)
    for value in (ALARM_CODE_FORMAT_NUMBER, ALARM_CODE_FORMAT_TEXT):
        hass.config_entries.async_update_entry(
            mock_entry, options={**mock_entry.options, CONF_ALARM_CODE_FORMAT: value}
        )
        await hass.async_block_till_done()  # the update listener reloads the entry
        assert hass.config_entries.async_get_entry(mock_entry.entry_id).state is ConfigEntryState.LOADED
        state = hass.states.get(unsecured)
        assert state is not None
        assert state.attributes["code_format"] is None
        assert state.attributes["code_arm_required"] is False


# ---------------------------------------------------------------------- #
# the options form: stores the choice, reloads, never flips a stored value
# ---------------------------------------------------------------------- #


async def _submit_options(hass, entry, **overrides):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    user_input = {
        "generate_groups": False,
        "generate_scenes": False,
        "generate_scenes_delay": 3,
        "generate_lightcontroller_subcontrols": False,
        CONF_ALARM_CODE_FORMAT: ALARM_CODE_FORMAT_TEXT,
        **overrides,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # the stored change reloads the entry


async def test_options_flow_stores_choice_and_attribute_follows(hass, mock_connection, mock_entry) -> None:
    """Drive the real options flow: pick ``number``, the keypad appears."""
    await _setup(hass, mock_connection, mock_entry)
    secured = _alarm_entity_id(hass, SECURED_UUID)
    assert hass.states.get(secured).attributes["code_format"] == "text"

    await _submit_options(hass, mock_entry, **{CONF_ALARM_CODE_FORMAT: ALARM_CODE_FORMAT_NUMBER})

    assert hass.config_entries.async_get_entry(mock_entry.entry_id).options[CONF_ALARM_CODE_FORMAT] == "number"
    assert hass.states.get(secured).attributes["code_format"] == "number"


async def test_options_form_defaults_to_stored_value(hass, mock_connection, mock_entry) -> None:
    """CORE-15 for the new option: saving the form must not flip an
    explicitly stored ``number`` -- the form pre-selects the stored value."""
    await _setup(hass, mock_connection, mock_entry, **{CONF_ALARM_CODE_FORMAT: ALARM_CODE_FORMAT_NUMBER})

    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]
    defaults = {}
    for k in schema.schema:
        value = k.default
        defaults[str(k)] = value() if callable(value) else value
    assert defaults[CONF_ALARM_CODE_FORMAT] == ALARM_CODE_FORMAT_NUMBER

    # "Submit without touching anything": the form re-sends the stored value,
    # and a second save keeps it (the async_update_entry merge preserves it
    # even for foreign keys, but the pre-selected default is what the user
    # actually sees and saves).
    await _submit_options(hass, mock_entry, **{CONF_ALARM_CODE_FORMAT: ALARM_CODE_FORMAT_NUMBER})
    assert hass.config_entries.async_get_entry(mock_entry.entry_id).options[CONF_ALARM_CODE_FORMAT] == "number"


# ---------------------------------------------------------------------- #
# the actual lockout fix: a letter code travels over the secured channel
# ---------------------------------------------------------------------- #


async def test_arm_secured_alarm_with_letter_code_through_service(hass, mock_connection, mock_entry) -> None:
    """``code="ab12"`` on a secured alarm reaches the secured websocket
    command verbatim (the text field exists so letters are typeable; the
    service accepts them, the hash is over the string)."""
    await _setup(hass, mock_connection, mock_entry)
    secured = _alarm_entity_id(hass, SECURED_UUID)

    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_away",
        {"entity_id": secured, "code": "ab12"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert {"uuid": SECURED_UUID, "value": "delayedon/1", "code": "ab12", "secured": True} in mock_connection.sent

    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": secured, "code": "passWord1"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert {"uuid": SECURED_UUID, "value": "off", "code": "passWord1", "secured": True} in mock_connection.sent
