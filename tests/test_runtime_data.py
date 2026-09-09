"""WP-5.2 regression tests: CORE-31 (``entry.runtime_data`` instead of
``hass.data[DOMAIN]``; no stale empty domain dict after the last unload).

Before the change the coordinator of every loaded entry lived in
``hass.data[DOMAIN][entry_id]``.  ``hass.data`` is never wiped per entry by
Home Assistant, so after the last unload an empty (or, worse, stale)
``hass.data[DOMAIN]`` dict survived.  The coordinator now rides on
``config_entry.runtime_data``, which HA itself removes when the entry
unloads.  These tests assert:

* the coordinator is exposed on the config entry (``runtime_data``) and the
  ``hass.data[DOMAIN]`` twin does not exist,
* ``get_miniserver_from_hass`` resolves through ``runtime_data`` (no
  ``hass.data`` lookup) — including on entries the tooling hands us that
  never had the attribute set at all,
* after unload the entry's ``runtime_data`` is gone and
  ``hass.data`` carries no stale loxone dict,
* the auth-failure bookkeeping (which *does* still use ``hass.data[DOMAIN]``
  internally, because it must survive the coordinator being re-instantiated
  on every setup attempt) removes the whole ``hass.data[DOMAIN]`` key once
  the last counter is cleared, so no stale dict survives the last unload
  even after a run of 401s.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState

from custom_components.loxone import (
    DOMAIN,
    LoxoneCoordinator,
    _clear_auth_failure,
    _record_auth_failure,
)
from custom_components.loxone.miniserver import get_miniserver_from_hass


async def test_coordinator_lives_on_runtime_data(hass, loxapp3, mock_connection, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(mock_entry.entry_id)
    assert ok
    assert mock_entry.state is ConfigEntryState.LOADED

    # The coordinator is on the entry, not under hass.data[DOMAIN].
    assert isinstance(mock_entry.runtime_data, LoxoneCoordinator)
    assert mock_entry.entry_id not in hass.data.get(DOMAIN, {})
    # get_miniserver_from_hass resolves via runtime_data.
    ms = get_miniserver_from_hass(hass, mock_entry)
    assert ms is mock_entry.runtime_data.miniserver
    assert ms is not None


async def test_unload_leaves_no_runtime_data_and_no_stale_domain_dict(
    hass, loxapp3, mock_connection, mock_entry
) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()

    # HA deletes runtime_data when the entry reports an unload OK.
    assert getattr(mock_entry, "runtime_data", None) is None
    # CORE-31's second complaint: no stale empty dict under hass.data.
    assert DOMAIN not in hass.data


async def test_get_miniserver_from_hass_degrades_without_runtime_data(hass) -> None:
    """An entry whose ``runtime_data`` was never set (never set up, or
    already unloaded by HA) must yield ``None``, not a ``KeyError``."""
    entry_with_none = SimpleNamespace(entry_id="x-none", runtime_data=None)
    assert get_miniserver_from_hass(hass, entry_with_none) is None

    # ...and an entry object that lacks the *attribute* entirely.
    entry_without_attr = SimpleNamespace(entry_id="x-attr")
    assert get_miniserver_from_hass(hass, entry_without_attr) is None


class _FakeHass:
    """Just the ``hass.data`` slice the helpers touch."""

    def __init__(self):
        self.data = {}


class _FakeEntry:
    entry_id = "entry-x"


def test_auth_failure_bookkeeping_drop_stale_domain_dict() -> None:
    """While any entry counts failures, ``hass.data[DOMAIN][auth_failures]``
    exists; when the last counter is cleared the whole ``hass.data[DOMAIN]``
    key must go away (CORE-31: no stale dict after the last unload)."""
    hass = _FakeHass()
    entry = _FakeEntry()

    # A run of 401s creates the store.
    _record_auth_failure(hass, entry, now=100.0)
    _record_auth_failure(hass, entry, now=200.0)
    assert DOMAIN in hass.data

    # Clearing the only counter removes the entire domain key.
    _clear_auth_failure(hass, entry)
    assert DOMAIN not in hass.data


def test_auth_failure_bookkeeping_two_entries() -> None:
    """Clearing one entry while the other still counts keeps the store;
    clearing the second one drops it entirely."""
    hass = _FakeHass()
    entry_a = SimpleNamespace(entry_id="entry-a")
    entry_b = SimpleNamespace(entry_id="entry-b")

    _record_auth_failure(hass, entry_a, now=100.0)
    _record_auth_failure(hass, entry_b, now=101.0)
    _clear_auth_failure(hass, entry_a)
    assert DOMAIN in hass.data  # entry_b still counting

    _clear_auth_failure(hass, entry_b)
    assert DOMAIN not in hass.data
