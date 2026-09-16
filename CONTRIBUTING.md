# Contributing to PyLoxone

Thanks for considering a contribution. The project follows the usual Home
Assistant custom-integration conventions; the
[Home Assistant developer docs](https://developers.home-assistant.io/) cover
most of what you need. This file covers what is specific to this repository.

## Repository layout

| Path | What |
|---|---|
| `custom_components/loxone/` | the integration: config flow, coordinator, one module per platform |
| `custom_components/loxone/pyloxone_api/` | the Loxone websocket protocol client (knows nothing about Home Assistant) |
| `tests/` | the pytest suite; `tests/fixtures/LoxAPP3.json` is the synthetic structure file every offline test runs against |
| `docs/` | user and maintainer documentation, see [`docs/README.md`](docs/README.md) |
| `scripts/setup` | devcontainer bootstrap: installs the runtime and seeds `config/` for a local Home Assistant |
| `scripts/lint` | formats and runs the blocking lint set; `scripts/lint --check` is what CI runs |

## Setting up a development environment

```
git clone git@github.com:alxzndr/PyLoxone.git
cd PyLoxone
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

You need **Python 3.14.2 or newer**: `pytest-homeassistant-custom-component`
(pinned in `requirements-dev.txt`) pins the Home Assistant release the tests
run on, and Home Assistant 2026.x refuses to import on older CPythons.

The devcontainer (`.devcontainer.json`) runs `scripts/setup` after creation,
which installs the runtime requirements and runs `hass --script ensure_config`
so `config/` is a valid Home Assistant configuration for a local debug run
(`.vscode/launch.json` has a "Home Assistant" launch target).

### The test harness

`pytest-homeassistant-custom-component` provides the `hass` and
`enable_custom_integrations` fixtures. `tests/conftest.py` re-points
`custom_components` at this repository and provides `loxapp3`,
`mock_connection` and `mock_entry`, which build a Miniserver out of the
committed `tests/fixtures/LoxAPP3.json`. Tests that talk to a Miniserver
over the network are marked `online` and deselected by default.

Prefer the real `hass` fixture and real service calls over mocks: several
production bugs (0.10.3 to 0.10.6) were invisible to mock-based tests and
only surfaced through the real config-entries manager and service registry.

## Running tests and lint

From the repository root:

```
pytest -q                 # full offline suite, with the coverage floor from pytest.ini
bash scripts/lint         # ruff format + the blocking lint set
bash scripts/lint --check # the same, report-only (what CI runs)
ruff check . --statistics # the advisory full `ALL` pass
```

The blocking lint set is defined once in `scripts/lint`
(`BLOCKING_SELECT`), mirrored in `.pre-commit-config.yaml`, and pinned by
`tests/test_lint_ratchet.py`. It only ever grows: add a rule family once the
tree is clean for it. The full `select = ALL` pass from `ruff.toml` is
advisory until the remaining style backlog is cleared. Do not add `# noqa`
to make a check pass.

A change is done when `pytest -q` and `scripts/lint --check` both pass.
The `CI` workflow (`.github/workflows/ci.yaml`) runs the same two plus
`hassfest`, HACS validation, a translation-parity check (`de.json` must
contain every key of `en.json`) and a docs check that every service in
`custom_components/loxone/services.yaml` is documented in `README.md`
(the same audit as `tests/test_docs_readme.py`).

## Documentation that tests pin

`tests/test_docs_readme.py` fails when the README and the code drift apart:
every service must be documented, the five configuration options must be in
the "Configuration options" table, the `loxone_event` payload (including
`entry_id`) must be described, the logger snippet must name real loggers,
and the minimum versions must match `hacs.json`. Update the README together
with the code.

## Commit style

```
<type>(<scope>): <summary> (<reference>)
```

`type` is one of `fix`, `feat`, `refactor`, `test`, `docs`, `build`, `ci`,
`chore`, `release`. The reference is optional: a finding id from
`docs/review/2026-09-findings.md` (`PC-07`), an upstream issue
(`JoDehli/PyLoxone#501`), or both.

```
fix(cover): send stop on Window stop_cover (PC-07, JoDehli/PyLoxone#501)
```

Add a line under `## Unreleased` in `CHANGELOG.md` for anything a user
would notice. Write it for users: what changed and why it matters, not the
internal steps.

## Protocol assumptions need a live check

A test can only prove the code does what we believe the Loxone protocol
wants. When you implement a command whose wire format you inferred rather
than observed, put it behind a small named helper, write the test for the
intended semantics, and add an entry to
`docs/review/LIVE-MINISERVER-CHECKS.md`. The alarm arm-home / arm-away
inversion in 0.10.0 is the cautionary tale: the suite stayed green because
the tests encoded the same wrong assumption.

## Translations

`custom_components/loxone/translations/en.json` is the source of truth.
`de.json` must contain every key of `en.json` (CI fails otherwise); `cs.json`
is best effort. For a new key, write the German text rather than copying the
English.

## Releases

1. Move the `## Unreleased` entries into a new `## <version> - <date>`
   section in `CHANGELOG.md` and bump `version` in
   `custom_components/loxone/manifest.json` in the same commit
   (`release: <version>`).
2. Tag it `v<version>` and push the tag.

The `release` workflow (`.github/workflows/release.yaml`) refuses a tag
that does not match the manifest version, then creates or updates the
GitHub release with that version's changelog section as the notes. HACS
shows the manifest version, so the bump is part of the release commit.
