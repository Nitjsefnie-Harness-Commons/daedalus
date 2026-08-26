#!/usr/bin/env python3
"""The classic service worker's module-boundary contract."""
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary import (observe_extension_worker_paths,  # noqa: E402
                       run_extension_capability_routes)
from _jsread import js_mask  # noqa: E402
from _repo import ROOT  # noqa: E402
from _worker_runtime import observe_worker_runtime  # noqa: E402
from _worker_sources import worker_source_paths  # noqa: E402

_JS_IDENTIFIER = r'[A-Za-z_$][\w$]*'
_WORKER_PLATFORM_GLOBALS = frozenset({
    'AbortController', 'Date', 'Error', 'Map', 'Math', 'Number', 'Object',
    'Promise', 'Set', 'String', 'TextDecoder', 'URL',
    'Uint8Array', 'atob', 'btoa', 'chrome', 'clearInterval', 'clearTimeout',
    'console', 'crypto', 'fetch', 'parseInt', 'performance', 'setInterval',
    'setTimeout',
})
_WORKER_NON_HANDLER_EXPORTS = (
    # Add one reviewed non-handler export per line.
    '_cdpError',
    '_cdpSessions',
    '_cdpSettle',
    'handleHotfixReplay',
    '_netCaptures',
    '_releaseCdpObjects',
)
_WORKER_REDECLARATION_EXCEPTIONS = (
    # Add one reviewed intentional top-level redeclaration per line.
)


def _worker_sources():
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding='utf-8'))
        for path in worker_source_paths()
    ]


