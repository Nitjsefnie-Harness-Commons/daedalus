# Daedalus

Remote browser control via a Chrome extension. Eval bridge + persistent hotfixes + per-tab control + screenshots, CDP, cookies, network capture on pages where Chrome runs the extension.

## Install

**Know this before you install:** ordinary eval uses banner-free MAIN-world
injection. When a source-free CSP probe cannot establish that dynamic
compilation is available, Daedalus tries the CDP fallback; a successful attach
shows Chrome's "Daedalus started debugging this browser" banner while that
fallback runs. A kept CDP session or network capture can hold the attachment
longer. The banner identifies a debugger attachment, not a value-integrity
guarantee.

Load the unpacked extension (`extension/`) in Chrome:

1. Visit `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**, select `extension/`

A unique token is auto-generated on first install and stored in `chrome.storage.local`. View/change it from the extension's options page (puzzle icon → Daedalus → Options).

For multi-tab parallel scraping, disable Chrome's background tab throttling:
```
chrome --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding
```

The bridge itself (`server.py`) is stdlib-only and needs no install. The CLI
and the in-process MCP endpoint are the Python package in this repository:

```bash
pip install .            # the `daedalus` CLI
pip install ".[mcp]"     # ... and the MCP endpoint's dependencies
```

Without the `mcp` extra the bridge still starts and serves everything else; it
reports the failed MCP bootstrap at startup and `/mcp` is not served.

### Verifying a published release

Every release publishes the wheel, the source distribution and a `SHA256SUMS`
file covering both. `SHA256SUMS` proves the files go together; it does not
prove where they came from, since it is published by the same authority as the
artifacts. Each artifact therefore also carries a GitHub build attestation — a
signed statement naming the workflow, the commit and the runner that produced
it:

```bash
gh attestation verify daedalus_cli-<version>-py3-none-any.whl \
  --repo Nitjsefnie-Harness-Commons/daedalus
```

Releases are immutable and `v*` tags cannot be moved or deleted, so a version,
once published, names one set of files permanently.

## How It Works

1. **Token**: Generated once on install via `crypto.randomUUID()`, stored in `chrome.storage.local`
2. **Tab IDs**: Chrome's native `tabs` API — each tab is identified by its Chrome `tabId`, registered on create/update with a 30s `chrome.alarms` heartbeat
3. **Single SSE stream**: `background.js` opens one persistent `fetch` SSE connection (`tab=extension`) and dispatches incoming commands to the right tab
4. **Page bridge**: `content.js` (ISOLATED world) relays `window.GM` messages between background and `page.js` (MAIN world). Eval uses `chrome.scripting` MAIN-world injection first. A source-free dynamic-compilation probe routes CSP-blocked pages to CDP; failure to attach there reaches the page relay. Every channel executes with page `MAIN`-world semantics.
5. **Hotfixes**: Stored in the extension-wide `chrome.storage.local` key `daedalus-hotfixes` (not per-token), replayed by the background in each eligible top-level page on load — MAIN-world injection, or CDP where page CSP forbids dynamic compilation — and version-gated for non-permanent fixes. Rotating the token neither isolates nor clears this store.

## Sending Commands

Daedalus exposes its extension command surface as an MCP server at `<your-bridge>/mcp` (streamable-HTTP transport). Before dispatching a request, it requires the Bearer value to exactly match the bridge token resolved through the CLI's existing configuration path: `TOKEN` overrides `DAEDALUS_TOKEN`, including an optional `_settings` provider. With no configured token, the MCP surface fails closed with `401`. The endpoint exists only when the `mcp` extra is installed (see "Install" above). Add it to Claude Code (or any MCP client) with:

```json
{
  "mcpServers": {
    "daedalus": {
      "url": "https://daedalus.example.com/mcp",
      "headers": { "Authorization": "Bearer <your-bridge-token>" }
    }
  }
}
```

The bridge token is the one the extension generates on install — visible in the extension options page (puzzle icon → Daedalus → Options).

That example fronts the MCP server with a public hostname, but the transport's default allowed hosts are loopback-only (`127.0.0.1:*,localhost:*`): name the public hostname in `DAEDALUS_MCP_ALLOWED_HOSTS` or the proxied requests are rejected. See "Server" below for all three MCP settings.

40 tools in 7 groups — tabs, eval/debug, media, cookies, CSS/blocking, hotfixes, network/CDP. See `CLAUDE.md` `<mcp>` section for the full list, or call `tools/list` on the MCP endpoint.

Manual verification helper (no MCP client needed):

```bash
TOKEN=<tok> python3 scripts/mcp_probe.py list
TOKEN=<tok> python3 scripts/mcp_probe.py call title '{"tab_id":"<tabId>"}'
TOKEN=<tok> python3 scripts/mcp_probe.py call screenshot '{"include_image":true}'
```

### CLI

A shell CLI ships as the `daedalus-cli` wheel in this repository
(`daedalus_cli/`), installed as the `daedalus` command. It reads `DAEDALUS_URL`
and `DAEDALUS_TOKEN` from the environment, with `TOKEN` as a one-off override
and `ID=<tabId>` for per-tab targeting (omit it to broadcast):

```bash
DAEDALUS_TOKEN=<tok> daedalus tabs
DAEDALUS_TOKEN=<tok> ID=<tabId> daedalus title
DAEDALUS_TOKEN=<tok> daedalus exec myid 'document.title'
```

`daedalus --help` lists every subcommand, and each takes its own `--help`. The
`exec` code you send is an expression or a function body whose return value
comes back as the result — see "Sending Commands" above for the contract.

An optional import seam is also available: if a module named `_settings` is
importable on `sys.path`, the CLI uses its `setting(name, default)` and
`required(name)` functions for `DAEDALUS_URL` and `DAEDALUS_TOKEN` instead of
the built-in environment fallback. The `TOKEN` environment variable remains
the one-off token override and takes precedence; without `_settings`,
`DAEDALUS_URL` uses its default and `DAEDALUS_TOKEN` is required. `ID` remains
the environment variable for per-tab targeting.

Or atomically publish a raw command file (no MCP, no CLI). Direct redirection
to the final `.json` name is unsupported because the stream can observe a file
before the writer finishes. Write a sibling name ending in `.tmp`, then rename
it within the same directory:

```bash
# Broadcast to all tabs
commands_dir="$DAEDALUS_DIR/commands"
final="$commands_dir/<token>.json"
tmp="$(mktemp "$commands_dir/.<token>.XXXXXX.tmp")"
printf '%s\n' '{"id":"test1","code":"document.title"}' > "$tmp" &&
  mv "$tmp" "$final"

