"""Shared fixtures for the Loxone integration test tree (WP-0.2).

`loxapp3`, `mock_connection`, `mock_entry` are the primitives the later Phase-1
HA-harness tests build on. `enable_custom_integrations` is re-defined here (not
the phcc stock fixture) so it also re-roots `custom_components` at the repo's
own `custom_components/`. Phcc 0.13.355's stock fixture only clears the cache;
its `hass` fixture pins `custom_components.__path__` at phcc's `testing_config`
directory, which would make `custom_components.loxone` un-importable in tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def loxapp3() -> dict:
    """Load the static LoxAPP3.json fixture (the shape of `api.structure_file`)."""
    return json.loads((FIXTURES_DIR / "LoxAPP3.json").read_text())


@pytest.fixture
def enable_custom_integrations(hass):
    """Re-points ``custom_components`` at the repo ``custom_components/`` and
    clears the loader integration cache.

    Used by any test that instantiates / sets up the Loxone integration (and
    by ``mock_connection``, which imports from ``custom_components.loxone``).
    """
    import custom_components

    from homeassistant import loader

    original = list(custom_components.__path__)
    custom_components.__path__ = [str(REPO_ROOT / "custom_components")]
    hass.data.pop(loader.DATA_CUSTOM_COMPONENTS)
    yield
    custom_components.__path__ = original


@pytest.fixture
def mock_connection(hass, loxapp3, enable_custom_integrations):
    """Patch `LoxoneConnection.open` to skip auth and seed `structure_file`.

    Yields an object whose only public method is `feed(uuid, value)`, which
    fires the `loxone_event` bus event that entities currently subscribe to
    (after WP-3.2 this becomes the per-uuid dispatcher; `feed` returns the
    coordinator/loxx event the integration publishes).
    """
    from custom_components.loxone.const import EVENT
    from custom_components.loxone.pyloxone_api.connection import LoxoneConnection

    async def _fake_open(self, session=None):
        # Skip all network: seed the structure file + a fake version, and
        # inject a mock websocket so `is_connected` reads True (the property
        # checks `connection.protocol.state.name == "OPEN"`).
        self.structure_file = loxapp3
        self.miniserver_version = loxapp3.get("softwareVersion")
        self.connected = True
        self.connection = Mock()
        self.connection.protocol.state.name = "OPEN"
        # A truthy 32-byte key so the reconnection/heartbeat task's
        # `if not self._session_key:` guard skips the (unmocked) AES key exchange.
        self._session_key = b"\x00" * 32
        self.event_bus = namespace
        return self

    namespace = SimpleNamespace(hass=hass)

    def feed(uuid: str, value) -> None:
        # Current state entry point (post-WP-3.2): a single bus event whose
        # `data` maps uuid -> value. After WP-3.2 this routes through
        # async_dispatcher_send(hass, f"loxone_{entry_id}_{uuid}", value).
        namespace.hass.bus.async_fire(EVENT, {uuid: value})

    namespace.feed = feed

    async def _fake_listen(self, callback=None):
        # Do NOT start a real websocket recv loop; state updates arrive via `feed`.
        # Returning False keeps the integration from spawning the message task.
        return None

    async def _fake_close(self, *args, **kwargs):
        return None

    async def _fake_send(self, entity_uuid, value, *args, **kwargs):
        return None

    with (
        patch.object(LoxoneConnection, "open", new=_fake_open),
        patch.object(LoxoneConnection, "start_listening", new=_fake_listen),
        patch.object(LoxoneConnection, "close", new=_fake_close),
        patch.object(LoxoneConnection, "send_websocket_command", new=_fake_send),
    ):
        yield namespace


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """A version-4 config entry with the BASE (non-migrated) options.

        `generate_scenes` is `False` by default: it schedules a background 3s
    timer in `scene.py` that would outlive the test event loop (a *lingering timer*
    error in phcc). Scene-relation WPs (i.e. WP-1.3) re-enable it explicitly.
    """
    return MockConfigEntry(
        domain="loxone",
        version=4,
        data={},
        options={
            "host": "loxberry.local",
            "port": 8080,
            "username": "admin",
            "password": "secret",
            "verify_ssl": True,
            "generate_scenes": False,
            "generate_scenes_delay": 3,
            "generate_lightcontroller_subcontrols": False,
        },
        unique_id="TEST-SERIAL-0001",
    )