def _runtime_observations(watched_by_source=None):
    if watched_by_source is None:
        watched_by_source = {}
    details = []
    for relative, source in _worker_sources():
        globals_ = _directive_names(source, 'global')
        details.append({
            'path': ROOT / relative,
            'globals': globals_,
            'probes': globals_ | _directive_names(source, 'exported'),
            'watched': watched_by_source.get(relative, ()),
        })
    observed = observe_worker_runtime(details)
    return {
        Path(path).relative_to(ROOT).as_posix(): details
        for path, details in observed['sources'].items()
    }, observed['shared']


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

    The existing VM harness records the paths the worker actually asks
    `importScripts` to load; that observation defines the module inventory.
    Each module's export directives, apart from explicit non-handler
    exceptions, define its handlers. An entry in the exception tuple removes
    that export from runtime route coverage and therefore requires deliberate
    review. Before probing, the guard requires unique handler ownership and
    exact, duplicate-sensitive route-symbol coverage. A route row for an
    unloaded module is also refused. Command types are runtime-probe inputs,
    not an exhaustive inventory of the dispatch surface.

    The probe checks replaceability first. A separate runtime observation
    requires one function-instantiation write before source execution and no
    later assignment to the published handler.

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
    routes = [
        ('worker/capture.js', 'handleScreenshot', 'screenshot'),
        ('worker/cookies.js', 'handleCookies', 'cookies'),
        ('worker/cookies.js', 'handleSetCookie', 'set-cookie'),
        ('worker/cookies.js', 'handleRemoveCookie', 'remove-cookie'),
        ('worker/cookies.js', 'handleClearCookies', 'clear-cookies'),
        ('worker/blocking.js', 'handleBlockRequests', 'block-requests'),
        ('worker/blocking.js', 'handleUnblockRequests', 'unblock-requests'),
        ('worker/blocking.js', 'handleListBlockRules', 'list-block-rules'),
        ('worker/tabs.js', 'handleCloseTab', 'close-tab'),
        ('worker/tabs.js', 'handleOpenTab', 'open-tab'),
        ('worker/tabs.js', 'handleOpenTabs', 'open-tabs'),
        ('worker/tabs.js', 'handleFocusTab', 'focus-tab'),
        ('worker/tabs.js', 'handleNavigate', 'navigate'),
        ('worker/tabs.js', 'handleReload', 'reload'),
        ('worker/tabs.js', 'handleInjectCss', 'inject-css'),
        ('worker/tabs.js', 'handleRemoveCss', 'remove-css'),
        ('worker/tabs.js', 'handleExtReload', 'ext-reload'),
        ('worker/tabs.js', 'handleFetchTimings', 'fetch-timings'),
        ('worker/tabs.js', 'handleExtTabs', 'tabs'),
        ('worker/cdp.js', 'handleCdp', 'cdp'),
        ('worker/netcapture.js', 'handleNetCapture', 'net-capture'),
        ('worker/netcapture.js', 'handleNetCaptureStop', 'net-capture-stop'),
        ('worker/netcapture.js', 'handleNetCaptureGet', 'net-capture-get'),
        ('worker/hotfixes.js', 'handleStoreHotfix', 'store-hotfix'),
        ('worker/hotfixes.js', 'handleClearHotfix', 'clear-hotfix'),
        ('worker/hotfixes.js', 'handleClearAllHotfixes',
         'clear-all-hotfixes'),
        ('worker/hotfixes.js', 'handleListHotfixes', 'list-hotfixes'),
        ('worker/hotfixes.js', 'handleSetPermanent', 'set-permanent'),
    ]
    duplicate_routes = sorted(
        route for route, count in Counter(routes).items() if count > 1)
    assert not duplicate_routes, (
        f'worker route table contains duplicate rows: {duplicate_routes}')
    duplicate_exceptions = sorted(
        name for name, count
        in Counter(_WORKER_NON_HANDLER_EXPORTS).items()
        if count > 1
    )
    assert not duplicate_exceptions, (
        'non-handler export exceptions contain duplicate entries: '
        f'{duplicate_exceptions}')
    non_handler_exports = set(_WORKER_NON_HANDLER_EXPORTS)

    extension_root = ROOT / 'extension'
    loaded_modules = [
        path.relative_to(extension_root).as_posix()
        for path in observe_extension_worker_paths()
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
                if name not in non_handler_exports
            ],
        })
    assert not duplicate_exports, (
        f'worker modules export names more than once: {duplicate_exports}')

    worker_exports = {
        name
        for details in module_details
        for name in _directive_names(details['source'], 'exported')
    }
    stale_exceptions = sorted(non_handler_exports - worker_exports)
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
    published_handlers = sorted(owners)
    watched_by_source = {
        f'extension/{details["relative"]}': details['handlers']
        for details in module_details
    }
    runtime, shared = _runtime_observations(watched_by_source)
    assert shared['error'] is None, shared
    isolated_errors = {
        relative: {
            'bindings': details['bindingExecutionError'],
            'handlers': details['handlerExecutionError'],
        }
        for relative, details in runtime.items()
        if (details['bindingExecutionError'] is not None
            or details['handlerExecutionError'] is not None)
    }
    assert not isolated_errors, isolated_errors
    background_names = set(
        runtime['extension/background.js']['bindings'])

    for details in module_details:
        relative = details['relative']
        module_routes = [
            (symbol, command_type)
            for route_module, symbol, command_type in routes
            if route_module == relative
        ]
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
                'publishedSymbols': published_handlers,
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
                f'{relative} must publish {symbol} through one function '
                'declaration and must not reassign it during source '
                'execution so the classic-worker route probe can replace it')
            assert observed['symbol'] == symbol, observed
            assert (observed['available'] and observed['replaceable']), (
                contract)
            source_runtime = runtime[f'extension/{relative}']
            assert symbol in source_runtime['bindings'], contract
            assert source_runtime['events'][symbol] == {
                'declarations': 1, 'writes': 0,
            }, contract
            assert symbol not in background_names, (
                f'background.js still declares {symbol} at top level')
            mutated_symbols = observed.get('mutatedSymbols', [])
            assert not mutated_symbols, (
                f'{relative} dispatch {command_type} mutated published '
                f'handlers: {mutated_symbols}')
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


