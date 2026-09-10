# PyLoxone code review — findings catalogue (2026-09-03)

Reviewed at commit `7561247` (== upstream JoDehli/PyLoxone tag 0.9.23). Every finding below was
verified by reading the code; line numbers refer to that commit. Items marked **VERIFY** are
internally inconsistent code where the correct direction needs confirmation against a live
Miniserver or the pinned HA version before changing behaviour.

Severity: **critical** = crashes HA / security / data loss; **high** = user-visible malfunction;
**medium** = wrong in some configurations, compliance, or robustness; **low** = quality/perf.
Effort: S < 1h, M = a few hours, L = a day or more for one agent.

The companion document `2026-09-remediation-plan.md` groups these IDs into work packages.

Upstream issue numbers refer to https://github.com/JoDehli/PyLoxone/issues.

---

## A. Connection layer — `custom_components/loxone/pyloxone_api/`

### API-01 [critical] `open()` never stores the connection; every setup opens two websockets and leaks one
- Where: `coordinator.py:66` (`await self.api.open(session)` return value discarded), `connection.py:463-465` (`start_listening` calls `open()` again because `self.connection` is `None`), `connection.py:1037-1057` (`open()` only *returns* the connection).
- Problem: the first socket (with HA's shared aiohttp session) is orphaned but stays connected with library pings; the second is opened with `session=None`. `LoxAPP3.json` is downloaded and parsed twice. Each reload adds another orphan; the Miniserver has a connection cap (error 901).
- Fix: in `open()` set `self.connection = connection` and keep `self._session`; make `start_listening()` reuse the open connection instead of re-running `open()`.
- Effort: S · Upstream: #486 #457 #487

### API-02 [critical] A clean close (code 1000) is invisible to the listen loop and surfaces 30s later as an ERROR
- Where: `connection.py:759-760` (`async for message in connection` — `websockets` swallows `ConnectionClosedOK` and the iterator simply ends), `connection.py:629-631` (`asyncio.wait(..., FIRST_EXCEPTION)` does not wake for a normal return), `connection.py:655-659` (`LoxoneConnectionClosedOk` is a custom class not matched by any specific handler → generic `except Exception` logs ERROR with traceback).
- Problem: token expiry, firmware restart and session-limit closes all take this path. The integration sits on a dead socket until `keep_alive` fails.
- Fix: after the `async for` ends, check `connection.close_code` and raise `LoxoneConnectionClosedOk`; use `return_when=FIRST_COMPLETED`; add `except LoxoneConnectionClosedOk` logging at INFO before the generic handler.
- Effort: M · Upstream: #514

### API-03 [high] `_LOGGER.error("Error while sending...", e)` has no placeholder → `--- Logging error ---` traceback on every send failure
- Where: `connection.py:281-283`.
- Fix: `_LOGGER.error("Error while sending command: %s", e)`.
- Effort: S · Upstream: #514

### API-04 [high] `_send_text_command` warns that the connection is closed, then sends anyway
- Where: `connection.py:272-283` — guard has no `return`/`raise`; `self.connection.send` on `None` raises `AttributeError`.
- Fix: raise `LoxoneConnectionClosedOk` (or return for keep-alives) when not connected.
- Effort: S · Upstream: #514

### API-05 [high] Bearer token, user salt/key and visual-password salt are fired on the HA event bus and logged
- Where: `connection.py:744-749` (`MessageType.TEXT` is in `callback_types`, so `gettoken`/`getjwt`/`getkey2`/`getvisusalt` responses reach the callback), `__init__.py:368-371` (`hass.bus.async_fire(EVENT, message)` + `_LOGGER.debug(f"{message}")`), `connection.py:1421-1426` (full token dict logged at ERROR), `websocket_protocol.py:47` (every sent command incl. auth hashes at DEBUG).
- Fix: do not forward protocol/auth text responses to the external callback (filter on `control` ∈ {getkey, getkey2, gettoken, getjwt, refreshjwt, refreshtoken, getvisusalt, keyexchange, authwithtoken}); redact tokens in logs; drop/truncate the `Sent:` debug line.
- Effort: M · Category: security

### API-06 [high] `websockets` protocol-level ping defaults (20s ping / 20s timeout) are not overridden
- Where: `connection.py:1028-1040` — `websocket_options` lacks `ping_interval`/`ping_timeout`.
- Problem: redundant with the Loxone `keepalive` (30s); a late pong kills a healthy connection with a 1011 and no diagnostics. **VERIFY** with `websockets` DEBUG logging against a real Miniserver.
- Fix: `ping_interval=None` (rely on Loxone keepalive) and explicit `close_timeout`.
- Effort: S · Upstream: #486 #457

### API-07 [high] `getvisusalt` handler overwrites the auth key `self._key` with a stringified dict
- Where: `connection.py:1319` (`self._key = value_dict.get("value", "")`).
- Problem: after any secured command, `_hash_token()` does `bytes.fromhex(self._key)` → `ValueError` → token refresh fails silently → token expires → clean close cascade.
- Fix: delete the line; the visual key already lives in `self._visual_hash`.
- Effort: S · Upstream: #514

### API-08 [high] `open()` retry loop (100 × 5s) blocks config-entry setup instead of letting `ConfigEntryNotReady` retry
- Where: `connection.py:819-836`, `const.py:12-13` (`RECONNECT_DELAY=5`, `RECONNECT_TRIES=100`). Only the first GET is retried; `LOXAPPPATH` (908) and `getPublicKey` (935) have none.
- Fix: ≤3 tries with exponential backoff, or no loop at all; apply uniformly.
- Effort: S · Upstream: #486

### API-09 [high] No in-place reconnect: every transient error propagates out and the integration reloads itself
- Where: `connection.py:589-614` (`reconnect_task` only raises `LoxoneTokenError` when `_reconnect_event` is set, which happens only on `authwithtoken` 401 at 1377-1380). See CORE-05 for the consumer side.
- Fix: supervising loop inside `LoxoneConnection` (re-`open`, re-auth, re-`enablebinstatusupdate`, exponential backoff with jitter) plus a connection-state callback so entities can flip `available` without being destroyed.
- Effort: L · Upstream: #475 #491 #486

### API-10 [high] Commands are sent as fragmented frames
- Where: `connection.py:276` (`await self.connection.send([command])` — a list triggers fragmentation) vs `connection.py:582-584` (key exchange sends a plain `str`, correctly).
- Fix: `await self.connection.send(command)`.
- Effort: S

### API-11 [medium] Loxone Cloud DNS redirect updates the host but not the scheme
- Where: `connection.py:895-904` (`self.url` rewritten from the redirect target), `connection.py:1020-1025` (`ws://`/`wss://` chosen from `self.scheme`, set once at `__init__` line 122). `_websocket_ssl_context` keys on the same stale scheme.
- Fix: derive `scheme`, `host`, `port` from `api_resp.url` after the redirect; strip trailing `/`.
- Effort: S

### API-12 [medium] Username is never URL-encoded in protocol commands
- Where: `connection.py:1166, 1243, 1272-1274, 1284-1286` (compare the encrypted path at line 270 which does `urllib.parse.quote`).
- Fix: `urllib.parse.quote(self.username, safe="")` once and reuse. **VERIFY** UTF-8 vs latin-1 percent-encoding expected by the Miniserver.
- Effort: S · Upstream: #506

### API-13 [medium] LL response codes other than `authwithtoken` 401 are ignored → bad credentials hang silently
- Where: `connection.py:1341-1364` (`gettoken`/`getjwt` handler never checks `mess_obj.code`; empty token → `ValueError` swallowed at the `except Exception` three lines later).
- Fix: check `code` in every auth handler; on 401/4003/1001 signal a typed `LoxoneUnauthorisedError`/`LoxoneTokenError` through `_reconnect_event`/a stored exception (the handler runs in a detached task, see API-14).
- Effort: M

### API-14 [medium] Fire-and-forget tasks swallow errors; `task_done()` fires before the send completes
- Where: `connection.py:784` (`_websocket_event` detached), `687-695` (each outbound command detached, `task_done()` immediately), `534-536`, `557` (`_refresh_token` detached).
- Fix: `await self._send_text_command(...)` inside `_process_message`; keep a `self._tasks: set` with a done-callback that logs/propagates for the rest.
- Effort: M · Upstream: #514

### API-15 [medium] `_secured_queue` stores un-awaited coroutines in a `maxsize=1` queue
- Where: `connection.py:203, 1171-1176, 1326-1333`; `_send_secure` dereferences `self._visual_hash.salt` without a `None` guard (386).
- Fix: queue parameters (dataclass), unbounded `deque`, clear it in `close()`, guard `_visual_hash is None`.
- Effort: S

### API-16 [medium] `check_refresh_token` spins at 1 Hz while no token exists; refresh failures are invisible
- Where: `connection.py:498-576` (`seconds_to_expire()` negative → clamp to 1s); `if self._key == old_key: continue` skips refresh entirely.
- Fix: wait on an "authenticated" event; `await` the refresh; escalate repeated failures to `LoxoneTokenError`.
- Effort: M · Upstream: #514

### API-17 [medium] Token persisted only at HA shutdown, never killed on the Miniserver; `unsecure_password` dropped
- Where: `__init__.py:578-589` (`stop_event` is the sole caller of `get_token_dict()`), no `killtoken` anywhere; `__init__.py:332-339` replaces the whole `data` dict (see CORE-10).
- Fix: persist on every token change via a coordinator callback, include `unsecure_password`, send `jdev/sys/killtoken/...` on entry removal.
- Effort: M · Upstream: #486

### API-18 [medium] Binary framing inferred from message length; `MessageHeader` for non-`0x03` leaves `payload_length` unset
- Where: `connection.py:766-790`, `message.py:200-212`. Any 8-byte payload is parsed as a header; a following `last_header.payload_length` access raises `AttributeError` and kills the listener. `websocket_protocol.py:51-103 recv_message()` implements the correct header→body sequencing but is dead code.
- Fix: always set `payload_length=0`/`estimated=False` in the UNKNOWN branch; better, adopt strict header→body reads.
- Effort: M

### API-19 [medium] `LoxAPP3.json` (often several MB) is `json.loads`-ed in the event loop
- Where: `connection.py:918-925`.
- Fix: parse in an executor (accept an optional `loads` callable or return raw bytes and let the coordinator parse via `hass.async_add_executor_job`).
- Effort: S

### API-20 [medium] Unreachable `except` blocks, possible `NameError` on `data`, unreleased aiohttp response
- Where: `connection.py:837-854` (`TimeoutError`/`ConnectionError` already caught as `OSError`), `data` only bound under `if api_resp:`, non-200 `LoxAPP3.json` response raised without `release()` (913-916).
- Fix: delete dead handlers, initialise `data = None`, use `async with` for responses.
- Effort: S

### API-21 [low] `struct.unpack("d", ...)` uses native byte order; truncated trailing records silently dropped
- Where: `message.py:272-287`.
- Fix: `"<d"`; `divmod(len, 24)` and log/raise on remainder.
- Effort: S

### API-22 [low] `check_and_decode_if_needed` is mostly unreachable (`latin-1` never fails); `detect_encoding` and the cache are dead
- Where: `message.py:24-27, 58-136`.
- Fix: `utf-8` then `latin-1` fallback; delete the rest.
- Effort: S

### API-23 [low] Dead code and stale declarations across the package
- `api.py` (8-line stub), `helper.py` (unused `hash_token`/`generate_hmac`; stdlib `HMAC` shadowed by `Crypto.Hash.HMAC`), `websocket_protocol.py:51-103` (`recv_message`), `message.py:30-55` (timers), `loxone_token.py` (`Salt`, unused imports), `const.py` (`THROTTLE_CHECK_TOKEN_STILL_VALID`, `TOKEN_REFRESH_*`, `CMD_ENCRYPT_CMD`, `DEFAULT_TOKEN_PERSIST_NAME`, `LOX_CONFIG`), `exceptions.py:27-97` (eight unused classes, some copy-pasted from a Samsung TV library), `connection.py:52-59` (`httpx` warnings filter — `httpx` is not imported anywhere), `__main__.py:5` (docstring argument order wrong).
- Fix: delete; drop `httpx` from `manifest.json` (see CORE-24).
- Effort: S

### API-24 [low] Misleading type hints and names
- `_send_text_command(...) -> TextMessage` returns `None` (244-246); `send_secured__websocket_command` double underscore (1155; callers at `__init__.py:396, 617`); `self._session_key: bytes` annotated but never initialised (149) → `start_listening:579` raises `AttributeError` not the intended `RuntimeError`.
- Effort: S

### API-25 [low] Crypto choices are protocol-mandated but undocumented
- Fixed AES-CBC IV per session (142-144, 266-269) is required by the Loxone key exchange; salt rotation (`SALT_MAX_USE_COUNT`/`SALT_MAX_AGE_SECONDS`) is the mitigation. `hashAlg` fallback to SHA1 (1260, `loxone_token.py:35`) is correct and handles #498.
- Fix: comment only; add a regression test for the missing-`hashAlg` case.
- Effort: S

### API-26 [low] Hot-path inefficiencies
- `message.py:375-380` (`BaseMessage.__subclasses__()` per message), `connection.py:773,784,788` (up to two `create_task` per message, unbounded during a state burst), `websocket_protocol.py:37-49` (f-string debug logs evaluated regardless of level).
- Fix: type→class dict at import; lazy `%s` logging; run the callback inline.
- Effort: S · Upstream: #475 (burst handling)

### API-27 [medium] Expected reconnect control flow is logged at ERROR
- Where: `connection.py:589-616` (`raise LoxoneTokenError` bare, as control flow), `637-660` (`ERROR: Token error`, `ERROR: Connection closed with error`, `ERROR: Miniserver out of service` for events the code then recovers from).
- Fix: dedicated `LoxoneReconnectRequested` exception at DEBUG; WARNING on first outage, DEBUG on repeats; one "lost/restored" pair per outage.
- Effort: M · Upstream: #514

---

## B. Integration core — `__init__.py`, `coordinator.py`, `miniserver.py`, `config_flow.py`, `diagnostics.py`, `system_health.py`, `const.py`, `helpers.py`, `manifest.json`, translations

### CORE-01 [critical] `LoxoneEntity.__init__` calls `sys.exit(-1)` inside the event loop
- Where: `__init__.py:668-675`. `SystemExit` is a `BaseException`; HA's `except Exception` guards do not catch it. The `AttributeError` branch logs `"Could set ..."` (typo) using `self.name`, which can itself raise. `fan.py:76,88,101,130` passes `device_class` kwargs that hit the read-only property and trigger this branch at every startup.
- Fix: `_LOGGER.exception("Could not set %s on %s", key, type(self).__name__)` and continue; longer term, stop `setattr`-ing raw JSON onto entities.
- Effort: S

### CORE-02 [critical] Entity bus listeners are never unsubscribed → unbounded leak across every reload
- Where: `__init__.py:691-697` (`self.listener = None` drops the unsubscribe callable without calling it). Correct pattern already exists at `sensor.py:555-558` and `climate.py:377-381`.
- Fix: `self.async_on_remove(self.hass.bus.async_listen(EVENT, self.event_handler))`; delete `async_will_remove_from_hass`.
- Effort: S · Upstream: #491 #514 #475

### CORE-03 [critical] The websocket listening task is never stored, never registered with HA, and can be garbage-collected
- Where: `__init__.py:568-576` (`listening_task` is a local), `102-112` (unload reads `coordinator._listening_task`, which nothing ever sets — dead code). Same class of bug at `602`, `616` (`_ = asyncio.create_task(...)`).
- Fix: `coordinator.listening_task = config_entry.async_create_background_task(hass, ..., name="loxone-listener")`; cancel on unload.
- Effort: M

### CORE-04 [critical] Services are registered per entry with a closure over one coordinator and removed unconditionally on unload
- Where: `__init__.py:625-633` (register), `126-133` (remove). `quick_shade`/`enable_sun_automation`/`disable_sun_automation` are cover *entity* services registered in `cover.py:84-99`; removing them here kills them for every other entry and logs "Unable to remove unknown service" on the second unload.
- Fix: register domain services once (guard with `hass.services.has_service`), resolve the coordinator per call from the target entity's `config_entry_id`; never remove entity services here; remove domain services only when the last entry unloads.
- Effort: M · Upstream: #491

### CORE-05 [critical] Reconnect strategy reloads *every* config entry via a service call from a task done-callback
- Where: `__init__.py:319-366` (`_reload_after_delay` → `hass.services.async_call("loxone", "reload")`), `421-431` (`handle_reload` unloads all entries, then `async_reload` unloads them again). `except Exception as e: raise e` at 365-366 raises inside a done-callback. No backoff.
- Fix: interim — `hass.config_entries.async_schedule_reload(config_entry.entry_id)` with backoff; final — API-09 in-place reconnect and no reload at all. `loxone.reload` service should reload only the targeted entry.
- Effort: L · Upstream: #491 #475

### CORE-06 [high] `EVENT_HOMEASSISTANT_STOP`/`STARTED` listeners accumulate across reloads; stale `stop_event` can overwrite a good token
- Where: `__init__.py:635-636` (unsubs discarded). `STARTED` never fires again, so group creation silently never runs after a reload.
- Fix: `config_entry.async_on_unload(...)` for all four listeners; `homeassistant.helpers.start.async_at_started` for the startup hook.
- Effort: S

### CORE-07 [high] `Platform.TEXT` missing from `LOXONE_PLATFORMS` → `text.py` is dead code
- Where: `const.py:13-27`. README line 64 advertises TextInput. Users get only the read-only `LoxoneTextSensor` (`sensor.py:228-231`). See PS-03 for bugs inside `text.py` itself.
- Fix: add `Platform.TEXT`; decide whether the sensor mirror stays.
- Effort: S

### CORE-08 [high] Diagnostics dump the whole `LoxAPP3.json` unredacted, for the wrong entry, and may return `None`
- Where: `diagnostics.py:13-21` (iterates `hass.data[DOMAIN]` and returns the first; ignores `config_entry`; no `async_redact_data`). Structure file contains `serialNr`, `localUrl`, `remoteUrl`, `projectName`, users, rooms.
- Fix: use `hass.data[DOMAIN][config_entry.entry_id]`; `async_redact_data` with `{serialNr, localUrl, remoteUrl, projectName, mac, token, password, username, key}`; also redact entry options.
- Effort: S · Category: security

### CORE-09 [critical] A transient 401 during setup permanently kills the integration (`return False`, no retry, no reauth); connection leaked on two error branches
- Where: `__init__.py:257-269`. `LoxoneUnauthorisedError` is the *only* setup failure that neither raises `ConfigEntryNotReady` nor closes the API; it does `return False`, which parks the entry in `setup_error` until a human reloads it. `LoxoneServiceUnAvailableError` also skips `await coordinator.api.close()`. No `async_step_reauth` exists (CORE-19).
- Incident: documented in `2026-09-02-pyloxone-401-setup-error.md` (repo root). A Miniserver firmware update (17.0.3.31) rebooted the device; its HTTP server came back before auth was initialised and answered `401` to `GET /data/LoxAPP3.json` at 21:56:11. The reconnect path (CORE-05) re-ran `async_setup_entry`, hit this branch, and the integration stayed dead for 10.5 hours with valid credentials (verified with `curl` and a second client during the outage). The 503/timeout branches retried correctly minutes earlier — only 401 is treated as fatal, and a booting Miniserver emits exactly that.
- Fix: never `return False`. Treat 401 during setup as retryable: raise `ConfigEntryNotReady` and count consecutive auth failures per entry (e.g. in `hass.data`/runtime data with a timestamp); only after N attempts spanning at least several minutes (suggest 5 attempts / 5 min) escalate to `ConfigEntryAuthFailed` (once WP-3.4 provides reauth) and a repair issue. Close the API in every failure branch. Same rule must apply inside the in-place reconnect loop (API-09): a 401 immediately after a reconnect is a boot artefact, not a credential change.
- Effort: S (retry + close) / L (reauth, see CORE-19)

### CORE-10 [high] `handle_task_result` replaces the entire `ConfigEntry.data` dict; coordinator checks key presence not truthiness
- Where: `__init__.py:327-341` (`data={"token": "", ...}` without `**config_entry.data`), `coordinator.py:47` (`if "token" in self.config_entry.data` — an empty-string token is still passed to `LoxoneConnection`).
- Fix: spread existing data; test `.get("token")`.
- Effort: S

### CORE-11 [high] `handle_websocket_command` dereferences a possibly-`None` registry entry; no service schema
- Where: `__init__.py:381-382`, `394-395`. Missing `uuid` and `device` silently sends `""` as UUID; entity not checked to belong to this integration.
- Fix: `vol.Schema` requiring exactly one of `uuid`/`device`; `ServiceValidationError` on unknown entity.
- Effort: S

### CORE-12 [high] `DataUpdateCoordinator` misused
- Where: `coordinator.py:20-45` (`config_entry` not passed to `super().__init__` → HA deprecation report; `async_config_entry_first_refresh` overridden without calling super, so `data`/`last_update_success` never set), `86` (`print("_async_update_data")`), `99-100` (`async_cleanup` calls `self.api.close()` on `None` if unload precedes first refresh), `29-32` (`options[...]` with `[]` → `KeyError` reported as a connection error).
- Fix: pass `config_entry=`; drop the print; guard `api is None`; either use `_async_setup()` or stop subclassing `DataUpdateCoordinator`.
- Effort: M

### CORE-13 [high] Unload tears down the coordinator before unloading platforms and ignores the unload result
- Where: `__init__.py:82-139` (`async_cleanup`, `hass.data` pop and service removal all happen before `async_unload_platforms`; a `False` result leaves a zombie LOADED entry; `except Exception as e: raise e` at 123-124).
- Fix: `unload_ok = await async_unload_platforms(...)`, then cleanup only if ok; register everything with `config_entry.async_on_unload`.
- Effort: M

### CORE-14 [medium] Redundant `async_load_platform` loop passes a `ConfigEntry` as `hass_config`
- Where: `__init__.py:307-317`. Verified: it creates no entities (every `async_setup_platform` is a stub except `sensor.py`, whose body is guarded by `if config:` and receives `{}`); YAML `platform: loxone` sensors are set up by the `sensor` component independently. It only survives because `async_forward_entry_setups` already registered the components. The discovery `EntityPlatform` it creates is never unloaded.
- Fix: delete the loop; delete the six stub `async_setup_platform` functions (`switch.py:28-35`, `button.py:28-35`, `number.py:26-33`, `text.py:26-33`, `select.py:84-91`, `scene.py:22-29`) and the `PLATFORM_SCHEMA` extensions that feed nothing (`alarm_control_panel.py:30-37`, `climate.py:75-79`).
- Effort: S

### CORE-15 [medium] Auto-group creation is broken in five ways
- Where: `__init__.py:433-566`: `lights` passed for the "Loxone Dimmer" group (520-522) while `dimmers` is unused; master group omits dimmers/climates/accontrollers (546-557); gated on `miniserver_type < 2` so Gen-2/Compact users get nothing (436); the comparison sits outside the `try` and raises `TypeError` when `miniserverType` is absent; runtime `async_setup_component(hass, "group", {})` (498) and test-only `hass.async_block_till_done()` (544). Also see PS-14 (device_type strings never match).
- Fix: fix the list, drop the gate, move inside `try`, declare `"dependencies": ["group"]`, remove `async_block_till_done`. Consider replacing groups with labels.
- Effort: M

### CORE-16 [medium] Miniserver device is never created; identity fields are wrong
- Where: `miniserver.py:91-116` — `async_update_device_registry` has **no callers**; `CONNECTION_NETWORK_MAC = "mac"` (16) populated with the host IP (105); `software_version` (71-72) iterates characters if `softwareVersion` is a string (same construct in `sensor.py:220-221`); `miniserver_id` (75-77) returns `config_entry.unique_id` which is always `None` (config flow never sets one) so dispatcher signals collide across entries; `identifiers={(DOMAIN, self.serial)}` with `serial` possibly `None`. Unused `asyncio`/`traceback` imports.
- Fix: call it from `async_setup_entry`; drop the fake MAC; type-aware version join; set entry `unique_id` to the serial (CORE-19).
- Effort: M

### CORE-17 [medium] Dead dispatcher wiring that also leaks
- Where: `cover.py:77-81`, `sensor.py:301-303`, `binary_sensor.py:87-92` subscribe to `async_signal_new_device` signals that nothing ever sends (`async_dispatcher_send` has zero callers); unsubs are appended to `MiniServer.listeners`, which is never iterated. `binary_sensor.py:29` defines `NEW_SENSOR = "binairy_sensors"` (typo, unused) and registers the same signal name as `sensor.py`. `cover.py:73-75` defines an unused `async_add_covers`.
- Fix: delete all of it (plus `MiniServer.listeners`/`async_signal_new_device`), or implement runtime discovery properly with `async_on_unload`.
- Effort: S

### CORE-18 [medium] `SchemaFlowError` raised with free text where a translation key is expected
- Where: `config_flow.py:36-39, 45-47`. No `config.error`/`options.error` section exists in any translation file.
- Fix: `SchemaFlowError("invalid_username_encoding")` etc. plus matching `error` keys in en/de/cs.
- Effort: S

### CORE-19 [medium] Config flow gaps: credentials in `options`, no unique id, no connection test, no reauth, no discovery, broken YAML import
- Where: `config_flow.py:108-133` (`SchemaConfigFlowHandler` stores everything in `options`; plaintext password round-trips to the frontend as a suggested value); no `async_set_unique_id` (same Miniserver can be added twice; `single_instance_allowed` translation key is dead); `validate_loxone_setup` never connects; `pyloxone_api/discover.py` exists but no `zeroconf`/`dhcp` step; `__init__.py:142-150` fires an `import` flow but no `async_step_import` exists → `UnknownStep` traceback at every startup for YAML users.
- Fix: hand-written `ConfigFlow` (user step with connection test → `unique_id = serial`, reauth, optional zeroconf) + `OptionsFlow` for preferences only; migrate creds to `data` (entry version 5); implement or delete the YAML import.
- Effort: L

### CORE-20 [medium] `helpers.device_registry` is a module-level global; first writer wins, dicts are shared by reference
- Where: `helpers.py:12-25`. Never cleared on reload (renames in Loxone Config need an HA restart), shared across config entries, the *same dict object* is handed to sibling entities as `_attr_device_info`, no `via_device`. See PC-04 for the concrete fan/presence symptom (upstream PR #513).
- Fix: build a fresh `DeviceInfo(...)` each time (registry de-duplicates on identifiers) or cache per entry; add `via_device=(DOMAIN, serial)`.
- Effort: M · Upstream: PR #513

### CORE-21 [medium] `system_health` reports only the first entry and has unguarded lookups
- Where: `system_health.py:21-42` (`return` inside the loop; `hass.data[DOMAIN]` `KeyError` if never set up; `hasattr(v, "miniserver")` always true because the coordinator initialises it to `None`; `["msInfo"][...]` unguarded; exposes `remoteUrl`).
- Fix: aggregate per entry; `.get()`; guard `miniserver is None`.
- Effort: S

### CORE-22 [medium] `override_reason` sensor translations are dead
- Where: `sensor.py:56-67` (`OVERRIDE_REASONS` values are display strings), `569-591` (no `_attr_translation_key`; runtime `_attr_options.append("Unknown (n)")`; magic clamp to 14 at 588 makes the fallback unreachable). Translations in en/de/cs use slug keys.
- Fix: `_attr_translation_key = "override_reason"`, slug values, a single `unknown` option.
- Effort: S

### CORE-23 [medium] Translation drift and stray escapes
- `de.json` lacks `services.sync_areas.*` (4 keys); `cs.json` has only the `entity` block (51 keys missing); no `error` sections anywhere; `services.yaml:71` and `en.json:74` contain a literal `re\-synchronized`; `config.abort.single_instance_allowed` is unused.
- Fix: backfill de, add error keys, remove the backslash and the dead key, add a CI key-parity check.
- Effort: S

### CORE-24 [medium] `manifest.json` / legacy declarations
- `iot_class` is `local_polling` (integration is websocket push); `httpx` required but never imported (HTTP layer is `aiohttp`); `pycryptodome` unpinned, `websockets>=14` unbounded; `dependencies: []` while `homeassistant.components.group` is imported at module scope; missing `integration_type`, `loggers`; `__init__.py:51` `REQUIREMENTS = [..., "numpy"]` is an HA 0.x relic that modern HA ignores.
- Fix: `local_push`; drop `httpx`; pin; `dependencies: ["group"]` (if groups stay); `loggers: ["custom_components.loxone"]`; delete `REQUIREMENTS`.
- Effort: S

### CORE-25 [critical] Declared minimum HA version is wrong everywhere and the code cannot import on the versions HACS advertises
- `hacs.json:6` says `2025.2.4`, `README.md:18` says `2024.1.0`, `requirements.txt` pins `2026.8.1`. `sensor.py:24` imports `UnitOfRatio` at module scope, which first exists in HA **2026.7.0** (verified by probing HA tags); `AlarmControlPanelState` (used in `alarm_control_panel.py`) does not exist in 2024.1. A user on 2025.2–2026.6 installs successfully via HACS and the integration dies with `ImportError`.
- Fix: set `hacs.json` to `2026.7.0` and the README to match (or add an import shim and CI-test the real floor).
- Effort: S

### CORE-26 [medium] No `has_entity_name`; `name`/`unique_id` overridden with `functools.cached_property`
- Where: `__init__.py:702-704, 728-731`. `_attr_has_entity_name` appears nowhere, so the UI shows "Living Room Light Living Room Light". The `name` override bypasses HA's translation/device-name logic and relies on `_attr_name` having no class default (line 663's `hasattr` routing). `switch.py:369` and `scene.py` assign `self.name = ...` directly.
- Fix: delete both overrides; set `_attr_name`/`_attr_unique_id` in `__init__`; adopt `_attr_has_entity_name = True` behind a release note (user-visible renames).
- Effort: M

### CORE-27 [medium] One global bus event per Miniserver message, one listener per entity, no per-entry namespacing
- Where: `__init__.py:368-371` (`hass.bus.async_fire(EVENT, message)`), `691-693` (every entity subscribes), `639-642` (`loxone_send` listener per entry on the same global event → with two Miniservers every command is sent to both; every state update reaches both entries' entities). All `event_handler`s are `async def` with no `await`, so HA creates a task per entity per event.
- Fix: one listener in the coordinator; `async_dispatcher_send(hass, f"loxone_{entry_id}_{uuid}", value)` per changed uuid; entities connect only to their own uuids with `@callback`; keep firing `loxone_event` for user automations (README documents it for the recorder). Route outbound commands through the entity's own coordinator.
- Effort: L · Upstream: #491

### CORE-28 [low] Entity availability is not tied to connection state
- No `available` on `LoxoneEntity`; entities keep reporting stale state until the reload storm recreates them. Depends on API-09.
- Effort: M · Upstream: #475

### CORE-29 [low] No options update listener; `async_config_entry_updated` stub is dead
- Where: `__init__.py:200-206`. Changing host/password/scene options has no effect until a manual reload.
- Fix: `config_entry.async_on_unload(config_entry.add_update_listener(...async_schedule_reload...))`.
- Effort: S

### CORE-30 [low] No repairs / issue-registry usage
- Candidates: invalid credentials, unsupported firmware, YAML config present, repeated reconnect failure. Upstream PR #515 adds message-center repairs.
- Effort: M

### CORE-31 [low] `hass.data[DOMAIN]` instead of `entry.runtime_data`; stale empty dict after last unload
- Where: `__init__.py:242-243, 305`; `miniserver.py:26-28`.
- Effort: M

### CORE-32 [low] Code hygiene in the core files
- f-string logging on the hot path (`__init__.py:370` runs for every message), German comments (`126, 340, 346, 352, 361`), unused imports (`EVENT_COMPONENT_LOADED`, `Platform`, `ATTR_COMMAND`, `DOMAIN_DEVICES`, `ERROR_VALUE`, `MiniServer`, `LoxoneConnection`, `LoxoneException`, `get_miniserver_type`), `_UNDEF` (77), mutable default `data={}` (398), `LoxoneEntity._clean_unit` duplicates `helpers.clean_unit` with a different `%%` fix, commented-out numpy helpers (`helpers.py:58-65`), `map_range` divides by zero when `in_min == in_max`, `get_all` crashes on missing `controls`/`type` (both reported by the Uni Ulm fuzzing PR #292).
- Effort: S

### CORE-33 [low] Two entity idioms coexist
- `LoxoneRoomControllerTemperatureSensor`/`OverrideSensor` (`sensor.py:537-591`) use explicit typed constructors, `_attr_unique_id` and `async_on_remove` — the right pattern — while ~30 other classes use `LoxoneEntity(**kwargs)` + `setattr` + `cached_property`. `_parent_uuid` stored but unused.
- Fix: converge on the explicit pattern.
- Effort: L

---

## C. Simple platforms — `sensor.py`, `binary_sensor.py`, `switch.py`, `button.py`, `number.py`, `text.py`, `select.py`, `scene.py`

### PS-01 [critical] `Any` used in annotations but never imported in `switch.py`
- Where: `switch.py:375, 379` (`**kwargs: Any`); no `typing` import and no `from __future__ import annotations`. On Python ≤ 3.13 the module fails to import (whole switch platform gone); on 3.14 it is latent until something resolves annotations.
- Fix: `from typing import Any` (and `from __future__ import annotations`).
- Effort: S

### PS-02 [high] Switches report `on` before their real state arrives; `turn_on` guard is dead
- Where: `switch.py:98-99, 203-204` (`self._attr_is_on = STATE_UNKNOWN` — the truthy string `"unknown"`), `239-248` (state written while unavailable, then `available` flipped on `uuidAction` alone), `225-232` (`if not self._attr_is_on` never true). Same in `LoxoneTimedSwitch`.
- Fix: `_attr_is_on = None`; write state once after the value is assigned; only mark available when `active` is known; remove the guard; coerce with `bool(float(v))`. Delete `_attr_state`/`_attr_assumed_state` annotations (93-94, 198-199).
- Effort: S · Upstream: #475

### PS-03 [high] `text.py` writes `_state` but reads `_native_value`; listens on the wrong uuid; no `native_max`
- Where: `text.py:66-69, 87-89, 96-108, 118`. Would show `""` forever even if the platform were loaded (CORE-07). `async_set_value` is not optimistic; `HA TextEntity` caps at 100 chars by default.
- Fix: assign `_attr_native_value`; listen on `states["text"]`; set `native_max` from the control.
- Effort: S

### PS-04 [high] Smoke alarm reads "signals muted" instead of alarm level; broken `if`/`elif`; unguarded `KeyError`
- Where: `binary_sensor.py:110-124` (`areAlarmSignalsOff` used for `SMOKE`; second `if` should be `elif`; `self.states["areAlarmSignalsOff"]` unguarded aborts the whole platform; `InfoOnlyDigital` uses `uuidAction` instead of `states["active"]`).
- Fix: `elif` chain, `.get()`, use `level` for smoke, per-control try/except in setup.
- Effort: S

### PS-05 [high] Unguarded `self.states[...]` in `event_handler`/`extra_state_attributes`
- Where: `switch.py:240, 290`; `select.py:153, 183`; `button.py:115`; `number.py:129`; `text.py:118`. `LoxoneIntercomSubControl` is built from unvalidated `subControls` (`switch.py:59-75`) — a missing `active` raises on every event.
- Fix: resolve state uuids once in `__init__` with `.get()`; skip the entity with a log when missing.
- Effort: S

### PS-06 [high] LightControllerV2 presence switch: guard and constructor read different keys
- Where: `switch.py:81-84` (`switch_entity.get("presence")` top-level) vs `364-373` (`kwargs["states"]["presence"]`). Either dead code or a `KeyError` that aborts the platform. `self.name = ...` at 368 assigns over a `cached_property`.
- Fix: `if "presence" in switch_entity.get("states", {})`; use `_attr_name`.
- Effort: S

### PS-07 [high] YAML sensor: `unique_id` raises `TypeError` without `name`; `value_template` is broken
- Where: `sensor.py:46-54` (`CONF_NAME` optional), `318-321` (`self.uuidAction + self._attr_name`), `191-206` (`CONF_VALUE_TEMPLATE` not in schema → arrives as `str` → `value_template.hass = hass` raises; template never applied anyway).
- Fix: fall back to `uuidAction` for the unique id; either add `cv.template` and render it, or delete the three lines.
- Effort: S

### PS-08 [high] One Meter without `storageFormat`/`totalFormat` aborts the entire sensor platform
- Where: `sensor.py:237-256` (`sensor["details"][format_key]` unguarded), `448` (`self.details["format"]`).
- Fix: `.get(format_key, "%.1f")`; per-control try/except in `async_setup_entry`.
- Effort: S

### PS-09 [medium] `ERROR_VALUE`/`None`/non-numeric values unhandled; rounding helper dead; blanket `MEASUREMENT`
- Where: `sensor.py:463-471` (unmatched units get `MEASUREMENT` even for text values → HA `ValueError`), `491-505` (`_get_lox_rounded_value` and `self._format` never used; `available` re-runs the `state` pipeline). `const.py:31 ERROR_VALUE = -1` is used nowhere, so `-1` is published as a real reading.
- Fix: map `ERROR_VALUE`/`None` to `None`; set `state_class` only for numeric values; drop the `available` override; use or delete the rounding helper.
- Effort: S · Upstream: #402 #492 #481

### PS-10 [medium] `device_class` properties return Loxone control-type strings
- Where: `sensor.py:417-420` (`"TextInput"`), `lights/lightcontroller.py:70-73` (`"LightControllerV2"`, which `scene.py:66` depends on as a marker), `cover.py:144` (`"Gate"`), `fan.py:198-234` (dead property/setter pair).
- Fix: return `None`/delete; expose the type via `extra_state_attributes["device_type"]` or device `model`.
- Effort: S

### PS-11 [medium] Binary sensors publish `off` before any value has arrived
- Where: `binary_sensor.py:100-131` (`_attr_available = True` unconditionally in `__init__`; `_state = STATE_UNKNOWN`), `182-185` (`is_on` returns `self._state == self._on_state` → `False`). Window/motion/alarm sensors claim "closed/no motion" until the first burst.
- Fix: `is_on` → `None` while unknown; keep unavailable until the first event.
- Effort: S · Upstream: #475

### PS-12 [medium] `should_poll` left at the default `True` on most entity classes; `update_before_add=True`
- Where: no `_attr_should_poll` on `LoxoneEntity`; set only in `sensor.py:387,449`, `switch.py:120,216`, `number.py:73`, `text.py:77`, `select.py:133`, `cover.py:131,454`. Climate, fan, lights, media player, alarm, `LoxoneWindow`, keep-alive/text/custom/climate-controller sensors all poll for nothing. `sensor.py:299,305` requests `update_before_add`.
- Fix: `_attr_should_poll = False` on `LoxoneEntity`; delete the per-class overrides; drop `update_before_add`.
- Effort: S

### PS-13 [medium] `async def event_handler` without any `await` → a task per entity per event
- Where: every `event_handler` in these files (`sensor.py:323,366,412,502,559,585,615`; `binary_sensor.py:163,213`; `switch.py:141,239,338,383`; `button.py:86`; `number.py:107`; `text.py:96`; `select.py:152`). `sensor.py:618` allocates a `set` per event.
- Fix: `@callback def`; precompute `frozenset` of state uuids. Superseded by CORE-27.
- Effort: S

### PS-14 [medium] `device_type` attribute strings never match the group-generation table
- Where: `sensor.py:477,512` (`"Sensor analog_sensor"`), `binary_sensor.py:153` (`"digital"`/`"presence"`/`"smoke"`), `switch.py:114` (`"TimeSwitch"`) vs `__init__.py:459-467` (`"analog_sensor"`, `"digital_sensor"`, `"TimedSwitch"`). Three groups are always empty.
- Fix: constants in `const.py` used on both sides; contract test.
- Effort: S

### PS-15 [medium] `number.py`: string sentinel as `native_value`, wrong state uuid, unguarded details, no unit/device class
- Where: `number.py:57-70` (`_state = STATE_UNKNOWN` returned as `native_value`; `details["min"/"max"/"step"]` unguarded), `102-119` (listens on `uuidAction` while advertising `states["value"]`; `schedule_update_ha_state` from the loop), `details["format"]` ignored.
- Fix: `None`; `states.get("value", uuidAction)`; `.get()`; reuse `clean_unit` + `match_sensor_description` for unit/device class/precision.
- Effort: M · Upstream: #492 #481

### PS-16 [medium] `button.py` overrides the `@final` `ButtonEntity.state`
- Where: `button.py:58, 73-84, 105-108` (`# noinspection PyFinal`); press timestamp does not move until the Miniserver echoes `active`; bypasses `RestoreEntity`. Bespoke `DeviceInfo` at 121-130 diverges from other platforms.
- Fix: delete the override; expose the echo as an attribute if wanted.
- Effort: S

### PS-17 [medium] Scene generation is time-delayed, untracked, and scrapes HA internals
- Where: `scene.py:32-106` (`hass.loop.call_later` never cancelled → callback after unload; `hass.data["light"].get_entity`; depends on the fake `device_class` marker; no `_attr_device_info`; `self.name = name`; returns `True` from `-> None`). Moods are already in `states["moodList"]` in the structure file.
- Fix: build scenes directly from `get_all(loxconfig, "LightControllerV2")` with no timer; if a delay stays, `async_call_later` + `async_on_unload`.
- Effort: M

### PS-18 [medium] `LoxoneClimateController` sets `self.hass` in `__init__` and fans out bus events from inside a listener
- Where: `sensor.py:262` (`"hass": hass` kwarg), `602-647` (one `CLIMATE_EVENT` per linked room → O(rooms²); `schedule_update_ha_state()` from the loop; set allocated per event).
- Fix: drop the kwarg; `async_dispatcher_send` per uuid; `async_write_ha_state`.
- Effort: M

### PS-19 [medium] `select.py`: empty options accepted, `locked` not enforced, double state write
- Where: `select.py:118-130, 142-164, 186`. A `Radio` with no outputs yields `options == []` (HA rejects); `async_select_option` sends while locked; two writes per event; redundant property overrides.
- Fix: skip empty Radios; raise `HomeAssistantError` when locked; single write.
- Effort: S

### PS-20 [medium] Keep-alive and version sensors have no device and no `entity_category`; version sensor stores `"unknown"` string
- Where: `sensor.py:350-403`. Both float outside the Miniserver device; `LoxoneVersionSensor` swallows exceptions into `STATE_UNKNOWN`.
- Fix: `DeviceInfo(identifiers={(DOMAIN, serial)})`, `EntityCategory.DIAGNOSTIC`, `None`; consider `entity_registry_enabled_default=False` for keep-alive.
- Effort: S

### PS-21 [medium] Blanket `TOTAL_INCREASING` for every kWh/L-formatted value
- Where: `sensor.py:97-127`. "Consumption today" values that reset, and the Meter `totalNeg` register, get `TOTAL_INCREASING` → spurious spikes in the energy dashboard.
- Fix: explicit per-register descriptions for Meter (`actual`→POWER/MEASUREMENT, `total`/`totalNeg`→ENERGY/TOTAL_INCREASING, `storage`→MEASUREMENT); `TOTAL` or `MEASUREMENT` for plain `InfoOnlyAnalog` unless name/category indicates a meter.
- Effort: M

### PS-22 [low] Static metadata (`uuid`, `platform`, `room`, `category`, `state_uuid`, `device_type`) is written to the recorder on every state change
- Where: `__init__.py:679-689` and every platform's `extra_state_attributes`.
- Fix: `_unrecorded_attributes = frozenset({...})`; move room/category to the device registry.
- Effort: S

### PS-23 [low] `_attr_state: None = None` / `_attr_assumed_state: None = None` nonsense annotations; `self._assumed` never read
- Where: `switch.py:93-94, 98-99, 198-199, 203-204, 208, 362`; `binary_sensor.py:101, 106`.
- Effort: S

### PS-24 [low] Duplicated setup boilerplate across all platforms; duplicated unit helper
- Every platform repeats `get_miniserver_from_hass` → `lox_config.json` → `get_all` → `add_room_and_cat_to_value_values`. `LoxoneEntity._clean_unit` duplicates `helpers.clean_unit`.
- Fix: shared `iter_controls(hass, entry, types)` helper; delete the duplicate.
- Effort: M

### PS-25 [low] Dead code and small defects
- `binary_sensor.py:8-23` unused imports (`cv`, `vol`, `CONF_*`, `DOMAIN`, `SENDDOMAIN`); `switch.py:64-65` `_` used as a real variable; `sensor.py:588` magic `14`; `const.py:31 ERROR_VALUE` unused; `sensor.py:454` `if precision:` treats `0` as "none".
- Effort: S

### PS-27 [medium] `NfcCodeTouch` and `LightsceneRGB` controls produce no entities
- Where: no platform matches either `type`. Confirmed against a live Miniserver
  (firmware 17.2.8.28) on 2026-09-10: both are present in a real structure file and
  the integration creates nothing for them.
- **`NfcCodeTouch`** (Loxone NFC Code Touch, read-only in practice). Live states:
  `lastuser`, `lastcode`, `lasttag`, `lastid`, `codeDate`, `historyDate`, `events`,
  `keyPadAuthType`, `nfcLearnResult`, `deviceState`, `jLocked`. `details` is just
  `{"jLockable": true}`, no sub-controls.
  Natural mapping: a sensor for `lastuser` (who last authenticated) with `codeDate` as
  a timestamp, plus diagnostic sensors for `deviceState`. `lastcode`/`lasttag` identify
  a credential and should **not** be exposed as state — treat them the way CORE-08
  treats the serial.
  This is an access-control device, so an HA event on each authentication is more
  useful than a polled sensor: it lets an automation react to a specific person
  arriving.
- **`LightsceneRGB`** (two present, in a home cinema). Live states: `activeScene`,
  `color`, `red`, `green`, `blue`, `jLocked`. `details` carries `sceneList` (empty on
  this installation) and `jLockable`.
  Natural mapping: a `light` with `ColorMode.RGB` driven by `red`/`green`/`blue`, or a
  `select` over `sceneList` when it is populated. Note the separate `color` state
  alongside the three channels: establish which one is authoritative before writing,
  since `lights/colorpickers.py` already has a parser for Loxone colour strings.
- Fix: two small platform packages following the WP-4.x pattern. Neither appears in
  any upstream issue, so there is no reported-behaviour reference: the block shapes
  above are the only evidence, and anything beyond them is inference that belongs in
  `LIVE-MINISERVER-CHECKS.md`.
- Effort: M (two packages) · Upstream: none

### PS-26 [gap] Control types that are cheap to add with existing patterns
- `InfoOnlyText` (clone of `LoxoneTextSensor`); `EnergyManager`/`EnergyManager2`/`PowerUnit`/`Wallbox` (Meter sub-state loop generalises); `Tracker` (JSON list, pattern in `LoxoneClimateController`); `UpDownDigital` (two buttons); `PresenceDetector` illumination/noise sub-sensors (#461, pattern in `fan.py:81-135`); `IntercomV2` (#466); message center → repairs (#515, upstream PR exists); `InfoOnlyDigital` device class from `details.text.on/off` and category (#402); recursive `get_all` over `subControls` (`helpers.py:125-135` scans top level only); icons from `details.image`.

---

## D. Complex platforms — `climate.py`, `cover.py`, `fan.py`, `alarm_control_panel.py`, `media_player.py`, `light.py`, `lights/*`

### PC-01 [critical] `eval()` on strings received from the Miniserver websocket
- Where: `lights/lightcontroller.py:198, 212, 216` (`activeMoods`, `moodList` after `replace("true","True")`, `additionalMoods`); `lights/colorpickers.py:103, 247, 254` (`hsv(...)`/`temp(...)` strings). Arbitrary code execution from a spoofed/compromised Miniserver (plain HTTP on 8080 is the default; TLS verification is user-disableable).
- Fix: `json.loads` for the mood payloads; regex parser `(hsv|temp)\(([\d.]+),([\d.]+)(?:,([\d.]+))?\)` for colours; try/except and skip.
- Effort: S · Category: security

### PC-02 [high] `NameError: comfort_cool` when only `target_temp_low` is set in building-protect mode
- Where: `climate.py:536-560` (fault at 556; `comfort_cool` only bound in the `target_temp_high` block at 512). Also semantically wrong — compare against `frostProtectTemperature`.
- Effort: S · Upstream: #416

### PC-03 [high] RGB colour picker: `turn_on` with brightness only sends nothing; `hs_color[0]` on `None`
- Where: `lights/colorpickers.py:181-238` (`_attr_color_mode` starts `UNKNOWN`, so neither branch at 213/225 matches and the `else` at 236 is unreachable when brightness is present; 219 dereferences `self.hs_color`).
- Fix: always emit exactly one command (hs → kelvin → mode-with-known-value → `setBrightness`).
- Effort: S · Upstream: PR #512

### PC-04 [high] Ventilation device is named/typed after its presence sub-sensor
- Where: `fan.py:80` constructs `LoxoneDigitalSensor` *before* `LoxoneVentilation` (138); `binary_sensor.py:137-147` overwrites `self.uuidAction` with the parent id so `unique_id` collides, then seeds `get_or_create_device` (CORE-20) with name `"<Fan> - Presence"`, model `"presence"`, area `""`. Meter sub-sensors have the same first-writer hazard (`sensor.py:473-480` vs `517-521`).
- Fix: parent-first construction; do not overwrite `uuidAction` (keep state uuid as unique id, parent only for `device_info`); see CORE-20.
- Effort: M · Upstream: PR #513

### PC-05 [high] Standalone colour pickers would register a device with identifier `None`
- Where: `lights/colorpickers.py:49-53, 160-164` (`else` branch passes `self._light_controller_id`, which is `None` there). `LumiTech` at 288 does it right. Latent only because standalone `ColorPickerV2` is never instantiated (PC-43).
- Fix: `self.unique_id`.
- Effort: S

### PC-06 [high] Alarm `code_format` is always TEXT; a property has side effects
- Where: `alarm_control_panel.py:91-99` (`code_arm_required` sets `self._code = "required"`), `248-255` (`re.search("^\d+$", "required")` never matches). `_validate_code` (241) is dead; codes are forwarded to the Miniserver via `SECUREDSENDDOMAIN`, which is correct.
- Fix: `_attr_code_arm_required = isSecured`; `_attr_code_format = CodeFormat.NUMBER if isSecured else None`; delete `_code`/`_validate_code`.
- Effort: S · Upstream: #413

### PC-07 [high] `stop_cover` on Window sends full-open/full-close; Gate reverses direction
- Where: `cover.py:315-324` (Window: closing → `fullopen`, opening → `fullclose`), `184-192` (Gate: opposite direction). `LoxoneJalousie.stop_cover` (585) correctly sends `stop`.
- Fix: Window → `stop`. Gate: **VERIFY** — re-send the same direction or `stop`; current opposite-direction logic is certainly a reversal.
- Effort: S · Upstream: #501 (related)

### PC-08 [high] Fan does not declare `FanEntityFeature.TURN_ON`/`TURN_OFF`
- Where: `fan.py:175-178`. Required since HA 2024.8 (compat shim removed ~2025.1); `_enable_turn_on_off_backwards_compatibility` is never set.
- Effort: S

### PC-09 [high] `LoxoneVentilation.set_preset_mode` is an empty no-op
- Where: `fan.py:242-243`; `PRESET_MODE` is advertised, `async_turn_off` (279-285) relies on it, `STR_TO_VENTILATION_PROFILE_SETTABLE` (31-33) is unused.
- Fix: implement (`setMode/<id>` — **VERIFY** command name); validate the mode.
- Effort: S

### PC-10 [high] `AcControl.get_state_value` raises `KeyError` for missing states (#479 only partially fixed)
- Where: `climate.py:775-779` (`self._stateAttribUuids[name]`); used by `target_temperature` (887), `fan_mode` (898), `swing_mode` (930), `hvac_mode` (816). `LoxoneRoomControllerV2.get_state_value` (432-436) is the correct version.
- Fix: `.get(name)` with default; mirror the V2 signature.
- Effort: S · Upstream: #479 #398

### PC-11 [high] `TunableWhiteLight.async_turn_on` crashes before the first state; sends `temp(50.0,None)`
- Where: `lights/colorpickers.py:70-92` (`_attr_brightness` never initialised → `hass_to_lox(None)` `TypeError`; brightness-only branch formats `None`). `RGBColorPicker` has the `or 255` fallback at 185.
- Fix: default brightness/kelvin at the top of `turn_on`.
- Effort: S

### PC-12 [high] `RoomControllerV2.target_temperature` returns `None` in dual modes while `TARGET_TEMPERATURE` is advertised
- Where: `climate.py:580-591` (falls off the end for AUTO/MANUAL_HEAT_COOL with COMFORT/ECONOMY/BUILDING_PROTECT), `383-403` (`TARGET_TEMPERATURE` unconditional, `TARGET_TEMPERATURE_RANGE` gated on three conditions).
- Fix: final `return self.get_state_value("tempTarget")`; advertise TARGET **or** RANGE, never both.
- Effort: M · Upstream: #416

### PC-13 [high] Unknown Loxone mode values raise `ValueError` inside `event_handler`
- Where: `climate.py:51-56` (`ActiveMode(base_value)` for values outside `{0,1,2,3,4,14,112}`), `67-73`, `417-430` (exception aborts the loop; entity freezes).
- Fix: try/except, keep previous value, log once; or `_missing_` → `UNKNOWN`.
- Effort: S

### PC-14 [high] Cover entity services are registered for Gate and Window, which lack the methods
- Where: `cover.py:84-99` (no `required_features`); `enable_sun_automation`/`disable_sun_automation`/`quick_shade` exist only on `LoxoneJalousie` (622-632) and are sync `def` calling `hass.bus.fire` — **VERIFY** whether HA runs them on the loop (would raise from `verify_event_loop_thread`).
- Fix: `required_features=[SUPPORT_SUN_AUTOMATION]`/`[SUPPORT_QUICK_SHADE]`; make them `async def` with `async_fire`.
- Effort: S

### PC-15 [medium] `animation` property raises `KeyError` where `__init__` was defensive
- Where: `cover.py:506-508` (Jalousie) and `146-148` (Gate) index `self.details["animation"]` directly; `__init__` (364-367) guarded it into `self._animation`, which is never read. `device_class` calls it on every write.
- Effort: S

### PC-16 [medium] Unguarded `states[...]`/`details[...]` indexing across five platforms
- Where: `cover.py:195, 241-243` (Window `targetPosition` — #501 — and `direction`), `407-413` (Jalousie `shadePosition`), `343-346` (mutates the shared structure dict to inject `autoInfoText`/`autoState`, see PC-36); `alarm_control_panel.py:103,107,119,123,127`; `media_player.py:106,110`; `climate.py:151, 364` (`details["timerModes"]` aborts the whole climate platform), `776`.
- Fix: shared `_state_uuid(name)` helper returning `.get()`; `if (u := ...) and u in e.data`.
- Effort: M · Upstream: #501

### PC-17 [medium] `lox2hass_mapped` clamps but does not rescale; write path ignores min/max
- Where: `helpers.py:50-55` (`(90,10,90) → 229.5` not 255), `lights/dimmer.py:73,98-114`, `lights/lightcontroller.py:148,179-194`. Slider snaps back on every full-brightness command.
- Fix: real bidirectional mapping via the existing `map_range`.
- Effort: M

### PC-18 [medium] HA brightness 1–2 rounds to Loxone `0` (off); brightness stored as float
- Where: `lights/dimmer.py:73,105-109`; `lightcontroller.py:148`; `colorpickers.py:250` vs `106,258` (int).
- Fix: `max(1, round(...))` on write when `> 0`; `round` on read.
- Effort: S

### PC-19 [medium] Full float precision sent in commands; undocumented random jitter
- Where: `colorpickers.py:78,89,196,207,221,231,238` (`hsv(0.0,100.0,39.21568627450981)`); `cover.py:592-619` (`random.uniform(0.000000001, 0.009)` ×3 to force a state echo).
- Fix: explicit formatting; named constant with a comment for the jitter.
- Effort: S

### PC-20 [medium] `setOperationMode/0` typo
- Where: `climate.py:727-730` vs `690` (`setOperatingMode`). Selecting the "schedule" preset sends an unknown command.
- Effort: S

### PC-21 [medium] LightControllerV2 mutates shared `event.data` in place
- Where: `lights/lightcontroller.py:205-212`. Every later listener sees `True`/`False`; corrupts mood names containing "true"/"false". Subsumed by PC-01.
- Effort: S

### PC-22 [medium] `_attr_color_mode = ColorMode.UNKNOWN` is not in `supported_color_modes`
- Where: `lights/colorpickers.py:32, 142`. HA logs an error on every write until the first colour event.
- Fix: leave unset, or initialise to the single implied mode.
- Effort: S

### PC-23 [medium] `AcControl.temperature_unit` returns Fahrenheit when `°` is at index 0
- Where: `climate.py:874-881` (`find("°")` truthiness). Two other correct copies exist at 292-305 and 469-486.
- Fix: one shared `_temperature_unit_from_format(fmt)`.
- Effort: S · Upstream: #398

### PC-24 [medium] `AcControl` declares FAN_MODE/SWING_MODE unconditionally; `json.loads(None)`
- Where: `climate.py:742-748, 907-924, 939-957` (`fan_modes`/`swing_modes` return `None`; `set_*` parse `None`; send `setFan/None` on unknown names; JSON parsed three times per property read).
- Fix: compute features from present states; `[]` not `None`; parse once in `event_handler`.
- Effort: M · Upstream: #398

### PC-25 [medium] `AcControl.set_hvac_mode(OFF)` sends `off` then `setMode/1`
- Where: `climate.py:829-857`.
- Fix: return after `off`.
- Effort: S

### PC-26 [medium] Legacy `IRoomController` mode table contradicts the V2 enum in the same file — **VERIFY**
- Where: `climate.py:263-289, 322-338` (`0=Auto,1=Heat,2=Cool,3=Heat/Cool,4=Off`) vs `58-65` (`3=MANUAL_HEAT_COOL, 4=MANUAL_HEAT, 5=MANUAL_COOL`). If V2 is right, `set_hvac_mode(OFF)` sends manual heating. `hvac_modes` hardcodes all five.
- Fix: confirm against a real V1 structure file; single shared table.
- Effort: M

### PC-27 [medium] Custom feature bits `1024`/`2048` OR'ed into `CoverEntityFeature`
- Where: `const.py:66-67`, `cover.py:397, 401`. No collision today (HA tops out at 128) but silent when HA claims those bits; `IntFlag` KEEP boundary hides it.
- Fix: move far out of HA's range or gate services on `isinstance`+`is_automatic`.
- Effort: S

### PC-28 [medium] `RoomControllerV2.hvac_action` is permanently IDLE without a `ClimateController` control (regression from `7561247`)
- Where: `climate.py:361, 405-410, 635-646` (`_demand` fed only by `CLIMATE_EVENT` from `sensor.py:634-637`; line 409 duplicates 408 without the default).
- Fix: fall back to the room controller's own states; delete 409.
- Effort: M

### PC-29 [medium] Ventilation `set_percentage` interpolates a mode *name* (or `None`) into `setTimer`
- Where: `fan.py:245-254` (`VENTELATION_INT_TO_STR.get(mode)` → `"Low"`/`None`; one-hour timer for a plain speed change).
- Fix: raw integer mode; guard `None`; **VERIFY** whether a non-timed speed command exists.
- Effort: S

### PC-30 [medium] `percentage` returns the raw Loxone `speed` unvalidated
- Where: `fan.py:224-227`. Float, possibly > 100.
- Fix: clamp/round; set `_attr_speed_count`.
- Effort: S

### PC-31 [medium] Alarm arm-home/arm-away parameter appears inverted relative to the state mapping — **VERIFY**
- Where: `alarm_control_panel.py:189-213` (`arm_home` → `delayedon/0`, `arm_away` → `delayedon/1`) vs `222-225` (`armed and disabled_move` → `ARMED_HOME`). Loxone's parameter is the disable-movement flag.
- Fix: swap after confirming round-trip on a live Miniserver.
- Effort: S

### PC-32 [medium] `preset_mode` can return a value not in `preset_modes`; `preset_modes` is dynamic
- Where: `climate.py:412-415, 695-713` (FIXED=14 / FIXED_DYNAMIC=112 are not in `timerModes` → `None`; `PRESET_SCHEDULE` removed dynamically; condition at 708 simplifies to `not (is_auto and is_overridden)` and `is_auto` includes `MANUAL_HEAT_COOL`).
- Fix: stable literals for fixed modes; constant `preset_modes`; add the extra names to translations.
- Effort: M

### PC-33 [medium] LightControllerV2 `turn_on` with brightness but no master value sends nothing; effect+brightness drops brightness
- Where: `lights/lightcontroller.py:140-156`; magic `changeTo/99` and `[778]` all-off sentinel (199).
- Fix: final `else`; handle both; named constants; share scaling with dimmer.
- Effort: M

### PC-34 [medium] `voluptuous.Any`/`Optional` used as type annotations
- Where: `fan.py:13, 225, 264`. Survives only via `from __future__ import annotations`; breaks `get_type_hints`.
- Fix: `int | None`, `typing.Any`.
- Effort: S

### PC-35 [medium] `device_class` returning non-enum strings (see PS-10)
- Where: `cover.py:136-144` (`"Gate"`), `lights/lightcontroller.py:70-73`, `fan.py:198-234`.
- Effort: S

### PC-36 [medium] `LoxoneJalousie.__init__` mutates the shared structure JSON
- Where: `cover.py:343-346` (injects `""` keys into `miniserver.lox_config.json`; `""` then participates in `in e.data` tests at 411-412). `climate.py:364` shows the correct copy discipline.
- Fix: local uuid attributes with `.get()`.
- Effort: S

### PC-37 [low] `masterColor` sub-control filter uses `find(...) > 1` instead of `> -1`
- Where: `light.py:81-85`.
- Effort: S

### PC-38 [low] `async_turn_off(self)` without `**kwargs`
- Where: `lights/colorpickers.py:64, 175`.
- Effort: S

### PC-39 [low] `_attr_min_color_temp_kelvin = 2000` below the documented Loxone range (2700)
- Where: `lights/colorpickers.py:20-21, 129-130`; `helpers.py:69` docstring.
- Effort: S

### PC-40 [low] Dead code in the complex platforms
- `helpers.py:42-47` (`lox2lox_mapped`), `68-83` (mired converters — confirmed unused; the lights are fully Kelvin-based), `cover.py:73-75, 118-121, 369-372`, `alarm_control_panel.py:158-175, 241-246` (`hidden`, `icon`, sync stubs, `_validate_code`), `alarm_control_panel.py:30-37`/`climate.py:75-79` (`PLATFORM_SCHEMA` for no-op YAML), `colorpickers.py:128`, `dimmer.py:129-142` (duplicate device-info block), `fan.py:109-122, 256-258, 273-277`, `lightcontroller.py:23,28,75-77,167-169`, `media_player.py:146-149` (`async_media_stop` without the `STOP` feature), unused imports (`DeviceInfo`/`DOMAIN` in `lights/*`, `DOMAIN` in alarm).
- Effort: M

### PC-41 [low] Docstrings, comments, formatting
- `fan.py:1`/`alarm_control_panel.py:1` say "Interfaces with Alarm.com alarm control panels"; f-string logging (`light.py:108,130,150`, `climate.py:751`, `media_player.py:83,90`); `climate.py:408-409` duplicate assignment; `cover.py:527` `shade_postion_as_text`; `climate.py:567` nested same-quote f-string (3.12+) with a suspicious `//`; `-> None` functions returning `True` (`climate.py:95`, `cover.py:43`, `light.py:48`, `alarm_control_panel.py:47,66`, `media_player.py:47`).
- Effort: S

### PC-42 [low] `PLATFORM_SCHEMA` imported from component modules — **VERIFY** availability in HA 2026.8.1
- Where: `alarm_control_panel.py:8-9`, `climate.py:14`, `sensor.py:16`. If removed upstream the whole platform fails to import. Cheap to delete since the YAML paths do nothing (except sensor/binary_sensor).
- Effort: S

### PC-43 [gap] Missing capabilities
- Standalone `ColorPickerV2` controls are never created (`light.py` only walks `LightControllerV2.subControls`); `AudioZoneV2` lacks sources/favourites/metadata/mute/on-off/shuffle/repeat/play_media; alarm lacks `ARM_NIGHT`/`ARM_VACATION`/`TRIGGER` and arming-delay surfacing (#323); Jalousie has no auto/shade select; Gate lacks `SET_POSITION` despite tracking `position`; Window lacks stop/tilt; `IRoomControllerV2` sub-controls not enumerated; Ventilation `temperatureIndoor` sensor commented out, no boost/timer entity; AcControl improvements (#398).

---

## E. Tests, CI, tooling, packaging, docs

### TOOL-01 [critical] No CI job runs pytest or ruff
- `.github/workflows/` has only hassfest, HACS validation and a disabled stale bot. 69 tests pass locally (`pytest -q tests` → `69 passed`) but nothing runs them; the two `pyloxone_api` tests have been broken for an unknown time.

### TOOL-02 [critical] Release 0.9.23 ships `manifest.json` version `0.9.22`
- Upstream tag `0.9.23` = commit `7561247`; `manifest.json:14` says `0.9.22`. Version bumps are manual commits and were skipped. Needs a release workflow or a tag-vs-manifest guard.

### TOOL-03 [critical] Minimum HA version — see CORE-25 (canonical entry).

### TOOL-04 [high] `pyloxone_api/tests/` is unrunnable and excluded
- `pytest.ini` `testpaths = tests`; `test_run_alone.py:17` calls a fixture directly (collection error), imports `dotenv` (not installed), body is `pass`; `test_discover.py` needs a live Miniserver and `pytest.mark.online` is unregistered so it fails instead of skipping.
- Fix: delete `test_run_alone.py`; register the marker with `-m "not online"` default; add `python-dotenv`/`pytest-asyncio` to dev requirements if kept.

### TOOL-05 [high] `.gitignore` swallows every `*.yaml`/`*.yml` outside two directories
- `git check-ignore` confirms `.pre-commit-config.yaml` and `.github/dependabot.yml` would be ignored; `services.yaml` and `.vscode/*` are tracked only because they predate the rule. `pre-commit` is in `requirements.txt` with no config file.
- Fix: targeted ignores or negations.

### TOOL-06 [high] Dev container cannot install the project's own requirements
- `.devcontainer.json:3` uses `python:3.13`; `homeassistant==2026.8.1` requires Python ≥ 3.14.2 (verified resolution failure). `.devcontainer.json:33` and `.vscode/settings.json:5` hard-code `python3.13/site-packages`.

### TOOL-07 [high] `ruff check` is commented out of `scripts/lint`; 2161 violations; 33 of 48 files unformatted
- `ruff.toml` selects `ALL` with a 7-entry ignore list; `scripts/lint:8-9` disables the check. Genuine defects buried in the noise: `F821` ×2 (PS-01), `F811` (helper.py HMAC), `PLE1205` (API-03), `E722` (message.py:233), `S307` ×6 (PC-01), `T201` ×2.
- Fix: two-tier — blocking defect ruleset in CI (`F, E9, PLE, B, T20, S307, ASYNC, RUF006`), advisory full run; `per-file-ignores` for tests; one `ruff format` commit with `.git-blame-ignore-revs`.

### TOOL-08 [high] 14% coverage; 21 of 33 modules at 0%
- `climate.py` (516 stmts), `cover.py` (353), `switch.py` (221), `lights/*` (456), `connection.py` (900 stmts, 13%) untested. Existing tests are good but narrow (sensor matching, select mapping, migration, TLS).

### TOOL-09 [medium] `test_python_compatibilty.py` is a tautology
- Asserts `ruff.toml` contains `py314` and scans for Python-2 `except` syntax. Filename misspelled.

### TOOL-10 [medium] `requirements.txt` mixes runtime/dev/test deps; test deps missing
- `pytest` unpinned; `pytest-asyncio`, `python-dotenv` missing; `httpx` unused; `pip`/`debugpy` are devcontainer concerns. Use `pytest-homeassistant-custom-component==0.13.355` (pins HA 2026.8.1) in a `requirements-dev.txt`.

### TOOL-11 [medium] `pytest.ini` registers no markers, no `asyncio_mode`, no coverage floor.

### TOOL-12 [medium] Outdated/unpinned actions; stale bot disabled
- `actions/checkout@v3`, `actions/stale@v5`, `hacs/action@main` (floating); `stale.yaml` has only `workflow_dispatch` with write permissions.

### TOOL-13 [medium] README gaps
- No documentation of `verify_ssl`, `generate_scenes`, `generate_scenes_delay` (min 3, unexplained), `generate_lightcontroller_subcontrols`; only 1 of 7 services documented; logger snippet names `custom_components.loxone.api`, which does not exist (the package is `pyloxone_api`); minimum-version claim wrong (CORE-25).

### TOOL-14 [low] No dependabot, release workflow, CHANGELOG, CONTRIBUTING; issue template has no feature-request form although README invites them; free-text version fields.

### TOOL-15 [low] `manifest.json` lacks `loggers`/`integration_type` — see CORE-24.

### TOOL-16 [low] `conftest.py` is a docstring only, and the documented setup is insufficient (needs `pytest-asyncio`, Python ≥ 3.14.2).
