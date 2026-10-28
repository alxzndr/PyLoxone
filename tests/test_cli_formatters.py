"""Unit tests for the pyloxone-cli output formatters and the uuid filter.

Plain unit tests: the CLI does not run inside Home Assistant. The
structure is a small synthetic dict, never the fixture file.

Fork rule (PS-27): the credential states of an NFC Code Touch
(``lastcode`` / ``lasttag`` / ``events``) are redacted in every format,
with no flag to turn redaction off. See ``test_redaction_*`` below -
they pin that upstream's formats (4230276, adc411b) never print those
values.
"""

import pytest

import custom_components.loxone.pyloxone_api.__main__ as cli

STRUCTURE = {
    "controls": {
        "<ctl-window>": {
            "name": "Fenster Buero",
            "type": "Jalousie",
            "uuidAction": "<ctl-window>",
            "states": {"up": "<uuid-up>", "position": "<uuid-pos>"},
            "subControls": {
                "<ctl-circuit>": {
                    "name": "Circuit 1",
                    "type": "Switch",
                    "uuidAction": "<ctl-circuit>",
                    "states": {"active": "<uuid-sub-active>"},
                }
            },
        },
        "<ctl-reader>": {
            "name": "Gate reader",
            "type": "NfcCodeTouch",
            "uuidAction": "<ctl-reader>",
            "states": {
                "lastuser": "<uuid-lastuser>",
                "lastcode": "<uuid-lastcode>",
                "lasttag": "<uuid-lasttag>",
                "events": "<uuid-events>",
                "historyDate": "<uuid-historydate>",
            },
        },
    },
    "autopilot": {
        "<ctl-ap>": {"uuidAction": "<ctl-ap>", "states": {"changed": "<uuid-changed>"}},
    },
}

SECRET_CODE = "4711"
SECRET_TAG = "04:1a:2b:3c:de:ad:be"
SECRET_EVENT = "Code 4711 entered"


@pytest.fixture
def lines(monkeypatch):
    """Collect everything the CLI prints."""
    collected: list[str] = []
    monkeypatch.setattr(cli, "_write", collected.append)
    monkeypatch.setattr(cli, "_error", collected.append)
    return collected


def _capture(formatter, data, lines) -> str:
    formatter.message(data)
    return lines[-1] if lines else None


def _ready(formatter) -> None:
    """Attach labels and redaction exactly as ``main`` does on connect."""
    formatter.labels = cli._state_labels(STRUCTURE)
    formatter.redact = cli._redacted_uuids(STRUCTURE)


# --- raw and pretty formats (upstream 4230276, adc411b) -----------------------


def test_pretty_reply_is_printed_in_full(lines):
    """Upstream adc411b: the reply is no longer cut to its command name.

    Safe in the fork because the connection never forwards auth command
    responses (getjwt, getkey2, getvisusalt, ...) to the message
    callback - the tool therefore never sees a token or a key.
    """
    formatter = cli.PrettyFormatter()

    line = _capture(formatter, {"control": "dev/sps/enablebinstatusupdate", "value": "1", "Code": 200}, lines)

    assert "reply" in line
    assert "dev/sps/enablebinstatusupdate" in line
    assert "1" in line
    assert "code=200" in line


def test_raw_formatter_prints_everything(lines):
    formatter = cli.RawFormatter()
    data = {"control": "x", "Code": 200}

    line = _capture(formatter, data, lines)

    assert line == str(data)


def test_pretty_state_line_with_label(lines):
    formatter = cli.PrettyFormatter()
    formatter.labels["<uuid-state>"] = "Buero.states.position"

    line = _capture(formatter, {"<uuid-state>": 1.0}, lines)

    assert "Buero.states.position" in line
    assert "[<uuid-state>]" in line
    assert "1.0" in line


def test_pretty_state_line_without_label_keeps_uuid(lines):
    formatter = cli.PrettyFormatter()

    line = _capture(formatter, {"<uuid-other>": "text"}, lines)

    assert "[<uuid-other>]" in line
    assert "text" in line


def test_pretty_keepalive(lines):
    formatter = cli.PrettyFormatter()

    line = _capture(formatter, {"keep_alive": "received"}, lines)

    assert "keep-alive" in line


