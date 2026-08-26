#!/usr/bin/env python3
"""What the shipped extension may carry, read out of its own source.

An extension is published to browsers, so what it must not contain is as
load-bearing as what it does: no default server, no token in a log line, no
capture limit spelled differently in two places, and no message type the
content script sends that the background has no branch for.
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary import run_extension_capability_route  # noqa: E402
from _jsread import (blank_js_comments, js_bracket_end,  # noqa: E402
                     js_mask, js_object_entries, js_split_top_level)
from _repo import ROOT  # noqa: E402
from _worker_sources import (imported_worker_paths,  # noqa: E402
                             worker_source_paths)


# GM.info is metadata about the shim, not a capability it grants, so the
# install-time warning has nothing to say about it.
_GM_NON_CAPABILITIES = frozenset({'GM.info'})

_JS_IDENTIFIER = r'[A-Za-z_$][\w$]*'
_TOP_LEVEL_DECLARATION = re.compile(
    rf'\b(?:async\s+)?function\s+({_JS_IDENTIFIER})\s*\('
    rf'|\bclass\s+({_JS_IDENTIFIER})\b'
    rf'|\b(?:const|let|var)\s+({_JS_IDENTIFIER})\b')
_TOP_LEVEL_FUNCTION_DECLARATION = re.compile(
    rf'(?:^|(?<=[;\x7d]))\s*'
    rf'(?:async\s+)?function\s+({_JS_IDENTIFIER})\s*\(')
_WORKER_PLATFORM_GLOBALS = frozenset({
    'AbortController', 'Date', 'Error', 'Map', 'Math', 'Number', 'Object',
    'Promise', 'Set', 'String', 'TextDecoder', 'URL',
    'Uint8Array', 'atob', 'btoa', 'chrome', 'clearInterval', 'clearTimeout',
    'console', 'crypto', 'fetch', 'parseInt', 'performance', 'setInterval',
    'setTimeout',
})


def _worker_sources():
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding='utf-8'))
        for path in worker_source_paths()
    ]


def _top_level_matches(source, pattern):
    mask = js_mask(source)
    top_level = []
    depth = 0
    for char in mask:
        top_level.append(depth == 0)
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
    return [
        match for match in pattern.finditer(mask)
        if top_level[match.start()]
    ]


def _top_level_declarations(source):
    return {
        next(group for group in match.groups() if group is not None)
        for match in _top_level_matches(source, _TOP_LEVEL_DECLARATION)
    }


def _top_level_function_declarations(source):
    return {
        match.group(1)
        for match in _top_level_matches(
            source, _TOP_LEVEL_FUNCTION_DECLARATION)
    }


def _directive_names(source, directive):
    names = set()
    pattern = re.compile(rf'/\*\s*{directive}\b([^*]*)\*/')
    for match in pattern.finditer(source):
        for item in match.group(1).split(','):
            name = item.strip().partition(':')[0]
            if name:
                assert re.fullmatch(_JS_IDENTIFIER, name), (
                    f'unreadable {directive} directive entry {name!r}')
                names.add(name)
    return names


def _masked_code_mentions(masked_source, name):
    return re.search(
        rf'(?<![\w$]){re.escape(name)}(?![\w$])', masked_source)


def test_each_worker_capability_lives_in_its_own_module(tmp):
    """Each module owns a replaceable function reached by runtime dispatch.

    Routing is observed when the sentinel is called; the original handler's
    promise is deliberately not awaited because this guard checks routing, not
    completion. The probe proves dispatch reaches the module, not that the
    module is the only code doing the work; retained background duplicates are
    constrained by the final size ratchet. Dispatch through an alias captured
    at load time is rejected because replacing the published symbol cannot
    update the captured reference.

    What is NOT enforced: `js_mask` does not parse regex literals, so regex
    text can still masquerade as a top-level declaration. A module function
    that delegates back to an implementation in background also passes,
    because the module is genuinely on the runtime route.
    """
    del tmp
    background = (ROOT / 'extension' / 'background.js').read_text(
        encoding='utf-8')
    capabilities = [('worker/cookies.js', 'handleCookies', 'cookies')]

    for relative, symbol, command_type in capabilities:
        module_path = ROOT / 'extension' / relative
        assert module_path.is_file(), f'{relative} does not exist'
        module = module_path.read_text(encoding='utf-8')
        assert symbol in _top_level_function_declarations(module), (
            f'{relative} must declare {symbol} as a top-level function '
            'declaration so the classic-worker route probe can replace it')
        assert symbol not in _top_level_declarations(background), (
            f'background.js still declares {symbol} at top level')
        observed = run_extension_capability_route(
            symbol, {'id': 'policy-capability', 'type': command_type})
        assert observed == {
            'callCount': 1,
            'calledType': command_type,
            'answered': True,
        }, (
            f"runtime dispatch for {command_type!r} bypasses {symbol}: "
            f'{observed}')


def test_worker_module_directives_resolve_to_worker_symbols(tmp):
    """Classic-worker directives form a used producer-consumer graph.

    The platform allowlist is trusted input: adding an entry does not prove it
    is a real platform global. This catches unresolved names and typos, not a
    dishonest allowlist edit. What is NOT enforced: `js_mask` does not parse
    regex literals, so regex text can masquerade as a declaration.
    """
    del tmp
    worker_sources = _worker_sources()
    source_details = [
        {
            'relative': relative,
            'source': source,
            'masked': js_mask(source),
            'consumed': _directive_names(source, 'global'),
            'exported': _directive_names(source, 'exported'),
        }
        for relative, source in worker_sources
    ]
    declarations = {
        name
        for details in source_details
        for name in _top_level_declarations(details['source'])
    }
    worker_code = '\n'.join(
        details['masked'] for details in source_details)
    unused_platform_names = sorted(
        name for name in _WORKER_PLATFORM_GLOBALS
        if not _masked_code_mentions(worker_code, name)
    )
    assert not unused_platform_names, (
        'worker platform allowlist contains unused names: '
        f'{unused_platform_names}')

    unresolved = {}
    unpublished = {}
    unused_directives = {}
    consumers = {}
    for details in source_details:
        relative = details['relative']
        consumed = details['consumed']
        exported = details['exported']
        missing = consumed - declarations - _WORKER_PLATFORM_GLOBALS
        if missing:
            unresolved[relative] = sorted(missing)
        absent = exported - _top_level_declarations(details['source'])
        if absent:
            unpublished[relative] = sorted(absent)
        unused = {
            name for name in consumed | exported
            if not _masked_code_mentions(details['masked'], name)
        }
        if unused:
            unused_directives[relative] = sorted(unused)
        for name in consumed:
            consumers.setdefault(name, set()).add(relative)
    assert not unresolved, (
        f'classic worker sources consume undeclared names: {unresolved}')
    assert not unpublished, (
        f'classic worker sources export undeclared names: {unpublished}')
    assert not unused_directives, (
        f'classic worker directives name unused symbols: {unused_directives}')

    unconsumed = {}
    for details in source_details:
        relative = details['relative']
        missing = {
            name for name in details['exported']
            if not (consumers.get(name, set()) - {relative})
        }
        if missing:
            unconsumed[relative] = sorted(missing)
    assert not unconsumed, (
        f'classic worker sources export unconsumed names: {unconsumed}')


def test_worker_imports_match_worker_modules(tmp):
    del tmp
    imported = [path.resolve() for path in imported_worker_paths()]
    duplicates = sorted(
        path.relative_to(ROOT).as_posix()
        for path, count in Counter(imported).items()
        if count > 1
    )
    assert not duplicates, (
        f'importScripts names duplicate worker paths: {duplicates}')
    named = set(imported)
    shipped = {
        path.resolve()
        for path in (ROOT / 'extension' / 'worker').glob('*.js')
    }
    assert named == shipped, {
        'named but absent': sorted(
            path.relative_to(ROOT).as_posix() for path in named - shipped),
        'shipped but unnamed': sorted(
            path.relative_to(ROOT).as_posix() for path in shipped - named),
    }


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

    What is NOT enforced: `js_mask` does not parse regex literals, so a
    harmless regex containing `const NET_CAPTURE_MAX = 19999;` is counted as
    a declaration and produces a false positive.
    """
    del tmp
    worker_sources = _worker_sources()
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

    declaration_pattern = re.compile(
        r'\b(?:const|let|var)\s+NET_CAPTURE_MAX\b')
    extension_declarations = [
        (name, source, match)
        for name, source in worker_sources
        for match in declaration_pattern.finditer(js_mask(source))
    ]
    declaration_sites = [name for name, _, _ in extension_declarations]
    assert len(extension_declarations) == 1, (
        'expected one extension NET_CAPTURE_MAX declaration, found '
        f'{declaration_sites}')
    extension_name, extension_source, declaration = (
        extension_declarations[0])
    literal = re.match(
        r'\b(?:const|let|var)\s+NET_CAPTURE_MAX\s*=\s*(\d+)\s*;',
        js_mask(extension_source)[declaration.start():])
    assert literal, (
        f'{extension_name} NET_CAPTURE_MAX declaration is not a decimal '
        'literal')
    extension_declared = (extension_name, int(literal.group(1)))
    mcp_match = re.search(r'NET_CAPTURE_MAX = (\d+)', mcp)
    assert mcp_match, 'no capture ceiling declared in mcp_server.py'
    values = {
        extension_declared[0]: extension_declared[1],
        'mcp_server.py': int(mcp_match.group(1)),
    }
    values[f'daedalus_cli/{declared[0][0]}'] = declared[0][1]
    assert len(set(values.values())) == 1, values

    # And the buffer is bounded by the validated value rather than by an
    # inline default that accepts whatever it is handed. Comments are blanked
    # first: the ones explaining this change quote the expression it replaced.
    code_sources = [
        (name, blank_js_comments(source))
        for name, source in worker_sources
    ]
    unvalidated = [
        name for name, code in code_sources if 'maxRequests || 1000' in code
    ]
    assert not unvalidated, f'an unvalidated fallback remains in {unvalidated}'
    validated = [
        name
        for name, code in code_sources
        for _ in re.finditer(
            re.escape('_netCaptureLimit(cmd.maxRequests)'), code)
    ]
    assert len(validated) == 1, (
        'expected one validated capture allocation, found '
        f'{validated}')


