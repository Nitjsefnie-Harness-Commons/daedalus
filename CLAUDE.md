# Daedalus

Daedalus is a Chrome-extension-based remote control system for matching web pages — eval bridge, screenshots, CDP, cookies, network capture, hotfixes, declarativeNetRequest blocking.

Read `README.md` first; it is the reference for the command surface. Full install + usage: `README.md`.

<investigate_before_answering>
Never make claims about this codebase's API, field names, or endpoints without reading the file first. Field names (`id`, `code`, `type`, `tab`, `token`) are exact — verify against `server.py` or this file before writing bridge commands. Never speculate about code you haven't opened.
</investigate_before_answering>

<scope>
These rules and notes apply to every session and every task in this repo, not just the current edit. Treat them as defaults in effect until explicitly overridden.
</scope>

<housekeeping>
- Don't over-engineer. Only make changes directly requested or clearly necessary. A bug fix doesn't need surrounding cleanup; a simple feature doesn't need extra configurability. Don't add docstrings, comments, or type annotations to code you didn't change. Don't add defensive handling for scenarios that can't happen.
- If you create test artifacts in `$DAEDALUS_DIR/commands/`, `$DAEDALUS_DIR/results/`, or `$DAEDALUS_DIR/uploads/`, remove them at the end of the task.
- Server endpoint changes are one unit with your reverse-proxy routing — update the vhost in the same pass and reload both.
</housekeeping>

<architecture>
Single delivery mode: Chrome extension (MV3). The legacy Tampermonkey userscript has been removed.

**Extension (`extension/`)**
- Token: auto-generated on first install, stored in `chrome.storage.local`.
- `content.js` (ISOLATED world): message relay between page and background. It also selects eligible objects from the extension-wide `daedalus-hotfixes` record and posts their full source-bearing objects into the page for replay; hotfix source is page-visible by design.
- `page.js` (MAIN world): provides the last-resort page relay's `new Function` / `eval` and Blob-script paths after the background's source-free injection probe and CDP fallback cannot run submitted source. A hostile same-tab page can choose the relay's one-shot JavaScript value. Exposes the documented `window.GM` Tampermonkey-style subset (`xmlhttpRequest`, `openInTab`, storage methods, `addStyle`, etc.); cookie operations remain typed operator commands, with no page-facing `GM.cookie` surface.
- `background.js` (service worker): SSE stream, command dispatch, fetch relay, screenshots, CDP, cookies, tabs, downloads, notifications. Eval first runs a constant MAIN-world `Function` probe containing no submitted source. A `true` result selects banner-free `page-main` injection; every outcome after that source dispatch is terminal. Any other probe result selects the CDP CSP fallback, whose successful attachment shows Chrome's debugger banner. CDP `Runtime.evaluate` uses REPL mode, reads direct values by reference, bounds promise settlement at 10 seconds, and releases result and exception handles even when a kept session or capture holds the attachment. Once CDP dispatches submitted source, every outcome is terminal; only attach or shape failure before source runs reaches the page relay. JavaScript evaluated in a page you do not control returns a value that page can choose, regardless of which channel executed it. On `page-main`, page-owned `eval` and `Function` bindings can also read the submitted source. On `cdp`, the page can choose the returned value whenever submitted source uses page-controlled state or page promise machinery. The `world` field records the execution channel (`page-main`, `cdp`, or `page:<hostname>`), not whether to trust the value. The background adds the mandatory `page:` prefix, so relay hostnames cannot collide with `cdp` or `page-main`; that namespace property is descriptive. The relay admits 1,000 live entries; a new one at capacity gets a terminal error without eviction, and a live entry expires with one terminal error after 300,000 ms.
- Tab tracking: `chrome.tabs` API, registered on create/update with a 30s `chrome.alarms` heartbeat.

**Server (`server.py`)**
- Service: restart the bridge process after editing `server.py`.
- Env: `DAEDALUS_DIR` (command/result/upload root) and `DAEDALUS_PORT` are both required; the bridge refuses to start without either.
- Reverse proxy: the vhost fronting the bridge (update + reload when endpoints change).
</architecture>

<endpoints>
Use these when writing server changes or constructing raw HTTP requests. Field names are exact.

Repeated authority carriers are rejected before any value is selected, including equal or blank repeats: `token` in a query or JSON body, and `job` / `sig` on segment-capability routes.

