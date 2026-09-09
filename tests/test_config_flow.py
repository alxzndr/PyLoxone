"""Tests for the PyLoxone config flow (CORE-19, CORE-18; reauth).

Happy paths run against the WP-0.2 ``mock_connection`` fixture, which
patches ``LoxoneConnection.open``/``close`` at the class level — important
because HA *sets up the entry itself* right after ``async_create_entry``
returns, and that second connection must not touch the network either.
The fake seeds the structure file from ``LoxAPP3.json``, whose
``msInfo.serialNr`` is the serial asserted below.

Failure paths patch the ``LoxoneConnection`` referenced by the *config
flow module* (no entry is created there, so no setup, so no second
connection).

All expected values are hand-derived from the plan (entry version 5, the
fixture serial, the preference defaults), not read from the flow's code.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.loxone import config_flow
from custom_components.loxone.const import (
    CONF_GENERATE_GROUPS,
    CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN,
    CONF_SCENE_GEN,
    CONF_SCENE_GEN_DELAY,
    DOMAIN,
)
from custom_components.loxone.pyloxone_api.exceptions import LoxoneUnauthorisedError
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Here: "the serial written into tests/fixtures/LoxAPP3.json"
SERIAL = "TEST-SERIAL-0001"

USER_DATA = {
    "host": "192.168.10.20",
    "port": 8080,
    "username": "admin",
    "password": "s3cr3t",
    "verify_ssl": True,
}


class _FakeLoxoneConnection:
    """Fails ``open`` with a pre-set exception; never opens a socket."""

    fail = None
    instances: list[_FakeLoxoneConnection] = []

    def __init__(self, host, port, username, password, verify_ssl, **_kw):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.structure_file: dict = {}
        self.miniserver_serial = ""
        _FakeLoxoneConnection.instances.append(self)

    async def open(self, session=None) -> None:
        if _FakeLoxoneConnection.fail is not None:
            raise _FakeLoxoneConnection.fail
        self.structure_file = {"msInfo": {"serialNr": SERIAL}}
        self.miniserver_serial = SERIAL

    async def close(self) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.fail = None
        cls.instances = []


@pytest.fixture(autouse=True)
def _clean_state():
    _FakeLoxoneConnection.reset()
    yield
    _FakeLoxoneConnection.reset()


async def _start_user_flow(hass) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


# ---------------------------------------------------------------------- #
# user step
# ---------------------------------------------------------------------- #


async def test_user_flow_success_creates_v5_entry_with_serial(hass, mock_connection):
    """Happy path: a version-5 entry is created, the connection keys are
    *data* not options, ``unique_id`` is the observed serial, and the
    post-creation setup succeeds on the same mocked connection."""
    result = await _start_user_flow(hass)
    # Step 1: the form is shown, no entry yet.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert hass.config_entries.async_entries(DOMAIN) == []

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.version == 5
    # Hand-derived: what the user typed, normalised to canonical types.
    assert entry.data == {
        "host": "192.168.10.20",
        "port": 8080,
        "username": "admin",
        "password": "s3cr3t",
        "verify_ssl": True,
    }
    # Fresh installs start with preference-only options, groups OFF (CORE-15).
    assert dict(entry.options) == {
        CONF_GENERATE_GROUPS: False,
        CONF_SCENE_GEN: True,
        CONF_SCENE_GEN_DELAY: 3,
        CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN: False,
    }
    assert entry.unique_id == SERIAL
    # The entry HA starts setting up automatically must come up on the
    # mocked connection (no real network).
    await hass.async_block_till_done()
    from homeassistant.config_entries import ConfigEntryState

    assert entry.state is ConfigEntryState.LOADED


async def test_user_flow_cannot_connect_shows_error_and_no_entry(hass, enable_custom_integrations):
    """Open fails with a plain connection error -> the form repeats with a
    translated ``cannot_connect`` key and no entry is created."""
    _FakeLoxoneConnection.fail = ConnectionRefusedError("refused")
    with patch.object(config_flow, "LoxoneConnection", _FakeLoxoneConnection):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_DATA)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result.get("errors") == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_flow_invalid_auth_shows_translated_error(hass, enable_custom_integrations):
    """401 at the connection test -> ``invalid_auth`` (CORE-09/CORE-19)."""
    _FakeLoxoneConnection.fail = LoxoneUnauthorisedError("401")
    with patch.object(config_flow, "LoxoneConnection", _FakeLoxoneConnection):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_DATA)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result.get("errors") == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_flow_encoding_errors_are_translation_keys(hass, enable_custom_integrations):
    """CORE-18: non-latin-1 credentials produce the translation-keyed
    errors without any connection attempt."""
    with patch.object(config_flow, "LoxoneConnection", _FakeLoxoneConnection):
        form = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(form["flow_id"], dict(USER_DATA, username="u\u4f60"))
        assert result["type"] is FlowResultType.FORM
        assert result.get("errors") == {"base": "invalid_username_encoding"}

        form = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(form["flow_id"], dict(USER_DATA, password="w\u4f60"))
        assert result["type"] is FlowResultType.FORM
        assert result.get("errors") == {"base": "invalid_password_encoding"}

    assert len(_FakeLoxoneConnection.instances) == 0  # no connection attempted
    assert hass.config_entries.async_entries(DOMAIN) == []


async def _create_entry_first(hass) -> MockConfigEntry:
    """Create one version-5 entry through the flow and return it."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result["result"]