def test_connection_state_lines(lines):
    raw = cli.RawFormatter()
    raw.connection(True)
    raw.connection(False)
    pretty = cli.PrettyFormatter()
    pretty.connection(True)

    assert lines[0] == "connected"
    assert lines[1] == "disconnected"
    assert "connected" in lines[2]


# --- uuid filter (upstream 4230276) -------------------------------------------


def test_control_uuids_keep_control_its_states_and_sub_controls():
    wanted = cli._control_uuids(STRUCTURE, ["<ctl-window>"])

    assert wanted == frozenset({"<ctl-window>", "<uuid-up>", "<uuid-pos>", "<ctl-circuit>", "<uuid-sub-active>"})
    # Nothing of the NFC reader or the autopilot leaks in.
    assert "<ctl-reader>" not in wanted
    assert "<uuid-changed>" not in wanted


def test_control_uuids_reject_unknown_root():
    with pytest.raises(ValueError, match="unknown control uuid"):
        cli._control_uuids(STRUCTURE, ["<nope>"])


def test_control_uuids_accept_autopilot_root():
    assert cli._control_uuids(STRUCTURE, ["<ctl-ap>"]) == frozenset({"<ctl-ap>", "<uuid-changed>"})


# --- state labels --------------------------------------------------------------


def test_labels_nest_sub_controls_and_label_autopilot():
    labels = cli._state_labels(STRUCTURE)

    assert labels["<uuid-pos>"] == "Fenster Buero.states.position"
    assert labels["<uuid-sub-active>"] == "Fenster Buero / Circuit 1.states.active"
    assert labels["<uuid-changed>"] == "autopilot.states.changed"


# --- PS-27 credential redaction (fork rule, every format) ----------------------


def test_redacted_uuids_are_resolved_from_the_structure_file():
    assert cli._redacted_uuids(STRUCTURE) == frozenset({"<uuid-lastcode>", "<uuid-lasttag>", "<uuid-events>"})


def test_redaction_scoped_to_nfc_reader_state_names():
    """A state called ``lastcode`` on another control type is not a credential here."""
    structure = {"controls": {"<c>": {"name": "Odd", "type": "Switch", "states": {"lastcode": "<s>"}}}}

    assert cli._redacted_uuids(structure) == frozenset()


@pytest.mark.parametrize("format_name", sorted(cli.FORMATTERS))
def test_redaction_lastcode_pretty_and_raw(format_name, lines):
    """The code value never appears; the state name may."""
    formatter = cli.FORMATTERS[format_name]()
    _ready(formatter)

    line = _capture(formatter, {"<uuid-lastcode>": SECRET_CODE}, lines)

    assert SECRET_CODE not in line
    assert cli.REDACTION in line
    if format_name == "pretty":
        assert "Gate reader.states.lastcode" in line


@pytest.mark.parametrize("format_name", sorted(cli.FORMATTERS))
def test_redaction_lasttag_and_events(format_name, lines):
    formatter = cli.FORMATTERS[format_name]()
    _ready(formatter)

    for uuid, secret in (("<uuid-lasttag>", SECRET_TAG), ("<uuid-events>", SECRET_EVENT)):
        line = _capture(formatter, {uuid: secret}, lines)
        assert secret not in line
        assert cli.REDACTION in line


@pytest.mark.parametrize("format_name", sorted(cli.FORMATTERS))
def test_redaction_keeps_the_non_credential_states_of_the_same_reader(format_name, lines):
    formatter = cli.FORMATTERS[format_name]()
    _ready(formatter)

    formatter.message({"<uuid-lastuser>": "Alice", "<uuid-lastcode>": SECRET_CODE})

    joined = "\n".join(lines)  # pretty prints one line per state
    assert "Alice" in joined
    assert SECRET_CODE not in joined


# --- main() wiring: password source, filter and redaction together -------------


