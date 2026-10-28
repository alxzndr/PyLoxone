"""Tests for the ``--format web`` view of the pyloxone-cli (upstream ecf4d65).

The page is rendered entirely in Python (aiohttp) and uses a plain HTML
``meta http-equiv="refresh"`` for live values - no JavaScript anywhere.
It binds ``127.0.0.1`` only, answers only ``Host`` headers that name the
loopback on its own port (WP-7.8, DNS rebinding) and has no command
endpoint (read-only).

Fork rule (PS-27): credential states (``lastcode`` / ``lasttag`` /
``events`` of an NFC Code Touch) are redacted before they are stored,
so the page never shows their values. The non-vacuity of those tests is
recorded in the commit that adds them.
"""

from unittest.mock import AsyncMock, Mock

from aiohttp.test_utils import make_mocked_request

import custom_components.loxone.pyloxone_api.__main__ as cli

STRUCTURE = {
    "controls": {
        "<ctl-a>": {
            "name": "Fenster Buero",
            "type": "Jalousie",
            "uuidAction": "<ctl-a>",
            "states": {"up": "<uuid-up>", "position": "<uuid-pos>"},
            "subControls": {
                "<ctl-a-sub>": {
                    "name": "Sub A",
                    "uuidAction": "<ctl-a-sub>",
                    "states": {"active": "<uuid-active>"},
                }
            },
        },
        "<ctl-b>": {"name": "Radio Bad", "type": "Radio", "uuidAction": "<ctl-b>", "states": {}},
        "<ctl-reader>": {
            "name": "Gate reader",
            "type": "NfcCodeTouch",
            "uuidAction": "<ctl-reader>",
            "states": {
                "lastuser": "<uuid-lastuser>",
                "lastcode": "<uuid-lastcode>",
                "lasttag": "<uuid-lasttag>",
                "events": "<uuid-events>",
            },
        },
    },
    "autopilot": {
        "<ctl-ap>": {
            "name": "Automatik-Regeln",
            "uuidAction": "<ctl-ap>",
            "states": {"changed": "<uuid-changed>"},
        }
    },
}

SECRET_CODE = "4711"
SECRET_TAG = "04:1a:2b:3c:de:ad:be"
SECRET_EVENT = "Code 4711 entered"


def _ui(states: dict | None = None) -> cli.WebUi:
    ui = cli.WebUi(port=8999)
    ui.set_structure(STRUCTURE)
    if states:
        ui.states.update(states)
    return ui


# --- _tree_nodes: the server-side tree model ---------------------------------


def test_tree_sections_cover_controls_and_autopilot():
    ui = _ui()

    assert [section["name"] for section in ui.sections] == ["controls", "autopilot"]


def test_tree_nodes_keep_json_order_and_type():
    nodes = _ui().sections[0]["nodes"]

    assert nodes[0]["name"] == "Fenster Buero"
    assert nodes[0]["type"] == "Jalousie"
    assert nodes[1]["name"] == "Radio Bad"


def test_tree_states_carry_labels():
    node = _ui().sections[0]["nodes"][0]

    assert node["states"] == [
        {"key": "up", "uuid": "<uuid-up>", "label": "Fenster Buero.states.up"},
        {
            "key": "position",
            "uuid": "<uuid-pos>",
            "label": "Fenster Buero.states.position",
        },
    ]


def test_tree_sub_controls_are_nested():
    nodes = _ui().sections[0]["nodes"]

    assert nodes[0]["subControls"][0]["name"] == "Sub A"
    assert nodes[0]["subControls"][0]["states"][0]["label"] == "Fenster Buero / Sub A.states.active"


def test_autopilot_states_are_labelled_by_section():
    node = _ui().sections[1]["nodes"][0]

    assert node["states"][0]["label"] == "autopilot.states.changed"


# --- message intake -----------------------------------------------------------


def test_message_updates_states():
    ui = _ui()

    ui.message({"<uuid-up>": 0.0, "<uuid-pos>": 1.0})

    assert ui.states["<uuid-up>"] == 0.0
    assert ui.states["<uuid-pos>"] == 1.0


def test_message_ignores_replies_and_keepalive():
    ui = _ui()

    ui.message({"control": "jdev/sps/io/x", "Code": 200, "value": "1"})
    ui.message({"keep_alive": "received"})

    assert ui.states == {}


# --- PS-27 credential redaction ------------------------------------------------


def test_set_structure_resolves_the_credential_uuids():
    ui = _ui()

    assert ui.redact == frozenset({"<uuid-lastcode>", "<uuid-lasttag>", "<uuid-events>"})


def test_page_never_shows_credential_values():
    """Values are redacted on intake: neither the stored state nor the
    rendered page carries them; the state names do appear."""
    ui = _ui()

    ui.message(
        {
            "<uuid-lastcode>": SECRET_CODE,
            "<uuid-lasttag>": SECRET_TAG,
            "<uuid-events>": SECRET_EVENT,
            "<uuid-lastuser>": "Alice",
        }
    )
    page = ui._render_page("")

    for secret in (SECRET_CODE, SECRET_TAG, SECRET_EVENT):
        assert secret not in page
    assert cli.REDACTION in page
    # The reader is still useful: name and the non-credential state show.
    assert "Gate reader" in page
    assert "Gate reader.states.lastcode" in page
    assert "Alice" in page


