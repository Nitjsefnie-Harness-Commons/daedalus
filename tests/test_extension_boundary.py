#!/usr/bin/env python3
"""What the service worker owes the bridge, across restarts and overlaps.

A delivery id is spent once even if the worker is restarted between the two
halves of that promise; a screenshot names the tab it captured; a refused
upload is an error rather than an envelope with nothing in it; a rule id of
zero does not widen into remove-all. These run the shipped background script
in a Node VM with a fake browser under it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402
from _boundary import HARNESS, run_extension_result_boundary  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import worker_source_paths  # noqa: E402


_VM_SOURCE_FILES = (
    ROOT / 'tests' / '_boundary.py',
    ROOT / 'tests' / '_cdpharness.py',
    ROOT / 'tests' / '_overlap.py',
    ROOT / 'tests' / '_relayharness.py',
    ROOT / 'tests' / 'test_dashboard_behaviour.py',
    ROOT / 'tests' / 'test_gm_storage.py',
    ROOT / 'tests' / 'test_gm_transfers.py',
)


def _vm_file_read_calls(source_files=None):
    comment = re.compile(
        r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
        r'`(?:\\.|[^`\\])*`)|//[^\r\n]*|/\*.*?\*/', re.DOTALL)

    def without_comments(source):
        return comment.sub(
            lambda match: match.group(1) or ' ', source)

    pattern = re.compile(
        r'vm\s*\.\s*runIn(?:Context|NewContext)\s*\(\s*'
        r'fs\s*\.\s*readFileSync\s*\(\s*'
        r'(?P<expr>[^,]+?)\s*,\s*[\'\"]utf8[\'\"]\s*\)'
        r'(?P<tail>.*?)\s*\)\s*;', re.DOTALL)
    calls = []
    paths = _VM_SOURCE_FILES if source_files is None else source_files
    for path in paths:
        source = without_comments(path.read_text(encoding='utf-8'))
        for match in pattern.finditer(source):
            calls.append((path, match.group('expr').strip(), match.group(0)))
    return calls


def _assert_vm_file_loads_are_named(calls, expected_count):
    """Enforce one canonical spelling for shipped-file VM loads.

    This is a spelling contract on our own test harnesses, chosen so the
    check can be exact without modelling JavaScript semantics. The cost is
    that a correct-but-unusual spelling must be rewritten rather than taught
    to the guard.
    """
    assert len(calls) == expected_count, [
        (path, expression) for path, expression, _ in calls]
    for path, expression, call in calls:
        arguments = _split_js_arguments(call)
        expected = '{filename:' + ''.join(expression.split()) + '}'
        actual = (''.join(arguments[2].split())
                  if len(arguments) == 3 else None)
        assert actual == expected, (
            path, expression, call,
            'Use exactly { filename: <the same expression passed to '
            'fs.readFileSync> }.')


def _skip_js_string(source, index):
    quote = source[index]
    index += 1
    while index < len(source):
        if source[index] == '\\':
            index += 2
        elif source[index] == quote:
            return index + 1
        else:
            index += 1
    return index


def _split_js_arguments(call):
    start = call.index('(', call.index('runIn')) + 1
    arguments = []
    argument_start = start
    depths = {'(': 0, '[': 0, '{': 0}
    index = start
    while index < len(call):
        char = call[index]
        if char in '\'"`':
            index = _skip_js_string(call, index)
            continue
        if char in '([{':
            depths[char] += 1
        elif char in ')]}':
            opener = {')': '(', ']': '[', '}': '{'}[char]
            if depths[opener]:
                depths[opener] -= 1
            elif char == ')':
                arguments.append(call[argument_start:index])
                return arguments
        elif char == ',' and not any(depths.values()):
            arguments.append(call[argument_start:index])
            argument_start = index + 1
        index += 1
    return arguments


def _guard_accepts_source(tmp, source):
    path = Path(tmp) / 'synthetic_harness.py'
    path.write_text(source, encoding='utf-8')
    calls = _vm_file_read_calls((path,))
    try:
        _assert_vm_file_loads_are_named(calls, 1)
    except AssertionError:
        return False
    return True


def test_every_vm_file_load_names_the_shipped_source(tmp):
    """Every VM load of a file supplies that file as V8's filename."""
    del tmp
    _assert_vm_file_loads_are_named(_vm_file_read_calls(), 19)


def test_guard_rejects_a_filename_value_with_a_suffix(tmp):
    """A filename expression with a suffix is not the read expression."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath + '.wrong' });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a filename value with a suffix')


def test_guard_rejects_filename_text_inside_a_comment(tmp):
    """A comment mentioning filename is not an options property."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "backgroundContext /* filename: backgroundPath */);")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted filename text inside a comment')


