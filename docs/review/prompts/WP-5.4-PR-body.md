## PR body — WP-5.4 Documentation

Findings: **TOOL-13** (README gaps), **TOOL-14 remainder** (CONTRIBUTING hardening), **CORE-24 docs side** (minimum-version claims). No upstream issue number is attached to WP-5.4 in the plan.

### What changed (by file)

| File | Change |
|---|---|
| `README.md` | New **Requirements** section (Home Assistant 2026.7.0 floor, aligned with `hacs.json` — CORE-24/CORE-25 docs side; CPython 3.14.2 for the runtime and dev environment). New **Configuring the integration** section documenting the connection fields incl. the `verify_ssl` security note. New **Configuration options** table — all five options with default and effect (`verify_ssl`, `generate_scenes`, `generate_scenes_delay` incl. *why the minimum is 3* — scene generation waited for the light platform to be loaded before scraping it, `3` sec was the smallest value that reliably waited that out; today the value is retained but scene generation is event-driven; `generate_lightcontroller_subcontrols` incl. the new-install `false` vs migrated `true` split; `generate_groups` incl. the new-install `false` / pre-option groups-on split). **Services** table covering **all 7** services of `services.yaml` (was 1 of 7). New **Entities and attributes** reference: Loxone control → HA domain, platform extras, common attributes and the recorder exclusion set. New **Events** section documenting the `loxone_event` payload (uuid→value keys, the `entry_id` field required for multi-instance discrimination, `keep_alive`) and the outbound `loxone_send`/`loxone_send_secured` bus events. Logger snippet kept correct (`custom_components.loxone` + `custom_components.loxone.pyloxone_api`; the real loggers of this checkout — `custom_components.loxone.api` does not exist). Recorder note extended. |
| `CONTRIBUTING.md` | Dev setup now installs **both** requirements files and states the minimum as **Python 3.14.2**; `scripts/setup` documented (devcontainer bootstrap: runtime install + `hass --script ensure_config`); `scripts/lint` described as the blocking-subset mirror of CI (the subset is named verbatim); CI paragraph extended with the new `docs-services` job. |
| `.github/workflows/ci.yaml` | New **`docs-services`** job: a dependency-free Python extractor reads every top-level key of `services.yaml` and fails if `loxone.<key>` is absent from `README.md` (the same audit as `tests/test_docs_readme.py`). |
| `tests/test_docs_readme.py` (new) | 9 doc-integrity tests: every `services.yaml` service documented as `loxone.<key>` in the README (same extractor as the CI job, pinned by an explicit 7-key test); all five options present in the **Configuration options** table with a default; `verify_ssl` row carries the security note; `generate_scenes_delay` row explains the minimum-3 floor; `loxone_event` section documents `entry_id` and `keep_alive`; logger snippet names only real loggers and not `custom_components.loxone.api`; README states the `hacs.json` HA floor **and** Python 3.14.2; CONTRIBUTING documents `scripts/setup`, `scripts/lint`, `pytest -q`, 3.14.2. |
| `CHANGELOG.md` | `## Unreleased` → `### Documentation` entry summarising the above. |

No `# noqa` added; no `manifest.json` version bump (per rule 8).

### Tests (real, pasted output)

Before the change (README/CONTRIBUTING at `HEAD`, new test file present):

```
tests/test_docs_readme.py::test_readme_documents_every_service FAILED        (6 of 7 services undocumented)
tests/test_docs_readme.py::test_readme_documents_configuration_options FAILED
tests/test_docs_readme.py::test_readme_documents_verify_ssl_security_note FAILED
tests/test_docs_readme.py::test_readme_documents_generate_scenes_delay_min_3_rationale FAILED
tests/test_docs_readme.py::test_readme_documents_loxone_event_payload_with_entry_id FAILED
tests/test_docs_readme.py::test_readme_minimum_versions_match_hacs_floor FAILED
tests/test_docs_readme.py::test_contributing_setup_requirements FAILED
7 failed, 2 passed in 0.17s
```

After the change, repo root:

```
$ ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ ruff format --check .
66 files already formatted

$ pytest -q
... (485 collected tests incl. the 9 new ones)
485 passed, 1 deselected, 4 warnings in 43.33s
Required test coverage of 20% reached. Total coverage: 58.40%
```

(the 1 deselected is the `online`-marked Miniserver test, as before.)

The CI job's check, run locally on this tree:

```
README documents all 7 services of services.yaml
```

### VERIFY items

None. This package changes no runtime behaviour and none of TOOL-13 / TOOL-14 / CORE-24 (docs side) is a VERIFY-marked finding; no live-Miniserver check is required. (The doc claims themselves were verified against the source of this checkout: option keys/defaults from `config_flow.py`/`const.py`, service set from `services.yaml`, entity extras from each platform file, event payload from `LoxoneCoordinator.handle_message`.)

### Follow-ups (outside this WP's scope, not fixed here)

1. **`except A, B:` (unparenthesised except-tuple) in 17 places** across `custom_components/loxone/` (e.g. `__init__.py:416`, `sensor.py:700/775`, `fan.py:51/85`, `select.py:150`, `climate.py:55/157`, `helpers.py:28`, `lights/dimmer.py:108/115/127`, `lights/lightcontroller.py:182/189/196`, `pyloxone_api/connection.py:532/2116`). On CPython 3.14 (our floor) this parses as `except (A, B):` and behaves correctly — I verified the AST and the catch semantics — but it is non-idiomatic and was a Python 2-ism; it would be a SyntaxError on any older runtime. Recommend normalising to parenthesised tuples in a follow-up WP (owner: none yet; it spans several WPs' file lists).
2. Earlier WPs left the README's old "Supported Loxone Entities" bullet list; the new **Entities and attributes** reference supersedes and it was removed in this WP (this PR, not a follow-up).

### Assumptions

- `verify_ssl` matters only on the HTTPS paths (local `8443` tunnel / Loxone Cloud `443`); the plain-HTTP local path (8080) is documented as not affected.
- The options-page description entry path follows the standard HA navigation wording.
