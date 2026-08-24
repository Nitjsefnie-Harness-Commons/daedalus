#!/usr/bin/env python3
"""What the shipped extension may carry, read out of its own source.

An extension is published to browsers, so what it must not contain is as
load-bearing as what it does: no default server, no token in a log line, no
capture limit spelled differently in two places, and no message type the
content script sends that the background has no branch for.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsread import (blank_js_comments, js_bracket_end,  # noqa: E402
                     js_mask, js_object_entries, js_split_top_level)
from _repo import ROOT  # noqa: E402


# GM.info is metadata about the shim, not a capability it grants, so the
# install-time warning has nothing to say about it.
_GM_NON_CAPABILITIES = frozenset({'GM.info'})


def test_the_security_warning_names_every_capability_the_shim_grants(tmp):
    """The warning has to keep up with the surface it is warning about.

    It described the consequence as cross-origin requests, while the same
    page-facing relay also opened tabs, started downloads, raised
    notifications, wrote the clipboard and shared extension-wide storage
    between origins. Those were all in the API table and none of them in the
    warning, which is the half a reader makes an install decision from.
    """
    del tmp
    readme = (_util.ROOT / 'README.md').read_text(encoding='utf-8')
    _, _, after = readme.partition('## GM Bridge')
    table, _, _ = after.partition('## Architecture')
    granted = {f'GM.{name}' for name in re.findall(r'`GM\.([a-zA-Z]+)', table)}
    granted -= _GM_NON_CAPABILITIES
    assert len(granted) > 5, granted  # the table was found and parsed

    _, _, after = readme.partition('## Security')
    warning, _, _ = after.partition('**The bridge token and server URL')
    missing = sorted(name for name in granted if name not in warning)
    assert not missing, f'not named in the install-time warning: {missing}'


def test_every_capture_limit_boundary_agrees_on_one_range(tmp):
    """One documented maximum, enforced at each place the value can enter.

    The buffer lives in the service worker and grows to hold headers and
    response bodies, so its size is a memory budget. `cmd.maxRequests || 1000`
    kept whatever arrived: -1 evicted the only event on arrival, leaving an
    empty capture, and 1e9 buffered everything.
    """
    del tmp
    background = (_util.ROOT / 'extension' / 'background.js').read_text(
        encoding='utf-8')
    # The ceiling may live in any module of the CLI package, so the package is
    # searched rather than one file named by hand. Every declaration found is
    # kept: concatenating the package and taking the first match would let a
    # stale copy answer for a module that had diverged, which is the one thing
    # this test exists to catch.
    package = sorted((_util.ROOT / 'daedalus_cli').glob('*.py'))
    declared = [(path.name, int(m.group(1)))
                for path in package
                for m in re.finditer(r'NET_CAPTURE_MAX = (\d+)',
                                     path.read_text(encoding='utf-8'))]
    assert len(declared) == 1, f'expected one CLI declaration, found {declared}'
    mcp = (_util.ROOT / 'mcp_server.py').read_text(encoding='utf-8')

    ceilings = {
        'background.js': re.search(r'NET_CAPTURE_MAX = (\d+)', background),
        'mcp_server.py': re.search(r'NET_CAPTURE_MAX = (\d+)', mcp),
    }
    missing = sorted(name for name, m in ceilings.items() if m is None)
    assert not missing, f'no capture ceiling declared in: {missing}'
    values = {name: int(m.group(1)) for name, m in ceilings.items()}
    values[f'daedalus_cli/{declared[0][0]}'] = declared[0][1]
    assert len(set(values.values())) == 1, values

    # And the buffer is bounded by the validated value rather than by an
    # inline default that accepts whatever it is handed. Comments are blanked
    # first: the ones explaining this change quote the expression it replaced.
    code = blank_js_comments(background)
    assert 'maxRequests || 1000' not in code, 'an unvalidated fallback remains'
    assert '_netCaptureLimit(cmd.maxRequests)' in code, \
        'the capture allocation does not validate its limit'


def test_every_registry_call_checks_its_http_status(tmp):
    """A refusal is not a success, and fetch does not say so on its own.

    fetch resolves normally for 401, 413 and 500, so `await fetch(...)` with
    only a network-error catch reads every refusal as a completed
    registration. All three registry routes went out that way, so the
    server's tab registry could sit stale with nothing reported anywhere.
    """
    del tmp
    source = (_util.ROOT / 'extension' / 'background.js').read_text(
        encoding='utf-8')

    # No registry route may be fetched outside the one helper.
    direct = []
    for route in ('/register', '/unregister', '/sync-tabs'):
        for match in re.finditer(
                r'fetch\(\s*config\.serverUrl\s*\+\s*[\'"]'
                + re.escape(route) + r'[\'"]', source):
            direct.append(f'{route} at offset {match.start()}')
    assert not direct, direct

    for route in ('/register', '/unregister', '/sync-tabs'):
        assert f"registryPost('{route}'" in source, route

    # And the helper is what actually looks at the status.
    _, marker, after = source.partition('async function registryPost(')
    assert marker, 'registryPost is not defined the way this test finds it'
    helper, _, _ = after.partition('\nasync function registerTab')
    assert 'resp.ok' in helper, helper
    assert 'console.error' in helper, helper


def test_the_extension_never_logs_the_bridge_token(tmp):
    """The token is a reusable browser-control credential, not a diagnostic.

    First-run bootstrap printed the whole generated token, which put it into
    extension DevTools output, screen recordings and any diagnostic bundle
    collected from them — all places a credential outlives the moment it was
    useful in. A truncated prefix is not what this pins: the version banner
    logs eight characters to say which bridge is configured, and that stays.
    """
    del tmp
    offenders = []
    for name in ('background.js', 'content.js', 'page.js', 'options.js'):
        path = _util.ROOT / 'extension' / name
        if not path.is_file():
            continue
        for number, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), 1):
            if 'console.' not in line or '.token' not in line:
                continue
            # A short prefix is a legitimate diagnostic — the version banner
            # prints one to identify which bridge this extension is talking
            # to. The whole value is the credential itself.
            if '.token.substring(' in line or '.token.slice(' in line:
                continue
            offenders.append(f'{name}:{number}: {line.strip()}')
    assert not offenders, offenders


def test_extension_ships_no_default_server(tmp):
    src = (ROOT / 'extension' / 'background.js').read_text(encoding='utf-8')
    # The constant exists and is empty: an unconfigured install must not dial
    # anything.
    m = re.search(r"const\s+DEFAULT_SERVER\s*=\s*'([^']*)'", src)
    assert m, 'DEFAULT_SERVER constant not found in background.js'
    assert m.group(1) == '', f'DEFAULT_SERVER ships a URL: {m.group(1)!r}'
    # No hardcoded bridge URL anywhere else in the service worker either.
    assert 'http://' not in src and 'https://' not in src, \
        'background.js contains a hardcoded URL'


def test_extension_startstream_stays_idle_without_url(tmp):
    src = (ROOT / 'extension' / 'background.js').read_text(encoding='utf-8')
    start = src.index('async function startStream()')
    rest = src[start:]
    nxt = rest.find('\nasync function ', 1)
    body = rest[:nxt] if nxt != -1 else rest
    guard = 'if (!config.serverUrl) return;'
    assert guard in body, 'startStream() lost its no-server-URL guard'
    # The guard must come before the first fetch the stream would make.
    assert body.index(guard) < body.index('fetch('), \
        'startStream() fetches before refusing an empty server URL'


def _relay_sent_types(content):
    """Runtime `type` value for each inline content-script send, or None."""
    mask = js_mask(content)
    sent_types = []
    for match in re.finditer(r'chrome\.runtime\.sendMessage\s*\(', mask):
        open_paren = mask.index('(', match.start())
        call_end = js_bracket_end(mask, open_paren)
        args = js_split_top_level(
            mask, content, open_paren + 1, call_end - 1)
        if not args:
            sent_types.append(None)
            continue
        start, end = args[0]
        if not mask[start:end].strip().startswith('{'):
            sent_types.append(None)
            continue
        obj_start = start + mask[start:end].index('{')
        runtime_type = None
        for key, value, _ in js_object_entries(mask, content, obj_start):
            # A spread after the last explicit type can replace it, so the
            # runtime value is no longer statically readable.
            if key is None:
                runtime_type = None
            elif key == 'type':
                found = re.fullmatch(r"'([^'\\]+)'", value or '')
                runtime_type = found.group(1) if found else None
        sent_types.append(runtime_type)
    return sent_types


def _relay_handled_types(background):
    """Unmasked single-quoted msg.type comparisons inside the listener."""
    mask = js_mask(background)
    listener_start = mask.index('chrome.runtime.onMessage.addListener')
    listener_end = js_bracket_end(mask, mask.index('(', listener_start))
    listener = background[listener_start:listener_end]
    handled = set()
    for match in re.finditer(r"msg\.type\s*===\s*'([^'\\]+)'", listener):
        start = listener_start + match.start()
        # Comments and strings are blank at the identifier's position. The
        # raw source supplies the literal value only after this code check.
        if mask[start:start + len('msg.type')] == 'msg.type':
            handled.add(match.group(1))
    return handled


def _relay_coverage_violations(content, background):
    """Return relay message types that the background listener cannot answer."""
    # One result per call makes an unreadable type fail closed. Object entries
    # are processed in source order, so a duplicate later `type` is the value
    # JavaScript sends at runtime.
    extracted = _relay_sent_types(content)
    sent_types = [item for item in extracted if item is not None]
    send_count = len(extracted)
    if len(sent_types) != send_count:
        return [
            f'content.js has {send_count} chrome.runtime.sendMessage call(s) '
            f'but only {len(sent_types)} readable single-quoted type(s) — '
            'the relay shape changed and this guard is stale']
    sent = set(sent_types)
    if not sent:
        return [
            'found no chrome.runtime.sendMessage types in content.js — '
            'the relay shape changed and this guard is stale']
    # Only unmasked branches inside the onMessage listener count: comparisons
    # in comments, strings, helpers, or code after the listener are excluded.
    handled = _relay_handled_types(background)
    missing = sorted(sent - handled)
    if missing:
        return [
            'extension/content.js sends message type(s) '
            + ', '.join(repr(item) for item in missing)
            + ' but extension/background.js onMessage listener has no branch '
            'for them — the send resolves undefined silently. Add the branch '
            'in extension/background.js or remove the send in '
            'extension/content.js.']
    return []


def test_every_content_script_message_type_has_a_background_branch(tmp):
    """Every type content.js sends must have a branch in the background.

    `content.js` relays page-context calls to the service worker with
    `chrome.runtime.sendMessage({ type: ... })`. If the `onMessage` listener
    in `background.js` has no branch for a type, the callback fires with
    `undefined` and the page-side promise resolves to `undefined` with no
    error logged anywhere. The `GM.cookie.list()` relay shipped exactly like
    that — documented in the README, wired in content.js, and dead from the
    day it was written because no `cookies` branch ever existed. (The
    page-facing surface has since been removed; this guard keeps any future
    relay from regressing the same way.) Duplicate object keys are evaluated
    in source order, so the last `type` is checked. Unreadable send shapes
    fail closed, and only unmasked comparisons inside the listener count as
    handlers. What is NOT enforced: `js_mask` does not parse regex literals,
    so a regex literal containing a full `msg.type === 'value'` comparison
    could still look like code to the handler scan.
    """
    del tmp
    content = (ROOT / 'extension' / 'content.js').read_text(encoding='utf-8')
    background = (ROOT / 'extension' / 'background.js').read_text(encoding='utf-8')
    violations = _relay_coverage_violations(content, background)
    assert not violations, '\n'.join(violations)

    reversions = [
        (
            'duplicate type whose last value wins at runtime',
            "chrome.runtime.sendMessage({ type: 'handled',"
            " type: 'runtimeOnly' });",
            "chrome.runtime.onMessage.addListener((msg) => {"
            " if (msg.type === 'handled') {} });",
            'runtimeOnly',
        ),
        (
            'comparison that exists only in a comment',
            "chrome.runtime.sendMessage({ type: 'commentOnly' });",
            "chrome.runtime.onMessage.addListener((msg) => {"
            " // if (msg.type === 'commentOnly') {}\n});",
            'commentOnly',
        ),
    ]
    for label, content_mutation, background_mutation, missing_type in reversions:
        found = _relay_coverage_violations(
            content_mutation, background_mutation)
        assert any(missing_type in item for item in found), (
            f'{label} was NOT caught — the guard asserts a contract it does '
            f'not enforce:\ncontent: {content_mutation}\n'
            f'background: {background_mutation}\nviolations: {found}')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='extensionpolicy_')


if __name__ == '__main__':
    raise SystemExit(main())
