#!/usr/bin/env python3
"""The classic service worker's module-boundary contract."""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary import run_extension_capability_routes  # noqa: E402
from _jsread import js_bracket_end, js_mask  # noqa: E402
from _repo import ROOT  # noqa: E402
from _worker_sources import (imported_worker_paths,  # noqa: E402
                             worker_source_paths)

_JS_IDENTIFIER = r'[A-Za-z_$][\w$]*'
_TOP_LEVEL_DECLARATION = re.compile(
    rf'(?P<function>(?:async\s+)?function\s+'
    rf'(?P<function_name>{_JS_IDENTIFIER})\s*\()'
    rf'|(?P<binding>(?:const|let|var)\s+'
    rf'(?P<binding_name>{_JS_IDENTIFIER})\b)'
    rf'|(?P<class>class\s+(?P<class_name>{_JS_IDENTIFIER})\b)')
_CONTROL_HEADER = re.compile(
    r'(?<![\w$.])(?:if|for|while|with)\s*\(')
_STATEMENT_CONTINUATION = frozenset('([{=,:.?+-*/%&|^!~<>')
_WORKER_PLATFORM_GLOBALS = frozenset({
    'AbortController', 'Date', 'Error', 'Map', 'Math', 'Number', 'Object',
    'Promise', 'Set', 'String', 'TextDecoder', 'URL',
    'Uint8Array', 'atob', 'btoa', 'chrome', 'clearInterval', 'clearTimeout',
    'console', 'crypto', 'fetch', 'parseInt', 'performance', 'setInterval',
    'setTimeout',
})
_WORKER_NON_HANDLER_EXPORTS = frozenset({
    # Add one reviewed non-handler export per line.
})


def _worker_sources():
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding='utf-8'))
        for path in worker_source_paths()
    ]


def _top_level_positions(mask):
    top_level = []
    braces = 0
    brackets = 0
    parentheses = 0
    for char in mask:
        top_level.append(braces == brackets == parentheses == 0)
        if char == '{':
            braces += 1
        elif char == '}':
            braces -= 1
        elif char == '[':
            brackets += 1
        elif char == ']':
            brackets -= 1
        elif char == '(':
            parentheses += 1
        elif char == ')':
            parentheses -= 1
    return top_level


def _starts_statement(mask, start):
    previous = start - 1
    while previous >= 0 and mask[previous].isspace():
        previous -= 1
    if previous < 0 or mask[previous] in ';}':
        return True
    if mask[previous] == ')':
        for control in _CONTROL_HEADER.finditer(mask, 0, previous + 1):
            opening = mask.find('(', control.start(), control.end())
            if js_bracket_end(mask, opening) == previous + 1:
                return False
    line_start = mask.rfind('\n', 0, start) + 1
    if mask[line_start:start].strip():
        return False
    return mask[previous] not in _STATEMENT_CONTINUATION


def _top_level_declarations(source):
    mask = js_mask(source)
    top_level = _top_level_positions(mask)
    declarations = []
    for match in _TOP_LEVEL_DECLARATION.finditer(mask):
        if not top_level[match.start()]:
            continue
        if not _starts_statement(mask, match.start()):
            continue
        kind = match.lastgroup.removesuffix('_name')
        name = match.group(f'{kind}_name')
        declarations.append((name, kind, match.start()))
    return declarations


def _top_level_reassigns(source, name, after):
    mask = js_mask(source)
    top_level = _top_level_positions(mask)
    assignment = re.compile(
        rf'(?<![\w$.]){re.escape(name)}\s*=(?!=|>)')
    return any(
        match.start() > after and top_level[match.start()]
        for match in assignment.finditer(mask)
    )


def _directive_entries(source, directive):
    names = []
    pattern = re.compile(rf'/\*\s*{directive}\b([^*]*)\*/')
    for match in pattern.finditer(source):
        for item in match.group(1).split(','):
            name = item.strip().partition(':')[0]
            if name:
                assert re.fullmatch(_JS_IDENTIFIER, name), (
                    f'unreadable {directive} directive entry {name!r}')
                names.append(name)
    return names


def _directive_names(source, directive):
    return set(_directive_entries(source, directive))


def _masked_code_mentions(masked_source, name):
    pattern = re.compile(
        rf'(?<![\w$]){re.escape(name)}(?![\w$])')
    for match in pattern.finditer(masked_source):
        previous = match.start() - 1
        while previous >= 0 and masked_source[previous].isspace():
            previous -= 1
        following = match.end()
        while (following < len(masked_source)
               and masked_source[following].isspace()):
            following += 1
        if previous >= 0 and masked_source[previous] == '.':
            continue
        if (following < len(masked_source)
                and masked_source[following] == ':'):
            continue
        return match
    return None


