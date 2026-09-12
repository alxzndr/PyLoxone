"""Lint ratchet guard (TOOL-07, WP-5.3).

The blocking rule set grows over time: WP-0.1 established the defect
families; WP-5.3 ratcheted in the advisory backlog groups
``F401/G004/ERA001/B006/BLE001/TRY400/ARG``. Each ratcheted group must
stay at zero violations, or the blocking CI lint job is silently broken.
The advisory full ``ALL`` backlog (its own `continue-on-error` CI job) is
intentionally NOT asserted on here — it trends down across WPs but only
reaches zero with the remaining style backlog.

Two scopes are asserted:
* everything except ``tests/`` — the whole blocking set,
* ``tests/`` itself — everything with the style exemptions the
  ``tests/**`` per-file-ignore in ``ruff.toml`` already grants
  (S101/ANN/D/PLR2004/ARG/ERA001), so a test-tree regression (e.g. a new
  ``except Exception`` or a logged f-string) still fails fast.
"""

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from subprocess import CompletedProcess

# Blocking defect families (WP-0.1) + ratcheted groups (WP-5.3).
BLOCKING_SELECT = "F,E9,PLE,B,T20,S307,ASYNC,RUF006,F401,G004,ERA001,B006,BLE001,TRY400,ARG"

REPO_ROOT = Path(__file__).parent.parent


def _ruff(*args: str) -> CompletedProcess[str]:
    # Prefer the interpreter's ruff module; no console script required.
    cmd = [sys.executable, "-m", "ruff", *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


def _assert_zero(result: CompletedProcess[str], args: str) -> None:
    assert result.returncode == 0, f"`ruff check {args}` reported violations:\n{result.stdout}"
    assert "Found 0 errors" in result.stdout or "All checks passed" in result.stdout


def test_blocking_lint_clean_outside_tests():
    """The full blocking set is clean for the integration code."""
    result = _ruff("check", "custom_components", "--select", BLOCKING_SELECT)
    _assert_zero(result, f"custom_components --select {BLOCKING_SELECT}")


def test_blocking_lint_clean_in_tests_minus_exemptions():
    """The test tree stays clean for all defect rules the per-file
    exemptions do NOT cover (each exempted style code is absent above, i.e.
    a new REAL defect in tests/ fails this test)."""
    result = _ruff("check", "tests", "--select", BLOCKING_SELECT)
    _assert_zero(result, f"tests --select {BLOCKING_SELECT}")


def test_ruff_format_check_clean():
    """`ruff format --check .` is part of the blocking CI job."""
    result = _ruff("format", "--check", ".")
    assert result.returncode == 0, f"`ruff format --check .` reported unformatted files:\n{result.stdout}"
