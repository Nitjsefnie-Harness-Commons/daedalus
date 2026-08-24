# Security Policy

## Reporting a vulnerability

**Use [private vulnerability reporting](https://github.com/Nitjsefnie-Harness-Commons/daedalus/security/advisories/new).**
It is enabled on this repository, so a report there is visible only to the
maintainers until an advisory is published. Do not open a public issue for
something exploitable — the issue tracker is world-readable and this project
drives a real browser.

There is no bounty and no SLA. This is a small project; expect a human reply
rather than a triage pipeline.

What helps, roughly in order of usefulness:

- the exact request, command, or page that triggers it, verbatim;
- what an attacker gains — reading a token, reaching an origin they should not,
  executing in a context they should not;
- the commit or release you tested, since the surface moves;
- whether it needs the bridge token, the browser extension installed, or
  neither.

If you are unsure whether something is a vulnerability or a documented design
decision, report it privately and ask. Several things that look like holes are
deliberate and written down — see below — and a wrong premise caught early is
cheaper than either of us chasing it.

## Supported versions

The latest release. Fixes land on `main` and go out in the next tagged release;
older tags are not patched.

## What is deliberately in the threat model

Read [the Security section of the README](README.md#security) before reporting.
Three properties there are design decisions rather than defects, and reports
that restate them will be closed as working-as-intended:

- **Any page matching the extension's `matches` can call `window.GM`.** Under
  the default `<all_urls>` that is every site you visit, and the shim grants
  cross-origin requests, tab opening, downloads, notifications, clipboard
  writes and shared extension storage. The README enumerates the full list. The
  mitigation is narrowing `matches`, and it is documented.
- **A page can choose the value an eval returns.** JavaScript evaluated in a
  page you do not control returns what that page decides, on every channel.
  `world` records which channel executed the source; it is not a trust signal.
- **Hotfix source is page-observable.** It executes in the page.

What is *not* in the model, and is worth reporting:

- the bridge token or server URL becoming readable from page context — the
  reserved-key rule exists to prevent exactly that;
- cookies reaching page context, which is an operator capability only;
- one bridge token reading another token's results, uploads or segment jobs;
- a segment capability authorizing anything outside its own job;
- an unauthenticated request reaching storage, or making the bridge consume
  unbounded memory, threads or disk.

## Running it safely

The bridge speaks plain HTTP on loopback and terminates no TLS of its own. If
you expose it, put it behind a reverse proxy that does, and read the
[Deployment section](README.md#deployment) — it names the two things the bridge
deliberately does not do, and what has to sit in front of it instead.

The bridge token is the whole authorization story for control and storage
routes. Treat it like an SSH key: it is generated on first install, it is not
rotated for you, and anything holding it can drive the browser.