# Target a specific tab
final="$commands_dir/<token>_<tabId>.json"
tmp="$(mktemp "$commands_dir/.<token>_<tabId>.XXXXXX.tmp")"
printf '%s\n' '{"id":"test1","code":"document.title"}' > "$tmp" &&
  mv "$tmp" "$final"
```

The reader ignores sibling `.tmp` names. If an older writer exposes malformed
JSON at the final name, the reader leaves it untouched and retries instead of
deleting a possibly in-progress write. After the atomic rename, the SSE stream
delivers and consumes the command. Result lands in `$DAEDALUS_DIR/results/<token>_<tabId>.json` (per-tab) and `$DAEDALUS_DIR/results/<token>.json` (last-writer-wins). `page-main` injection and page-relay eval completions can carry an `exec_ms` field — the page-context execution time in milliseconds. A page can falsify or omit this page-timed field on either channel. CDP completions carry no `exec_ms`. Results associated with a queued delivery also carry `roundtrip_ms` — the full server-observed roundtrip in milliseconds, from command enqueue (`PUT /command`) to result arrival (`POST /result`); it spans queue wait + SSE delivery + client relay + execution + return trip, so `roundtrip_ms − exec_ms` approximates transport/queue overhead when both fields are present. These measurements use different clocks: `exec_ms` uses the page's `performance.now()`, while `roundtrip_ms` uses server wall-clock milliseconds, so their difference is an approximation rather than an exact subtraction. A legacy frame without `_did` has no `roundtrip_ms`. The extension retries a result POST whose response it never saw; the bridge remembers the delivery ids it has stored, so a retry is answered `{ok, duplicate: true}` and cannot republish a finished result over whatever landed after it.

### Async Support

The default `page-main` channel runs expressions through page-owned `eval` and
function bodies through page-owned `Function`; an async wrapper supplies
top-level `await`. Before submitting source, the background injects only a
constant `Function` probe. If that probe returns `true`, the source injection is
attempted once and every outcome is terminal: a value, an exception, or an
injection transport error is reported without retrying the source on CDP. The
page owns `Function` and can influence this routing hint, but the probe contains
no submitted source, so choosing another channel cannot duplicate its side
effects.

If the source-free probe does not return `true`, most commonly because page CSP
blocks dynamic compilation, Daedalus tries CDP. A successful attach displays
Chrome's debugger banner. `Runtime.evaluate` uses REPL mode for top-level
`await`; source containing `return` is treated as a function body unless a
wrapper probe parses it as an expression. That wrapper probe is a parser
heuristic, not a non-executing security boundary: operator-crafted source can
escape it and run. After the final CDP evaluation is dispatched, every outcome
is terminal. Only attach or shape failure before submitted source runs reaches
the page relay. CDP promise settlement is bounded at 10 seconds, and result and
exception object handles are released even when a kept session or capture
retains the debugger attachment.

JavaScript evaluated in a page you do not control returns a value that page can
choose, regardless of which channel executed it. The `world` field records only
which execution channel ran the submitted source; it is diagnostic metadata for
CSP and debugger behaviour, not a trust signal. Its eval values are `page-main`
for ordinary injection, `cdp` for the inspector fallback, and
`page:<hostname>` for the final relay, where `<hostname>` is the content
script's `location.hostname`. The background adds the `page:` prefix, so a
relay hostname cannot collide with `cdp` or `page-main`; this namespace fact
does not say anything about the returned value. The CLI renders the field as
`channel=...`, the dashboard does the same, and MCP `exec`, `put`, `result`, and
`ping` preserve the exact `world` value. None assigns a trust class.

The default injection and CDP fallback preserve classic-script behaviour for
the documented sloppy-mode cases: `with` compiles, legacy octal literals are
accepted, and an undeclared assignment creates a global. CDP REPL mode also
allows repeated `let` or `const` declarations. CLI and MCP result waits default
to 15 seconds; a caller timeout does not cancel code already running in the
page. The Blob relay separately waits up to 10 seconds for code containing
`await`, and 3 seconds otherwise, before reporting a fallback timeout.

Relay correlation remains descriptive and bounded: the sender tab must match,
one accepted message consumes the random relay id, and no relay id is registered
until the source-free injection probe and the pre-dispatch CDP fallback have
finished. These controls prevent cross-tab or duplicate relay completion; they
do not establish value integrity.

At most 1,000 page-relay entries may be live. A new fallback at capacity receives one terminal capacity error without evicting live work. Each registered entry expires after 300,000 ms and receives one terminal timeout error if no same-tab completion or earlier send failure removes it.

## Dashboard

A browser-based control surface at `<your-bridge>/dashboard` that drives the whole extension command set without the CLI — live tab list, eval REPL, screenshots, cookies, hotfixes, block rules, net capture, CDP, CSS injection, fetch timings, upload browser.

1. Open the URL in a tab the extension controls.
2. Scroll to §12 Settings and paste the token from the extension options page (puzzle icon → Daedalus → Options). Save.
3. The SSE status dot in the top bar turns cyan when the dashboard is subscribed to live events.

Served directly by `server.py` from the repo's `dashboard/` directory (vanilla JS + ES modules, no build). Live updates come through the existing `/stream` endpoint: `server.py` enqueues events into `commands/<token>_dashboard/<ts>_<uuid>.json` when `/register` updates an existing tab, and on the successful paths of `/sync-tabs`, `/unregister`, and `/result`; `/unregister` emits even when no tab was present. The dashboard drains them as `kind:'event'` frames.

**Caveat:** the extension's content + page scripts inject into matching pages, including the dashboard's tab. Broadcast eval commands (`exec -b`) run inside the dashboard too — prefer per-tab targeting or close the dashboard before broadcasting disruptive code.

## Self-Patch

### Hotfixes

Persist small patches that replay on each eligible top-level page load. Via MCP tools: `store_hotfix`, `list_hotfixes`, `clear_hotfix`, `clear_hotfixes`, `set_permanent`. Example (via the probe script):

```bash
TOKEN=<tok> python3 scripts/mcp_probe.py call store_hotfix '{"fix_id":"my-fix","code":"console.log(\"patched\")"}'
TOKEN=<tok> python3 scripts/mcp_probe.py call store_hotfix '{"fix_id":"always-on","code":"console.log(\"baseline\")","permanent":true}'
TOKEN=<tok> python3 scripts/mcp_probe.py call set_permanent '{"fix_id":"my-fix","permanent":true}'
TOKEN=<tok> python3 scripts/mcp_probe.py call list_hotfixes
TOKEN=<tok> python3 scripts/mcp_probe.py call clear_hotfix '{"fix_id":"my-fix"}'
TOKEN=<tok> python3 scripts/mcp_probe.py call clear_hotfixes
TOKEN=<tok> python3 scripts/mcp_probe.py call clear_hotfixes '{"include_permanent":true}'
```

Hotfixes are version-gated by default. After an extension version change, retained non-permanent fixes are skipped rather than deleted; storing a fix updates the record to the current version and makes its retained non-permanent fixes eligible again. Mark a fix as **permanent** (via `permanent: true` on `store_hotfix`, or `set_permanent`) to replay it across version changes. `clear_hotfixes` removes non-permanent fixes and keeps permanents by default; pass `include_permanent: true` to remove the entire store.

## Extension Commands

The background service worker accepts typed commands (issued via MCP tools or by writing JSON with `"type": "..."` to `$DAEDALUS_DIR/commands/`):

| Command | Purpose |
|---------|---------|
| `screenshot` | Capture visible tab as PNG. `--output` downloads the exact file this capture produced, by the `path` its result names — screenshot ids are reused, so `GET /screenshot?id=...` answers with whichever capture finished last |
| `cdp` | Issue raw Chrome DevTools Protocol calls |
| `net-capture` / `net-capture-stop` / `net-capture-get` | Full request/response interception via CDP |
| `cookies` / `set-cookie` / `remove-cookie` / `clear-cookies` | Cookie jar access |
| `open-tab` / `open-tabs` / `focus-tab` / `close-tab` / `navigate` / `reload` | Tab control |
| `inject-css` / `remove-css` | Per-tab CSS injection |
| `block-requests` / `unblock-requests` / `list-block-rules` | declarativeNetRequest blocking |
| `store-hotfix` / `clear-hotfix` / `clear-all-hotfixes` / `list-hotfixes` / `set-permanent` | Hotfix management (permanent fixes survive version bumps) |
| `ext-reload` | Reload the extension itself from disk |
| `fetch-timings` | Diagnostic ring buffer for fetch relays |

## GM Bridge

`window.GM` (in `page.js`, MAIN world) provides this Tampermonkey-style subset:

| Method | Description |
|--------|-------------|
| `GM.getValue(key, default)` | Read a non-reserved string key from extension-wide `chrome.storage.local` (not per-token) |
| `GM.setValue(key, value)` | Write a non-reserved string key to extension-wide storage |
| `GM.deleteValue(key)` | Delete a non-reserved string key from extension-wide storage |
| `GM.listValues()` | List non-reserved storage keys |
| `GM.xmlhttpRequest(opts)` | Background-relayed HTTP request (CSP-immune) |
| `GM.addStyle(css)` | Inject CSS |
| `GM.setClipboard(text, type)` | Write to clipboard; returns a promise that rejects if the browser refuses the write |
| `GM.notification(opts)` | Desktop notification |
| `GM.openInTab(url, opts)` | Open new tab |
| `GM.download(opts)` | Trigger download |
| `GM.info` | Script metadata |

Cookie access is an operator capability, not a page one: it goes through the
token-authenticated `cookies` / `set-cookie` / `remove-cookie` / `clear-cookies`
commands above and is deliberately not exposed to page context — `page.js` runs
in each matching top-level page, which could otherwise read cookies its own
`document.cookie` cannot see.

## Architecture

```
Browser (matching tab)                           Server (your bridge host)
┌────────────────────────────────────────────┐   ┌──────────────────────┐
│ MAIN world                                 │   │ bridge (server.py)   │
│  ├─ page-main: default injection channel   │   │ /stream?token        │
│  └─ page.js: GM + relay channel            │   │ watches commands/    │
│            ▲                               │   │                      │
│            │ window.postMessage            │   │ /result writes       │
│ content.js (ISOLATED)                      │   │    results/          │
│            ▲                               │   │                      │
│            │ chrome.runtime                │   │                      │
│ background.js (service worker)             │   │                      │
│  ├─ CDP: CSP fallback channel              │   │                      │
│  ├─ single SSE stream ◄────────────────────┼───┤                      │
│  └─ POST result, fetch ────────────────────┼──►│                      │
└────────────────────────────────────────────┘   └──────────────────────┘
```

- **One SSE stream**: Background opens a single `fetch` SSE connection (`tab=extension`) and routes commands to the targeted tab via `chrome.tabs.sendMessage`. 30s watchdog forces reconnect on stale streams.
- **Per-tab routing**: Commands address a specific Chrome `tabId` or broadcast to all tabs.
- **CSP behaviour**: `GM.xmlhttpRequest` sends HTTP work to the background service worker's `fetch`. Eval normally uses banner-free MAIN-world injection. When the source-free probe reports dynamic compilation unavailable, CDP supplies the CSP fallback and shows Chrome's debugger banner if it attaches; an attach failure reaches the page/Blob relay. The resulting `world` value describes that channel and makes no integrity claim.

## Server

Start the bridge with its three mandatory settings — it exits at startup without `DAEDALUS_DIR` or `DAEDALUS_PORT`, and every bridge-control route fails closed without a configured token:

```bash
DAEDALUS_DIR=<data-dir> DAEDALUS_PORT=<port> DAEDALUS_TOKEN=<bridge-token> python3 server.py
```

`server.py` runs under whatever supervisor you use (a systemd unit here). Put a TLS-terminating reverse proxy in front of it if you expose it beyond localhost; the bridge itself speaks plain HTTP. Bridge-control and storage routes compare their token with one configured secret, resolved through the CLI configuration path: `TOKEN` is the one-off override, otherwise `DAEDALUS_TOKEN` is required (and an embedding `_settings` module may supply it). Missing configuration and mismatches fail closed. Only the page-facing `POST /segment` and `GET /segment-status` routes use a job-scoped capability instead.

The in-process MCP front end takes three optional settings, documented together because they describe one listener: `DAEDALUS_MCP_PORT` (default `8086`) is the loopback port it binds; its tool handlers use the bridge's actual bound loopback URL, including when `DAEDALUS_PORT=0`; `DAEDALUS_LOCAL_URL` explicitly overrides that URL for a standalone MCP deployment fronting a bridge that runs elsewhere; and `DAEDALUS_MCP_ALLOWED_HOSTS` (default `127.0.0.1:*,localhost:*`) is the comma-separated host allowlist its DNS-rebinding protection accepts, so fronting `/mcp` with a public hostname means naming that hostname there.

Endpoints: `GET /stream`, `GET /tabs`, `GET /health`, `GET /dashboard[/<asset>]`, `POST /register`, `POST /sync-tabs`, `POST /unregister`, `POST /poll`, `POST /result`, `PUT /command`, `GET /result`, `POST/GET/DELETE /upload`, `GET /screenshot`, `POST /segment-job` (mint) + `GET /segment-job` (look up without minting), `POST /segment` + `GET /segment-status`. `POST /segment-job` requires the configured bridge token; only `POST /segment` and `GET /segment-status` take the job-scoped `sig`. See `CLAUDE.md` for payload and endpoint notes.

`POST /poll` consumes and deletes the legacy broadcast command file when one is present.

`PUT /command` enqueues into a per-target FIFO directory queue (back-to-back commands to one tab no longer overwrite), stamps each with a delivery id (`_did`) so the extension can dedup a redelivered frame, and TTL-drops commands unclaimed after `DAEDALUS_CMD_TTL` seconds (default 90). A background collector applies that TTL and removes empty queue directories even when no SSE consumer ever connects. `GET /health` reports stream/registry/last-delivery liveness for detecting a silently-dead bridge. `POST /register` is update-only and answers `{ok, updated}`; `updated: false` means the registry held no such tab, so the client should re-sync through `POST /sync-tabs` instead of treating the call as a refresh.

The bridge rejects repeated authority carriers instead of selecting one value:
this includes `token` in query strings or JSON bodies and `job` / `sig` on the
segment-capability routes, even when the repeated values are equal or blank.
The MCP transport likewise rejects repeated `Authorization`, `Mcp-Session-Id`,
`Host`, and `Origin` headers, plus repeated `job` arguments for the segment
tools.

When the extension posts a result, the server exposes the command's `_did` as
`deliveryId` and assigns a fresh `resultGeneration`. CLI and MCP waiters first
peek at the shared result slot, match both command id and `deliveryId`, then
conditionally consume that generation with
`GET /result?...&consume=1&expected=<resultGeneration>`. If another result
replaces the slot between those requests, the conditional consume leaves the
new result in place and reports that it was not consumed. A bare `consume=1`
without `expected` remains a destructive compatibility read of the current
slot.

`GET /stream` holds the SSE connection open indefinitely, proving liveness with a keepalive comment every `DAEDALUS_STREAM_KEEPALIVE` seconds (default 15) rather than cycling the connection on a timer; `DAEDALUS_STREAM_MAX_AGE` (default 3600) is a last-resort ceiling only. The close path is pinned by `tests/test_stream_lifecycle.py`.

`DAEDALUS_MAX_BODY_SIZE` (default `64 * 1024 * 1024` bytes, 64 MiB) bounds request bodies read by the `POST`, `PUT`, and `DELETE` handlers; a declared body larger than the limit receives `413`, and a request that declares no `Content-Length` receives `411` rather than being read as an empty body. Increase it when relaying larger segments or other payloads.

`DAEDALUS_MAX_JSON_DEPTH` (default `100`, range 1-500) bounds how deeply a JSON request body may nest arrays and objects. The raw bytes are scanned before parsing, so a body past the bound receives `400 JSON body too deeply nested` on every supported interpreter — previously the depth actually enforced was whichever recursion limit the running Python had, and one body could be refused on 3.13 and parsed on 3.14.

`DAEDALUS_REQUEST_TIMEOUT` (default 60 seconds) bounds each socket operation of a request — request line, headers and body. It renews on every operation that makes progress, so a large upload that keeps arriving is never cut short, while a peer that declares a body and then stops sending receives `408` and gives its worker back instead of holding it for as long as it keeps the socket open. `DAEDALUS_MAX_REQUEST_WORKERS` (default 256, range 1-4096) caps how many connections are served at once: past the cap a new connection is closed without an answer rather than given a thread. An open `GET /stream` holds one of those slots for its lifetime, so raise the cap rather than lower it if many streams share one bridge.

Filesystem-backed caller values use one path-component policy: it rejects
`..`, C0/C1 control and surrogate characters, Windows-invalid path characters
and device names, trailing dots or spaces, and UTF-8 encodings longer than 240
bytes. Bridge tokens are stricter and also reject dots and underscores. Other
UTF-8 job names are accepted, so clients must URL-encode them in query strings.

`POST /segment-job` copies three fixed quotas into each new job record:
`DAEDALUS_MAX_SEGMENT_INDEX` (default `99999`),
`DAEDALUS_MAX_SEGMENTS_PER_JOB` (default `10000`), and
`DAEDALUS_MAX_SEGMENT_JOB_SIZE` (default `4 * 1024 * 1024 * 1024` bytes,
4 GiB). Changing those settings affects subsequently created jobs; a job with
stored quotas continues to use its recorded values. `DAEDALUS_MAX_BODY_SIZE`
also bounds each individual segment request.

## Security

Read this before installing the extension.

**It injects a `window.GM` shim into every matching top-level page**
(`<all_urls>`, MAIN world),
which is what lets a script sent with `put` make cross-origin requests. The
consequence is the part to be deliberate about: **any matching site you visit
can call that shim**, and what it reaches is not only the network. Under the
default match a hostile or compromised origin can:

- **issue cross-origin HTTP requests** with the extension's privileges, reading
  responses its own `fetch` could not — `GM.xmlhttpRequest`;
- **open tabs** at URLs of its choosing — `GM.openInTab`;
- **start downloads** — `GM.download`;
- **raise desktop notifications** — `GM.notification`;
- **write your clipboard** — `GM.setClipboard`;
- **read, write, enumerate and delete extension-wide storage** — every
  non-reserved key, shared by every site the shim runs in, so one origin reads
  what another left behind — `GM.getValue`, `GM.setValue`, `GM.listValues`,
  `GM.deleteValue`;
- **inject CSS into its own page** — `GM.addStyle`.

That is the authority a userscript manager with a wildcard `@match` hands out,
and it is granted to every origin rather than to a script you chose. Two things
are deliberately outside it, both covered below: the bridge token and server
URL, which the reserved-key rule keeps out of page reach, and cookies, which
stay an operator capability. If that surface is not acceptable for your
browsing, narrow the `matches` in `extension/manifest.json` to the origins you
actually drive, and accept that the bridge then does nothing on other pages.

**The bridge token and server URL are not reachable through the page's GM
storage methods.** They live in `chrome.storage.local` under the `daedalus-`
prefix, and the relay refuses to read, write or enumerate that namespace for
page scripts. Without that rule any visited site could read your bridge token
with `GM.getValue('daedalus-token')` or repoint the bridge with
`GM.setValue('daedalus-server', ...)`, silently. `tests/test_repo_contract.py`
pins the rule.

Hotfix source is deliberately page-observable state, not confidential
extension state. The background selects permanent fixes plus non-permanent
fixes matching the extension version and runs each one in the page through
the same channels ordinary eval uses — MAIN-world injection where the page
permits dynamic compilation, CDP where it does not — so the page can observe
the source it executes on either channel.

**An MCP bearer cannot name a server-host file for the MCP process to read.**
The MCP `put`, CSS injection/removal, and hotfix-storage tools accept inline
source only. The local CLI keeps its file-path convenience, but the CLI process
reads that operator-selected file and submits its contents inline. Holding the
bridge token therefore grants the documented browser and extension-control
authority, including reading stored hotfix source, but it does not add an
arbitrary host-filesystem read through these tools.

**Bridge-control and storage routes require the configured bridge token;
`/segment` and `/segment-status` are the only capability exception.** The server
compares request tokens with the secret resolved from `TOKEN` or
`DAEDALUS_TOKEN` and refuses requests when no secret is configured.
`POST /segment-job` requires that bridge token because it mints the job-scoped
capability; untrusted page JavaScript uses the capability only to post and query
that job. Anyone who holds the bridge token can drive your browser. Do not expose
the bridge port beyond loopback without a reverse proxy that terminates TLS, and
treat the token as a credential.

**Eval results carry no value-integrity guarantee.** JavaScript evaluated in a
page you do not control returns a value that page can choose, regardless of
which channel executed it. The `world` field says how the submitted source ran,
not whether to trust its value: `page-main` is ordinary MAIN-world injection,
`cdp` is the inspector CSP fallback, and `page:<hostname>` is the relay. The
mandatory `page:` prefix is added outside page context, so no hostname can
produce `cdp` or `page-main`; that collision resistance is descriptive only.
On `page-main`, page-owned `eval` and `Function` bindings can read the submitted
source as well as influence its returned value.

CDP compilation avoids resolving the page's `eval` and `Function` bindings, and
the implementation retrieves direct handles by reference before serializing
them. Those transport mechanics do not change the trust boundary: submitted
source still reads page-controlled state and can route any value, including a
primitive, through page promise machinery before CDP receives it. Whenever the
submitted source uses those page-controlled paths, the page can choose the value
returned on `cdp` too.

The relay accepts only the stored invocation associated with that random id,
requires the sender's tab to match, and consumes the entry once. The downgrade
does not disclose the bridge token, server URL, delivery id or result route,
does not grant additional extension or browser authority, and cannot affect an
invocation in another tab. Those properties protect routing and browser
authority; they do not turn the relay marker or its returned JavaScript value
into a trust signal.

**Repository secret scanning.** Secret scanning and push protection are enabled
on this repository, so a provider-recognised credential is detected in the
history and refused at push time. Two adjacent controls are not enabled and
cannot be here: non-provider pattern scanning and validity checks require
GitHub Advanced Security, which this plan does not include — the REST API
accepts a request to turn them on and reports them disabled afterwards. So a
generic secret, an ad-hoc token or a password left in a config example, is
unscanned in this repository. Keep one out of the tree rather than relying on
detection to catch it.

## Deployment

The bridge speaks plain HTTP on loopback. Bridge-control and storage routes
require the configured token and fail closed when it is absent; only `/segment`
and `/segment-status` use the job-scoped capability. Two things it deliberately
does NOT do, because they belong to whatever sits in front of it:

- **TLS and CORS.** `server.py` sends no CORS headers. When the bridge is
  cross-origin, the example HLS relay's page-side `fetch` calls need access to
  both
  `GET /segment-status` and `POST /segment`; the proxy must allow the page
  origin on both routes and handle the methods/headers required by the POST's
  preflight. If the status GET is blocked, unavailable, non-2xx, or invalid,
  the example treats the job as fresh and re-POSTs every segment. Those writes
  still replace the same per-index files, but the run loses its resume/skip
  savings. Route both requests through the extension's GM bridge instead if
  the deployment cannot provide that CORS policy.
- **Serving stored uploads.** The dashboard links downloads at `/uploads/<path>`,
  but the bridge has no such route. Do not serve caller-supplied upload names and
  bytes directly on the dashboard origin, where executable content would share
  an origin with the dashboard's token-bearing storage. Either leave those links
  unavailable, or make `/uploads/` redirect to a separate download-only origin
  that forces `Content-Disposition: attachment`, uses
  `application/octet-stream`, and sends `X-Content-Type-Options: nosniff`.
  `GET /upload` (listing) and `GET /screenshot` are served by the bridge itself
  and need no proxy help.

Run it directly with a configured token and both of those are simply absent —
everything else works.

## Files

| File | Description |
|------|-------------|
| `extension/manifest.json` | MV3 manifest |
| `extension/background.js` | Service worker — SSE, command dispatch, fetch relay, screenshots, CDP, cookies, downloads |
| `extension/content.js` | Message relay between page and background |
| `extension/page.js` | MAIN-world bridge — `window.GM` + eval handler |
| `extension/options.html` / `options.js` | Token/server settings UI |
| `server.py` | Debug server (also hosts the MCP daemon thread on 127.0.0.1:8086) |
| `mcp_server.py` | MCP server — bridges the extension command surface to `server.py` over HTTP |
| `scripts/mcp_probe.py` | Minimal MCP client helper for manual verification |
| `scripts/check_versions.py` | Version-consistency check / bump across every version site |
| `.githooks/` | pre-commit + pre-push version-consistency gates |
| `daedalus_cli/` | The shell CLI, published as the `daedalus-cli` wheel |
| `dashboard/` | The browser control surface `server.py` serves at `/dashboard` |
| `examples/` | Scripts to run in a page via `put` — see below |
| `tests/`, `run_tests.py` | The suites; `python3 run_tests.py` runs all of them |

## Examples

`examples/` holds scripts meant to be run inside a page with `put`. Five of
the six demonstrate a part of the bridge rather than a particular site; the
Discord one is deliberately site-specific, because scrolling back through a
virtualised list is a technique you cannot show without a real virtualised
list:

| Example | Shows |
|---|---|
| `request-log-hotfix.js` | A permanent MAIN-world hotfix at `document_start`, and why all three of those words are load-bearing |
| `hls-segment-relay.js` | The `/segment` + `/segment-status` relay: resumable, idempotent, bounded retry |
| `kill-hls.js` | Finding and destroying a player instance that will not stop retrying |
| `react-fill-input.js` | Filling a React-controlled input so React's own state actually changes |
| `scrape-discord-messages.js` | Scrolling back through a virtualised message list and extracting it |
| `open-tabs.js` | The smallest possible `GM.openInTab` call |

They take their configuration through `__PLACEHOLDER__` substitution before
being sent, because `put` ships a script rather than calling a function and has
no way to pass arguments.

Each one is the BODY of an async function, not a standalone script: the bridge
wraps what you send. That is why most end in a top-level `return`, and one of
them — `scrape-discord-messages.js` — also uses a top-level `await`; both are
valid where these actually run. `node --check` parses each file in the
CommonJS wrapper, which permits the top-level `return`, but it rejects the
top-level `await` — so exactly that one file fails the syntax check.

## Development

`python3 run_tests.py` runs every suite and prints one aggregate line naming
what it verified: how many suites ran, how many tests passed, and how many
skipped. A suite in which every test skipped verified nothing, so it is
reported as
`OVERALL: INCOMPLETE` with a nonzero exit rather than as a pass; the usual
cause is a missing dependency, and the `NO COVERAGE` line above it names the
suite. Installing the `mcp` extra (see "Install") is what the MCP suite needs.

The version string lives in several places across the extension, the dashboard
and the CLI package. `python3 scripts/check_versions.py` is the list — it names
every site and reports how many it found, which is why this paragraph does not
restate a count that would go stale the next time one is added.
Bump them together, never by hand:

```bash
python3 scripts/check_versions.py --set 0.18.0   # rewrite every site
python3 scripts/check_versions.py                # verify the working tree
```

`.githooks/pre-commit` checks the index and `.githooks/pre-push` checks each pushed
commit, so a half-bump can't land. **Every clone must opt in once** — git won't run
hooks out of a tracked directory otherwise:

```bash
git config core.hooksPath .githooks
```

If the browser loads the extension from a different checkout than the one you
edit, install the hooks there too — and remember that reloading the extension
re-reads the files on the browser's side, not yours.
