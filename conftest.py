"""Pytest root configuration (TOOL-16).

This file is intentionally minimal: the real test options live in ``pytest.ini``
(testpaths, ``-m "not online"``, ``asyncio_mode = auto``, the ``online`` marker,
and the coverage floor) so contributors who run pytest by hand get the same
behaviour.

Requirements (a failure to install here is not a test failure):
  * CPython >= 3.14.2.  ``homeassistant==2026.8.1`` (pinned by
    ``pytest-homeassistant-custom-component``) refuses to import on older
    Pythons; see WP-0.1 / CORE-25.
  * Both requirements files must be installed, in this order, into the venv
    that runs the tests:

        python -m pip install -r requirements.txt -r requirements-dev.txt

    ``requirements.txt`` is the *integration* runtime (mirrors
    ``manifest.json``); ``requirements-dev.txt`` is the test/dev tooling
    (pytest, HA test harness, ruff, ...).

Per-package fixtures live in ``tests/conftest.py``; the ``online`` marker
(deselects tests that require a reachable Miniserver) and ``asyncio_mode =
auto`` come from ``pytest.ini``.
"""