def test_page_redaction_survives_the_filter():
    """Filtering by the credential's own name must not leak its value."""
    ui = _ui()
    ui.message({"<uuid-lasttag>": SECRET_TAG})

    page = ui._render_page("lasttag")

    assert "Gate reader.states.lasttag" in page
    assert SECRET_TAG not in page
    assert cli.REDACTION in page


def test_page_stores_no_credential_even_unrendered():
    """The stored states are redacted, not just the HTML: a query string
    or a future renderer cannot leak them either."""
    ui = _ui()

    ui.message({"<uuid-lastcode>": SECRET_CODE})

    assert ui.states["<uuid-lastcode>"] == cli.REDACTION


# --- page rendering -----------------------------------------------------------


def test_page_contains_refresh_and_no_javascript():
    ui = _ui({"<uuid-up>": 0.0})
    page = ui._render_page("")

    assert '<meta http-equiv="refresh" content="2">' in page
    assert "<script" not in page


def test_page_shows_states_with_labels_and_values():
    ui = _ui({"<uuid-up>": 0.0})
    page = ui._render_page("")

    assert "Fenster Buero.states.up" in page
    assert "[&lt;uuid-up&gt;]" in page
    assert '<span class="value off">0</span>' in page  # {:g} renders 0.0 as 0


def test_page_escapes_names_and_values():
    ui = cli.WebUi()
    ui.set_structure({"controls": {"<x>": {"name": "<b>Bold</b>", "uuidAction": "<x>", "states": {"t": "<s>"}}}})
    ui.states["<s>"] = "<img onerror>"
    page = ui._render_page("")

    assert "&lt;b&gt;Bold&lt;/b&gt;" in page
    assert "&lt;img onerror&gt;" in page


def test_page_escapes_a_control_named_script():
    """A control literally named ``<script>`` renders as text, not code."""
    ui = cli.WebUi()
    ui.set_structure({"controls": {"<x>": {"name": "<script>", "uuidAction": "<x>", "states": {}}}})

    page = ui._render_page("")

    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    assert "<script" not in page


def test_page_filter_narrows_the_tree():
    ui = _ui()

    page = ui._render_page("radio bad")
    assert "Radio Bad" in page
    assert "Fenster Buero" not in page

    empty = ui._render_page("nothing matches this")
    assert "Fenster Buero" not in empty


def test_page_colors_values_by_state():
    ui = _ui({"<uuid-up>": 1.0, "<uuid-pos>": 0.0})
    page = ui._render_page("")

    assert '<span class="value on">1</span>' in page
    assert '<span class="value off">0</span>' in page


def test_value_class_text_values_stay_neutral():
    assert cli._value_class(1.0) == "on"
    assert cli._value_class(0) == "off"
    assert cli._value_class(True) == "on"
    assert cli._value_class("Off / Lock") == ""
    assert cli._value_class(None) == ""


def test_paused_page_has_no_refresh_and_keeps_pause_across_filter():
    ui = _ui()

    page = ui._render_page("radio", paused=True)

    assert "http-equiv" not in page
    assert '<input type="hidden" name="pause" value="1">' in page
    assert 'name="pause" value="1">Pause updates' not in page
    assert 'href="/?q=radio"' in page  # resume link drops the pause flag


def test_page_has_render_time_footer():
    ui = _ui()

    assert "rendered at" in ui._render_page("")


# --- server: loopback-only, read-only ------------------------------------------


async def test_run_binds_loopback_only_on_the_chosen_port(monkeypatch):
    started = {}

    runner = Mock(setup=AsyncMock(), cleanup=AsyncMock())
    monkeypatch.setattr(cli.web, "AppRunner", Mock(return_value=runner))

    def fake_tcp_site(_runner, host, port):
        started["host"] = host
        started["port"] = port
        return Mock(start=AsyncMock())

    monkeypatch.setattr(cli.web, "TCPSite", fake_tcp_site)
    ui = cli.WebUi(port=8999)

    await ui.run()
    await ui.stop()

    assert started == {"host": "127.0.0.1", "port": 8999}


async def test_page_refuses_foreign_host_headers():
    """WP-7.8: binding ``127.0.0.1`` alone does not keep a foreign origin
    out.  A malicious page in the user's browser can resolve its own
    domain to 127.0.0.1 (DNS rebinding) and read the tree - control names,
    alarm state, ``lastuser``.  The ``Host`` header is what tells the two
    requests apart, so only the loopback names on *this* view's port are
    answered; everything else gets 400."""
    ui = _ui()

    async def status(host: str) -> int:
        request = make_mocked_request("GET", "/", headers={"Host": host})
        return (await ui._page(request)).status

    assert await status("127.0.0.1:8999") == 200
    assert await status("localhost:8999") == 200
    assert await status("evil.example:8999") == 400
    assert await status("evil.example") == 400
    # right ip, foreign port: not this view's request either
    assert await status("127.0.0.1:1234") == 400


def test_app_is_read_only():
    """Only GET /: no command endpoint, the page cannot change anything."""
    ui = _ui()

    routes = list(ui.build_app().router.routes())

    # aiohttp mirrors GET with HEAD; there is nothing else - no POST
    # command endpoint exists, the page cannot change anything.
    assert {(route.method, route.resource.canonical) for route in routes} == {("GET", "/"), ("HEAD", "/")}


def test_connection_line_shows_the_local_url(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(cli, "_write", lines.append)

    cli.WebUi(port=8999).connection(True)

    assert "http://127.0.0.1:8999/" in lines[-1]
