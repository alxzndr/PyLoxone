"""Tests for Loxone config entry migrations (v1..v4 -> v5).

Version 5 (CORE-19 / WP-3.4) moves the connection keys from ``options`` to
the entry ``data``; ``options`` keeps only the preference keys.  The
expected end states below are hand-derived from the plan ("credentials
location": host/port/user/password/verify_ssl live in ``data``,
preferences stay in options), not read from the migration code.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.loxone import async_migrate_entry
from custom_components.loxone.const import (
    CONF_GENERATE_GROUPS,
    CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN,
    CONF_SCENE_GEN,
    CONF_SCENE_GEN_DELAY,
    DEFAULT_DELAY_SCENE,
)


class _ConfigEntries:
    def __init__(self) -> None:
        self.calls = []

    def async_update_entry(self, entry, **changes) -> None:
        self.calls.append(changes)
        for key, value in changes.items():
            setattr(entry, key, value)


def _make_entry(version, options=None, data=None) -> SimpleNamespace:
    return SimpleNamespace(
        version=version,
        options=dict(options or {}),
        data=dict(data or {}),
        entry_id="test-entry",
    )


CONNECTION_KEYS = ("host", "port", "username", "password", "verify_ssl")


def _v4_options() -> dict:
    return {
        "host": "192.0.2.1",
        "port": 8080,
        "username": "admin",
        "password": "secret",
        "verify_ssl": True,
        CONF_SCENE_GEN: False,
        CONF_SCENE_GEN_DELAY: 3,
        CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN: True,
        CONF_GENERATE_GROUPS: True,  # a WP-3.3-era option that must survive
    }


def _assert_migrated_v5(entry, config_entries) -> None:
    assert entry.version == 5
    # connection keys moved into data
    assert entry.data["host"] == "192.0.2.1"
    assert entry.data["port"] == 8080
    assert entry.data["username"] == "admin"
    assert entry.data["password"] == "secret"
    assert entry.data["verify_ssl"] is True
    # ...and left options entirely
    for key in CONNECTION_KEYS:
        assert key not in entry.options, f"{key!r} must not remain in options"
    assert len(config_entries.calls) == 1
    # the update carries both the cleaned options and the new data
    call = config_entries.calls[0]
    assert call["version"] == 5
    assert "data" in call and "options" in call


def test_version_four_moves_connection_keys_to_data() -> None:
    """v4 -> v5: connection keys move from options to data, preferences
    stay, a token already in data is preserved and the entry is written
    exactly once."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    options = _v4_options()
    entry = _make_entry(
        4,
        options=options,
        data={"token": "tok", "hash_alg": "SHA256", "valid_until": 1234567},
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    _assert_migrated_v5(entry, config_entries)
    assert entry.data["token"] == "tok"  # pre-existing data preserved
    # preferences survive unchanged
    assert entry.options[CONF_SCENE_GEN] is False
    assert entry.options[CONF_SCENE_GEN_DELAY] == 3
    assert entry.options[CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN] is True
    assert entry.options[CONF_GENERATE_GROUPS] is True


def test_version_four_partial_options_get_defaults_in_data() -> None:
    """Missing connection keys are filled in data with their documented
    defaults (port 8080, verify_ssl True, empty strings) instead of being
    dropped -- the coordinator reads these keys unconditionally."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = _make_entry(4, options={"username": "admin", "password": "secret", "host": "10.0.0.5"})

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert entry.version == 5
    assert entry.data["host"] == "10.0.0.5"
    assert entry.data["port"] == 8080  # DEFAULT_PORT
    assert entry.data["verify_ssl"] is True  # DEFAULT_VERIFY_SSL
    assert entry.data["username"] == "admin"
    assert entry.data["password"] == "secret"


def test_version_one_migrates_through_all_versions_in_one_update() -> None:
    """The v1 -> v5 chain (lightcontroller default, scene delay, verify_ssl,
    then the data move) ends in a single update call at version 5."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = _make_entry(1, options={"host": "192.0.2.1", "username": "admin", "password": "secret"})

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    _assert_migrated_v5(entry, config_entries)
    # v1 additions land in options (pure preferences)
    assert entry.options[CONF_LIGHTCONTROLLER_SUBCONTROLS_GEN] is True
    assert entry.options[CONF_SCENE_GEN_DELAY] == DEFAULT_DELAY_SCENE
    # verify_ssl moved to data in v5 (asserted above via the migration checks)


def test_existing_v3_migration_still_runs() -> None:
    """The old v3 behaviour (verify_ssl default) is preserved on the way
    up to v5 - a saved value is not overwritten by the v3 step."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = _make_entry(3, options=_v4_options())

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    _assert_migrated_v5(entry, config_entries)


def test_current_version_does_not_update_entry() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = _make_entry(5, options={}, data={})

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == []


def test_v4_unique_id_is_not_guessed_by_migration() -> None:
    """CORE-19: migration cannot know the Miniserver serial.  An entry that
    already has a unique_id keeps it; one without keeps ``None`` (setup
    stamps it from ``msInfo.serialNr`` on the next successful connect)."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)

    with_id = _make_entry(4, options=_v4_options(), data={})
    with_id.unique_id = "MS-SERIAL-OLD"
    asyncio.run(async_migrate_entry(hass, with_id))
    assert with_id.unique_id == "MS-SERIAL-OLD"

    config_entries.calls.clear()
    without_id = _make_entry(4, options=_v4_options(), data={})
    without_id.unique_id = None
    asyncio.run(async_migrate_entry(hass, without_id))
    assert without_id.unique_id is None
    # ...and the unique_id is never smuggled into the data write
    for call in config_entries.calls:
        assert "unique_id" not in call
