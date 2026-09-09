"""Documentation integrity tests (WP-5.4, TOOL-13, TOOL-14 remainder).

These are *source + fixture* audits like ``test_contracts.py``: no HA
runtime, no network.  They pin the README (and CONTRIBUTING) to the
artefacts of the integration so a drift in one side of the
documentation is a red build, not a user-finding:

* every service of ``services.yaml`` is documented in the README;
* the five configuration options are documented;
* the ``loxone_event`` payload (incl. the multi-instance ``entry_id``
  field) is documented;
* the logger snippet of the README names loggers that actually exist
  (``custom_components.loxone.api`` never did);
* the minimum-version claims of README and CONTRIBUTING match the
  ``hacs.json`` floor and the pinned Home Assistant (CPython 3.14.2,
  see ``conftest.md``/CORE-25).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = ROOT / "custom_components" / "loxone"
SERVICES_YAML = INTEGRATION_DIR / "services.yaml"
README = ROOT / "README.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
HACS_JSON = ROOT / "hacs.json"

# The five options/config fields a user can tune (TOOL-13: they were
# undocumented).
OPTION_KEYS = (
    "verify_ssl",
    "generate_groups",
    "generate_scenes",
    "generate_scenes_delay",
    "generate_lightcontroller_subcontrols",
)


def _service_keys() -> list[str]:
    """Top-level keys of ``services.yaml``.

    A top-level service is a line starting in column 0 with a bare
    identifier and a colon; this mirrors the extractor of the
    ``docs-services`` CI job in ``.github/workflows/ci.yaml``.
    """
    keys: list[str] = []
    for line in SERVICES_YAML.read_text().splitlines():
        match = re.match(r"^([A-Za-z_]\w*):", line)
        if match:
            keys.append(match.group(1))
    return keys


def test_readme_documents_every_service() -> None:
    """Every service of ``services.yaml`` appears in the README.

    The README shows the services under their domain-qualified name
    (``loxone.<service>``) -- the form the user types into an automation.
    """
    keys = _service_keys()
    assert keys, "services.yaml parsed to zero services; the extractor is broken"
    readme = README.read_text()
    missing = [key for key in keys if f"loxone.{key}" not in readme]
    assert not missing, f"services.yaml services missing from README.md: {missing}"


# Guard the extractor itself: the seven services as of WP-5.4.
def test_service_extractor_sees_all_seven_services() -> None:
    assert _service_keys() == [
        "event_websocket_command",
        "event_secured_websocket_command",
        "sync_areas",
        "reload",
        "enable_sun_automation",
        "disable_sun_automation",
        "quick_shade",
    ]


def test_readme_documents_configuration_options() -> None:
    """TOOL-13: the option names must be in the README, each with a
    default column (the table row of the "Configuration options"
    section)."""
    readme = README.read_text()
    section = readme.split("## Configuration options", 1)
    assert len(section) == 2, "the README has no 'Configuration options' section"
    table = section[1].split("\n", 1)[1].split("##", 1)[0]
    missing = [key for key in OPTION_KEYS if f"`{key}`" not in table]
    assert not missing, f"README options table is missing: {missing}"


def test_readme_documents_verify_ssl_security_note() -> None:
    """The verify_ssl row must carry the security advice (not just the default)."""
    readme = README.read_text()
    table = readme.split("## Configuration options", 1)[1].split("##", 1)[0]
    row = [line for line in table.splitlines() if "`verify_ssl`" in line]
    assert row, "no verify_ssl row in the options table"
    assert re.search(r"(?i)man.in.the.middle|downgrade|mitm", row[0]), "verify_ssl row lacks the security note"


def test_readme_documents_generate_scenes_delay_min_3_rationale() -> None:
    """TOOL-13: the option's minimum (3) must be *explained*, not just stated."""
    table = README.read_text().split("## Configuration options", 1)[1].split("##", 1)[0]
    row = next(line for line in table.splitlines() if "`generate_scenes_delay`" in line)
    assert "3" in row
    light = "light" in row.lower()
    assert light, "generate_scenes_delay row does not explain why the minimum is 3"


def test_readme_documents_loxone_event_payload_with_entry_id() -> None:
    """CORE-27 remainder for docs: the event's payload keys, incl. entry_id."""
    readme = README.read_text()
    section = readme.split("### `loxone_event`", 1)
    assert len(section) == 2, "the README has no loxone_event section"
    event_doc = section[1].split("###", 1)[0]
    assert "entry_id" in event_doc, "loxone_event docs miss the entry_id field"
    assert "keep_alive" in event_doc, "loxone_event docs miss the keep_alive key"


def test_readme_logger_snippet_names_real_loggers() -> None:
    """TOOL-13: ``custom_components.loxone.api`` does not exist; the real
    package is ``custom_components.loxone.pyloxone_api``."""
    readme = README.read_text()
    assert not re.search(r"custom_components\.loxone\.api\b", readme), (
        "README still names the nonexistent logger custom_components.loxone.api"
    )
    assert "custom_components.loxone.pyloxone_api" in readme
    assert "custom_components.loxone: debug" in readme


def test_readme_minimum_versions_match_hacs_floor() -> None:
    """CORE-25 docs side: the README states the same HA floor as hacs.json
    and the CPython floor of the pinned Home Assistant (3.14.2)."""
    import json

    hacs = json.loads(HACS_JSON.read_text())
    floor = hacs["homeassistant"]
    readme = README.read_text()
    assert floor in readme, f"README does not state the HA floor {floor}"
    assert "3.14.2" in readme, "README does not state the Python 3.14.2 floor"


def test_contributing_setup_requirements() -> None:
    """TOOL-14 remainder: CONTRIBUTING documents the dev entry points
    (scripts/setup, scripts/lint, pytest) and the Python 3.14.2 floor."""
    contributing = CONTRIBUTING.read_text()
    for needle in ("scripts/setup", "scripts/lint", "pytest -q", "3.14.2"):
        assert needle in contributing, f"CONTRIBUTING.md is missing: {needle!r}"
