# Contributing to PyLoxone

Thank you for considering a contribution. The project follows the standard
Home Assistant custom-integration conventions; the
[Home Assistant developer
docs](https://developers.home-assistant.io/) cover most of what you need.

## Repository layout

| Path | What |
|---|---|
| `custom_components/loxone/` | the HA integration (config flow, platforms, coordinator) |
| `custom_components/loxone/pyloxone_api/` | the Loxone websocket protocol client (hass-unaware) |
| `tests/` | pytest suite (see below) |
| `docs/review/` | the October 2026 findings catalogue + remediation plan (WPs) |
| `scripts/lint` | local lint script (ruff) |

## Setting up a dev environment

```
git clone git@github.com:JoDehli/PyLoxone
cd PyLoxone
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

Requires **Python 3.14** (the Home Assistant runtime `requirements.txt`
pinned at 2026.8.1 demands it).

### `pytest-homeassistant-custom-component` (phcc)

phcc provides the `hass` and `enable_custom_integrations` pytest fixtures
used by the integration tests. Installed from `requirements-dev.txt` at
`0.13.355`. `tests/conftest.py` re-exports the fixtures for the tree and
points `mock_connection` at the committed
`tests/fixtures/LoxAPP3.json` (a 26-control synthetic LoxApp3 tree from
which an unreleased Miniserver is rebuilt — the same fixture the Phase 1
WPs build on, see the `docs/review/prompts/WP-0.2.md` doc).

## Running tests / lint

From the repository root:

```
pytest -q                                     # full suite (offline markers only)
ruff check .                                  # lint
ruff format --check .                         # format check
bash scripts/lint                             # both at once
```

A contribution is not considered *done* until all three of the above pass.
The `ci` GitHub Action (`.github/workflows/ci.yaml`) runs the exact same
matrix, plus `hassfest`, `hacs`, and a translation-key-parity check
(`de.json` must be a superset of `en.json`).

## Commit style

`<type>(<scope>): <summary> (finding-id)`

Example:

```
fix(cover): send stop on Window stop_cover (PC-07)
```

Finding ids are the `XXXX-##` codes from `docs/review/2026-09-findings.md`;
they make it easy to trace a commit back to the review that found it.

## Adding translations when you add strings

`custom_components/loxone/translations/en.json` is the source of truth;
`de` must at least be a *superset* of `en` (the CI job `translations`
walks each nesting key under `en` and fails the build if it's missing from
`de`). For a new key, provide the German text rather than just
copying the English.

## Releases

Releases are cut by the `release` GitHub Action
(`.github/workflows/release.yaml`) on a tag push. It
1. asserts `manifest.json version == tag`, bailing out if not;
2. moves the contents of the `## Unreleased` block of `CHANGELOG.md`
   into the release notes;
3. creates (or updates) the GitHub release for the tag.

The `version` field on `manifest.json` is what HACS posts on its listing,
so bumping it is part of the release commit, not a separate chore.
