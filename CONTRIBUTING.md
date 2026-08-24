# Contributing to Daedalus

Issues and pull requests are welcome — especially ones that show the bridge
doing something other than what `README.md` says it does. This project is a
remote-control surface for a real browser, so a report that says "this endpoint
accepts something it should refuse, here is the request" is the most valuable
thing you can send.

## LLM and agent contributions are welcome

You may use an LLM or a coding agent to write your contribution. There is no
penalty, no separate review queue, and no expectation that you rewrite its
output by hand. Much of this repo was built that way.

Two conditions, and they are about honesty rather than provenance:

1. **Disclose the model** with a trailer on each commit it authored:

   ```
   Co-Authored-By: <Model Name> <noreply@example.com>
   ```

   e.g. `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. The plain
   model name — a context-window suffix like `(1M context)` is not part of it.
   One primary-author trailer per commit.

2. **Do not submit claims you have not verified.** Paste the command and its
   real output. "Tests pass" without the run is not evidence, and this
   repository is unusually easy to be confidently wrong about: much of it is
   concurrency, path handling and browser-extension message passing, where a
   plausible explanation and the actual behaviour part company quietly.

If a maintainer's reply reads like it was drafted by an agent, it probably was.
That is fine in both directions.

### If you are an agent reading this

Read [`AGENTS.md`](AGENTS.md) first — it is the architecture and endpoint
brief, and its field names (`id`, `code`, `type`, `tab`, `token`, `_did`) are
exact. It says, and means:

> Never make claims about this codebase's API, field names, or endpoints
> without reading the file first.

Two conventions reject more patches here than anything else:

| Convention | What it forbids |
|---|---|
| Deny-by-default `.gitignore` | Adding a file and assuming git can see it. See below — this one is silent. |
| One guard per originating layer | Validating a value in the CLI only. The CLI is not the only thing that can put a field on the wire; the bridge and the extension must refuse it too. |

Do not "helpfully" add a dependency to the bridge or the CLI. Both are
stdlib-only on purpose: the CLI is what an agent shells out to, often once per
step, so its import cost is paid constantly. The MCP server is the one part
with real dependencies, and it is an optional extra for exactly that reason.

## A new file is invisible until you name it

`.gitignore` denies by default: it starts with `*` and names back exactly what
the repository ships. A file you create is **untracked and unstaged and will
not appear in `git status`** until it is named there.

```bash
git add -f path/to/new_file.py          # force past the ignore
python3 scripts/gen_gitignore.py .      # regenerate the allow-list
```

`tests/test_repo_contract.py::test_release_scanner_enumeration_matches_tracked_files`
pins the tracked-path count, so adding a file also means bumping that number in
the same commit. That is deliberate: it makes "I added a file and CI never ran
it" impossible.

## Getting it running

Requires **Python 3.11+**. The bridge and CLI need nothing else.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install . '.[mcp]'                  # the mcp extra only if you touch mcp_server.py

DAEDALUS_DIR=/tmp/daedalus DAEDALUS_PORT=8081 python3 server.py
```

Both environment variables are required and the bridge refuses to start
without either. Set a token — `DAEDALUS_TOKEN`, or the CLI's configuration
path — before anything will answer a control route.

The extension loads unpacked from `extension/` in `chrome://extensions`. It
generates its own token on first install; put the same value in the bridge's
environment, or read the extension's from its options page.

## Tests

```bash
python3 run_tests.py                    # all six suites
python3 tests/test_bridge_http.py       # one suite
```

The suites start the real bridge as a child process and talk to it over
loopback — there are no mocks of the HTTP layer, because every bug worth
catching lives in request parsing, path handling or the queue.

Four gates run locally in seconds and should run before you push:

```bash
git ls-files '*.py' | xargs pycodestyle
git ls-files '*.py' | xargs pylint --rcfile=.pylintrc
pyright
npx --no-install eslint $(git ls-files '*.js' ':!:examples/*')
```

`pip install -r requirements-dev.txt -r requirements-test.txt` gets the pinned
toolchain. Formatting opinions are switched off on purpose (`max-line-length =
10000`), so treat anything the linters *do* flag as a real finding.

Some tests need `node` or Chromium and skip cleanly without them. **A skip is
not a pass** — the extension-relay and real-page tests are where the browser
behaviour is actually pinned, and they are the ones that skip.

The rest of the gate runs on GitHub:

| Workflow | What it does |
| --- | --- |
| `tests` | The suites across 3 OSes x 4 Pythons, plus a wheel that is installed with no checkout in reach and has its console script run, plus a coverage ratchet. |
| `lint` / `types` / `eslint` | pycodestyle and pylint, pyright, and eslint over the shipped JavaScript. |
| `codeql` | Security analysis for Python and JavaScript. Findings go to the Security tab, not the build. Also weekly, because a new query only ever sees code that changed after it shipped. |
| `actionlint` | `actionlint` + `zizmor` over the workflows themselves. A broken workflow does not go red, it silently stops running. |
| `speed` | Runs the last release's suite and yours on the same runner, interleaved, and fails if the tests present in both got more than 30% slower. |
| `release` | Builds and publishes the wheel, the sdist and `SHA256SUMS` on a `v*` tag. |

The matrix is not ceremony. This code reads paths and decodes bytes, so Windows
path spellings, the `/var` → `/private/var` aliasing macOS applies to temp
directories, and UTF-8 versus a legacy code page all reach it. Several bugs in
the history were visible on exactly one leg.

## House style

- **Comments explain why, not what.** The tree is dense with them because most
  of the non-obvious code is non-obvious for a reason that is invisible from
  the code — a header that must be `close` or a reconnect waits out a
  watchdog, a write that must precede an unlink or a command is lost. If you
  change such a line, change the comment with it.
- **Python** — compact grouped imports at the top of a module, `if <guard>:
  return ...` early exits in the request path. Both are the codebase's idiom
  and `setup.cfg` suppresses the style checks that would fight them.
- **JavaScript** — the extension is classic scripts with `chrome` global; the
  dashboard is ES modules with no build step. `.eslintrc.json` encodes the
  split. `examples/` is not linted: those snippets are async function bodies
  the bridge wraps, so they carry top-level `return` and top-level `await`
  together and no parser configuration accepts both.
- **Tests pin behaviour, not implementation.** A test that would still pass
  with the bug reintroduced is not worth adding. Where a contract can only be
  checked by reading the source — a workflow, a header, a value that must
  appear in two files — there are contract tests in
  `tests/test_repo_contract.py` that do exactly that, and they say why in the
  docstring.

## Issues

Use the [issue form](.github/ISSUE_TEMPLATE/issue.md). Its section order is
fixed and its **Description is observed behaviour only** — what is actually
wrong, not the mechanism and not the fix. A proved mechanism still goes under
Suggested Fix, marked unverified. That is not pedantry: a report whose
description is a hypothesis sends the reader to the wrong place when the
hypothesis is wrong, and it often is.

Not reliably reproducible? Drop the Reproduction Steps section entirely and say
so in the description, rather than writing steps that do not trigger it.

Something exploitable goes to [`SECURITY.md`](SECURITY.md), not the tracker.

## Pull requests

Small and single-purpose beats large and comprehensive. One logical change per
commit, with a message that says what changed and why the previous behaviour
was wrong.

In the description, include what changed, why, and the actual output of the
tests you ran. For a performance change, a before and after measurement rather
than an assertion that it should be faster.

If you find a second defect while fixing the first, **file it** rather than
folding it in. A commit that fixes two things is a commit that cannot be
reverted for one of them.