def test_guard_accepts_whitespace_between_file_load_tokens(tmp):
    """Whitespace and line breaks do not change a valid file-load call."""
    source = (
        'vm.runInContext(\n'
        '  fs.readFileSync(\n'
        '    backgroundPath,\n'
        "    'utf8'\n"
        '  ),\n'
        '  context,\n'
        '  { filename: backgroundPath });')
    assert _guard_accepts_source(tmp, source), (
        'guard rejected harmless whitespace and line breaks')


def test_guard_rejects_a_duplicate_filename_key(tmp):
    """A later duplicate filename key is outside the spelling contract."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath, "
              "filename: backgroundPath + '.wrong' });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a duplicate filename key')


def test_guard_rejects_a_later_filename_spread(tmp):
    """A spread that can overwrite filename is outside the contract."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath, "
              "...{ filename: backgroundPath + '.wrong' } });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a later filename spread')


def test_guard_rejects_a_filename_from_a_spread(tmp):
    """A spread-only filename is outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { ...{ filename: backgroundPath } });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a filename supplied by a spread')


def test_guard_rejects_a_second_options_property(tmp):
    """A second options property is outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath, context });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a second options property')


def test_guard_rejects_a_computed_filename_key(tmp):
    """A computed filename key is outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { ['filename']: backgroundPath });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a computed filename key')


def test_guard_rejects_an_options_variable(tmp):
    """An options object held in a variable is outside the contract."""
    source = ("const backgroundOptions = { filename: backgroundPath };\n"
              "vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, backgroundOptions);")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted an options variable')


def test_guard_rejects_a_parenthesized_filename_value(tmp):
    """Parentheses around filename are outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: (backgroundPath) });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a parenthesized filename value')


def test_guard_accepts_the_canonical_filename_spelling(tmp):
    """The canonical spelling accepts whitespace and line breaks."""
    source = (
        'vm . runInContext ( fs . readFileSync ( backgroundPath ,\n'
        "  'utf8' ) , context , {\n"
        '  filename :\n  backgroundPath\n} );')
    assert _guard_accepts_source(tmp, source), (
        'guard rejected the canonical filename spelling')


def test_v8_coverage_attributes_the_shipped_background_script(tmp):
    """A real Node coverage dump names the shipped background script."""
    coverage = Path(tmp) / 'v8-coverage'
    coverage.mkdir()
    node = shutil.which('node')
    assert node, 'node is required to collect V8 coverage'
    background_path = EXTENSION_ROOT / 'background.js'
    env = dict(os.environ)
    env['NODE_V8_COVERAGE'] = str(coverage)
    result = subprocess.run(
        [node, '-e', HARNESS, str(background_path), 'capacity'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    dumps = sorted(coverage.glob('*.json'))
    assert dumps, 'Node emitted no V8 coverage dump'
    urls = []
    for dump in dumps:
        payload = json.loads(dump.read_text(encoding='utf-8'))
        urls.extend(item.get('url') for item in payload.get('result', [])
                    if item.get('url'))
    shipped_url = background_path.resolve().as_uri()
    shipped = [url for url in urls if url == shipped_url]
    assert shipped, urls


def test_extension_same_id_overlap_keeps_each_delivery_id(tmp):
    """Both completion orders preserve each command's server delivery id."""
    del tmp
    commands = [
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-a',
         '_did': 'did-a'},
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-b',
         '_did': 'did-b'},
    ]
    actual = {
        'a-first': _overlap.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-a', 'owner-b']),
        'b-first': _overlap.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-b', 'owner-a']),
    }
    expected = {
        'a-first': [
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
        ],
        'b-first': [
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
        ],
    }
    assert actual == expected, actual


def test_eval_relay_capacity_rejects_1001st_and_preserves_first(tmp):
    """The 1,001st relay fails while the first live relay remains valid."""
    del tmp
    actual = run_extension_result_boundary('capacity')
    assert actual == {
        'firstId': 'existing-0',
        'sentMessages': 0,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'new-at-capacity',
            'error': 'Eval relay capacity exceeded',
        }],
    }, actual


def test_eval_relay_expiry_posts_one_timeout_at_300000_ms(tmp):
    """The exact relay TTL removes the entry and posts one terminal error."""
    del tmp
    actual = run_extension_result_boundary('expiry')
    assert actual == {
        'stillPending': False,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'slow-eval',
            'error': 'Eval relay timed out after 300000 ms',
        }],
    }, actual