def test_each_worker_capability_lives_in_its_own_module(tmp):
    """Every exported handler has one replaceable runtime dispatch route.

    The loader defines the module inventory. Each module's export directives,
    apart from explicit non-handler exceptions, define its handlers. An entry
    in the exception set removes that export from runtime route coverage and
    therefore requires deliberate review. Before probing, the guard requires
    unique handler ownership and exact, duplicate-sensitive route coverage.
    A route row for an unloaded module is also refused.

    The probe checks replaceability first; afterwards, text checks require one
    statement-position function declaration and reject recognised top-level
    reassignment.

    Routing is observed when the sentinel is called; the original handler's
    promise is deliberately not awaited because this guard checks routing, not
    completion. The probe proves dispatch reaches the module, not that the
    module is the only code doing the work; retained background duplicates are
    constrained by the final size ratchet. Dispatch through an alias captured
    at load time is rejected because replacing the published symbol cannot
    update the captured reference.

    A module function that delegates back to an implementation in background
    also passes, because the module is genuinely on the runtime route.
    """
    del tmp
    background = (ROOT / 'extension' / 'background.js').read_text(
        encoding='utf-8')
    routes = [
        ('worker/cookies.js', 'handleCookies', 'cookies'),
        ('worker/cookies.js', 'handleSetCookie', 'set-cookie'),
        ('worker/cookies.js', 'handleRemoveCookie', 'remove-cookie'),
        ('worker/cookies.js', 'handleClearCookies', 'clear-cookies'),
    ]
    background_names = {
        name for name, _, _ in _top_level_declarations(background)
    }
    duplicate_routes = sorted(
        route for route, count in Counter(routes).items() if count > 1)
    assert not duplicate_routes, (
        f'worker route table contains duplicate rows: {duplicate_routes}')

    extension_root = ROOT / 'extension'
    loaded_modules = [
        path.relative_to(extension_root).as_posix()
        for path in imported_worker_paths()
    ]
    duplicate_modules = sorted(
        relative for relative, count in Counter(loaded_modules).items()
        if count > 1
    )
    assert not duplicate_modules, (
        f'importScripts names duplicate worker modules: {duplicate_modules}')
    unloaded_routes = sorted(
        (relative, symbol)
        for relative, symbol, _ in routes
        if relative not in loaded_modules
    )
    assert not unloaded_routes, (
        'worker route table names modules not loaded by importScripts: '
        f'{unloaded_routes}')

    module_details = []
    duplicate_exports = {}
    for relative in loaded_modules:
        module_path = extension_root / relative
        assert module_path.is_file(), f'{relative} does not exist'
        module = module_path.read_text(encoding='utf-8')
        exported = _directive_entries(module, 'exported')
        duplicates = sorted(
            name for name, count in Counter(exported).items() if count > 1)
        if duplicates:
            duplicate_exports[relative] = duplicates
        module_details.append({
            'relative': relative,
            'source': module,
            'handlers': [
                name for name in exported
                if name not in _WORKER_NON_HANDLER_EXPORTS
            ],
        })
    assert not duplicate_exports, (
        f'worker modules export names more than once: {duplicate_exports}')

    worker_exports = {
        name
        for details in module_details
        for name in _directive_names(details['source'], 'exported')
    }
    stale_exceptions = sorted(_WORKER_NON_HANDLER_EXPORTS - worker_exports)
    assert not stale_exceptions, (
        f'non-handler export exceptions no longer exist: {stale_exceptions}')

    owners = {}
    for details in module_details:
        for handler in details['handlers']:
            owners.setdefault(handler, []).append(details['relative'])
    duplicate_owners = {
        handler: relatives
        for handler, relatives in sorted(owners.items())
        if len(relatives) > 1
    }
    assert not duplicate_owners, (
        f'worker handlers must have exactly one owning module: '
        f'{duplicate_owners}')

    for details in module_details:
        relative = details['relative']
        module = details['source']
        module_routes = [
            (symbol, command_type)
            for route_module, symbol, command_type in routes
            if route_module == relative
        ]
        declarations = _top_level_declarations(module)
        handler_counts = Counter(details['handlers'])
        route_counts = Counter(symbol for symbol, _ in module_routes)
        uncovered = sorted((handler_counts - route_counts).elements())
        unexpected = sorted((route_counts - handler_counts).elements())
        assert not uncovered and not unexpected, (
            f'{relative} route table mismatch: uncovered handlers '
            f'{uncovered}; non-handler or unexported routes {unexpected}')
        observations = run_extension_capability_routes([
            {
                'symbol': symbol,
                'command': {
                    'id': 'policy-capability', 'type': command_type,
                },
            }
            for symbol, command_type in module_routes
        ])
        assert len(observations) == len(module_routes), observations
        for (symbol, command_type), observed in zip(
                module_routes, observations):
            contract = (
                f'{relative} must publish {symbol} as one unreassigned '
                'top-level function declaration so the classic-worker route '
                'probe can replace it')
            assert observed['symbol'] == symbol, observed
            assert (observed['available'] and observed['replaceable']), (
                contract)
            functions = [
                start for name, kind, start in declarations
                if name == symbol and kind == 'function'
            ]
            valid_declaration = (
                len(functions) == 1
                and not _top_level_reassigns(module, symbol, functions[0])
            )
            assert valid_declaration, contract
            assert symbol not in background_names, (
                f'background.js still declares {symbol} at top level')
            assert observed == {
                'symbol': symbol,
                'available': True,
                'replaceable': True,
                'callCount': 1,
                'calledType': command_type,
                'answered': True,
            }, (
                f"runtime dispatch for {command_type!r} bypasses {symbol}: "
                f'{observed}')


def test_worker_module_directives_resolve_to_worker_symbols(tmp):
    """Best-effort text check for classic-worker directive typos.

    Export-derived exact table coverage and the per-handler runtime probe are
    the primary guarantee. This secondary graph catches ordinary typos cheaply,
    but reassignment detection is position- and spelling-dependent, an object
    method key can look like a consumer, and `js_mask` does not parse regex
    literals (issue 198). The platform allowlist is trusted input, not proof of
    a platform global.
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
            'declared': {
                name for name, _, _ in _top_level_declarations(source)
            },
        }
        for relative, source in worker_sources
    ]
    declarations = {
        name
        for details in source_details
        for name in details['declared']
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
        absent = exported - details['declared']
        if absent:
            unpublished[relative] = sorted(absent)
        used = {
            name for name in consumed | exported
            if _masked_code_mentions(details['masked'], name)
        }
        unused = (consumed | exported) - used
        if unused:
            unused_directives[relative] = sorted(unused)
        for name in consumed & used:
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='workermodule_')


if __name__ == '__main__':
    raise SystemExit(main())
