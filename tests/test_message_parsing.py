"""Pure wire-protocol unit tests (WP-0.2, no Miniserver).

Covers the message envelopes in
``custom_components/loxone/pyloxone_api/message.py`` plus the
``pyloxone_api/loxone_token.py`` helpers.

The single protocol finding pinned here (MessageHeader on a non-0x03 frame
leaves payload_length unset -- API-18) is marked xfail so the next WP that
touches that class must flip the marker before it merges.
"""

from __future__ import annotations

import json
import struct
import uuid as pyuuid

import pytest

from custom_components.loxone.pyloxone_api.exceptions import LoxoneException
from custom_components.loxone.pyloxone_api.loxone_token import (
    LxJsonKeySalt,
    LoxoneToken,
)
from custom_components.loxone.pyloxone_api.message import (
    LLResponse,
    MessageHeader,
    MessageType,
    TextStatesTable,
    ValueStatesTable,
    parse_message,
)


def _uuid_key(bytes_le: bytes) -> str:
    """Reproduce the key format ValueStatesTable produces from a bytes_le."""
    u = pyuuid.UUID(bytes_le=bytes_le)
    f = u.urn.split("urn:uuid:")[1].split("-")
    return f"{f[0]}-{f[1]}-{f[2]}-{f[3]}{f[4]}"


# --------------------------------------------------------------------------- #
# MessageHeader
# --------------------------------------------------------------------------- #
def test_message_header_roundtrips_pins() -> None:
    header = b"\x03" + bytes([MessageType.VALUE_STATES]) + b"\x00\x00" + struct.pack("<I", 512)
    mh = MessageHeader(header)
    assert mh.message_type == MessageType.VALUE_STATES
    assert mh.payload_length == 512
    assert mh.estimated is False


def test_message_header_unknown_bin_type() -> None:
    # cBinType=0x07 => not 3 => UNKNOWN.
    header = b"\x07" + b"\x00" * 7
    mh = MessageHeader(header)
    assert mh.message_type == MessageType.UNKNOWN


def test_message_header_unknown_also_exposes_payload_length() -> None:
    header = b"\x07" + b"\x00" * 7
    mh = MessageHeader(header)
    assert mh.message_type == MessageType.UNKNOWN
    # WP-0.2 pinned this as xfail; WP-2.1 made the UNKNOWN branch always set
    # payload_length, so the pin is now flipped (see the test matrix above).
    assert hasattr(mh, "payload_length")


# --------------------------------------------------------------------------- #
# ValueStatesTable
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [0.0, -1.0, 1.0, 1234.5, float("inf")])
def test_value_states_table_roundtrip(value: float) -> None:
    b16 = pyuuid.uuid4().bytes_le
    msg = b16 + struct.pack("<d", value)
    d = ValueStatesTable(msg).as_dict()
    assert d == {_uuid_key(b16): value}


def test_value_states_table_multiple_records() -> None:
    def rec(b16: bytes, v: float) -> bytes:
        return b16 + struct.pack("<d", v)

    a = pyuuid.uuid4().bytes_le
    b = pyuuid.uuid4().bytes_le
    c = pyuuid.uuid4().bytes_le
    d = ValueStatesTable(rec(a, 1.5) + rec(b, -2.5) + rec(c, 3.0)).as_dict()
    assert d == {
        _uuid_key(a): 1.5,
        _uuid_key(b): -2.5,
        _uuid_key(c): 3.0,
    }


# --------------------------------------------------------------------------- #
# TextStatesTable
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text_length", [0, 1, 3, 4, 5])
def test_text_states_table_padding(text_length: int) -> None:
    payload = b"X" * text_length
    u = pyuuid.uuid4()
    icon = pyuuid.uuid4()
    msg = u.bytes_le + icon.bytes_le + struct.pack("<I", text_length) + payload
    d = TextStatesTable(msg).as_dict()
    assert _uuid_key(u.bytes_le) in d, f"missing key for text_length={text_length}"


