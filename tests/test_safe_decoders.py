"""WP-1.2 safe value decoders + eval() removal (S307).

The light platform used to call ``eval()`` on raw Loxone value strings (6
call-sites: 3 in ``lights/lightcontroller.py`` for the mood lists, 3 in
``lights/colorpickers.py`` for colour/temperature).  ``eval`` can execute
arbitrary code, so those are now routed through two ``helpers`` decoders that
use ``json.loads`` / ``ast.literal_eval`` and return ``None`` on a parse
failure (so a malformed value keeps prior state instead of crashing).
"""

from __future__ import annotations

from pathlib import Path

from custom_components.loxone.helpers import json_decoder, literal_decoder

LIGHTS_DIR = Path(__file__).parent.parent / "custom_components" / "loxone"


def test_json_decoder_roundtrip() -> None:
    assert json_decoder("[778, 779]") == [778, 779]
    assert json_decoder('[{"id": 1, "isTrigger": false}]') == [{"id": 1, "isTrigger": False}]
    assert json_decoder("true") is True
    # malformed → None, not an exception
    assert json_decoder("[778,") is None
    assert json_decoder("not json at all") is None


def test_literal_decoder_roundtrip() -> None:
    assert literal_decoder("[0.85, 2700]") == [0.85, 2700]
    assert literal_decoder("(1, 3, 80)") == (1, 3, 80)
    # malformed → None
    assert literal_decoder("[0.85,") is None
    assert literal_decoder("import os") is None  # code → must NOT eval


def test_non_string_passthrough() -> None:
    assert json_decoder([1, 2]) == [1, 2]
    assert literal_decoder(42) == 42


def test_light_platform_has_no_eval_call_sites() -> None:
    """The S307 fix: no bare ``eval(`` call-sites remain in the light files."""
    for f in ("lights/lightcontroller.py", "lights/colorpickers.py"):
        src = (LIGHTS_DIR / f).read_text()
        offenders = []
        for lineno, line in enumerate(src.splitlines(), 1):
            if "eval(" in line and "literal_eval" not in line and not line.strip().startswith("#"):
                offenders.append((lineno, line.strip()))
        assert not offenders, f"{f} still calls eval(): {offenders}"