- `GET /stream?token=X&tab=Y` — SSE command stream. Extension uses `tab=extension` and picks up all tab-targeted commands. Liveness is policed by a keepalive comment every `DAEDALUS_STREAM_KEEPALIVE` seconds (default 15) — a dead peer raises on the next write — and by replacement when the same `(token, tab)` reconnects. `DAEDALUS_STREAM_MAX_AGE` (default 3600) is only a last-resort ceiling on a wedged connection, not a rollover timer. The response carries `Connection: close`, which is load-bearing: `keep-alive` makes `BaseHTTPRequestHandler` hold the socket open after the stream loop ends, so the client sees silence instead of EOF and its reconnect waits out a watchdog.
- `GET /tabs?token=X` — list active tabs, each with the `age` in seconds since its last registration. Nothing is pruned by age: an entry lives until it is unregistered or replaced by a sync.
- `POST /register` — refresh an existing tab entry (body: `token`, `tabId`, `url`, `title`; the first two are required). It does not create an entry; `POST /sync-tabs` replaces the token's registry and supplies new tabs.
- `POST /result` — extension posts results here. The server exposes a nonempty command `_did` as `deliveryId`, assigns a fresh `resultGeneration`, and writes the shared result slots while holding the result lock.
- `PUT /command` — submit command: `{token, tab, id, code}` for eval, or `{token, tab, id, type, ...}` for typed extension commands. Enqueues into a per-target directory queue (FIFO); back-to-back commands to the same tab queue instead of overwriting. Response includes the assigned `did` (delivery id); the delivered SSE frame carries it as `_did` so the extension can dedup a redelivered command. Commands unclaimed for `DAEDALUS_CMD_TTL` seconds (default 90) are dropped rather than delivered stale.
- `GET /result?token=X[&tab=Y]` — peek at the current shared result slot without deleting it.
- `GET /result?token=X[&tab=Y]&consume=1[&expected=G]` — consume the current result. With `expected=<resultGeneration>`, deletion occurs only when that generation still occupies the slot; a replacement is left in place and returns `{consumed:false}`. Without `expected`, this remains a destructive compatibility read that returns the deleted body.
- `GET /health` — bridge liveness: `{ok, uptime_s, active_streams, stream_tabs, registry:{tokens,tabs}, last_delivery_s_ago, cmd_ttl_s, stream_max_age_s}`. No token required (exposes no secrets). Use it to detect a silently-dead stream from the server side.
- `POST /upload` — store files: `{token, id, data (base64), format}` or `{token, id, data, filename}`.
- `GET /upload?token=X[&id=Y]` — list uploaded files.
- `DELETE /upload` — remove: `{token, id, filename}` | `{token, id}` | `{token}`.
- `GET /screenshot?token=X[&id=Y]` — serve latest screenshot.
- `GET /dashboard[/<asset>]` — serve the web dashboard (HTML + ES modules) from `dashboard/` in the repo. A reverse proxy routes it to the bridge; paths containing `..` are rejected.
- `POST /segment-job` — mint (or re-fetch) an HLS job's segment capability: `{token, job}` → `{ok, sig}`. Idempotent for the owning token; 409 when the job name is owned by a different token.
- `POST /segment?job=X&seg=N&total=T&sig=S` — raw binary HLS segment storage (`$DAEDALUS_DIR/segments/{job}/{seg:06d}.ts`); `sig` is the job-scoped capability from `POST /segment-job`, never the bridge token.
- `GET /segment-status?job=X&sig=S` — list received segments `{done[], count}`.

`DAEDALUS_MAX_BODY_SIZE` (default `64 * 1024 * 1024` bytes) bounds every request body read by `POST`, `PUT`, and `DELETE`; an invalid or negative `Content-Length` returns 400, and a declared body over the limit returns 413.

Filesystem-backed caller values share `_unsafe_component`: reject `..`, C0/C1 controls, surrogate code points, Windows-invalid path characters and device names, trailing dots/spaces, and UTF-8 encodings over 240 bytes. Tokens are also nonempty and reject dots and underscores. Accepted job names can still contain query delimiters such as `&` and `#`, so URL construction must encode them.

Every newly minted segment job records fixed values from `DAEDALUS_MAX_SEGMENT_INDEX` (default `99999`), `DAEDALUS_MAX_SEGMENTS_PER_JOB` (default `10000`), and `DAEDALUS_MAX_SEGMENT_JOB_SIZE` (default `4 * 1024 * 1024 * 1024` bytes). Existing jobs with valid quota fields continue to use their recorded values; the request-body limit separately caps each segment POST.

