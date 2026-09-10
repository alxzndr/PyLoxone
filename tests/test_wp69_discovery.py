"""WP-6.9 regression tests: config-flow discovery via ``pyloxone_api.discover``.

The Miniserver can be found on the LAN with the (documented) LoxLIVE UDP
broadcast: client sends ``b"\x00"`` to ``255.255.255.255:7070``, the
Miniserver replies on ``7071`` with
``LoxLIVE:<version> <ip>:<port> (Mac ...) Hardware: ...``.  This WP wires
that into the setup form: the first render of the user step runs ONE
best-effort probe and prefills host/port; the probe can never block
manual entry (no answer / blocked UDP / malformed reply -> blank form),
and a user-submitted value always wins over the prefilled one.

Deliberately NOT an HA ``zeroconf`` step: the Miniserver runs mDNS but
advertises no Loxone mDNS service, so there is no service type a
manifest hook could match on.

All expected values are hand-derived (the LoxLIVE response format, the
2-second probe budget pinned as a literal, DEFAULT_PORT = 8080) — none
are produced by calling the code under test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol
from custom_components.loxone import config_flow
from custom_components.loxone.const import DEFAULT_PORT, DISCOVERY_WAIT, DOMAIN
from custom_components.loxone.pyloxone_api.discover import parse_discovery_response
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType

# The serial seeded by the WP-0.2 ``mock_connection`` fixture from
# tests/fixtures/LoxAPP3.json (same literal asserted in test_config_flow.py).
SERIAL = "TEST-SERIAL-0001"

USER_DATA = {
    "host": "192.168.10.20",
    "port": 8080,
    "username": "admin",
    "password": "s3cr3t",
    "verify_ssl": True,
}

HAND_DISCOVERED: tuple[str, int] = ("192.168.178.42", 4711)


def _form_defaults(form) -> dict:
    """Explicit `{key: default}` pairs read off the form schema's markers.

    HA passes the prefilled defaults in the markers themselves (the
    widgets don't store them), so a test screens the marker objects
    directly rather than the rendered widgets.
    """
    return {mark.schema: mark.default() for mark in form["data_schema"].schema if isinstance(mark, vol.Marker)}


class _RejectingLoxoneConnection:
    """Constructor-compatible stub whose ``open`` refuses, so the flow
    returns to the form — enough for render-only tests (no entry is
    created, so HA never sets one up)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.structure_file: dict = {}

    async def open(self, session=None) -> None:
        raise ConnectionRefusedError("refused")

    async def close(self) -> None:
        return None


async def _no_answer(wait: int = 5):
    return None


async def _malformed_answer(wait: int = 5):
    return [1, 2]


async def _wrong_miniserver(wait: int = 5):
    return ("10.255.0.1", 9999)


# ---------------------------------------------------------------------- #
# parse_discovery_response (pure)
# ---------------------------------------------------------------------- #


def test_parse_discovery_response_hand_valid():
    # Hand-derived from the documented LoxLIVE format.
    assert parse_discovery_response("LoxLIVE:8.75 192.168.178.42:80 (Mac 00:18:BC:27:87:B2:2C) Hardware: Pro") == (
        "192.168.178.42",
        80,
    )


def test_parse_discovery_response_bare_announcement():
    assert parse_discovery_response("LoxLIVE:1.2.3.4 10.0.0.7:8080 ") == ("10.0.0.7", 8080)


def test_parse_discovery_response_requires_space_after_port():
    # The observed format carries "(Mac ...)" after the port; without the
    # trailing space the parser must not guess at a port that is really
    # the start of the next field.
    assert parse_discovery_response("LoxLIVE:8.75 192.168.178.42:80") is None
    assert parse_discovery_response("LoxLIVE:8.75 192.168.178.42:80(Mac 00:18:BC)") is None


@pytest.mark.parametrize(
    "response",
    [
        "",
        "HTTP/1.1 200 OK",
        "LoxLIVE",
        "NOTLOXLIVE:1.0 192.168.1.2:80 ",
        "lives out:1.0 192.168.1.2:80 ",
        "garbage LoxLIVE:1.0 192.168.1.2:80 ",  # the announcement must start right after any whitespace-free prefix
        b"LoxLIVE:8.75 192.168.178.42:80 (Mac 00:18:BC:27:87:B2:2C)",
        7,
    ],
)
def test_parse_discovery_response_rejects_non_payload(response):
    assert parse_discovery_response(response) is None


@pytest.mark.parametrize(
    "response,comment",
    [
        ("LoxLIVE:1 300.1.2.3:80 ", "octet 300 is not an IPv4 octet"),
        ("LoxLIVE:1 192.168.1.2:70000 ", "port above 65535"),
        ("LoxLIVE:1 2001:db8::1:80 ", "no IPv4 address in the payload"),
    ],
)
def test_parse_discovery_response_rejects_unusable_values(response, comment):
    assert parse_discovery_response(response) is None, comment


def test_parse_discovery_response_boundary_values():
    # 255 is the largest legal octet, 65535 the largest legal port.
    assert parse_discovery_response("LoxLIVE:1 255.255.255.255:65535 ") == ("255.255.255.255", 65535)


# ---------------------------------------------------------------------- #
# _discovered_prefill (pure — hand-derived literals)
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "found,expected",
    [
        (("192.168.178.42", 80), ("192.168.178.42", 80)),
        (("192.168.178.42", 65535), ("192.168.178.42", 65535)),
        (None, None),
        (("192.168.178.42",), None),  # wrong arity
        (("", 80), None),  # empty host
        ((None, 80), None),  # host is not a string
        ((7, 80), None),  # host is not a string
        (("1.2.3.4", None), None),  # port is not an int
        (("1.2.3.4", 0), None),  # port below 1
        (("1.2.3.4", 65536), None),  # port above 65535
        (("1.2.3.4", True), None),  # a bool is not a usable port
        (("1.2.3.4", 80.0), None),  # a float is not a usable port
        (([1, 2],), None),  # not a (host, port) pair
    ],
)
def test_discovered_prefill(found, expected):
    assert config_flow._discovered_prefill(found) == expected


