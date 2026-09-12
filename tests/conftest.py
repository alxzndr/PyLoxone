"""Shared fixtures for the Loxone integration test tree (WP-0.2).

`loxapp3`, `mock_connection`, `mock_entry` are the primitives the later Phase-1
HA-harness tests build on. `enable_custom_integrations` is re-defined here (not
the phcc stock fixture) so it also re-roots `custom_components` at the repo's
own `custom_components/`. Phcc 0.13.355's stock fixture only clears the cache;
its `hass` fixture pins `custom_components.__path__` at phcc's `testing_config`
directory, which would make `custom_components.loxone` un-importable in tests.
"""

from __future__ import annotations

import asyncio
import inspect
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


@pytest.fixture(autouse=True)
def loxone_flow_discovery_off():
    """WP-6.9: the config flow's LoxLIVE broadcast probe is off in tests.

    In production the first render of the user step broadcasts a one-byte
    payload to 255.255.255.255:7070 (``pyloxone_api.discover``).  Test
    machines must not fire real broadcasts onto a LAN (nor spend a 2-second
    no-answer window in every flow test): this pins
    ``config_flow.loxone_broadcast_discover`` to "no answer", which is
    exactly the pre-WP-6.9 behaviour the rest of the suite keeps testing.
    ``tests/test_wp69_discovery.py`` repatches the same attribute per test
    to exercise the probe itself.
    """
    import custom_components

    original_path = list(custom_components.__path__)
    custom_components.__path__ = [str(REPO_ROOT / "custom_components")]
    try:
        from custom_components.loxone import config_flow
    except ImportError:
        custom_components.__path__ = original_path
        return

    async def _no_answer(wait: int = 5):
        return None

    with patch.object(config_flow, "loxone_broadcast_discover", _no_answer):
        yield
    custom_components.__path__ = original_path


@pytest.fixture
def mock_connection(hass, loxapp3, enable_custom_integrations):
    """Patch `LoxoneConnection.open` to skip auth and seed `structure_file`.

    Yields an object whose only public method is `feed(uuid, value)`; it
    routes a single state message through the loaded entry's coordinator
    :meth:`LoxoneCoordinator.handle_message` — the one place where the
    public ``loxone_event`` bus event (for user automations, CORE-27) and
    the entry-scoped per-uuid dispatcher signal (entities, PS-13) are
    both emitted.  ``namespace.sent`` / ``namespace.feed_calls`` record
    outbound sends and fed messages for assertion.
    """
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

    namespace = SimpleNamespace(hass=hass, drops=0, current_session=None, sent=[], feed_calls=[])

    def drop() -> None:
        """Drop the current stub socket mid-session, like a Miniserver
        restart. ``LoxoneConnection.run`` must close, flip entities
        unavailable, and come back as a fresh live session (which stays
        live until the next ``drop()``)."""
        namespace.drops += 1
        session = namespace.current_session
        namespace.current_session = None
        if session is not None:
            session.set()

    namespace.drop = drop

    def feed(uuid: str, value) -> None:
        """Feed one state message into the loaded loxone entry.

        Goes through ``coordinator.handle_message`` (the production message
        path): this fires the public ``loxone_event`` bus event *and*
        dispatches ``loxone_{entry_id}_{uuid}`` with ``value`` — so an
        entry's entities only ever see their own state (CORE-27).
        """
        for entry in hass.config_entries.async_entries("loxone"):
            # CORE-31 (WP-5.2): the coordinator is on ``runtime_data``,
            # not in ``hass.data["loxone"]``.
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is not None and hasattr(coordinator, "handle_message"):
                coordinator.handle_message({uuid: value})
                namespace.feed_calls.append((uuid, value))
                return
        raise AssertionError("no loaded loxone entry available for feed()")

    namespace.feed = feed

    async def _fake_listen(self, callback=None):
        # Emulate a *live* websocket session against the API-09
        # ``LoxoneConnection.run`` supervisor: signal the session as
        # authenticated (the moment run() flips ``on_state(True)``)
        # and stay connected until the test drops the socket
        # (``namespace.drop()``) or the task is cancelled.
        session = asyncio.Event()
        namespace.current_session = session
        self._authenticated_event.set()
        await session.wait()

    async def _fake_run(self, on_state, callback=None):
        # WP-2.3 moved the supervisor from start_listening() to run(on_state).
        # Emulate it faithfully: a *loop* that brings a session up, announces
        # the connection (entities are `unavailable` until this fires, and HA
        # skips unavailable entities in entity-service dispatch), waits for the
        # test to drop it, announces the drop, and reconnects -- exactly what
        # API-09 does instead of reloading the config entry.
        async def _signal(connected):
            result = on_state(connected)
            if inspect.isawaitable(result):
                await result

        try:
            while True:
                session = asyncio.Event()
                namespace.current_session = session
                self._authenticated_event.set()
                await _signal(True)
                await session.wait()
                namespace.current_session = None
                await _signal(False)
                # The real supervisor backs off between attempts. Keep a short
                # but observable down window here: without it the session is
                # back before a test can see that it ever went away.
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            await _signal(False)
            raise

    async def _fake_close(self, *args, **kwargs):
        self.connection = None

    async def _fake_send(self, entity_uuid, value, *args, **kwargs):
        namespace.sent.append({"uuid": entity_uuid, "value": value, "code": kwargs.get("code")})

    async def _fake_send_secured(self, entity_uuid, value, code, *args, **kwargs):
        namespace.sent.append({"uuid": entity_uuid, "value": value, "code": code, "secured": True})

    with (
        patch.object(LoxoneConnection, "open", new=_fake_open),
        patch.object(LoxoneConnection, "start_listening", new=_fake_listen),
        patch.object(LoxoneConnection, "run", new=_fake_run),
        patch.object(LoxoneConnection, "close", new=_fake_close),
        patch.object(LoxoneConnection, "send_websocket_command", new=_fake_send),
        patch.object(LoxoneConnection, "send_secured_websocket_command", new=_fake_send_secured),
        patch.object(LoxoneConnection, "send_secured__websocket_command", new=_fake_send_secured),
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
