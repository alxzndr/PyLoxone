"""A single unparseable frame must not end the listening session (#517)."""

import asyncio
import logging
import struct
from types import SimpleNamespace

from custom_components.loxone.pyloxone_api.connection import LoxoneConnection

# Loxone binary header (WsBinHdr): 0x03, identifier, info, reserved, UINT32 length (LE).
TEXT, KEEPALIVE = 0, 6


def _header(msg_type: int, length: int) -> bytes:
    return struct.pack("<cBccI", b"\x03", msg_type, b"\x00", b"\x00", length)


class FakeFeed:
    """Just enough of the websocket connection for the listening loop."""

    def __init__(self, frames):
        self._frames = list(frames)
        # The loop compares ``connection.state == connection.state.CLOSED``;
        # this namespace never equals its own CLOSED marker, i.e. "open".
        self.state = SimpleNamespace(CLOSED="closed")

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def _listen(frames):
    conn = LoxoneConnection(host="192.168.1.5", username="admin", password="secret", port=8080)
    seen = []

    async def callback(data):
        seen.append(data)

    async def run():
        await conn._do_start_listening(callback, FakeFeed(frames))
        # Keep-alives are dispatched to the callback as tasks; let them run.
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(run())
    return seen


class TestUnparseableFrameIsSkipped:
    def test_bad_text_frame_is_logged_and_the_next_message_still_arrives(self, caplog):
        # A TEXT payload the JSON parser rejects. Before the fix the resulting
        # ValueError escaped the listening loop and the websocket stayed dead
        # until the integration was reloaded.
        bad_body = b'{"LL": not json'
        assert len(bad_body) == 15  # hand-counted; must match the header

        with caplog.at_level(logging.WARNING):
            seen = _listen([_header(TEXT, 15), bad_body, _header(KEEPALIVE, 0)])

        # The loop survived and the keep-alive that followed was delivered.
        assert seen == [{"keep_alive": "received"}]
        assert "Skipping unparseable TEXT frame (15 bytes)" in caplog.text

    def test_a_well_formed_stream_logs_nothing(self, caplog):
        with caplog.at_level(logging.WARNING):
            seen = _listen([_header(KEEPALIVE, 0)])
        assert seen == [{"keep_alive": "received"}]
        assert "Skipping unparseable" not in caplog.text
