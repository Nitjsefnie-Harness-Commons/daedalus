#!/usr/bin/env python3
"""The extension in a real Chrome, driven over CDP.

Everything else about eval is checked against a Node VM, which cannot answer
what a page's own CSP does to an injection or what the debugger banner costs.
These load the unpacked extension into a real browser, point it at pages that
forbid what the relay needs, and read which channel each value came back on —
a hostile page can choose the value, never the claim about how it was run.
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser_controls  # noqa: E402
import _util  # noqa: E402
from _deliveries import real_eval, real_ext_command  # noqa: E402
from _realbrowser import (BrowserEnvironmentSkipped,  # noqa: E402
                          browser_requirements, cdp_call, cdp_eval,
                          eval_page_server, hostile_eval_matrix,
                          real_extension_page)
from _repo import EXTENSION_ROOT  # noqa: E402
sys.path.insert(0, str(_util.ROOT))
import daedalus_cli.output as CLI_OUTPUT  # noqa: E402


def test_hostile_page_eval_matrix_has_descriptive_channels_only(tmp):
    """A page-selected value never gains a trust claim from its channel.

    The fixture poisons eval, Function, all four function-constructor
    prototypes, both same-origin iframe routes, Worker, and the page's Promise
    machinery. The last case deliberately routes a primitive through that
    hostile Promise rather than merely placing `await` beside a direct value.
    It does not claim that Promise-prototype poison alone changes a direct
    object; that narrower reproduction does not occur.
    """
    matrix = hostile_eval_matrix(tmp)
    for label, actual in matrix.items():
        assert 'result' in actual, (label, actual)
        world = actual.get('world')
        assert isinstance(world, str) and world, (label, actual)
        rendered = CLI_OUTPUT._format_eval_world(world)
        assert rendered == f'channel={world}', (label, actual, rendered)
        assert 'privileged' not in rendered, (label, actual, rendered)
        assert 'untrusted' not in rendered, (label, actual, rendered)
    assert matrix['page-promise'].get('result') == 'FORGED-BY-PAGE', matrix


def test_main_world_transport_failure_and_genuine_null_are_distinct(tmp):
    """A failed injection is an error while evaluated `null` is a value."""
    token = 'mainworldtok'
    actual = {}
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            cases = (
                ('performance-poison', '/performance-poison.html', '2 + 2'),
                ('genuine-null', '/plain.html', 'null'),
            )
            for label, path, code in cases:
                case_tmp = Path(tmp) / label
                case_tmp.mkdir()
                with real_extension_page(
                        case_tmp, bridge_url, token,
                        pages + path) as (_node, _page, tab_id):
                    actual[label] = real_eval(
                        bridge_url, token, tab_id, label, code)

    poisoned = actual['performance-poison']
    assert poisoned.get('result') is None, poisoned
    assert 'page killed performance.now' in (poisoned.get('error') or ''), poisoned
    assert poisoned.get('world') == 'page-main', poisoned

    genuine_null = actual['genuine-null']
    assert 'result' in genuine_null, genuine_null
    assert genuine_null['result'] is None, genuine_null
    assert genuine_null.get('error') is None, genuine_null
    assert genuine_null.get('world') == 'page-main', genuine_null


def test_a_worker_that_loads_broken_is_a_failure_not_a_skip(tmp):
    """A broken extension must not be reported as a broken machine.

    The fixture skipped when the worker did not come up ready, so a real MV3
    defect passed CI in silence. The Node-based tests do not cover what that
    skip hides: they run the same source against fakes with no
    chrome.runtime.id, so a fault conditioned on being a real worker is
    invisible there too — which is why this mutation is exactly that fault.

    A worker that answers is one the browser has reached, so what it says
    about itself is the extension's own behaviour and fails. A worker that
    cannot be reached at all is decided by the control extension: the
    machine skips only when the control fails to load too.
    """
    browser_requirements()  # skips honestly where no browser exists
    broken = Path(tmp) / 'broken-extension'
    shutil.copytree(EXTENSION_ROOT, broken)
    worker = broken / 'background.js'
    # Appended, and conditioned on being a real MV3 worker: the script still
    # installs and answers, and what breaks is the extension's own state.
    # A top-level throw instead makes Chrome retire the registration, which
    # is what the control extension now tells apart from a machine that
    # cannot reach a worker at all.
    worker.write_text(
        worker.read_text(encoding='utf-8')
        + "\nif (chrome.runtime.id) { startStream = undefined; }\n",
        encoding='utf-8')

    token = 'workerboottok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            reported = None
            try:
                # The verdict is certain - the ready probe can never go true -
                # so the short patience only skips waiting for it.
                with real_extension_page(
                        tmp, bridge_url, token, pages + '/plain.html',
                        extension_root=broken, worker_ready_patience=2.0):
                    raise AssertionError(
                        'the fixture yielded with a worker that cannot boot')
            except BrowserEnvironmentSkipped:
                raise
            except _util.Skipped as skipped:
                raise AssertionError(
                    'a broken extension was reported as an environment skip: '
                    + str(skipped)) from skipped
            except AssertionError as failure:
                reported = str(failure)
            assert reported and 'service worker' in reported, reported


def test_a_page_that_never_reports_ready_is_a_failure_not_a_skip(tmp):
    """Past the fixture's own boundary, a page that will not load is a bug.

    By the time readiness is awaited, the browser has started, exposed
    DevTools, booted the extension's service worker and taken its
    configuration — the fixture's own docstring says everything from there
    on is the extension's behaviour and stays a hard failure. The fixture
    page and the script that sets __evalPageReady are repository files, so a
    skip there hides a defect in them behind an environment excuse.

    The check itself has to distinguish the two skips: a machine with no
    browser skips honestly, and only a skip raised AT the readiness step is
    the defect under test.
    """
    token = 'readyboundarytok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            # Served as a 404, so nothing ever sets __evalPageReady.
            page_url = pages + '/never-ready.html'
            reported = None
            try:
                # A 404 never sets __evalPageReady, so the short timeout
                # waits only on a verdict already decided.
                with real_extension_page(
                        tmp, bridge_url, token, page_url,
                        page_ready_timeout=2.0):
                    raise AssertionError(
                        'the fixture yielded a page that never reported ready')
            except BrowserEnvironmentSkipped:
                raise
            except _util.Skipped as skipped:
                raise AssertionError(
                    'page readiness was reported as an environment skip: '
                    + str(skipped)) from skipped
            except AssertionError as failure:
                reported = str(failure)
            assert reported and '__evalPageReady' in reported, reported


def test_the_fixture_reaches_its_own_worker_past_another_extension(tmp):
    """A second extension's background worker is not mistaken for ours.

    Every ubuntu CI leg runs a browser that carries an extension of its own,
    so DevTools lists two service workers whose URL ends in /background.js —
    and it lists the other one first. A fixture that took the first match
    attached to it and polled it for declarations it does not have, which is
    what the legs reported: a worker answering with none of them, or nothing
    at all once that worker's target had stopped.
    """
    browser_requirements()  # skips honestly where no browser exists
    decoy = Path(tmp) / 'decoy-extension'
    decoy.mkdir()
    (decoy / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3,
        'name': 'decoy',
        'version': '1.0',
        'background': {'service_worker': 'background.js'},
    }), encoding='utf-8')
    (decoy / 'background.js').write_text(
        'globalThis.__decoyWorker = true;\n', encoding='utf-8')

    token = 'decoyworkertok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            with real_extension_page(
                    tmp, bridge_url, token, pages + '/plain.html',
                    extra_extensions=(decoy,)) as (_node, _page, tab_id):
                # Reaching a value back through the bridge is what proves the
                # configured worker was this extension's: the decoy has no
                # stream to carry the command.
                answer = real_eval(bridge_url, token, tab_id, 'decoy-eval',
                                   'return 1 + 1')
                assert answer.get('error') is None, answer
                assert answer.get('result') == 2, answer


def test_a_hotfix_replays_on_a_page_that_forbids_eval_and_blob(tmp):
    """A stored hotfix reaches a page whose CSP refuses the page relay.

    Replay used to run in the page: the page's own `eval`, then a blob
    <script>. A CSP with neither `unsafe-eval` nor `blob:` — github.com's,
    and this fixture's strict page — refuses both, and the blocked blob load
    reported nothing back, so the fix simply never applied. The background
    can reach the page by the same route ordinary eval uses when the page
    refuses dynamic compilation, so replay goes through it.
    """
    token = 'hotfixcsptok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            with real_extension_page(
                    tmp, bridge_url, token,
                    pages + '/plain.html') as (node, page, _tab_id):
                stored = real_ext_command(bridge_url, token, 'store-csp-fix', {
                    'type': 'store-hotfix',
                    'fixId': 'csp-fix',
                    'code': 'globalThis.__hotfixApplied = true;',
                    'permanent': True,
                })
                assert stored.get('error') is None, stored

                # A load of the strict page replays it: script-src 'self',
                # so neither page-side path the old relay had is available.
                cdp_call(node, page, 'Page.navigate',
                         {'url': pages + '/strict.html'})
                deadline = time.time() + 20
                applied = None
                while time.time() < deadline:
                    if cdp_eval(node, page,
                                'globalThis.__evalPageReady === true') is True:
                        applied = cdp_eval(
                            node, page, 'globalThis.__hotfixApplied === true')
                        if applied is True:
                            break
                    time.sleep(0.1)
                assert applied is True, (
                    'the hotfix never applied on a page whose CSP forbids '
                    f'eval and blob scripts (last read: {applied!r})')


def test_strict_csp_page_uses_cdp_once_after_source_free_preflight(tmp):
    """A source-free CSP probe falls back before the command runs once."""
    token = 'cspevaltok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            page_url = pages + '/strict.html'
            with real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                actual = real_eval(
                    bridge_url, token, tab_id, 'csp-eval',
                    'globalThis.__userSideEffects++; '
                    'return globalThis.__userSideEffects')
                state = cdp_eval(node, page, '({'
                                 'blocks: globalThis.__dataUrlBlocks,'
                                 'evalBlocks: globalThis.__evalBlocks,'
                                 'sideEffects: globalThis.__userSideEffects'
                                 '})')
                assert actual.get('error') is None, actual
                assert actual.get('result') == 1, actual
                assert actual.get('world') == 'cdp', actual
                # The constant probe is the only page evaluation CSP rejects;
                # submitted source goes to CDP once and no data URL is tried.
                assert state['blocks'] == 0, state
                assert state['evalBlocks'] == 1, state
                assert state['sideEffects'] == 1, state


def test_cdp_eval_throw_is_terminal(tmp):
    """An exception is returned once and never retried on another evaluator."""
    token = 'cdpthrowtok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            page_url = pages + '/strict.html'
            with real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                actual = real_eval(
                    bridge_url, token, tab_id, 'cdp-throw',
                    'globalThis.__throwSideEffects = '
                    '(globalThis.__throwSideEffects || 0) + 1; '
                    'throw new Error("callable failed")')
                side_effects = cdp_eval(
                    node, page, 'globalThis.__throwSideEffects')
                assert 'callable failed' in (actual.get('error') or ''), actual
                assert actual.get('world') == 'cdp', actual
                assert side_effects == 1, side_effects


def main():
    return _realbrowser_controls.run_real_browser_tests(
        globals(), tmp_prefix='realbrowser_', requires='Chromium and Node')


if __name__ == '__main__':
    raise SystemExit(main())