def test_capability_batch_restores_every_original_handler(tmp):
    """An earlier route's sibling mutation is reported, then restored."""
    extension = Path(tmp) / 'extension'
    shutil.copytree(ROOT / 'extension', extension)
    background_path = extension / 'background.js'
    background = background_path.read_text(encoding='utf-8')
    route = "    case 'cookies': return handleCookies(cmd);"
    mutated = """    case 'cookies':
      handleSetCookie = function corruptedSetCookie() { return false; };
      return handleCookies(cmd);"""
    assert background.count(route) == 1
    background_path.write_text(
        background.replace(route, mutated), encoding='utf-8')

    result = run_extension_capability_routes([
        {
            'symbol': 'handleCookies',
            'command': {'id': 'batch-first', 'type': 'cookies'},
            'verifyBatchRestoration': True,
        },
        {
            'symbol': 'handleSetCookie',
            'command': {'id': 'batch-later', 'type': 'set-cookie'},
            'verifyBatchRestoration': True,
        },
    ], background_path=background_path)

    assert result['observations'][0].get('mutatedSymbols') == [
        'handleSetCookie',
    ], result
    assert result['restored'] == {
        'handleCookies': True,
        'handleSetCookie': True,
    }, result


def test_worker_module_directives_resolve_to_worker_symbols(tmp):
    """Best-effort directive check against runtime worker symbols.

    Export-derived exact route-symbol coverage and the per-handler runtime
    probe are the primary guarantee. Command types remain probe input rather
    than an exhaustive dispatch inventory. This secondary graph catches
    ordinary typos cheaply. Usage remains a best-effort text check: an object
    method key can look like a consumer, and `js_mask` does not parse regex
    literals (issue 198). The platform allowlist is trusted input, not proof
    of a platform global.
    """
    del tmp
    worker_sources = _worker_sources()
    runtime, shared = _runtime_observations()
    assert shared['error'] is None, shared
    assert all(
        details['bindingExecutionError'] is None
        and details['handlerExecutionError'] is None
        for details in runtime.values()), runtime
    source_details = [
        {
            'relative': relative,
            'source': source,
            'masked': js_mask(source),
            'consumed': _directive_names(source, 'global'),
            'exported': _directive_names(source, 'exported'),
            'declared': set(runtime[relative]['bindings']),
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


def test_classic_worker_top_level_declarations_are_unique(tmp):
    """Reject collisions in the classic worker's shared global namespace.

    The exception tuple is narrow, duplicate-sensitive reviewed input. Source
    is executed in isolated contexts for ownership and in load order for
    lexical redeclaration errors; no declaration grammar is maintained here.
    """
    del tmp
    duplicate_exceptions = sorted(
        name for name, count
        in Counter(_WORKER_REDECLARATION_EXCEPTIONS).items()
        if count > 1
    )
    assert not duplicate_exceptions, (
        'worker redeclaration exceptions contain duplicate entries: '
        f'{duplicate_exceptions}')

    runtime, shared = _runtime_observations()
    declaration_sources = {}
    for relative, details in runtime.items():
        for name in details['bindings']:
            declaration_sources.setdefault(name, []).append(relative)
    collisions = {
        name: sources
        for name, sources in sorted(declaration_sources.items())
        if len(sources) > 1
    }
    exceptions = set(_WORKER_REDECLARATION_EXCEPTIONS)
    stale_exceptions = sorted(exceptions - collisions.keys())
    assert not stale_exceptions, (
        'worker redeclaration exceptions no longer collide: '
        f'{stale_exceptions}')
    unexpected = {
        name: sources
        for name, sources in collisions.items()
        if name not in exceptions
    }
    assert not unexpected, (
        f'classic worker top-level declarations collide: {unexpected}')
    if shared['error'] is not None:
        match = re.search(
            r"Identifier '([^']+)' has already been declared",
            shared['error']['message'])
        allowed = match and match.group(1) in exceptions
        assert allowed, (
            f'classic worker load failed in {shared["error"]["source"]}: '
            f'{shared["error"]}')


def test_worker_imports_match_worker_modules(tmp):
    """The honest worker loads every shipped module exactly once.

    The loader inventory comes from Node vm and is not a security boundary:
    host functions expose their realm intrinsics, so deliberately hostile
    worker source can forge the trace. This guard catches honest split drift;
    it does not prove resistance to the worker's author.
    """
    del tmp
    imported = [path.resolve() for path in observe_extension_worker_paths()]
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