def test_every_registry_call_checks_its_http_status(tmp):
    """A refusal is not a success, and fetch does not say so on its own.

    fetch resolves normally for 401, 413 and 500, so `await fetch(...)` with
    only a network-error catch reads every refusal as a completed
    registration. All three registry routes went out that way, so the
    server's tab registry could sit stale with nothing reported anywhere.
    """
    del tmp
    worker_sources = _worker_sources()

    # No registry route may be fetched outside the one helper.
    direct = []
    for route in ('/register', '/unregister', '/sync-tabs'):
        for name, source in worker_sources:
            for match in re.finditer(
                    r'fetch\(\s*config\.serverUrl\s*\+\s*[\'"]'
                    + re.escape(route) + r'[\'"]', source):
                direct.append(
                    f'{route} in {name} at offset {match.start()}')
    assert not direct, direct

    for route in ('/register', '/unregister', '/sync-tabs'):
        calls = [
            f'{name} at offset {match.start()}'
            for name, source in worker_sources
            for match in re.finditer(
                re.escape(f"registryPost('{route}'"), source)
        ]
        assert len(calls) == 1, f'{route}: {calls}'

    # And the helper is what actually looks at the status.
    helper_sources = [
        (name, source)
        for name, source in worker_sources
        if 'async function registryPost(' in source
    ]
    assert len(helper_sources) == 1, (
        f'expected one registryPost definition, found '
        f'{[name for name, _ in helper_sources]}')
    helper_name, source = helper_sources[0]
    _, marker, after = source.partition('async function registryPost(')
    assert marker, (
        f'registryPost in {helper_name} is not defined the way this test '
        'finds it')
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
    paths = [
        *worker_source_paths(),
        _util.ROOT / 'extension' / 'content.js',
        _util.ROOT / 'extension' / 'page.js',
        _util.ROOT / 'extension' / 'options.js',
    ]
    for path in paths:
        name = path.relative_to(_util.ROOT / 'extension').as_posix()
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
    hardcoded = [
        name
        for name, source in _worker_sources()
        if 'http://' in source or 'https://' in source
    ]
    assert not hardcoded, (
        f'worker source contains a hardcoded URL: {hardcoded}')


def test_extension_startstream_stays_idle_without_url(tmp):
    matches = [
        (name, source)
        for name, source in _worker_sources()
        if 'async function startStream()' in source
    ]
    assert len(matches) == 1, (
        f'expected one startStream definition, found '
        f'{[name for name, _ in matches]}')
    _, src = matches[0]
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
    listeners = [
        (name, source)
        for name, source in _worker_sources()
        if 'chrome.runtime.onMessage.addListener' in source
    ]
    assert len(listeners) == 1, (
        f'expected one runtime message listener, found '
        f'{[name for name, _ in listeners]}')
    _, background = listeners[0]
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
