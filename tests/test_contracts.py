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

# Device-type literals the grouping table (`loxone_discovered` in __init__.py)
# currently matches on (based on the table at commit 7561247).
GROUP_TABLE_LITERALS = {
    "analog_sensor",
    "Meter",
    "digital_sensor",
    "Jalousie",
    "Gate",
    "Window",
    "Switch",
    "TimedSwitch",
    "Pushbutton",
    "LightControllerV2",
    "Dimmer",
    "IRoomControllerV2",
    "Ventilation",
    "AcControl",
    "Slider",
    "TextInput",
}


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


def _device_type_literals() -> set[str]:
    """Extract the quoted literals of `device_type == X` / `in [...]` in
    `loxone_discovered` -- the strings the grouping table currently matches."""
    src = (INTEGRATION_DIR / "__init__.py").read_text()
    func_start = src.index("async def loxone_discovered")
    # Stop at the next top-level def after loxone_discovered to bound the scan.
    nxt = src.find("\n    async def ", func_start + 1)
    if nxt == -1:
        nxt = src.find("\nasync def ", func_start + 1)
    body = src[func_start:nxt]
    literals: set[str] = set()
    for m in re.finditer(r"device_type\s*(?:==|in)\s*([^;]+(?:\]\)?|\))", body):
        literals |= set(re.findall(r'["\']([^"\']+)["\']', m.group(1)))
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
@pytest.mark.xfail(
    strict=True,
    reason="CORE-07: Platform.TEXT missing from LOXONE_PLATFORMS, so text.py is "
    "dead code. Add Platform.TEXT before removing this xfail.",
)
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
@pytest.mark.xfail(
    strict=True,
    reason="PS-14: the grouping table expects 'analog_sensor', 'digital_sensor' "
    "and 'TimedSwitch', but the platforms use 'Sensor analog', 'digital' and "
    "'TimeSwitch'; three auto-groups are always empty. Consolidate to "
    "constants before removing this xfail.",
)
def test_grouping_table_device_types_are_produced() -> None:
    """Every string literal in __init__.py's grouping table must be produced by
    some platform's control class."""
    literal_table = _device_type_literals()
    assert literal_table, "device_type table extraction returned empty"
    # Expected: production of all literals in the current table.
    platform_literals: set[str] = set()
    for path in INTEGRATION_DIR.glob("*.py"):
        text = path.read_text()
        # e.g. `self.type = "TimeSwitch"` or `self.type = "Sensor analog"`.
        for m in re.finditer(r"self\.type\s*=\s*[\"']([^\"']+)[\"']", text):
            platform_literals.add(m.group(1))

    # PS-14 reports yesterday's device_type strings.  Use today's table.
    unmatched = literal_table - platform_literals
    assert not unmatched, (
        f"device_type literals missing from platform output: {sorted(unmatched)}. "
        f"Platforms use {sorted(platform_literals)}; rewrite in one of the "
        "tables (PS-14)."
    )


# Constrain the reader: no glob side-effects.
if __name__ == "__main__":  # pragma: no cover
    # Not run directly; provided for IDE convenience.
    pytest.main([__file__, "-q"])
