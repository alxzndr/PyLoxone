# PyLoxone incident report — transient 401 during Miniserver reboot permanently kills the integration

**Date of incident:** 2026-09-02, ~21:54–21:56 CEST (integration stayed dead until manual reload on 2026-09-03 ~08:45)
**Status:** root cause confirmed in PyLoxone 0.9.23 source; to be reported upstream at [JoDehli/PyLoxone](https://github.com/JoDehli/PyLoxone)

---

## Suggested issue title

> `LoxoneUnauthorisedError` during setup returns `False` instead of retrying — a Miniserver reboot (firmware update) permanently kills the integration with valid credentials

## Summary

When the Loxone Miniserver reboots (here: an automatic firmware update to 17.0.3.31), its web server comes back up **before authentication is fully initialized** and briefly answers **401 Unauthorized** to requests that would normally succeed. PyLoxone's reconnect path re-runs `async_setup_entry`, which treats *any* `LoxoneUnauthorisedError` as a permanent credential failure (`return False`). The config entry lands in `setup_error` with **no retry and no reauth flow**, and stays dead until a human reloads it — in our case 10.5 hours later. The stored credentials were valid the whole time (verified independently, see below).

The asymmetry is the bug: every other failure during setup — 503 out-of-service, timeout, `OSError`, even generic `Exception` — raises `ConfigEntryNotReady` so HA retries with backoff. Only 401 is treated as fatal, and a booting Miniserver emits exactly that 401.

## Environment

| Component | Value |
|---|---|
| PyLoxone | 0.9.23 (HACS, latest at time of incident) |
| Home Assistant Core | 2026.8.1 (HAOS, bare-metal NUC) |
| Miniserver | snr `50:4F:94:10:9A:CB`, firmware **17.0.3.31** after the update, `http://10.0.3.1:80` |
| Auth config | username/password (`admin`), token flow; `hashAlg` reported by `getkey2` = SHA1 |

## Timeline (all times CEST, from HA `system_log`)

| Time | Event | Source |
|---|---|---|
| 21:54:18 | `Miniserver out of service:` — websocket drops, Miniserver starts firmware update/reboot | `pyloxone_api/connection.py:642` |
| 21:54:19 | First `Service Unavailable (503)` (4 occurrences until 21:56:06) — the `ConfigEntryNotReady` retry loop working **as designed** | `pyloxone_api/loxone_http_client.py:223` |
| 21:55:05, 21:55:41 | `Timeout error for http://10.0.3.1/jdev/cfg/apiKey` (2×) — still rebooting, still retrying correctly | `pyloxone_api/loxone_http_client.py:105` |
| 21:56:11 | HTTP server is back up, but auth is not ready yet: `GET /data/LoxAPP3.json` → **`Unauthorized (401)`** → `LoxoneUnauthorisedError` → `Failed to get structure file` → `Could not connect to Loxone Miniserver` → **`setup_error`** | chain: `loxone_http_client.py:191` → `connection.py:908` → `coordinator.py:68` → `__init__.py:266` |
| 21:56:11 → 08:45 next day | Integration dead. No retries, no repair issue, no reauth prompt. | — |
| 08:45 (2026-09-03) | Manual `homeassistant.reload_config_entry` → connects instantly with the **same stored credentials**; structure file fetched (softwareVersion sensor now reads 17.0.3.31) | — |

## Proof the credentials were valid

Run the morning after, before the manual reload, with the exact username/password stored in the config entry:

```
$ curl -u 'admin:*****' -o /dev/null -w "%{http_code}" http://10.0.3.1/data/LoxAPP3.json
200

$ curl 'http://10.0.3.1/jdev/sys/getkey2/admin'
{"LL":{"control":"dev/sys/getkey2/admin","code":"200", ...}}
```

A second, independent client (a Loxone MCP server) also authenticated against the same Miniserver without issue during the outage window. The 401 at 21:56:11 was purely a boot-phase artifact of the Miniserver.

## Root cause (PyLoxone 0.9.23 code walkthrough)

**1. The reconnect path funnels a runtime drop back into `async_setup_entry`.**
When the websocket dies, `handle_task_result` in `custom_components/loxone/__init__.py` catches `LoxoneOutOfServiceException` / `LoxoneConnectionError` / `ConnectionClosedOK` and schedules `_reload_after_delay(1.0)`, which calls the `loxone.reload` service → `hass.config_entries.async_reload(...)` → `async_setup_entry` runs again while the Miniserver is still booting.

**2. In `async_setup_entry`, 401 is the only non-retryable branch** (`custom_components/loxone/__init__.py`, ~line 256):

```python
    try:
        await coordinator.async_config_entry_first_refresh()
    except LoxoneServiceUnAvailableError as err:
        # "unavailable (service restarting?). Will retry automatically"
        raise ConfigEntryNotReady from err
    except LoxoneUnauthorisedError:
        _LOGGER.error(
            "Could not connect to Loxone Miniserver. Unauthorised. Please check username and password."
        )
        return False                      # <-- permanent setup_error, no retry, no reauth
    except OSError as err:
        raise ConfigEntryNotReady from err
    except (LoxoneConnectionError, LoxoneConnectionClosedOk, TimeoutError, ConnectionError) as err:
        raise ConfigEntryNotReady from err
    except Exception as err:
        raise ConfigEntryNotReady from err   # even unknown errors retry — only 401 doesn't
```

The 503 branch shows the reboot scenario was anticipated ("service restarting? Will retry automatically") — the missing piece is that a rebooting Miniserver transitions through a short window where it answers **401** instead of 503, after the web server is up but before auth is initialized.

**3. Where the 401 originates:** `connection.py::open()` fetches the structure file over HTTP (`connector.get(LOXAPPPATH)`); `loxone_http_client._handle_error` maps status 401 → `LoxoneUnauthorisedError`, which propagates up unchanged.

## Suggested fix (for the upstream issue)

Either of these, ideally both:

1. **Make 401 retryable with a bound.** Treat `LoxoneUnauthorisedError` during setup like the other transient errors (`raise ConfigEntryNotReady`) for the first N attempts (or the first ~5 minutes) before concluding the credentials are actually wrong. HA's `ConfigEntryNotReady` backoff already provides the pacing. A simple heuristic that would have covered this incident: if the connection *previously succeeded with these same credentials this HA session* (i.e. we got here via the reconnect/reload path, not initial user setup), a 401 should always start as retryable.
2. **When 401 persists, raise `ConfigEntryAuthFailed` instead of `return False`.** That is the HA-idiomatic terminal state for bad credentials: the user gets a visible reauth prompt in the UI instead of a silently dead entry that only recovers via a manual reload.

## Workaround until fixed

- One manual reload of the config entry fixes it (Settings → Devices & Services → PyLoxone → Reload, or `homeassistant.reload_config_entry`).
- This will recur on every Miniserver firmware update / reboot that PyLoxone happens to catch in the 401 boot window, so an HA automation that watches for the entry entering `setup_error` shortly after a `Miniserver out of service` log line and fires a reload is a viable stopgap.

## Raw log excerpt (HA system_log, deduplicated)

```
2026-09-02 21:54:18 ERROR custom_components.loxone.pyloxone_api.connection (connection.py:642)
    Miniserver out of service:
2026-09-02 21:54:19 ERROR custom_components.loxone.pyloxone_api.loxone_http_client (loxone_http_client.py:223)  [count: 4, until 21:56:06]
    Service Unavailable (503): <errorcode>503</errorcode> <errordetail>Service Unavailable</errordetail>
2026-09-02 21:55:05 ERROR custom_components.loxone.pyloxone_api.loxone_http_client (loxone_http_client.py:105)  [count: 2]
    Timeout error for http://10.0.3.1/jdev/cfg/apiKey
2026-09-02 21:56:11 ERROR custom_components.loxone.pyloxone_api.loxone_http_client (loxone_http_client.py:191)
    Unauthorized (401): <errorcode>401</errorcode> <errordetail>Unauthorized</errordetail>
2026-09-02 21:56:11 ERROR custom_components.loxone.pyloxone_api.connection (connection.py:908)
    Failed to get structure file: Unauthorized ...
    Failed to initialize connection: Unauthorized ...
    Traceback: ... loxone_http_client.py:194 in _handle_error → LoxoneUnauthorisedError
2026-09-02 21:56:11 ERROR custom_components.loxone.coordinator (coordinator.py:68)
    Could not connect to Loxone Miniserver
2026-09-02 21:56:11 ERROR custom_components.loxone (__init__.py:266)
    Could not connect to Loxone Miniserver. Unauthorised. Please check username and password.
→ config entry state: setup_error (until manual reload 2026-09-03 08:45)
```