def test_stream_timers_drive_reconnect_watchdog_and_keepalive(tmp):
    """Scheduled callbacks, not wall time, prove stream lifecycle effects."""
    del tmp
    actual = run_extension_result_boundary('stream-timers')
    assert actual == {
        'reconnectDelays': [5000, 3000],
        'reconnectFetches': 2,
        'watchdogDelays': [5000],
        'watchdogAborted': True,
        'watchdogFetches': 2,
        'keepaliveDelay': 20000,
        'keepaliveCalls': 1,
    }, actual


def test_result_route_snapshot_covers_retries_and_side_operations(tmp):
    """Config rotation cannot retarget result retries or side operations."""
    del tmp
    actual = run_extension_result_boundary('route')
    assert actual == {
        'requests': [
            {
                'kind': 'upload',
                'url': 'https://initial.example.com/upload',
                'token': 'initial-token',
                'id': 'route-snapshot',
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'block-route-snapshot',
                'error': None,
            },
        ],
        'excludedRequestDomains': ['initial.example.com'],
    }, actual


def test_a_targeted_screenshot_captures_the_tab_it_names(tmp):
    """Naming a tab has to select it, because capture does not.

    captureVisibleTab captures whatever is active in the WINDOW it is given,
    so a screenshot aimed at an inactive tab returned the active sibling's
    pixels under the requested tab's url and title. Nothing in the answer said
    the image was of a different page.
    """
    del tmp
    actual = run_extension_result_boundary('screenshot-target')
    assert actual['captured'] == 'captured:8', actual
    assert actual['posted'] == [
        {'tabUrl': 'about:blank#target', 'error': None}], actual
    # And the window is left as it was found.
    assert actual['activeAfter'] == 7, actual
    assert actual['activations'] == [8, 7], actual


def test_rejected_screenshot_upload_is_reported_as_an_error(tmp):
    """A 400 from /upload must not become a success envelope with no path."""
    del tmp
    actual = run_extension_result_boundary('screenshot-reject')
    assert actual == {
        'uploads': 1,
        'posted': [{
            'result': None,
            'error': 'Screenshot upload failed: invalid path component',
        }],
    }, actual


