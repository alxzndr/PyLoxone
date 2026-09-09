"""Integration contract tests (WP-0.2).

Wiring the CLASS: does ``custom_components/loxone/`` declare, register,
import, and translate what it was told it supports?  Every fixture already
exists; these tests catch *contract* drift (a platform declared but not loaded,
a routing entry without a translation, a manifest requirement nobody imports,
...).

Runs on the real source tree in the integration root.  No network, no HA
runtime -- this is a source + fixture audit.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
import yaml

INTEGRATION_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "loxone"
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"
SERVICES_YAML = INTEGRATION_DIR / "services.yaml"
MANIFEST_JSON = INTEGRATION_DIR / "manifest.json"

def _load_en() -> dict:
    return json.loads((TRANSLATIONS_DIR / "en.json").read_text())


def _load_de() -> dict:
    return json.loads((TRANSLATIONS_DIR / "de.json").read_text())


def _flat_keys(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in d.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _flat_keys(value, path)
        else:
            out.add(path)
    return out


def _const_module():
    """The loxone ``const`` module (the PS-14 constants live there)."""
    import custom_components.loxone.const as c

    return c


def _resolve_device_type_token(token: str) -> str | None:
    """A device-type token: a quoted literal (``"Sensor analog"``) or a PS-14
    constant identifier (``DEVICE_TYPE_ANALOG``) — since WP-3.3 both sides of
    the table legally use either form.  Non-device tokens (e.g.
    ``self.type``) resolve to ``None``."""
    t = token.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", t):
        value = getattr(_const_module(), t, None)
        if isinstance(value, str):
            return value
    return None


def _device_type_literals() -> set[str]:
    """The strings the grouping table matches.

    The table is the ``LOXONE_GROUPS_BY_OBJECT_ID`` dict in
    ``__init__.py``: ``{object_id: (group name, (type token, ...))}``.
    """
    src = (INTEGRATION_DIR / "__init__.py").read_text()
    m = re.search(r"LOXONE_GROUPS_BY_OBJECT_ID.*?=\s*\{", src)
    assert m, "LOXONE_GROUPS_BY_OBJECT_ID not found in __init__.py"
    start = m.end() - 1  # the opening brace
    depth = 0
    end = start
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = src[start : end + 1]
    tokens = re.findall(r"DEVICE_TYPE_[A-Z0-9_]+", body)
    tokens += re.findall(r'"([^"]*)"', body)
    literals: set[str] = set()
    for token in tokens:
        # skip the group display names ("Loxone Analog Sensors", ...)
        if token.startswith("Loxone "):
            continue
        value = _resolve_device_type_token(token)
        if value is not None and not value.startswith("Loxone "):
            literals.add(value)
    return literals



def _platform_modules() -> list[str]:
    """All ``.py`` files in the integration root that define async_setup_entry,
    minus ``__init__.py``."""
    mods = []
    for path in sorted(INTEGRATION_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        if "async def async_setup_entry" in path.read_text():
            mods.append(path.stem)
    return mods


def _platform_values() -> set[str]:
    from custom_components.loxone.const import LOXONE_PLATFORMS

    values = set()
    for p in LOXONE_PLATFORMS:
        values.add(getattr(p, "value", str(p)))
    return values


# --------------------------------------------------------------------------- #
# 1.  Platform modules <-> LOXONE_PLATFORMS symmetry
# --------------------------------------------------------------------------- #
def test_platforms_in_code_match_loxone_platforms():
    """Every module that defines async_setup_entry is in LOXONE_PLATFORMS and
    vice-versa."""
    declared = _platform_values()
    modules = set(_platform_modules())

    missing_from_declared = modules - declared
    orphaned_platforms = declared - modules
    assert not missing_from_declared, (
        f"Modules that declare async_setup_entry but are absent from LOXONE_PLATFORMS: {sorted(missing_from_declared)}"
    )
    assert not orphaned_platforms, (
        f"LOXONE_PLATFORMS entries with no implementation module: {sorted(orphaned_platforms)}"
    )


# --------------------------------------------------------------------------- #
# 2.  Manifest requirements are each imported somewhere
# --------------------------------------------------------------------------- #
def test_every_manifest_requirement_is_imported():
    """Every ``requirements`` package in manifest.json is imported somewhere in
    the integration source. (CORE-24 — flipped from ``xfail`` in WP-0.3 now
    that the dead ``httpx`` requirement is gone.)"""
    manifest = json.loads(MANIFEST_JSON.read_text())
    assert "requirements" in manifest

    # Build a set of all importable module names present in the integration.
    imported_roots: set[str] = set()
    src_files = list(INTEGRATION_DIR.rglob("*.py"))
    for path in src_files:
        text = path.read_text()
        for name in re.findall(r"^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)", text, re.M):
            imported_roots.add(name.split(".")[0])

    for requirement in manifest["requirements"]:
        # Requirement lookalikes "websockets>=14"; the importable name is
        # everything before [><=!~; \s].
        m = re.match(r"([A-Za-z_][A-Za-z0-9_.\-]*)", requirement)
        assert m, f"Could not parse package name from requirement {requirement!r}"
        name = m.group(1)
        # ``httpx`` is imported as ``httpx``; ``websockets`` as ``websockets``;
        # ``pycryptodome`` as ``Crypto``.
        expected = {"pycryptodome": "Crypto", "websockets": "websockets", "httpx": "httpx"}.get(name, name)
        assert expected in imported_roots, (
            f"{name!r} is in manifest['requirements'] but I saw no import of {expected!r} in custom_components/loxone/."
        )


# --------------------------------------------------------------------------- #
# 3.  de.json key superset of en.json
# --------------------------------------------------------------------------- #
def test_de_translations_superset_of_en():
    # This was broken until WP-0.1 backfilled services.sync_areas; the test
    # stays as a regression gate.
    en = _flat_keys(_load_en())
    de = _flat_keys(_load_de())
    missing = en - de
    assert not missing, (
        f"de.json is missing keys present in en.json: {sorted(missing)} (add them to translations/de.json)"
    )


# --------------------------------------------------------------------------- #
# 4.  services.yaml keys have at least an en.json entry
# --------------------------------------------------------------------------- #
def test_services_yaml_keys_have_en_entries() -> None:
    services = yaml.safe_load(SERVICES_YAML.read_text())
    assert services, "services.yaml loaded empty"
    en_services = _load_en().get("services", {})
    for key in services:
        assert key in en_services, f"services.yaml declares service {key!r}; no en.json entry under services.{key}"


# --------------------------------------------------------------------------- #
# 5.  manifest.version is valid semver
# --------------------------------------------------------------------------- #
def test_manifest_version_is_semver() -> None:
    manifest = json.loads(MANIFEST_JSON.read_text())
    version = manifest["version"]
    m = re.match(r"^\d+\.\d+\.\d+$", version)
    assert m, f"manifest.version {version!r} is not a strict semver"


# --------------------------------------------------------------------------- #
# 6.  All 14 platform modules import cleanly
# --------------------------------------------------------------------------- #
def test_all_platform_modules_import() -> None:
    """Every ``custom_components/loxone/<platform>.py`` that defines
    async_setup_entry must import cleanly.  14 modules are expected today."""
    mods = _platform_modules()
    # Text switches etc. -- all of the current 14 should import cleanly.  The
    # count itself is a soft check (a 15th should not fail -- the assertion is
    # that the imported ones load).
    assert len(mods) >= 14, f"Expected >= 14 platform modules, found {len(mods)}: {sorted(mods)}"
    for mod in mods:
        importlib.import_module(f"custom_components.loxone.{mod}")


# --------------------------------------------------------------------------- #
# 7.  device_type literals in the grouping table are all produced by some
#     platform  (PS-14)
# --------------------------------------------------------------------------- #
def test_grouping_table_device_types_are_produced() -> None:
    """Every string in ``LOXONE_GROUPS_BY_OBJECT_ID`` must be emitted by some
    platform class (PS-14; consolidated on the ``const`` constants in
    WP-3.3 — the xfail that pinned the old mismatch is gone).

    "Emitted" = assigned to ``self.type`` or written into the
    ``device_type`` state attribute, as a quoted literal or via a
    ``const`` identifier.  Both sides resolve to the same strings,
    so either form is legal on either side.
    """
    table_literals = _device_type_literals()
    assert table_literals, "device_type table extraction returned empty"

    produced: set[str] = set()
    self_type_re = re.compile(r"self\.type\s*=\s*(\"[^\"]+\"|'[^']+'|[a-zA-Z_][a-zA-Z0-9_.]*)")
    device_type_re = re.compile(r"(?:^|[,({\s])\"device_type\"\s*:\s*(\"[^\"]+\"|'[^']+'|[a-zA-Z_][a-zA-Z0-9_.]*)")
    for path in INTEGRATION_DIR.rglob("*.py"):  # includes the lights/ subpackage
        text = path.read_text()
        for m in self_type_re.finditer(text):
            value = _resolve_device_type_token(m.group(1))
            if value is not None:
                produced.add(value)
        for m in device_type_re.finditer(text):
            value = _resolve_device_type_token(m.group(1))
            if value is not None:
                produced.add(value)

    unmatched = table_literals - produced
    assert not unmatched, (
        f"device_type values missing from platform output: {sorted(unmatched)}. "
        f"Platforms emit {sorted(produced)}; fix the table or a platform (PS-14)."
    )



# Constrain the reader: no glob side-effects.
if __name__ == "__main__":  # pragma: no cover
    # Not run directly; provided for IDE convenience.
    pytest.main([__file__, "-q"])