# ---------------------------------------------------------------------- #
# flow behaviour
# ---------------------------------------------------------------------- #


async def test_user_form_prefills_discovered_host_and_port(hass, mock_connection):
    """A Miniserver answering the LoxLIVE probe prefills host AND port in
    the first form; submitting those values stores them in the entry,
    and the entry's unique id is still the serial, not the address."""
    calls: list[Any] = []

    async def fake_discover(wait: int = 5):
        calls.append(wait)
        return HAND_DISCOVERED

    with patch.object(config_flow, "loxone_broadcast_discover", fake_discover):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert form["type"] is FlowResultType.FORM
        assert form["step_id"] == "user"

    # Hand-derived: exactly what the fake probe answered.
    defaults = _form_defaults(form)
    assert defaults[CONF_HOST] == "192.168.178.42"
    assert defaults[CONF_PORT] == 4711
    # The probe ran exactly once, with the pinned 2-second budget.
    assert calls == [2]

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            "host": HAND_DISCOVERED[0],
            "port": HAND_DISCOVERED[1],
            "username": "admin",
            "password": "s3cr3t",
            "verify_ssl": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data == {
        "host": "192.168.178.42",
        "port": 4711,
        "username": "admin",
        "password": "s3cr3t",
        "verify_ssl": True,
    }
    assert result["result"].unique_id == SERIAL
    # Nothing after the first render re-broadcast.
    assert calls == [2]