`GET /upload` accepts optional `&limit=N&offset=M` (default limit 200 when either is set); when paged, returns `{items, total, limit, offset}` instead of a bare array.

The dashboard subscribes to `/stream?tab=dashboard`. Events (not commands) are enqueued into `commands/{token}_dashboard/<ts>_<uuid>.json` by `_notify_dashboard` in server.py; the SSE loop drains that directory per tick. Emitted from: `/register` (`tab-updated`), `/sync-tabs` (`tabs-synced`), `/unregister` (`tab-unregistered`), `/result` (`result`). Dashboard frames carry `kind: 'event'`; broadcast eval frames that also reach the dashboard stream are ignored client-side.
</endpoints>

<directories>
- Commands: `$DAEDALUS_DIR/commands/` — directory queues drained FIFO by the SSE loop: `<token>_<tabId>/<seq>.json` (per-tab), `<token>/<seq>.json` (broadcast), `<token>_extension/<seq>.json` (typed extension commands), `<token>_dashboard/<ts>_<uuid>.json` (dashboard events). `<seq>` is `<ms>_<counter>`, lexically sortable. Legacy single-file drops (`<token>.json`, `<token>_<tabId>.json`) are supported only through sibling `.tmp` write plus same-directory atomic rename. Readers ignore `.tmp` names and leave malformed visible final files untouched so an in-progress older writer is never unlinked. Complete files older than `DAEDALUS_CMD_TTL` (default 90s) are TTL-dropped.
- Results: `$DAEDALUS_DIR/results/` — `<token>_<tabId>.json` + `<token>.json` (last-writer-wins compat). Results accepted through `POST /result` receive `resultGeneration`; those carrying `_did` also receive `deliveryId` for invocation matching.
- Uploads: `$DAEDALUS_DIR/uploads/<token>/<id>/` — screenshots + generic files.
- Segments: `$DAEDALUS_DIR/segments/<job>/` — HLS segment relay; `<job>.json` beside the directory records the owning token and the minted capability.
- Dashboard assets: `dashboard/` in the repo (served by server.py at `/dashboard`).
</directories>

<mcp>
An MCP server runs in-process alongside `server.py` on `127.0.0.1:8086` (streamable-HTTP transport), fronted by the reverse proxy at `<your-bridge>/mcp`. Before dispatch, it requires `Authorization: Bearer <bridge-token>` to exactly match the token resolved through the CLI's existing configuration path (`TOKEN`, otherwise `DAEDALUS_TOKEN`, including the optional `_settings` provider); no configured token fails closed with `401`. It rejects repeated `Authorization`, `Mcp-Session-Id`, `Host`, and `Origin` headers, and repeated `job` arguments for `segment_job` / `segment_status`, without selecting one value. Source: `mcp_server.py`. Port overridable via `DAEDALUS_MCP_PORT`; public-hostname allowlist via `DAEDALUS_MCP_ALLOWED_HOSTS` (FastMCP auto-enables DNS rebinding protection on localhost binds).

The bridge listener is bound before MCP starts. Its actual loopback URL is
passed into the MCP HTTP client, including for `DAEDALUS_PORT=0`;
`DAEDALUS_LOCAL_URL` remains the explicit standalone override. Remotely
callable MCP tools accept inline JavaScript and CSS only: `put`, `inject_css`,
`remove_css`, and `store_hotfix` have no server-local path argument. CLI file
arguments are read by the CLI process and sent as inline content.

40 tools, by group:
- **Tabs**: `list_tabs`, `open_tab`, `open_tabs`, `focus_tab`, `close_tab`, `ext_navigate`, `ext_reload`
- **Eval / debug**: `exec`, `put`, `result`, `ping`, `navigate`, `reload`, `title`, `url`, `ext_self_reload`
- **Media**: `screenshot` (optional `include_image=true` to inline bytes), `uploads`, `delete_upload`, `segment_job`, `segment_status`
- **Cookies**: `get_cookies`, `set_cookie`, `remove_cookie`, `clear_cookies`
- **CSS / blocking**: `inject_css`, `remove_css`, `block_requests`, `unblock_requests`, `list_block_rules`
- **Hotfixes**: `store_hotfix`, `clear_hotfix`, `clear_hotfixes`, `list_hotfixes`, `set_permanent`
- **Network / CDP**: `net_capture`, `net_capture_stop`, `net_capture_get`, `cdp`, `fetch_timings`

Manual verification helper: `TOKEN=<tok> python3 scripts/mcp_probe.py list|call <tool> [json-args]`.
</mcp>
