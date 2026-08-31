# Daedalus

Daedalus is a Chrome-extension-based remote control system for pages in your
browser. It can evaluate JavaScript, keep small hotfixes across page loads, and
control tabs, screenshots, cookies, Chrome DevTools Protocol sessions, and
network capture.

You can drive it through the `daedalus` CLI, an MCP client, or the browser
dashboard.

## Install

Daedalus has two parts: an unpacked Chrome extension and the bridge in this
repository.

### Load the extension

1. Open `chrome://extensions` in Chrome.
2. Enable **Developer mode**.
3. Click **Load unpacked** and select the repository's `extension/` directory.
4. Open Daedalus's extension options from the puzzle menu.

The extension generates a token on first install and stores it in
`chrome.storage.local`. Copy that token from the options page; the bridge and
its clients use the same value.

The extension has no default bridge URL. Leave its options page open for the
first-run setup below.

### Install the Python package

From the repository root, install the CLI:

```bash
pip install .
```

Install the optional MCP dependencies if you plan to use an MCP client:

```bash
pip install ".[mcp]"
```

The bridge itself is implemented with the Python standard library. Without the
`mcp` extra, it still serves the CLI and dashboard surfaces and reports the MCP
bootstrap failure at startup.

## First run: evaluate JavaScript in a tab

Start the bridge from the repository root. Replace `<token>` with the value
from the extension options page:

```bash
DAEDALUS_DIR="$PWD/.daedalus" \
DAEDALUS_PORT=8081 \
DAEDALUS_TOKEN=<token> \
python3 server.py
```

Back in the extension options, set **Server URL** to
`http://127.0.0.1:8081`, keep the same token, and save. Open a normal web
page that the extension can control, then evaluate its title with the CLI:

```bash
DAEDALUS_TOKEN=<token> ID=<tab-id> \
  daedalus exec page-title 'document.title'
```

Use the Chrome tab ID reported by `daedalus tabs`. The command returns the
page's value together with the execution channel.

For the complete command reference, run `daedalus --help` and
`daedalus <command> --help`.

## Call an MCP tool

The MCP listener starts in-process with the bridge when the `mcp` extra is
installed. This repository includes a minimal client for checking one tool
call:

```bash
TOKEN=<token> python3 scripts/mcp_probe.py call title \
  '{"tab_id":"<tab-id>"}'
```

For MCP client configuration, authentication, settings, and the tool
inventory, see the [`<mcp>` section of `AGENTS.md`](AGENTS.md).

## Use the dashboard

The bridge serves a browser control surface with no separate build step:

1. Open `http://127.0.0.1:8081/dashboard` in a tab controlled by the
   extension.
2. Open **Settings** in the dashboard.
3. Paste the token from the extension options page and save.
4. Select a tab and evaluate `document.title` in the **Eval** panel.

The dashboard can also drive the extension's tab, screenshot, cookie, hotfix,
request-blocking, network-capture, CDP, CSS, timing, and upload capabilities.
Avoid broadcast evaluations while the dashboard is open: the extension also
runs in the dashboard tab.

## GM Bridge

Matching pages receive a Tampermonkey-style `window.GM` subset from `page.js`:

| Method | Purpose |
|---|---|
| `GM.getValue(key, default)` | Read a non-reserved extension storage key |
| `GM.setValue(key, value)` | Write a non-reserved extension storage key |
| `GM.deleteValue(key)` | Delete a non-reserved extension storage key |
| `GM.listValues()` | List non-reserved extension storage keys |
| `GM.xmlhttpRequest(opts)` | Make a background-relayed HTTP request |
| `GM.addStyle(css)` | Inject CSS into the page |
| `GM.setClipboard(text, type)` | Write to the clipboard |
| `GM.notification(opts)` | Show a desktop notification |
| `GM.openInTab(url, opts)` | Open a tab |
| `GM.download(opts)` | Start a download |
| `GM.info` | Read shim metadata |

Cookie access is intentionally absent from the page-facing shim. It remains an
operator capability available through the authenticated control surfaces.

## Architecture

For component responsibilities, repository layout, data directories, eval
routing, and storage behavior, see the
[`<architecture>` section of `AGENTS.md`](AGENTS.md).

## Security

Read this before installing the extension.

The manifest currently matches every URL. Each matching top-level page can use
the page-facing shim to make cross-origin requests with
`GM.xmlhttpRequest`, open tabs with `GM.openInTab`, start downloads with
`GM.download`, show notifications with `GM.notification`, write the clipboard
with `GM.setClipboard`, share non-reserved extension storage through
`GM.getValue`, `GM.setValue`, `GM.deleteValue`, and `GM.listValues`, and inject
CSS with `GM.addStyle`. Narrow the `matches` entries in
`extension/manifest.json` if that authority is too broad for your browser.

**The bridge token and server URL are not reachable through the page's GM
storage methods.** Their reserved `daedalus-` keys are filtered out of reads,
writes, deletion, and enumeration. Treat the token as a credential anyway:
anyone who has it can drive the browser through the bridge.

JavaScript results from a page you do not control are not integrity-protected.
The reported execution channel describes how the source ran; it does not make
the returned value trustworthy.

Ordinary evaluation uses banner-free MAIN-world injection. When page policy
blocks that route, Daedalus can fall back to CDP, which displays Chrome's
debugger banner while attached. A kept CDP session or network capture can hold
the attachment longer.

The bridge listens on loopback and speaks plain HTTP. If you expose it through
a reverse proxy, terminate TLS there and preserve token authentication. The
bridge intentionally does not supply deployment-specific CORS or public upload
hosting policy.

## Release verification

Published releases include a wheel, source distribution, and `SHA256SUMS`.
Each artifact also carries a GitHub build attestation. Verify the wheel with:

```bash
gh attestation verify daedalus_cli-<version>-py3-none-any.whl \
  --repo Nitjsefnie-Harness-Commons/daedalus
```

The checksum proves that the published files belong to the same release; the
attestation identifies the workflow, commit, and runner that built the
artifact.

## Where to go next

- Server endpoints and payloads are documented only in the
  [`<endpoints>` section of `AGENTS.md`](AGENTS.md).
- Contributor setup, tests, house style, issues, and pull requests are
  documented in [`CONTRIBUTING.md`](CONTRIBUTING.md).