def test_failed_net_capture_setup_leaves_no_capture_and_no_attachment(tmp):
    """Attach and enable failures roll back; a detach ends the capture."""
    del tmp
    actual = run_extension_result_boundary('net-capture')
    assert actual == {
        'outcomes': [
            {
                'step': 'attach-fails',
                'result': None,
                'error': 'Another debugger is already attached',
            },
            {
                'step': 'enable-fails',
                'result': None,
                'error': 'Network.enable failed',
            },
            {
                'step': 'succeeds',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
            {
                'step': 'after-detach',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
        ],
        # One attach per call — a failed setup never answers `already: true`.
        'attachCalls': 4,
        # Only the enable failure had an attachment to give back.
        'detachCalls': 1,
    }, actual


def test_concurrent_hotfix_stores_both_survive(tmp):
    """Two stores dispatched together must both be in the record afterwards."""
    del tmp
    actual = run_extension_result_boundary('hotfix-race')
    assert actual == {
        'posted': [
            {
                'result': {'stored': 'fix-a', 'total': 1, 'permanent': False},
                'error': None,
            },
            {
                'result': {'stored': 'fix-b', 'total': 2, 'permanent': False},
                'error': None,
            },
        ],
        'storedIds': ['fix-a', 'fix-b'],
    }, actual


def test_a_delivery_id_is_spent_once_across_worker_restarts(tmp):
    """At-most-once has to survive the worker, or it is at-most-once per boot.

    The ledger of spent delivery ids was module state, so an MV3 restart
    emptied it. The bridge redelivers a command whose socket write succeeded
    but whose unlink did not, which is exactly the case dedup exists for — and
    a worker that restarted in between executed it a second time.
    """
    del tmp
    actual = run_extension_result_boundary('dedup-restart')
    assert actual['created'] == 1, actual
    assert actual['posted'].count('did-dedup-1') == 1, actual


def test_clearing_cookies_removes_the_partitioned_ones_too(tmp):
    """A cookie the browser refused to remove must not be counted as removed.

    `chrome.cookies.remove` matches a partitioned cookie only when the
    partition is named, and the call dropped `partitionKey` — so a CHIPS
    cookie stayed readable while the count said it had gone. The count was
    incremented per iteration rather than per removal, which is what let the
    two disagree in the first place.
    """
    del tmp
    actual = run_extension_result_boundary('clear-partitioned')
    assert actual['remaining'] == [], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'] is None, actual
    assert actual['posted'][0]['result']['removed'] == 2, actual
    assert actual['posted'][0]['result']['failed'] == [], actual
    partitioned = [call for call in actual['removeCalls']
                   if call['partitionKey']]
    assert len(partitioned) == 1, actual['removeCalls']


def test_rule_id_zero_is_refused_rather_than_removing_everything(tmp):
    """A specific id that is invalid must not widen into remove-all.

    `if (cmd.ruleId)` is false for 0, so `unblock-requests` with ruleId 0 fell
    through to the branch that removes every session rule and reported them as
    removed. The narrowest possible request destroyed the most.
    """
    del tmp
    actual = run_extension_result_boundary('unblock-zero')
    assert actual['installedIds'] == [9001, 9002, 9003], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'], actual
    assert actual['posted'][0]['removed'] is None, actual


def test_block_rule_ids_survive_a_worker_restart(tmp):
    """Session rules outlive the worker, so ids must not restart at the base."""
    del tmp
    actual = run_extension_result_boundary('block-rule-restart')
    assert actual == {
        'posted': [
            {'ruleId': 9001, 'error': None},
            {'ruleId': 9002, 'error': None},
            {'ruleId': 9003, 'error': None},
            {'ruleId': 9004, 'error': None},
        ],
        'installedIds': [9001, 9002, 9003, 9004],
    }, actual


def test_the_gm_fetch_relay_bounds_the_response_while_it_reads(tmp):
    """An oversized response is abandoned at the limit, not measured after it.

    The shim is injected into every matching top-level page, so any visited
    site can invoke this relay. It had no ceiling at all: an 8 MiB response
    was materialized whole and then copied again into 11,184,812 base64
    characters. Reading through a counter is the part that matters — a size
    check after `arrayBuffer()` learns the size only once the worker is
    already holding every byte.
    """
    del tmp
    actual = run_extension_result_boundary('fetch-bound')
    steps = {step['name']: step for step in actual['steps']}
    assert len(steps) == 4, actual

    mib = 1024 * 1024
    # Exactly the default is allowed: the limit is a ceiling, not a threshold
    # the last permitted byte trips.
    at_default = steps['at the default']
    assert at_default['error'] is None, at_default
    assert at_default['dataLength'] == 8 * mib, at_default
    assert at_default['cancelled'] is False, at_default

    over = steps['over the default']
    assert over['tooLarge'] is True, over
    assert '8388608' in (over['error'] or ''), over
    assert over['dataLength'] is None, over
    # The read stopped at the chunk that crossed the limit and cancelled the
    # body, rather than draining the response and rejecting it afterwards.
    assert over['chunksRead'] == 9, over
    assert over['cancelled'] is True, over

    raised = steps['raised by opt-in']
    assert raised['error'] is None, raised
    assert raised['dataLength'] == 12 * mib, raised

    # The binary path still base64s, because chrome.runtime.sendMessage is
    # JSON-serialized and an ArrayBuffer does not survive it.
    binary = steps['binary under the default']
    assert binary['error'] is None, binary
    assert binary['dataLength'] > mib, binary

    assert actual['limits'] == {
        # Anything that is not a usable positive number means "no preference".
        'omitted': 8 * mib,
        'zero': 8 * mib,
        'negative': 8 * mib,
        'text': 8 * mib,
        # A caller may ask for LESS, which is a safer request, not a weaker
        # one — including the floor of a fractional value.
        'fractional': 1,
        'below the default': 1024,
        # And may not ask its way past the ceiling.
        'above the ceiling': 64 * mib,
    }, actual['limits']

    # The diagnostic ring records the refusal as its own outcome and the raw
    # byte count for the rest; the text path used to record characters.
    assert [entry['error'] for entry in actual['timings']] == [
        None, 'too-large', None, None], actual['timings']
    assert [entry['bodySize'] for entry in actual['timings']] == [
        8 * mib, None, 12 * mib, mib], actual['timings']


def test_the_relay_ceiling_is_declared_once_and_bounds_the_default(tmp):
    """Both limits live in the worker, and the ceiling is the larger one."""
    del tmp
    source = '\n'.join(
        path.read_text(encoding='utf-8') for path in worker_source_paths())
    found = dict(re.findall(
        r'const (GM_FETCH_MAX_RESPONSE|GM_FETCH_RESPONSE_CEILING) = '
        r'([0-9 *]+);', source))
    assert set(found) == {'GM_FETCH_MAX_RESPONSE',
                          'GM_FETCH_RESPONSE_CEILING'}, found
    values = {}
    for name, expression in found.items():
        product = 1
        for factor in expression.split('*'):
            product *= int(factor.strip())
        values[name] = product
    assert values['GM_FETCH_MAX_RESPONSE'] > 0, values
    assert (values['GM_FETCH_RESPONSE_CEILING']
            >= values['GM_FETCH_MAX_RESPONSE']), values


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='extboundary_')


if __name__ == '__main__':
    raise SystemExit(main())
