#!/usr/bin/env python3
"""What the shipped extension may carry, read out of its own source.

An extension is published to browsers, so what it must not contain is as
load-bearing as what it does: no default server, no token in a log line, no
capture limit spelled differently in two places, and no message type the
content script sends that the background has no branch for.
"""
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsread import (blank_js_comments, js_bracket_end,  # noqa: E402
                     js_mask, js_object_entries, js_split_top_level)
from _jsroute_keys import decode_string_literal  # noqa: E402
from _repo import ROOT  # noqa: E402
from _worker_sources import worker_source_paths  # noqa: E402


# GM.info is metadata about the shim, not a capability it grants, so the
# install-time warning has nothing to say about it.
_GM_NON_CAPABILITIES = frozenset({'GM.info'})


def _worker_sources():
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding='utf-8'))
        for path in worker_source_paths()
    ]


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

    What is NOT enforced: a regex body is blanked rather than read, so a
    declaration written inside one is not counted; where the previous-token
    guess behind that body is wrong, the mask can still desynchronise.
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
    assert len(declared) == 1, (
        f'expected one CLI declaration, found {declared}')
    mcp = (_util.ROOT / 'daedalus_mcp' / 'tools_network.py').read_text(
        encoding='utf-8')

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
    assert mcp_match, (
        'no capture ceiling declared in daedalus_mcp/tools_network.py')
    values = {
        extension_declared[0]: extension_declared[1],
        'daedalus_mcp/tools_network.py': int(mcp_match.group(1)),
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


def _console_arguments(mask):
    member = re.compile(
        r'(?<![\w$])console\s*(?:\??\.\s*[\w$]+|(?:\?\.\s*)?\[)')
    for sink in member.finditer(mask):
        end = sink.end()
        if mask[end - 1] == '[':
            # Every computed console member is a potential logging sink.
            end = js_bracket_end(mask, end - 1)
        grouped = re.match(r'\s*(?:\)\s*)*', mask[end:])
        end += grouped.end()
        wrapper = re.match(r'\??\.\s*([\w$]+)|(?:\?\.\s*)?\[', mask[end:])
        if wrapper:
            if wrapper.group(1) not in ('call', 'apply'):
                yield None
                continue
            end += wrapper.end()
        call = re.match(r'\s*(?:\?\.\s*)?\(', mask[end:])
        if call:
            start = end + call.end() - 1
            yield start + 1, js_bracket_end(mask, start) - 1
        elif wrapper:
            yield None


def _logs_bridge_token(line, mask):
    # Identifier escapes are unsupported; a slash after a brace is ambiguous
    # to js_mask (function expressions versus blocks). Neither certifies clean.
    if '\\' in mask or re.search(r'}\s*/', mask):
        return True
    access = re.compile(
        r'\.\s*([\w$]+)|(?<=[\w$)\]])\s*(?:\?\.\s*)?\[')
    prefix = re.compile(
        r'\s*\.\s*(?:substring|slice)\s*\(\s*0\s*,\s*[1-8]\s*\)')
    for bounds in _console_arguments(mask):
        if bounds is None:
            return True
        start, end = bounds
        arguments = mask[start:end]
        for match in access.finditer(arguments):
            read_end = match.end()
            if match.group(1) is not None and match.group(1) != 'token':
                continue
            if arguments[read_end - 1] == '[':
                read_end = js_bracket_end(arguments, read_end - 1)
                raw = line[start + match.end():start + read_end - 1]
                raw = blank_js_comments(raw).strip()
                # The shared decoder does not resolve legacy numeric escapes.
                if re.search(r'\\[0-9\u2028\u2029]', raw):
                    return True
                key = decode_string_literal(raw)
                if key is None:
                    if re.fullmatch(r'[0-9]+', raw):
                        continue
                    return True
                if key != 'token':
                    continue
            # A prefix exempts this read only, never a neighbouring read.
            if not prefix.match(arguments, read_end):
                return True
    return False


def test_the_extension_never_logs_the_bridge_token(tmp):
    """The token is a reusable browser-control credential, not a diagnostic.

    First-run bootstrap printed the whole generated token, which put it into
    extension DevTools output, screen recordings and any diagnostic bundle
    collected from them — all places a credential outlives the moment it was
    useful in. A truncated prefix is not what this pins: the version banner
    logs eight characters to say which bridge is configured, and that stays.
    This line-local policy refuses potential reads in console arguments;
    it does not evaluate control flow or the values of argument expressions.
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
        source = path.read_text(encoding='utf-8')
        # Mask once to retain comment/template state across line boundaries;
        # call argument matching remains line-local (multiline is #848).
        normalized = source.translate(dict.fromkeys(
            map(ord, '\ufeff\u2028\u2029'), ' '))
        lines = zip(source.split('\n'), js_mask(normalized).split('\n'))
        for number, (line, mask) in enumerate(lines, 1):
            if _logs_bridge_token(line, mask):
                offenders.append(f'{name}:{number}: {line.strip()}')
    assert not offenders, offenders


def test_token_log_scanner_distinguishes_code_from_text(tmp):
    del tmp
    cases = [
        (True,
         'console.log(config.token);'),
        (True,
         "console.log(config['token']);"),
        (True,
         'console.log(config["token"]);'),
        (True,
         "const key = 'token'; console.log(config[key]);"),
        (True,
         "console.log(config?.['token']);"),
        (True,
         "const key = 'token'; console.log(config?.[key]);"),
        (True,
         'console.log(config ?. token);'),
        (True,
         "console['log'](config.token);"),
        (True,
         'console . log(config.token);'),
        (True,
         'console?.log(config.token);'),
        (True,
         "console.log(config [ 'token' ]);"),
        (True,
         'console.log(config . token);'),
        (True,
         "console.log(config\t[\t'token'\t]);"),
        (True,
         'console.log(config./* diagnostic */token);'),
        (True,
         "console.log(config /* diagnostic */ ['token']);"),
        (False,
         'console.log(config.token.substring(0, 8));'),
        (False,
         'console.log(config.token.slice(0, 8));'),
        (False,
         "console.log(config['token'].slice(0, 8));"),
        (True,
         'console.log(config.token.slice(0, 9));'),
        (True,
         'const n = 32; console.log(config.token.slice(0, n));'),
        (True,
         'console.log(config.token.slice(0, 8), config.token);'),
        (True,
         "console.log(config.token.slice(0, 8), config['token']);"),
        (True,
         "const keys = ['token']; console.log(config[keys[0]]);"),
        (True,
         "console.log(config['token']['toString']());"),
        (True,
         "const keys = ['token']; console.log(config?.[keys[0]]);"),
        (True,
         'console.log(config[`token`]);'),
        (True,
         "const key = 'token'; console.log(config[`${key}`]);"),
        (True,
         "console.log(`credential=${config['token']}`);"),
        (False,
         "console.log(config['tabId']);"),
        (False,
         "console.log('token');"),
        (False,
         'console.log("config[\'token\']");'),
        (False,
         "console.log('config.token');"),
        (False,
         "console.log('status[ready]');"),
        (False,
         "console.log(`config['token']`);"),
        (False,
         'console.log(`status[ready]`);'),
        (False,
         "const value = config['token']; void value;"),
        (False,
         'const value = config.token; void value;'),
        (False,
         "console.log('ready'); const value = config['token']; void value;"),
        (False,
         "console.log(['ready'][0]);"),
        (False,
         "// console.log(config['token']);"),
        (False,
         "console.log(config['tab]Id']);"),
        (False,
         "console.log(config['tab\\u0049d']);"),
        (True,
         "console.log(config['to\\u006ben']);"),
        (True,
         'console[method](config.token);'),
        (True,
         "console?.['log'](config.token);"),
        (True,
         'console.log(config[key].substring(0, 8));'),
        (True,
         "console.log(config['token'].slice(0));"),
    ]
    for refused, source in cases:
        assert _logs_bridge_token(source, js_mask(source)) == refused, (
            source, refused)


def test_token_log_prefix_budget_accepts_each_literal_bound(tmp):
    del tmp
    for method in ('slice', 'substring'):
        for bound in range(1, 9):
            source = f"console.log(config.token.{method}(0, {bound}));"
            assert not _logs_bridge_token(source, js_mask(source)), source


def _check_token_sources(tmp, cases):
    root = Path(tmp)
    target = root / 'extension' / 'content.js'
    target.parent.mkdir(exist_ok=True)
    failures = []
    for refused, source in cases:
        target.write_text(source, encoding='utf-8')
        with (patch.object(_util, 'ROOT', root),
              patch(__name__ + '.worker_source_paths', return_value=[])):
            try:
                test_the_extension_never_logs_the_bridge_token(None)
            except AssertionError:
                actual = True
            else:
                actual = False
        if actual != refused:
            failures.append((source, refused, actual))
    assert not failures, failures


def test_token_guard_refuses_identifier_and_key_escapes(tmp):
    _check_token_sources(tmp, [
        (True, r'console.log(config.\u0074oken);'),
        (True, r'console.log(config.to\u006ben);'),
        (True, r'console.log(config.\u{74}oken);'),
        (True, r'console.\u006cog(config.token);'),
        (True, r'con\u0073ole.log(config.token);'),
        (True, r"console.log(config['\164oken']);"),
        (True, r"console.log(config['\164oken'].slice(0, 8));"),
        (False, 'console.log(config.token$);'),
        (False, 'console.log(config.token_more);'),
    ])


def test_token_guard_recognizes_wrapped_console_calls(tmp):
    _check_token_sources(tmp, [
        (True, '(console.log)(config.token);'),
        (True, "(console['log'])(config['token']);"),
        (True, 'console.log.call(console, config.token);'),
        (True, 'console.log.apply(console, [config.token]);'),
        (True, 'console.log?.apply(console, [config.token]);'),
        (True, 'console.log.bind(console)(config.token);'),
        (True, 'console.log[wrapper](console)(config.token);'),
        (False, '(console.log)(config.token.slice(0, 8));'),
        (False, 'console.log.call(console, config.token.slice(0, 8));'),
        (True, "console.assert(true, config['token']);"),
        (True, 'console.log(() => config.token);'),
    ])


def test_token_guard_recognizes_javascript_whitespace(tmp):
    _check_token_sources(tmp, [
        (True, source)
        for space in ('\ufeff', '\u00a0', '\u2028', '\u2029', '\t')
        for source in (f'console{space}.log(config.token);',
                       f'console.log(config{space}["token"]);')
    ])


def test_token_guard_masks_whole_sources(tmp):
    _check_token_sources(tmp, [
        (False, "/* open\n*/ const value = config['token'];"),
        (False, "/* open\nconsole.log(config['token']);\n*/"),
        (False, "const text = `open\nconsole.log(config.token);\nend`;"),
        (True, "/* open\n*/ console.log(config['token']);"),
        (True, 'const text = `open\n${console.log(config.token)}\nend`;'),
    ])


def test_token_guard_refuses_ambiguous_masking(tmp):
    _check_token_sources(tmp, [
        (True, "const n = function() {} / /'/.source.length; "
         'console.log(config.token);'),
        (True, "const n = function() {} / /'/.source.length;\n"
         'console.log(config.token);'),
        (False, "console.log(/'/.source);"),
        (False, "const text = `function() {} / /'/`;"),
    ])


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


def _relay_coverage_violations(content, listener, listener_module):
    """Return relay message types that the background listener cannot
    answer."""
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
    handled = _relay_handled_types(listener)
    missing = sorted(sent - handled)
    if missing:
        return [
            'extension/content.js sends message type(s) '
            + ', '.join(repr(item) for item in missing)
            + f' but {listener_module} onMessage listener has no branch '
            'for them — the send resolves undefined silently. Add the branch '
            f'in {listener_module} or remove the send in '
            'extension/content.js.']
    return []


def test_every_content_script_message_type_has_a_background_branch(tmp):
    """Every type content.js sends must have a branch in the background.

    `content.js` relays page-context calls to the service worker with
    `chrome.runtime.sendMessage({ type: ... })`. If the `onMessage` listener
    has no branch for a type, the callback fires with
    `undefined` and the page-side promise resolves to `undefined` with no
    error logged anywhere. The `GM.cookie.list()` relay shipped exactly like
    that — documented in the README, wired in content.js, and dead from the
    day it was written because no `cookies` branch ever existed. (The
    page-facing surface has since been removed; this guard keeps any future
    relay from regressing the same way.) Duplicate object keys are evaluated
    in source order, so the last `type` is checked. Unreadable send shapes
    fail closed, and only unmasked comparisons inside the listener count as
    handlers. What is NOT enforced: a regex body is blanked rather than
    read, so a comparison written inside one is not seen; where the
    previous-token guess behind that body is wrong, the mask can still
    desynchronise.
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
    listener_module, listener = listeners[0]
    violations = _relay_coverage_violations(
        content, listener, listener_module)
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
    for (label, content_mutation,
         background_mutation, missing_type) in reversions:
        listener_module = 'extension/worker/runtime.js'
        found = _relay_coverage_violations(
            content_mutation, background_mutation, listener_module)
        assert any(missing_type in item for item in found), (
            f'{label} was NOT caught — the guard asserts a contract it does '
            f'not enforce:\ncontent: {content_mutation}\n'
            f'background: {background_mutation}\nviolations: {found}')
        assert all(listener_module in item for item in found), found


def _shipped_manifest():
    return json.loads(
        (ROOT / 'extension' / 'manifest.json').read_text(encoding='utf-8'))


def test_the_manifest_declares_the_lowest_chrome_the_code_needs(tmp):
    """Sub-minute heartbeat alarms need Chrome 120, so the manifest says so."""
    del tmp
    floor = _shipped_manifest().get('minimum_chrome_version')
    assert isinstance(floor, str) and floor, floor
    head = floor.split('.')[0]
    assert head.isdigit() and int(head) >= 120, floor
    background = ROOT / 'extension' / 'background.js'
    source = background.read_text(encoding='utf-8')
    masked = js_mask(source)
    heartbeat = re.search(
        r'alarms\.create\(([^,]*?),\s*'
        r'\{\s*periodInMinutes:\s*0\.5\s*\}\s*\)', masked)
    assert heartbeat, 'the sub-minute heartbeat the 120 floor needs is gone'
    name = source[heartbeat.start(1):heartbeat.end(1)]
    assert name.strip() == "'daedalus-heartbeat'", name


def test_the_manifest_does_not_grant_activeTab_beside_all_urls(tmp):
    """<all_urls> already grants what activeTab could, so it is not held."""
    del tmp
    manifest = _shipped_manifest()
    permissions = manifest.get('permissions', [])
    assert 'activeTab' not in permissions, permissions
    hosts = manifest.get('host_permissions', [])
    assert '<all_urls>' in hosts, (
        'the <all_urls> host permission the activeTab removal leans on '
        'is gone')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='extensionpolicy_')


if __name__ == '__main__':
    raise SystemExit(main())