async def test_user_form_blank_when_discovery_finds_nothing(hass, enable_custom_integrations):
    """No answer on the LAN: the form opens exactly as it did before this
    feature (empty host, DEFAULT_PORT = 8080) and nothing is created."""
    with (
        patch.object(config_flow, "LoxoneConnection", _RejectingLoxoneConnection),
        patch.object(config_flow, "loxone_broadcast_discover", _no_answer),
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "user"
    defaults = _form_defaults(form)
    assert defaults[CONF_HOST] == ""
    assert defaults[CONF_PORT] == DEFAULT_PORT
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_form_blank_when_discovery_raises(hass, mock_connection):
    """A probe that raises (blocked UDP, port 7071 occupied, bad decode)
    must never block manual entry: blank form, the flow still completes
    with the manually typed address."""

    async def broken_discover(wait: int = 5):
        raise OSError("Network is unreachable")

    with patch.object(config_flow, "loxone_broadcast_discover", broken_discover):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert form["type"] is FlowResultType.FORM
        defaults = _form_defaults(form)
        assert defaults[CONF_HOST] == ""
        assert defaults[CONF_PORT] == DEFAULT_PORT

        result = await hass.config_entries.flow.async_configure(form["flow_id"], USER_DATA)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data == USER_DATA


async def test_user_form_blank_when_discovery_answer_malformed(hass, enable_custom_integrations):
    """A probe answer that is not a usable (host, port) pair means a blank
    form, not a crash."""
    with (
        patch.object(config_flow, "LoxoneConnection", _RejectingLoxoneConnection),
        patch.object(config_flow, "loxone_broadcast_discover", _malformed_answer),
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert form["type"] is FlowResultType.FORM
    defaults = _form_defaults(form)
    assert defaults[CONF_HOST] == ""
    assert defaults[CONF_PORT] == DEFAULT_PORT
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_input_wins_over_discovered_values(hass, mock_connection):
    """The prefill is not sticky: whatever the user types is what is
    stored — the discovered host is ignored entirely here."""
    with patch.object(config_flow, "loxone_broadcast_discover", _wrong_miniserver):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        # The probe answered for a *different* unit ...
        defaults = _form_defaults(form)
        assert defaults[CONF_HOST] == "10.255.0.1"
        assert defaults[CONF_PORT] == 9999

        # ... and the user typed their real one.
        result = await hass.config_entries.flow.async_configure(form["flow_id"], USER_DATA)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data == USER_DATA


async def test_probe_runs_once_per_flow(hass, enable_custom_integrations):
    """One probe per flow instance: the first render runs it; the form
    that comes back after a rejected credential reuses the cached result
    (and keeps the same prefill) without broadcasting again."""
    calls: list[int] = []

    async def counting_discover(wait: int = 5):
        calls.append(wait)
        return HAND_DISCOVERED

    with (
        patch.object(config_flow, "LoxoneConnection", _RejectingLoxoneConnection),
        patch.object(config_flow, "loxone_broadcast_discover", counting_discover),
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        defaults = _form_defaults(form)
        assert defaults[CONF_HOST] == "192.168.178.42"
        assert calls == [DISCOVERY_WAIT]

        again = await hass.config_entries.flow.async_configure(form["flow_id"], USER_DATA)
        assert again["type"] is FlowResultType.FORM
        assert again.get("errors") == {"base": "cannot_connect"}
        # Prefill survived the error re-render, no second broadcast.
        assert _form_defaults(again)[CONF_HOST] == "192.168.178.42"
        assert calls == [DISCOVERY_WAIT]

    assert calls == [DISCOVERY_WAIT]


async def test_reauth_flow_does_not_probe(hass, mock_connection):
    """The reauth flow takes its values from the stored entry; it must
    not broadcast (in a multi-Miniserver home, the answer would likely
    come from the wrong unit)."""
    probes: list[int] = []

    async def counting_discover(wait: int = 5):
        probes.append(wait)
        return None

    with patch.object(config_flow, "loxone_broadcast_discover", counting_discover):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        created = (await hass.config_entries.flow.async_configure(form["flow_id"], USER_DATA))["result"]
        # The user flow probed exactly once (its first form render) ...
        assert probes == [DISCOVERY_WAIT]

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reauth", "entry_id": created.entry_id}, data=created.data
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # ... and the reauth flow added no new probe.
    assert probes == [DISCOVERY_WAIT]