async def test_user_flow_duplicate_serial_aborts(hass, mock_connection):
    """Adding the same Miniserver a second time must abort on its serial."""
    first = await _start_user_flow(hass)
    first = await hass.config_entries.flow.async_configure(first["flow_id"], USER_DATA)
    assert first["type"] is FlowResultType.CREATE_ENTRY

    second = await _start_user_flow(hass)
    second = await hass.config_entries.flow.async_configure(second["flow_id"], USER_DATA)

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


# ---------------------------------------------------------------------- #
# reauth
# ---------------------------------------------------------------------- #


async def test_reauth_flow_updates_data_and_aborts_successful(hass, mock_connection):
    """The reauth flow re-takes the connection description, verifies it
    and updates the entry's *data* (not options) on success."""
    created = await _create_entry_first(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reauth", "entry_id": created.entry_id}, data=created.data
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "host": "192.168.10.20",
            "port": 8443,
            "username": "admin",
            "password": "new_pass_2",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    # Hand-derived: only the changed keys (password, port) are new.
    assert created.data == {
        "host": "192.168.10.20",
        "port": 8443,
        "username": "admin",
        "password": "new_pass_2",
        "verify_ssl": True,
    }
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_flow_invalid_auth_repeats_form(hass, enable_custom_integrations):
    """A rejected reauth repeats the form with ``invalid_auth`` and leaves
    the stored credentials untouched."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        unique_id=SERIAL,
        data={
            "host": "10.0.0.1",
            "port": 8080,
            "username": "admin",
            "password": "s3cr3t",
            "verify_ssl": True,
        },
        options={CONF_SCENE_GEN: False},
    )
    entry.add_to_hass(hass)
    _FakeLoxoneConnection.fail = LoxoneUnauthorisedError("401")
    with patch.object(config_flow, "LoxoneConnection", _FakeLoxoneConnection):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.10.20", "port": 8080, "username": "admin", "password": "s3cr3t"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result.get("errors") == {"base": "invalid_auth"}
    assert entry.data["password"] == "s3cr3t"


# ---------------------------------------------------------------------- #
# options flow
# ---------------------------------------------------------------------- #


async def test_options_flow_updates_preferences_only(hass, enable_custom_integrations):
    """The options form carries preference keys; connection keys stay in
    data and never appear in options (CORE-19)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        unique_id="MS-OPTS-1",
        data={
            "host": "10.0.0.1",
            "port": 8080,
            "username": "admin",
            "password": "pw",
            "verify_ssl": True,
        },
        options={CONF_SCENE_GEN: False},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    new_options = {
        CONF_GENERATE_GROUPS: False,
        CONF_SCENE_GEN: True,
        CONF_SCENE_GEN_DELAY: 3,
        CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN: True,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], new_options)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == new_options
    # Connection keys still live in data, not options.
    assert "password" not in entry.options
    assert "host" not in entry.options
    assert entry.data["password"] == "pw"