class _FakeConnection:
    """Stands in for LoxoneConnection: feeds canned messages, never any network."""

    instances: list["_FakeConnection"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.structure_file = STRUCTURE
        self.state_cb = None
        self.message_cb = None
        _FakeConnection.instances.append(self)

    async def run(self, state_cb, callback=None):
        self.state_cb = state_cb
        self.message_cb = callback
        state_cb(True)
        callback({"<uuid-pos>": 1.0, "<uuid-lastcode>": SECRET_CODE, "<uuid-lasttag>": SECRET_TAG})
        callback({"<ctl-reader>": "irrelevant", "<uuid-up>": 0.0})

    async def close(self):
        pass


class _EarlyConnection(_FakeConnection):
    """Feeds a state message *before* the connect callback has run.

    Nothing guarantees the order in the real supervisor; the redaction set
    is derived from the structure file, which only exists once
    ``on_state(True)`` has built it (WP-7.8 fail-closed).
    """

    async def run(self, state_cb, callback=None):
        callback({"<uuid-lastcode>": SECRET_CODE})
        await super().run(state_cb, callback)


async def test_main_drops_messages_before_the_redaction_exists(monkeypatch, lines):
    """WP-7.8 fail-closed: before ``on_state(True)`` has filled
    ``sink.redact`` from the structure file, every dict message is dropped
    — the default path gets the guard ``--uuid`` mode already had, so a
    credential can never be printed unredacted."""
    _FakeConnection.instances = []
    monkeypatch.setattr(cli, "LoxoneConnection", _EarlyConnection)
    monkeypatch.setenv("LOXONE_PASSWORD", "not-on-the-command-line")

    await cli.main(["host", "80", "admin", "--format", "raw"])

    joined = "\n".join(lines)
    assert SECRET_CODE not in joined
    # the guard is only for the unready phase: the connected feed still prints,
    # and the second canned message carries <uuid-up>.
    assert "<uuid-up>" in joined


@pytest.mark.parametrize("format_name", ["raw", "pretty"])
async def test_main_without_uuid_redacts_in_both_formats(monkeypatch, lines, format_name):
    """WP-7.8 test gap: the only end-to-end ``main()`` test so far filtered
    the NFC reader out with ``--uuid``, leaving the ``sink.redact`` wiring
    in ``main()`` itself untested.  Without a filter everything prints —
    so this asserts the redaction of ``lastcode``/``lasttag`` runs through
    the real ``main()`` in both formats, while the non-credential states
    still show."""
    _FakeConnection.instances = []
    monkeypatch.setattr(cli, "LoxoneConnection", _FakeConnection)
    monkeypatch.setenv("LOXONE_PASSWORD", "not-on-the-command-line")

    await cli.main(["host", "80", "admin", "--format", format_name])

    joined = "\n".join(lines)
    assert SECRET_CODE not in joined
    assert SECRET_TAG not in joined
    assert cli.REDACTION in joined
    # proof the run really printed the reader and the window control: raw
    # shows uuids, pretty shows their labels.
    if format_name == "pretty":
        assert "Gate reader.states.lastcode" in joined
        assert "Fenster Buero.states.position" in joined
    else:
        assert "<uuid-lastcode>" in joined
        assert "<uuid-pos>" in joined


async def test_main_filters_by_uuid_and_redacts(monkeypatch, lines):
    """One control only: its states print, other controls do not, NFC stays hidden."""
    _FakeConnection.instances = []
    monkeypatch.setattr(cli, "LoxoneConnection", _FakeConnection)
    monkeypatch.setenv("LOXONE_PASSWORD", "not-on-the-command-line")

    await cli.main(["host", "80", "admin", "--format", "pretty", "--uuid", "<ctl-window>"])

    api = _FakeConnection.instances[0]
    assert api.kwargs["password"] == "not-on-the-command-line"
    joined = "\n".join(lines)
    assert "Fenster Buero.states.position" in joined
    assert "Fenster Buero.states.up" in joined
    # The reader is filtered out even before its redaction would matter.
    assert "Gate reader" not in joined
    assert SECRET_CODE not in joined


def test_password_is_never_a_positional_or_option(monkeypatch):
    """The password is env or getpass only: argv accepts no fourth value."""
    monkeypatch.delenv("LOXONE_PASSWORD", raising=False)

    with pytest.raises(SystemExit):
        cli._parse_args(["host", "80", "admin", "hunter2"])
    with pytest.raises(SystemExit):
        cli._parse_args(["host", "80", "admin", "--password", "hunter2"])
