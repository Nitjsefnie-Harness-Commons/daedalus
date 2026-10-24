# Daedalus

Chrome-extension remote control for matching web pages: eval bridge, screenshots, CDP, cookies, network capture, hotfixes, declarativeNetRequest blocking. `README.md` is the reference for the command surface, install and usage; read it first. The full contract behind every entry below (edge cases, locking, storage) is in `.claude/skills/changing-daedalus/SKILL.md`, "Bridge internals reference": read the part for the code you change.

<rules>
These bind every session and task in this repo, not just the current edit, until explicitly overridden.
- Never claim anything about this codebase's API, field names or endpoints without opening the file. Field names (`id`, `code`, `type`, `tab`, `token`) are exact: verify against `server.py` or this file before writing bridge commands.
- Change only what is requested or clearly necessary: no cleanup around a bug fix, no extra configurability, no docstrings, comments or type annotations on code you didn't change, no defensive handling for scenarios that can't happen.
- Remove test artifacts you create in `$DAEDALUS_DIR/commands/`, `results/` or `uploads/` at the end of the task.
- Comments: concise, at most a 15% comment ratio (bytes of `#` comments and bare string statements over all bytes, whitespace included on both sides, across the repo's Python sources); exceed it only where the reasoning genuinely needs it. Write only what the code cannot show; never restate the next line.
- A server endpoint change includes the reverse-proxy vhost fronting the bridge: update it in the same pass and reload both.
- Restart the bridge process after editing `server.py`.
</rules>

<architecture>
Delivery is the MV3 Chrome extension only (no userscript).

**Extension (`extension/`)**
- Token: auto-generated on first install, in `chrome.storage.local`. Tab tracking: `chrome.tabs`, registered on create/update, 30s `chrome.alarms` heartbeat.
- `content.js` (ISOLATED world): page/background message relay. On load it asks the background to replay hotfixes (`replayHotfixes`); the background runs each eligible fix from the extension-wide `daedalus-hotfixes` record through eval routing, so a page whose CSP forbids dynamic compilation is reached over CDP. Hotfix source executes in the page (page-observable by design) but is not posted into it.
- `page.js` (MAIN world): the last-resort relay's `new Function` / `eval` and Blob-script paths, reached only when the source-free probe and CDP fallback cannot run submitted source; a hostile same-tab page can choose its one-shot value. Exposes the documented `window.GM` Tampermonkey-style subset (`xmlhttpRequest`, `openInTab`, storage methods, `addStyle`, `segmentJob`, etc.); no page-facing `GM.cookie`: cookie operations stay typed operator commands.
- `background.js` (service worker): SSE stream, command dispatch, fetch relay, screenshots, CDP, cookies, tabs, downloads, notifications, segment mint. The mint (`worker/segment_mint.js`) decides on the content script's `sender.origin` alone, compared verbatim with the `daedalus-segment-origins` allowlist, and fails closed: an unreported origin, an unlisted one or an empty list is refused before any bridge request.
  - Eval routing: a constant MAIN-world `Function` probe with no submitted source runs first; `true` selects banner-free `page-main` injection, anything else the CDP CSP fallback (a successful attach shows Chrome's debugger banner). Once either channel dispatches submitted source, every outcome is terminal; only an attach or shape failure before source runs reaches the page relay.
  - CDP `Runtime.evaluate`: REPL mode, direct values by reference, promise settlement bounded at 10 seconds, result and exception handles released even when a kept session or capture holds the attachment.
  - Trust: a page you do not control can choose the returned value on any channel. On `page-main` its `eval` / `Function` bindings can also read the submitted source; on `cdp` it can choose the value whenever that source uses page-controlled state or page promise machinery. `world` (`page-main`, `cdp`, `page:<hostname>`, the background adding the mandatory `page:` prefix so relay hostnames cannot collide, a descriptive property, not a trust boundary) records the channel, not trust.
  - Relay: at most 1,000 live entries (a new one at capacity gets a terminal error, without eviction); a live entry expires with one terminal error after 300,000 ms.
  - `store-hotfix` scope (`match` pattern, `clearScope` flag, refusals): skill, "Hotfix scope".

**Server (`server.py`)**
- `DAEDALUS_DIR` (command/result/upload root) and `DAEDALUS_PORT` are both required; the bridge refuses to start without either. A data root belongs to one bridge process: a second on an in-use root exits nonzero at startup.
- `server.py` keeps the entry point, `ThreadingHTTPServer`, `Handler(RequestMixin)` with its `do_*` verbs and the path comparisons dispatching them, `GET /health` and `PUT /command`. Every other route body lives in a `daedalus_bridge` module (map: skill, "Bridge modules"): routes `tab_registry`, `stream_route`, `stream_service`, `upload_routes`, `static_routes`, `result_routes`, `segment_routes`, `segment_jobs`; transport `http_transport` (the only module that reads `config`), `json_body`, `route_answer`; state and helpers `command_queue`, `queue_order`, `result_store`, `segment_store`, `delivery_stripes`, `path_safety`, `atomic_file`, `config`, `data_root_lock`, `env_config`, `log_safe`, `parent_watch`, `mcp_bootstrap`, `dashboard_drain`.
- A route function returns `(status, payload)` or a `FileAnswer`/`BytesAnswer`, never `None`, and never writes to the socket; the Handler writes it with `self.answer(...)`.
- Every route module and both stores bind no configuration of their own: a store takes its root directory and limits as parameters, so it imports with no bridge configuration set, and a route's directory argument governs every file the request writes.
</architecture>

<endpoints>
Use these when writing server changes or constructing raw HTTP requests; the per-route edge cases (path rules, `_did` handling, stream spelling rules) are in the skill's "Bridge internals reference". Field names are exact.

- Repeated authority carriers are rejected before any value is selected, equal or blank repeats included: `token` in a query or JSON body, `Authorization`, `X-Daedalus-Segment-Sig` (400 `duplicate segment capability header`), `job` / `sig` on segment-capability routes.
- Authenticated `GET`s: `Authorization: Bearer <token>`, then `?token=`. Body verbs (`POST`, `PUT`, `DELETE`) settle the bearer before reading the body; a body `token` counts only for a declared body within `DAEDALUS_MAX_UNAUTHENTICATED_BODY` (default `64 * 1024` bytes), and a larger body with no header gets 401 unread (`POST /segment` authorizes on the query string and headers and never reaches this gate). Two carriers must name the same token, else 400 `conflicting token`.
- `POST /segment` and `GET /segment-status` take their job capability, never the bridge token, from `X-Daedalus-Segment-Sig`, then `sig` in the query, by the same rules (`/segment-job` takes the bridge token). Every in-repo client sends header forms, so no reusable credential lands in a request target.

Routes:
- `GET /stream?token=X&tab=Y`: SSE command stream; the extension uses `tab=extension` and gets all tab-targeted commands. A keepalive every `DAEDALUS_STREAM_KEEPALIVE` seconds (default 15) finds dead peers; a reconnect of the same `(token, tab)` replaces the old stream; `DAEDALUS_STREAM_MAX_AGE` (default 3600) is a last-resort ceiling, not a rollover timer. `Connection: close` is load-bearing: with `keep-alive` the client sees silence instead of EOF and its reconnect waits out a watchdog.
- `GET /stream?tab=dashboard`: a fan-out event subscription, not an addressed delivery: every window on a token gets every event published while connected, so windows coexist and a reconnect never evicts a peer. Events (`tab-updated`, `tabs-synced`, `tab-unregistered`, `result`; frames carry `kind: 'event'`) carry a bridge-written `id` and `kind` no publisher can forge; `dashboard/sse.js` drops a repeat by `id`. Broadcast eval frames never reach it.
- `POST /poll` `{token}`: consume the legacy broadcast file `commands/<token>.json` for a client without an SSE stream, under the SSE drain's claim; answers the command or `{}`.
- `GET /tabs?token=X`: tabs with `age` in seconds since last registration; never pruned by age.
- `POST /register` `{token, tabId, url, title}` (first two required): refreshes an existing entry only and answers `{ok, updated}`; `updated` false means re-sync. `POST /sync-tabs` replaces the token's registry. `POST /unregister` removes an entry.
- `PUT /command`: `{token, tab, id, code}` for eval, `{token, tab, id, type, ...}` for typed extension commands; FIFO per-target queue. Answers `did` and `command` (the queue file's exact dict: body minus `token` and `tab`, plus `_did`); the SSE frame carries the same `_did` for dedup. Commands unclaimed for `DAEDALUS_CMD_TTL` seconds (default 90) are dropped, not delivered stale. With no `tab`, an eval runs once in the browser's active tab (result filed under that tab) and a typed command posts its result under `extension`.
- `POST /result`: the extension posts results; a nonempty command `_did` becomes `deliveryId` (also kept at `results/deliveries/<target>/<deliveryId>.json`), every result gets a fresh `resultGeneration`, the authenticating `token` is never stored, and an already-stored delivery id answers `{ok, duplicate: true}` without republishing. Delivery writes lock one stripe of a fixed per-process-secret table keyed on the delivery directory's entry; that a caller can discover stripe sharing from the wait its write incurs is accepted, since a bounded stripe table costs less than an unbounded lock table keyed on caller-named targets.
- `GET /result?token=X[&tab=Y][&delivery=D]`: peek the shared slot, or exactly delivery `D` (absent: `{pending:true}`). `consume=1` with `expected=<resultGeneration>` deletes only if that generation still holds the result (else `{consumed:false}`); without `expected` it is a destructive compatibility read.
- `GET /health` (no token; exposes no secrets): `{ok, uptime_s, active_streams, stream_tabs, registry:{tokens,tabs}, last_delivery_s_ago, cmd_ttl_s, stream_max_age_s, mcp}`, `mcp` being `starting` / `up` / `down` with no error detail. Use it to detect a silently dead stream server-side.
- `POST /upload` `{token, id, data (base64), format}` or `{token, id, data, filename}`: answers the `path` `<id>/<file>`, relative to the token directory, never the credential.
- `GET /upload?token=X[&id=Y]`: list; `&limit=N&offset=M` (default limit 200) returns `{items, total, limit, offset}`. `GET /upload?path=<id>/<file>` with the bearer header: exactly that file in the caller's namespace (a token-led selector is 400); the dashboard fetches uploads this way.
- `DELETE /upload`: `{token, id, filename}` | `{token, id}` | `{token}`.
- `GET /screenshot?token=X[&id=Y]`: latest screenshot; `&path=P`: exactly the upload `path` P (or legacy `<token>/<id>/<file>`). Ids are reused, so CLI and MCP clients download by `path`.
- `GET /dashboard[/<asset>]`: the dashboard from `dashboard/` in the repo; paths with `..` rejected; every response refuses framing (`Content-Security-Policy: frame-ancestors 'none'`, `X-Frame-Options: DENY`).
- `POST /segment-job` `{token, job}` → `{ok, sig}`: mint or re-fetch an HLS job's capability (idempotent for the owner; 409 for another owner or a reserved name; 500 for an unreadable record, never re-minted). `GET /segment-job?token=X&job=Y` looks up **without minting** (404 none, 409 another owner); status queries use it. The page never holds the bridge token: the extension mints via `GM.segmentJob` only for an allowlisted origin (`allow-segment-origin` / `revoke-segment-origin` / `list-segment-origins` over `daedalus-segment-origins`) and hands the page only the sig.
- `POST /segment?job=X&seg=N&total=T`: raw HLS segment to `$DAEDALUS_DIR/segments/{job}/{seg:06d}.ts`, refused before its body is buffered. `GET /segment-status?job=X`: `{done[], count}`.

Limits (default):
- `DAEDALUS_MAX_BODY_SIZE` (`64 * 1024 * 1024` bytes) on every `POST`/`PUT`/`DELETE` body: no `Content-Length` 411, invalid or negative 400, over 413.
- `DAEDALUS_REQUEST_TIMEOUT` (60 s) per socket operation; a stalled body gets 408.
- `DAEDALUS_MAX_REQUEST_WORKERS` (256, range 1-4096) concurrent connections; excess ones are closed unanswered, and held SSE streams count.
- `DAEDALUS_MAX_JSON_DEPTH` (100, range 1-500), checked on raw bytes before `json.loads`: 400 `JSON body too deeply nested`.
- `DAEDALUS_MAX_DELIVERY_RESULTS` (`1024`) unconsumed delivery files per target; eviction failures and ties favor retention.
- `DAEDALUS_MAX_SEGMENT_INDEX` (`99999`), `DAEDALUS_MAX_SEGMENTS_PER_JOB` (`10000`), `DAEDALUS_MAX_SEGMENT_JOB_SIZE` (`4 * 1024 * 1024 * 1024` bytes), fixed into each job at mint.

Caller values used as path components go through `daedalus_bridge/path_safety.py`'s `unsafe_component` (rejects `..`, C0/C1 controls, surrogates, Windows-invalid characters and device names, trailing dots/spaces, UTF-8 over 240 bytes); tokens are also nonempty, without dots or underscores. Job names may contain `&` and `#`: URL-encode them. Logs print at most the credential's 8-character prefix in every bridge-derived value (paths, queue and slot names, rendered OSErrors).
</endpoints>

<directories>
- Commands `$DAEDALUS_DIR/commands/`: queues drained FIFO by the SSE loop, `<token>_<tabId>/<seq>.json` (per-tab), `<token>/<seq>.json` (broadcast), `<token>_extension/<seq>.json` (typed), `<token>_dashboard/<ms>_<counter>.json` (dashboard events). `<seq>` is `<ms>_<counter>`, publish-ordered across clock steps and restarts (`queue_order.next_seq`). Legacy single files (`<token>.json`, `<token>_<tabId>.json`) only via a sibling `.tmp` write and same-directory atomic rename; readers ignore `.tmp`. Complete files older than `DAEDALUS_CMD_TTL` (default 90s) are TTL-dropped. Legacy `_did` scheme: skill, "Storage".
- Results `$DAEDALUS_DIR/results/`: `<token>_<tabId>.json` + `<token>.json` (last-writer-wins compat) and `deliveries/<target>/<deliveryId>.json`.
- Uploads `$DAEDALUS_DIR/uploads/<token>/<id>/`: screenshots and generic files.
- Segments `$DAEDALUS_DIR/segments/<job>/` with a `<job>.json` record (owner, capability, quotas, running totals); `.<job>.json.dirty` and `.<job>.json.tmp` are reserved names. `DAEDALUS_DEBUG_TIMING=1` prints a `[SEGMENT-TIMING]` line per segment write.
</directories>

<mcp>
An in-process MCP server runs beside `server.py` on `127.0.0.1:8086` (streamable HTTP), fronted by the reverse proxy at `<your-bridge>/mcp`. In `daedalus_mcp/`: `server.py` composes the listener, `auth.py` authenticates, `request_guard.py` decides pre-body refusals, `transport.py` owns bridge HTTP sessions, seven `tools_*.py` modules register the tool groups.

- Auth: `Authorization: Bearer <bridge-token>` must exactly match the token the CLI's configuration resolves (`DAEDALUS_TOKEN_OVERRIDE`, else `DAEDALUS_TOKEN`, including the optional `_settings` provider); no configured token fails closed with 401. Repeated `Authorization`, `Mcp-Session-Id`, `Host` or `Origin` headers, and repeated `job` arguments for `segment_job` / `segment_status`, are rejected without selecting a value.
- Credentials are decided before the body is read, so an unauthenticated request is never parsed; a declared body over `DAEDALUS_MCP_MAX_BODY_SIZE` (default `64 * 1024 * 1024` bytes) gets 413 unread, an undeclared one is bounded after.
- `DAEDALUS_MCP_PORT` (default 8086, range 0-65535) and `DAEDALUS_MCP_MAX_BODY_SIZE` use the bridge's bounded reader: an invalid value stops startup naming the setting. `DAEDALUS_MCP_ALLOWED_HOSTS` is the whole public-hostname allowlist (DNS rebinding protection is on, so the transport's localhost default does not apply).
- The bridge binds first and passes its actual loopback URL to the MCP HTTP client, `DAEDALUS_PORT=0` included; `DAEDALUS_LOCAL_URL` is the standalone override.
- Remote tools take inline JavaScript and CSS only: `put`, `inject_css`, `remove_css`, `store_hotfix` have no server-local path argument; the CLI reads file arguments itself and sends them inline.

43 tools:
- **Tabs**: `list_tabs`, `open_tab`, `open_tabs`, `focus_tab`, `close_tab`, `ext_navigate`, `ext_reload`
- **Eval / debug**: `exec`, `put`, `result`, `ping`, `navigate`, `reload`, `title`, `url`, `ext_self_reload`
- **Media**: `screenshot` (`include_image=true` inlines bytes), `segment_job`, `segment_status`, `allow_segment_origin`, `revoke_segment_origin`, `list_segment_origins`, `uploads`, `delete_upload`
- **Cookies**: `get_cookies`, `set_cookie`, `remove_cookie`, `clear_cookies`
- **CSS / blocking**: `inject_css`, `remove_css`, `block_requests`, `unblock_requests`, `list_block_rules`
- **Hotfixes**: `store_hotfix`, `clear_hotfix`, `clear_hotfixes`, `list_hotfixes`, `set_permanent`
- **Network / CDP**: `net_capture`, `net_capture_stop`, `net_capture_get`, `cdp`, `fetch_timings`

Every tool that sends a command takes `wait` (default `true`), except `navigate` and `reload`, which always report the command they enqueued; the six that send nothing (`list_tabs`, `result`, `segment_job`, `segment_status`, `uploads`, `delete_upload`) do not take it. `wait=true` polls and answers the result byte for byte; `wait=false` answers `{"command": <the command the bridge enqueued>}` (its `PUT /command` answer's `command`, the queue file's content) without validating `timeout`, polling, raising the bridge error or adding `roundtrip_ms`. The tool signatures, not this list, settle which tools take `wait`.

`net_capture`'s `max_requests` is an integer 1-20000 (`daedalus_mcp/tools_network.py`'s `NET_CAPTURE_MAX`, also enforced by the CLI's `--max` and the extension); the service-worker buffer holds headers and bodies, so the bound is a memory budget.

Manual check: `DAEDALUS_TOKEN=<tok> python3 scripts/mcp_probe.py list|call <tool> [json-args]`.
</mcp>