def test_text_states_table_rejects_str() -> None:
    with pytest.raises(LoxoneException):
        TextStatesTable("not bytes").as_dict()  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# LLResponse
# --------------------------------------------------------------------------- #
def test_llresponse_scalar_and_nested_value() -> None:
    # code 0 is falsy in the ``.. or ..`` chain in LLResponse (a bug that
    # swallows code 0 into the fallback).  Use a non-zero code for the happy
    # path; that bug is out of scope for this WP.
    r = LLResponse('{"LL": {"control": "svc", "code": 100, "value": 5}}')
    assert r.code == 100
    assert r.control == "svc"
    assert r.value == "5"
    assert r.value_as_dict == {"value": "5"}

    nested = LLResponse('{"LL": {"control": "svc", "code": 100, "value": {"A": 1, "B": 2}}}')
    # The value attribute is a ``str`` of the parsed object.
    assert nested.value == "{'A': 1, 'B': 2}"
    d = nested.value_as_dict
    assert d == {"value": "{'A': 1, 'B': 2}", "A": 1, "B": 2}


def test_llresponse_missing_control_raises() -> None:
    with pytest.raises(ValueError):
        LLResponse('{"LL": {"code": 0, "value": 5}}')  # no 'control' key


# --------------------------------------------------------------------------- #
# parse_message dispatch
# --------------------------------------------------------------------------- #
def test_parse_message_dispatch_roots_to_class() -> None:
    b16 = pyuuid.uuid4().bytes_le
    msg = b16 + struct.pack("<d", 1.25)
    m = parse_message(msg, MessageType.VALUE_STATES)
    assert isinstance(m, ValueStatesTable)
    assert m.as_dict() == {_uuid_key(b16): 1.25}


def test_parse_message_dispatch_rejects_unknown() -> None:
    with pytest.raises(LoxoneException):
        parse_message(b"", MessageType.UNKNOWN)


# --------------------------------------------------------------------------- #
# LoxoneToken
# --------------------------------------------------------------------------- #
def test_seconds_to_expire_returns_positive_when_far() -> None:
    t = LoxoneToken(token="abc", valid_until=4_999_999_999_999, key="k")
    secs = t.seconds_to_expire()
    assert secs > 0


def test_seconds_to_expire_raises_when_valid_until_zero() -> None:
    t = LoxoneToken.__new__(LoxoneToken)
    t.token = ""
    t.valid_until = 0
    t.key = ""
    t.hash_alg = ""
    t.unsecure_password = False
    with pytest.raises(ValueError, match="valid_until"):
        t.seconds_to_expire()


def test_empty_token_resets_to_invalid_value() -> None:
    t = LoxoneToken(token="", valid_until=0)
    assert t.token == ""
    assert t.valid_until == -1
    assert t.key == ""


# --------------------------------------------------------------------------- #
# LxJsonKeySalt (regression for #498 -- missing hashAlg must default to SHA1)
# --------------------------------------------------------------------------- #
def test_lx_json_key_salt_hashalg_explicit() -> None:
    s = LxJsonKeySalt()
    body = json.dumps({"LL": {"value": {"key": "k", "salt": "s", "hashAlg": "SHA256"}}})
    s.read_user_salt_response(body)
    assert s.hash_alg == "SHA256"
    assert s.key == "k" and s.salt == "s"


def test_lx_json_key_salt_hashalg_default_sha1_for_498() -> None:
    # The #498 regression: a salt response without hashAlg must default to SHA1
    # and NOT leave the previous value in place.
    s = LxJsonKeySalt(hash_alg="SHA256")
    body = json.dumps({"LL": {"value": {"key": "k", "salt": "s"}}})
    s.read_user_salt_response(body)
    assert s.hash_alg == "SHA1"


def test_lx_json_key_salt_malformed_response_raises() -> None:
    s = LxJsonKeySalt()
    with pytest.raises((json.JSONDecodeError, KeyError)):
        s.read_user_salt_response(json.dumps({"LL": {}}))
