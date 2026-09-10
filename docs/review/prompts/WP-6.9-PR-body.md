# WP-6.9 — Discovery of the Miniserver via `discover.py` (config flow)

Branch: `fix/wp-6-9-discovery` from `c99a964` == the tip the harness calls
`origin/master` (master tip; all of Phase 6's prerequisites are in, incl.
the prerequisite **WP-5.3** at `862f688`). WP-3.4 *skipped* the
discovery wiring, as the recorded plan allows ("optional `zeroconf` step
using `discover.py`… **VERIFY**; skip if not") — its PR body says
verbatim: "`pyloxone_api/discover.py` … is UDP broadcast, not mDNS — the
optional `zeroconf` step was therefore **skipped** as the plan allows;
fix/replace it for WP-6.9." This WP is that follow-up.

Catalogue entry behind this WP: **CORE-19** ("Config flow gaps: …
no discovery"). There is **no upstream issue number** for discovery.
**Upstream PR check (done first, per the plan):** upstream
`JoDehli/PyLoxone` has *no* `zeroconf` string anywhere in the repo — no
manifest hook, no flow step; only the vendored (broken-on-import, see
below) `discover.py`. Nothing to port; this is new wiring.

## Why LoxLIVE broadcast, not an HA `zeroconf` step

The WP title says "Zeroconf", and the plan's WP-3.4 design said "using
`discover.py` if the Miniserver advertises via mDNS (**VERIFY**)". We
resolved the VERIFY against the Miniserver networking documentation
(`sarnau/Inside-The-Loxone-Miniserver`, referenced by `discover.py`
itself): the Miniserver runs mDNS on port 5353 but "**There are no
special Miniserver related services being offered by them**", while the
discovery protocol is documented as "UDP 7070/7071 — Miniserver
send/answer – used for discovery". A `manifest.json` `"zeroconf"` entry
could therefore only ever name a service type that exists nowhere —
an unverifiable fiction that would ship into a release. The honest
implementation of "discovery via `discover.py`" is a **best-effort
LoxLIVE broadcast probe inside the setup form**, using the vendored
`discover.py` untouched on the wire path. `manifest.json` therefore
gains **no** `zeroconf` entry; the one-line follow-up that closes the
loop on real-world mDNS behaviour is live-check #20.

## What changed (by file)

- `custom_components/loxone/pyloxone_api/discover.py`
  - Extracted the reply parsing into a pure, stand-alone helper
    **`parse_discovery_response(response) -> (ip, port) | None`** with
    the same regex as before plus value validation: non-LoxLIVE lines,
    non-string input, IPv4 octets above 255, and ports above 65535
    yield `None` instead of feeding a bogus host/port into the flow.
    15 hand-derived parser tests pin it.
  - Re-wrote the vendor module header and the blocked-UDP branch;
    behaviour of `discover(wait)` and `_discover_blocking` is
    unchanged (same 3×1-byte broadcast to 255.255.255.255:7070, same
    7071 bind, same 1024-byte read, same `(ip, port, raw)` return).
  - Re-verification note: WP-3.4's PR body reported a `SyntaxError`
    here (`except socket.timeout, TimeoutError, OSError:`). On this
    workspace's interpreter (Python 3.14.2) that form is accepted and
    behaves like an explicit tuple (verified: it compiles and catches
    `OSError`), so the module imports and this workspace's baseline
    suite (654 passed) was green before this WP. The comma-separated
    exception list is still not portable Python 3 (it is a hard
    `SyntaxError` on ≤3.13 interpreters), so it was normalised to an
    explicit `(TimeoutError, OSError)` tuple as part of this WP.
- `custom_components/loxone/config_flow.py`
  - New module helpers, both clearly named so the VERIFY assumption
    sits in one readable place:
    - **`_discovered_prefill(found) -> str, int | None`** — the single
      gate deciding what a raw `discover()` answer may contribute to
      the form: a 2-tuple of non-empty host string and 1..65535 port
      (bools/floats rejected); anything else → `None`.
    - **`_discover_miniserver()`** — the probe itself: awaits
      `discover(DISCOVERY_WAIT)`, catches the concrete failure modes
      of the vendored transport (`OSError` — socket
      bind/send/recv incl. the timeout — and `UnicodeDecodeError` —
      undecodable reply byte sequence) and the gating of
      `_discovered_prefill`, all of which collapse to "no usable
      address". A probe failure can therefore *not* break manual
      entry; the form simply opens blank, exactly as before WP-6.9.
  - `LoxoneConfigFlow.__init__` gained `self._discovered`, and
    `async_step_user` runs the probe **once per flow** on the first
    form render (the error re-render reuses the cached result, and the
    reauth flow — which must not answer for the wrong unit in a
    multi-Miniserver home — never probes).
  - `DATA_SCHEMA_USER` became `_user_form_schema(host, port)` so the
    cache can prefill `vol.Required(...).default` for host and port;
    prefill never restricts input — a submitted value always wins.
- `custom_components/loxone/const.py` — `DISCOVERY_WAIT = 2` with the
  rationale comment.
- `custom_components/loxone/translations/en.json` — one extra sentence
  in the user-step description ("If a Miniserver answered the network
  probe, its address is already prefilled — adjust it if it is not
  your unit.").
- `tests/conftest.py` — an autouse fixture pins
  `config_flow.loxone_broadcast_discover` to "no answer" for the whole
  HA-harness tree: no test may fire a real UDP broadcast (or spend the
  2-second no-answer window) on a development machine. The
  *pre-WP-6.9* behaviour the rest of the suite documents stays
  byte-for-byte identical under that pin.
- `tests/test_wp69_discovery.py` (new, 35 tests) — pure-helper tables
  (all expected values hand-derived literals, none produced by the code
  under test) plus the HA-harness flow tests: prefill appears on the
  first form with exactly the discovered host/port; `None` /
  *raise* / *malformed answer* all yield the blank form and a
  completing flow; a typed value overrides the discovered one (the
  discovered host is never persisted); exactly one broadcast per flow
  run (a rejected credential's form re-render does not re-broadcast);
  the reauth flow broadcasts nothing.
- `CHANGELOG.md` — entry under *Unreleased → Added*.
- `docs/review/LIVE-MINISERVER-CHECKS.md` — new non-blocking item
  **#20** (below); the suite count was updated to the observed 689.

Nothing else was touched: no `manifest.json` change at all (hence no
version bump, per the rules; and deliberately **no `zeroconf` entry**,
see above), no changes outside the discovery surface, no new `# noqa`,
no new imports in platform files.

## VERIFY items (live Miniserver check required before merge)

Live-check **#20** in `docs/review/LIVE-MINISERVER-CHECKS.md` covers
all three of the remaining assumptions, each isolated in one
named helper so a disagreement is a one-place flip:

1. **Reply format** — the parsed regex
   `^LoxLIVE:<version> (<ip>):(<port>) ` (incl. the trailing space)
   still matches a *current* Miniserver's broadcast answer, and the
   port in it is the HTTP service port (not the discovery port).
   → fix lives in `parse_discovery_response`.
2. **UDP reachability** — the 7070 broadcast actually reaches the
   Miniserver from the default HA deployment (Docker bridge / macvlan /
   VLAN). If it does not in a given deployment, the symptom is
   "blank fields, ≤2 s delay" — i.e. the feature degrades to
   exactly the pre-WP-6.9 behaviour, which is the intended
   fail-open. (If UDP is blocked in a deployment, that is a
   deployment property, and the probe must remain best-effort and
   silent — do not "fix" that by adding retries.)
3. **Prefill port** — the prefilled port is the port the Miniserver
   actually serves HTTP on (related to 1; if only the *host* is
   trustworthy, one line in `_discovered_prefill` drops the port and
   keeps host-only prefill).

## Follow-ups (out of scope here)

- **`manifest.json` `"zeroconf"` entry** — *only* if live-check #20
  shows the Miniserver ever advertises a Loxone mDNS service. Until
  then adding a `zeroconf` line would be a fabricated service name
  that matches nothing.
- **Docker compose `network_mode: host`** — for multi-interface
  homes one could document that the probe needs the same L2
  segment as the Miniserver (the broadcast is unicast-L3-scoped).
  Documentation, not code.
- **`dhcp`/`ssdp`** — upstream flagged the same design space as
  "no discovery at all" (CORE-19); a subsequent WP could consider a
  genuine `dhcp` filter (Loxone devices' DHCP fingerprints) if desired
  — this WP stays strictly on the documented broadcast path.

## Verification output (from the working directory, this branch)

```
$ .venv/bin/ruff check . --select F,E9,PLE,B,T20,S307,ASYNC,RUF006
All checks passed!

$ .venv/bin/ruff format --check .
79 files already formatted

$ .venv/bin/python -m pytest -q
...
Required test coverage of 20% reached. Total coverage: 59.45%
689 passed, 1 deselected in 72.21s (0:01:12)
```

Baseline before this WP: **654 passed, 1 deselected** (verified on
an untouched checkout). Net: **+35 tests, all in
`tests/test_wp69_discovery.py`** (14 test functions = 35 after
parametrization), the suite now at 689.
