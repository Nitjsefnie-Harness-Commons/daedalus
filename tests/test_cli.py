#!/usr/bin/env python3
"""Suite for daedalus_cli — configuration resolution and subcommands.

Subcommands that talk to the bridge are driven against a real bridge() so the
CLI's request construction is checked against the server that answers it, not
against a model of it. The CLI is always run as a subprocess, the way a shell
would run it.
"""
import base64
import contextlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))
from daedalus_cli import __version__  # noqa: E402

# Keep bridge children off the fixed MCP port (see tests/_bridge.py).
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

CLI = [sys.executable, '-c', 'from daedalus_cli.cli import main; main()']

# The CLI picks its decorative markers from what the console can encode, so a
# test that pinned the arrow would pass here and fail on a Windows code page
# for a CLI that was behaving correctly. What is contracted is that a marker
# immediately precedes the id, not which glyph carries it.
OUT_MARKS = ('\u2192', '->')
IN_MARKS = ('\u2190', '<-')
TOK = 'clitok'
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK


def cli_env(**overrides):
    """A clean environment: none of the CLI's config vars leak in from ours."""
    env = dict(os.environ)
    # PYTHONIOENCODING goes too, so the CLI applies its own rule for a piped
    # stream instead of inheriting whatever this runner was started with. A
    # test that wants a specific one passes it back through overrides.
    for k in ('DAEDALUS_URL', 'DAEDALUS_TOKEN', 'TOKEN', 'ID',
              'PYTHONIOENCODING'):
        env.pop(k, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(overrides)
    return env


def run_cli(args, env, timeout=60):
    return subprocess.run(CLI + args, cwd=str(_util.ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          timeout=timeout)


def run_python(code, env, timeout=60):
    return subprocess.run([sys.executable, '-c', code], cwd=str(_util.ROOT),
                          env=env, capture_output=True, text=True,
                          encoding='utf-8', timeout=timeout)


def _wait_for(predicate, timeout=15, what='condition'):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {what}')


def test_version_flag(tmp):
    r = run_cli(['--version'], cli_env())
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert r.stdout.strip() == f'daedalus {__version__}', r.stdout


def test_help_exits_zero(tmp):
    r = run_cli(['--help'], cli_env())
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert 'usage' in r.stdout.lower()


def test_result_printer_labels_eval_world_as_a_channel(tmp):
    del tmp
    code = (
        'from daedalus_cli.output import print_result\n'
        'base = {"id":"channel", "result":4, "error":None, "ts":1, '
        '"token":"tok"}\n'
        'print_result({**base, "world":"cdp"})\n'
        'print_result({**base, "world":"page-main"})\n'
        'print_result({**base, "world":"page:example.com"})\n'
        'print_result({**base, "world":"module-main"})\n')
    result = run_python(code, cli_env())
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert '@channel=cdp' in result.stdout, result.stdout
    assert '@channel=page-main' in result.stdout, result.stdout
    assert '@channel=page:example.com' in result.stdout, result.stdout
    assert '@channel=module-main' in result.stdout, result.stdout
    assert '[privileged]' not in result.stdout, result.stdout
    assert '[untrusted]' not in result.stdout, result.stdout


def test_the_printer_survives_a_console_that_cannot_encode_it(tmp):
    """A legacy code page degrades the output; it never aborts the command.

    Windows consoles default to one — cp1252 on the hosted runners — where
    `print` raises UnicodeEncodeError rather than degrading, and the arrow in
    the result header used to abort `daedalus result` with a traceback and no
    output at all. Two different things can be unencodable: the markers this
    module chooses, which fall back to ASCII, and caller data such as a tab
    title, which cannot be chosen and is replaced character by character.
    Both are exercised here, because fixing only the markers would leave the
    command still able to die on somebody's page title.
    """
    del tmp
    code = (
        'from daedalus_cli.output import print_result\n'
        'print_result({"id":"job7", "result":"caf\\u00e9 \\u4e16\\u754c", '
        '"error":None, "ts":1, "token":"tok", "world":"cdp"})\n')
    # Parent and child agree on the code page, the way a real console and the
    # process writing to it do; decoding this child as UTF-8 would fail in the
    # test rather than in the code under test.
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=str(_util.ROOT),
        env=cli_env(PYTHONIOENCODING='cp1252'), capture_output=True,
        text=True, encoding='cp1252', timeout=60)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert 'UnicodeEncodeError' not in result.stderr, result.stderr
    assert '<- job7' in result.stdout, result.stdout
    assert '@channel=cdp' in result.stdout, result.stdout


def test_the_entry_point_leaves_an_explicit_encoding_alone(tmp):
    """An operator's PYTHONIOENCODING survives the command being run.

    The module's own stdio policy treats an explicit PYTHONIOENCODING as an
    operator decision and leaves it alone, and a test already covers that at
    import. The entry point then reconfigured both streams to UTF-8 a second
    time, unconditionally, so the policy held for anything that imported the
    module and not for anyone who actually ran the command — the bytes a
    caller received were UTF-8 whatever they asked for. Raw bytes, because
    decoding them here with the encoding under test would pass either way.
    """
    with _util.bridge(tmp) as (base, _docroot):
        _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': '11', 'url': 'https://example.com/a',
             'title': 'caf\u00e9'}]})
        result = subprocess.run(
            CLI + ['tabs'], cwd=str(_util.ROOT),
            env=cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK,
                        PYTHONIOENCODING='cp1252'),
            capture_output=True, timeout=60)
        assert result.returncode == 0, (result.returncode, result.stderr)
        assert b'caf\xe9' in result.stdout, result.stdout
        assert b'caf\xc3\xa9' not in result.stdout, result.stdout


def test_uploads_delete_refuses_a_filename_without_an_id(tmp):
    """Naming one file must not become deleting the token's whole namespace."""
    with _util.bridge(tmp) as (base, docroot):
        status, _ = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'alpha', 'filename': 'one.txt',
            'data': base64.b64encode(b'keep me').decode()})
        assert status == 200, status
        r = run_cli(['uploads', '--delete', '--filename', 'one.txt'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert '--id' in r.stderr, r.stderr
        assert (Path(docroot) / 'uploads' / TOK / 'alpha' / 'one.txt').is_file()


def test_unblock_refuses_rule_id_zero_before_sending_it(tmp):
    """Zero is not a rule id, and it must not reach the extension as one.

    The extension read a present-but-false ruleId as absent and removed every
    session rule, so the CLI refusing it here is the outer half of that fix.
    """
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['unblock-requests', '--rule-id', '0'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'positive' in r.stderr, r.stderr
        queue = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(queue.glob('*.json')) if queue.is_dir() else []
        assert queued == [], queued


def test_set_permanent_refuses_a_value_it_cannot_read(tmp):
    """A misspelling must not read as false and clear the flag it was setting.

    Every value outside the true list was taken as false, so `ture` turned a
    permanent hotfix version-gated and reported success while doing it. The
    refusal has to come before the mutation is sent, not after.
    """
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['set-permanent', 'critical-fix', 'ture'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'ture' in r.stderr, r.stderr
        queue = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(queue.glob('*.json')) if queue.is_dir() else []
        assert queued == [], queued


def test_set_permanent_reads_every_documented_spelling(tmp):
    """Both halves of the documented set parse, in any case."""
    del tmp
    code = ('from daedalus_cli.commands_content import _boolean_argument as b\n'
            'print([b(v) for v in ("true", "1", "yes", "y", "on", "TRUE")])\n'
            'print([b(v) for v in ("false", "0", "no", "n", "off", "OFF")])\n')
    r = run_python(code, cli_env())
    assert r.returncode == 0, (r.returncode, r.stderr)
    lines = r.stdout.strip().splitlines()
    assert lines[0] == str([True] * 6), lines
    assert lines[1] == str([False] * 6), lines


def test_missing_token_is_an_error(tmp):
    # No TOKEN and no DAEDALUS_TOKEN: required() must refuse before any HTTP.
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL='http://127.0.0.1:1'))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'DAEDALUS_TOKEN is not set' in r.stderr, r.stderr


def test_url_default_and_override(tmp):
    code = 'from daedalus_cli.transport import URL; print(URL)'
    r = run_python(code, cli_env())
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'http://127.0.0.1:8081', r.stdout
    r = run_python(code, cli_env(DAEDALUS_URL='http://127.0.0.1:9999/x'))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'http://127.0.0.1:9999/x', r.stdout


def test_imports_cleanly_without_settings_module(tmp):
    # A public install has no `_settings` module; the env-var fallback must be
    # the whole configuration path and must import cleanly.
    code = ('import importlib.util, sys\n'
            'if importlib.util.find_spec("_settings") is not None:\n'
            '    sys.exit("ambient _settings present")\n'
            'import daedalus_cli.cli\n'
            'print("clean-import-ok")\n')
    r = run_python(code, cli_env())
    if r.returncode != 0 and 'ambient _settings' in r.stderr:
        _util.skip('an ambient _settings module is installed here')
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert 'clean-import-ok' in r.stdout


def test_tabs_against_real_bridge(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': '11', 'url': 'https://example.com/a', 'title': 'A'}]})
        r = run_cli(['tabs'], cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert 'https://example.com/a' in r.stdout, r.stdout
        # A well-shaped token other than the configured secret is refused.
        r = run_cli(['tabs'], cli_env(DAEDALUS_URL=base,
                                      DAEDALUS_TOKEN='tokghost'))
        assert r.returncode != 0, (r.returncode, r.stderr)
        assert "HTTP 401: {'error': 'unauthorized'}" in r.stderr, r.stderr


def test_tabs_encodes_every_accepted_custom_token(tmp):
    """Ampersand, fragment, and Unicode tokens survive the CLI query boundary."""
    tokens = ('alpha&beta', 'alpha#beta', 'tökén')
    for index, custom_token in enumerate(tokens):
        case = Path(tmp) / f'token-{index}'
        with _util.bridge(
                case,
                env={'DAEDALUS_TOKEN': custom_token, 'TOKEN': ''}) as (base, _docroot):
            status, body = _util.post_json(base + '/sync-tabs', {
                'token': custom_token,
                'tabs': [{'tabId': str(index),
                          'url': f'https://example.com/token-{index}',
                          'title': f'Token {index}'}],
            })
            assert status == 200, (custom_token, status, body)
            result = run_cli(
                ['tabs'], cli_env(DAEDALUS_URL=base,
                                  DAEDALUS_TOKEN=custom_token))
            assert result.returncode == 0, (
                custom_token, result.returncode, result.stderr)
            assert f'example.com/token-{index}' in result.stdout, result.stdout


def test_token_one_off_override_wins(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': '11', 'url': 'https://example.com/override',
             'title': 'O'}]})
        # TOKEN shadows DAEDALUS_TOKEN: the request must go out with TOK even
        # though DAEDALUS_TOKEN names a token with no tabs.
        r = run_cli(['tabs'], cli_env(DAEDALUS_URL=base,
                                      DAEDALUS_TOKEN='tokghost', TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert 'https://example.com/override' in r.stdout, r.stdout


def test_exec_no_result_enqueues_broadcast(tmp):
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['exec', 'job1', 'return 1+1', '--no-result', '-b'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert any(f'{m} job1' in r.stdout for m in OUT_MARKS), r.stdout
        assert 'broadcast' in r.stdout, r.stdout
        qdir = Path(docroot) / 'commands' / TOK
        files = sorted(qdir.glob('*.json'))
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == 'job1' and data['code'] == 'return 1+1', data


def test_exec_full_round_trip(tmp):
    """exec without --no-result waits; we play the extension over HTTP."""
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab9')
        proc = subprocess.Popen(
            CLI + ['exec', 'job7', 'document.title', '-t', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_tab9'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the enqueued command file')
            queued = json.loads(sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
            assert queued['id'] == 'job7' and queued['code'] == 'document.title'
            # The extension's answer, posted the way the extension posts it.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'tab9', 'id': 'job7',
                'result': 'Hello Title', 'error': None, 'ts': 1,
                'world': 'page:cdp', '_did': queued['_did']})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert any(f'{m} job7' in out for m in IN_MARKS), out
        assert '@channel=page:cdp' in out, out
        assert 'Hello Title' in out, out
        # The CLI consumed its own result.
        assert not (Path(docroot) / 'results' / f'{TOK}_tab9.json').exists()


def test_waiter_leaves_a_foreign_result_in_place(tmp):
    """A waiter that sees another caller's result must not consume it.

    The foreign result is posted while the CLI waiter is already polling, the
    waiter's own result never arrives, and the foreign result remains readable
    afterwards. Driven through `cookies` because typed extension commands use
    the shared extension result slot.
    """
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the enqueued command file')
            # Another caller's result lands while our waiter is polling.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'theirs',
                'result': 'not yours', 'error': None, 'ts': 1})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        # Our result never arrived, so the waiter times out ...
        assert proc.returncode != 0, (proc.returncode, out, err)
        assert 'Timeout' in err, (out, err)
        # ... and the foreign result is still there to be read.
        status, body = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200, status
        assert body.get('id') == 'theirs', body
        assert body.get('result') == 'not yours', body


_WAIT_HARNESS = (
    'import time\n'
    'from daedalus_cli import transport\n'
    'calls = []\n'
    'calls_timeout = []\n'
    'PENDING = %s\n'
    'def fake_api(method, path, body=None, timeout=None):\n'
    '    calls_timeout.append(timeout)\n'
    '    calls.append(path)\n'
    '    if PENDING:\n'
    '        return {"pending": True}\n'
    '    if "consume=1" in path:\n'
    '        return {"consumed": True, "resultGeneration": "g1"}\n'
    '    return {"id": "c1", "deliveryId": "d1", "resultGeneration": "g1",\n'
    '            "result": 7, "error": None}\n'
    'transport.api = fake_api\n'
    'start = time.monotonic()\n'
    'res = transport.wait_for_result("c1", "extension", "d1", %s)\n'
    'print("ELAPSED", round(time.monotonic() - start, 3))\n'
    'print("POLLS", len(calls))\n'
    'print("BOUNDED", all(t is not None and t > 0 for t in calls_timeout[:1]))\n'
    'print("RESULT", res if res is None else res["result"])\n')


def _wait_harness_output(stdout):
    """(elapsed, polls, result) from one _WAIT_HARNESS run."""
    fields = {}
    for line in stdout.splitlines():
        key, _, value = line.partition(' ')
        if key in ('ELAPSED', 'POLLS', 'RESULT'):
            fields[key] = value
    return float(fields['ELAPSED']), int(fields['POLLS']), fields['RESULT']


def test_the_result_wait_polls_before_it_sleeps_the_full_interval(tmp):
    """An already available result must not cost a fixed half second.

    The waiter slept its whole interval before the first poll, so every
    command that waited for a result paid 500ms of dead time even when the
    result was already in the slot. The MCP poller had the same shape and was
    already fixed; this is the CLI half.
    """
    del tmp
    r = run_python(_WAIT_HARNESS % ('False', '2'), cli_env(DAEDALUS_TOKEN=TOK))
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    elapsed, polls, result = _wait_harness_output(r.stdout)
    assert result == '7', r.stdout
    # One peek and one conditional consume, and neither waited on a timer.
    assert polls == 2, r.stdout
    assert elapsed < 0.25, (elapsed, r.stdout)


def test_a_stalled_poll_cannot_outlast_the_requested_timeout(tmp):
    """The timeout bounds the whole wait, not just the top of each lap.

    The loop checked the clock before each iteration and then handed every
    HTTP call its own fixed 30s, so one stalled poll ran far past what the
    caller asked for — a 50ms wait returned after 320ms against a single
    300ms stall.
    """
    del tmp
    code = (
        'import time\n'
        'from daedalus_cli import transport\n'
        'seen = []\n'
        'def fake_api(method, path, body=None, timeout=None):\n'
        '    seen.append(timeout)\n'
        '    time.sleep(0.3)\n'       # the stall
        '    return {"pending": True}\n'
        'transport.api = fake_api\n'
        'start = time.monotonic()\n'
        'res = transport.wait_for_result("c1", "extension", "d1", 0.5)\n'
        'print("ELAPSED", round(time.monotonic() - start, 3))\n'
        'print("RESULT", res)\n'
        'print("FIRST_TIMEOUT", seen[0] if seen else None)\n')
    r = run_python(code, cli_env(DAEDALUS_TOKEN=TOK))
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    fields = dict(
        line.split(' ', 1) for line in r.stdout.splitlines() if ' ' in line)
    assert fields['RESULT'] == 'None', r.stdout
    # Two correct outcomes, and which one appears depends on how fast the
    # interpreter got here: either no poll was started at all, because the
    # deadline went before the first sleep returned — the loop refusing a
    # poll it has no budget for — or exactly one was started and handed the
    # REMAINING budget rather than 30 seconds. What must not happen is a poll
    # carrying a timeout larger than the wait it belongs to.
    if fields['FIRST_TIMEOUT'] != 'None':
        first = float(fields['FIRST_TIMEOUT'])
        assert 0 < first <= 0.5, r.stdout
    # One stall of 0.3s can be absorbed; a second would mean the loop kept
    # polling past its deadline. A generous ceiling, because this asserts the
    # absence of a 30-second poll, not the scheduler's precision.
    assert float(fields['ELAPSED']) < 3.0, r.stdout


def test_the_result_wait_backs_off_while_the_result_stays_pending(tmp):
    """The short first wait is a ramp, not a busy loop.

    Polling a pending slot at the opening interval for the whole timeout
    would trade half a second of latency for a request flood, so the pinned
    property is both bounds at once: more polls than a flat half-second wait
    allows, far fewer than an unbacked-off one would make.
    """
    del tmp
    r = run_python(_WAIT_HARNESS % ('True', '1.0'), cli_env(DAEDALUS_TOKEN=TOK))
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    _elapsed, polls, result = _wait_harness_output(r.stdout)
    assert result == 'None', r.stdout
    assert 4 <= polls <= 12, r.stdout


def test_a_negative_timeout_is_refused_before_the_command_is_sent(tmp):
    """Refusing the wait is only useful if nothing was admitted first.

    The command was PUT before the deadline was evaluated, so a negative
    timeout polled zero times, told the caller it had timed out, and left a
    command the browser was still free to execute. Retrying after that
    report runs the side effect twice.
    """
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['screenshot', '--timeout', '-1'], env)
        assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
        assert 'timeout must not be negative' in r.stderr, r.stderr
        # Nothing reached the bridge: no queue directory, or an empty one.
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
        assert queued == [], queued

        # Zero keeps the meaning it already had — "unset", so the
        # subcommand's own default applies — and is not refused.
        r = run_cli(['screenshot', '--timeout', '0', '--help'], env)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


def test_waiter_skips_a_foreign_result_and_finds_its_own(tmp):
    """A foreign result seen mid-wait is neither returned as ours nor fatal:
    the waiter keeps polling and completes when its own result arrives."""
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the enqueued command file')
            queued = json.loads(sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
            # A foreign result first (results share one slot per tab, so the
            # own result posted after it overwrites the slot) ...
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'theirs',
                'result': 'not yours', 'error': None, 'ts': 1})
            assert status == 200, status
            time.sleep(0.6)  # let the waiter see the foreign result at least once
            # ... then our own.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': queued['id'],
                'result': [], 'error': None, 'ts': 2,
                '_did': queued['_did']})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert '0 cookies' in out, out
        assert 'not yours' not in out, out
        # The waiter consumed its own result.
        assert not (Path(docroot) / 'results' / f'{TOK}_extension.json').exists()


def test_typed_command_does_not_return_a_stale_fixed_id_result(tmp):
    """A prior `_cookies` result cannot satisfy a new cookies invocation."""
    with _util.bridge(tmp) as (base, docroot):
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': '_cookies',
            'result': [{'domain': 'stale.invalid', 'name': 'stale',
                        'value': 'old'}],
            'error': None, 'ts': 1, '_did': '1000000000000_000001'})
        assert status == 200, status

        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'], cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the fresh cookies command')
            queued = json.loads(sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
            # Let the first poll observe the stale result before answering the
            # newly queued invocation as the extension would.
            time.sleep(0.7)
            if proc.poll() is None:
                status, _ = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': 'extension', 'id': queued['id'],
                    'result': [{'domain': 'fresh.invalid', 'name': 'fresh',
                                'value': 'new'}],
                    'error': None, 'ts': 2, '_did': queued['_did']})
                assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert 'fresh.invalid' in out, out
        assert 'stale.invalid' not in out, out


def _run_same_id_client_overlap(tmp, completion_order):
    owners = ('owner-a', 'owner-b')
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        processes = {
            owner: subprocess.Popen(
                # The client's patience has to cover this fixture's whole
                # setup -- a node spawn and the harness's own waits -- and the
                # default ten seconds does not on a loaded Windows runner:
                # both clients exited with `Timeout (10s)` before the first
                # result was posted, leaving nobody to consume it.
                CLI + ['cookies', '--domain', owner, '--timeout', '120'],
                cwd=str(_util.ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8')
            for owner in owners
        }
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(
                lambda: qdir.is_dir()
                and len(list(qdir.glob('*.json'))) == len(owners),
                what='both same-id client commands')
            queued = [json.loads(path.read_text(encoding='utf-8'))
                      for path in sorted(qdir.glob('*.json'))]
            by_owner = {command['domain']: command for command in queued}
            assert set(by_owner) == set(owners), by_owner
            commands = [by_owner[owner] for owner in owners]
            try:
                posted = _overlap.run_background_overlap(
                    _util.ROOT / 'extension' / 'background.js', commands,
                    completion_order, result_base=base, token=TOK,
                    wait_between=False)
            except AssertionError as failure:
                raise AssertionError(
                    f'{failure}; clients: '
                    f'{_overlap.client_states(processes, grace=1)}'
                ) from failure
            states = _overlap.client_states(processes, grace=20)
            _overlap.assert_clients_exited(states, posted)
            results = {}
            for owner, state in states.items():
                foreign = owners[1] if owner == owners[0] else owners[0]
                results[owner] = {
                    'returncode': state['returncode'],
                    'ownResult': owner in state['stdout'],
                    'foreignResult': foreign in state['stdout'],
                    'stderr': state['stderr'],
                }
            return results
        finally:
            for proc in processes.values():
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()


def test_two_same_id_clients_receive_only_their_own_results(tmp):
    """Two CLI callers stay correlated in either completion order."""
    actual = {
        'a-first': _run_same_id_client_overlap(
            Path(tmp) / 'a-first',
            ['owner-a', 'owner-b']),
        'b-first': _run_same_id_client_overlap(
            Path(tmp) / 'b-first',
            ['owner-b', 'owner-a']),
    }
    per_owner = {
        owner: {
            'returncode': 0,
            'ownResult': True,
            'foreignResult': False,
            'stderr': '',
        }
        for owner in ('owner-a', 'owner-b')
    }
    assert actual == {
        'a-first': per_owner,
        'b-first': per_owner,
    }, actual


def test_put_reads_code_from_file(tmp):
    src = Path(tmp) / 'snippet.js'
    src.write_text('  1 + 2;\n')
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['put', 'pid1', str(src), '--no-result', '-b'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        files = sorted((Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == 'pid1' and data['code'] == '1 + 2;', data


def _queued_extension_command(base, docroot, argv, what):
    """Run one CLI invocation and return the extension command it enqueued.

    The invocation waits for a result no extension is going to post, so it is
    started rather than run: what these tests ask about is what went onto the
    queue, which is decided before the wait begins.
    """
    proc = subprocess.Popen(
        CLI + argv, cwd=str(_util.ROOT),
        env=cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding='utf-8')
    try:
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')), what=what)
        return json.loads(
            sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
    finally:
        proc.kill()
        proc.communicate()


def test_store_hotfix_refuses_a_file_that_is_not_there(tmp):
    """A mistyped path is an error, not a hotfix whose source is that path.

    The single positional this replaced decided between a path and inline
    source by asking whether the path existed, so a typo stored the literal
    string as persistent page code and reported success.
    """
    missing = Path(tmp) / 'not-here.js'
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['store-hotfix', 'typo', '--file', str(missing)],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'File not found' in r.stderr, r.stderr
        queue = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(queue.glob('*.json')) if queue.is_dir() else []
        assert queued == [], queued


def test_store_hotfix_refuses_the_positional_that_meant_either(tmp):
    """The ambiguous form fails loudly rather than picking a meaning."""
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['store-hotfix', 'legacy', 'console.log(1)'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        queue = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(queue.glob('*.json')) if queue.is_dir() else []
        assert queued == [], queued


def test_store_hotfix_sends_a_code_value_verbatim(tmp):
    """`--code` is source even when the string names a file that exists."""
    decoy = Path(tmp) / 'decoy.js'
    decoy.write_text('/* FROM THE FILE */\n', encoding='utf-8')
    with _util.bridge(tmp) as (base, docroot):
        queued = _queued_extension_command(
            base, docroot,
            ['store-hotfix', 'ambiguous', '--code', str(decoy)],
            'the store-hotfix command')
    assert queued['code'] == str(decoy), queued
    assert queued['fixId'] == 'ambiguous', queued


def test_store_hotfix_reads_the_file_it_was_given(tmp):
    """`--file` sends the contents, not the path."""
    src = Path(tmp) / 'fix.js'
    src.write_text('/* real hotfix */\n', encoding='utf-8')
    with _util.bridge(tmp) as (base, docroot):
        queued = _queued_extension_command(
            base, docroot, ['store-hotfix', 'realfix', '--file', str(src)],
            'the store-hotfix command')
    assert queued['code'] == '/* real hotfix */\n', queued
    assert queued['fixId'] == 'realfix', queued


def test_navigate_constructs_location_href(tmp):
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['navigate', 'https://example.com/x?a="b"'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        files = sorted((Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == '_nav'
        assert data['code'] == 'location.href = "https://example.com/x?a=\\"b\\""', data


def test_reload_dispatches_to_the_tab_and_to_every_tab(tmp):
    """`reload` reached its handler and died there, in every invocation.

    The handler reads `args.broadcast` the way `put` and `exec` do, but the
    subparser declared no arguments at all, so the attribute never existed and
    the command raised AttributeError before building a request. Nothing
    caught it because no test dispatched `reload`: the eval-style commands are
    tested one at a time, and this was the one nobody wrote.
    """
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab-7')
        r = run_cli(['reload'], env)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert 'AttributeError' not in r.stderr, r.stderr
        targeted = sorted(
            (Path(docroot) / 'commands' / f'{TOK}_tab-7').glob('*.json'))
        assert len(targeted) == 1, targeted
        data = json.loads(targeted[0].read_text(encoding='utf-8'))
        assert data['id'] == '_reload', data
        assert data['code'] == 'location.reload()', data

        # -b is what the handler was written for: no tab, so the command goes
        # to the token's broadcast queue instead of the per-tab one.
        r = run_cli(['reload', '-b'], env)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        broadcast = sorted((Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(broadcast) == 1, broadcast
        data = json.loads(broadcast[0].read_text(encoding='utf-8'))
        assert data['id'] == '_reload', data
        assert data['code'] == 'location.reload()', data
        # Still one: -b replaces the per-tab target rather than adding to it.
        targeted = sorted(
            (Path(docroot) / 'commands' / f'{TOK}_tab-7').glob('*.json'))
        assert len(targeted) == 1, targeted


def test_result_subcommand_fetch_and_consume(tmp):
    with _util.bridge(tmp) as (base, docroot):
        _util.post_json(base + '/result', {
            'token': TOK, 'id': 'r9', 'result': {'a': 1}, 'error': None,
            'ts': 1})
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['result', '--raw'], env)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert '"a": 1' in r.stdout, r.stdout
        # Not consumed by the plain read.
        assert (Path(docroot) / 'results' / f'{TOK}.json').exists()
        r = run_cli(['result', '-c', '--raw'], env)
        assert r.returncode == 0 and '"a": 1' in r.stdout, (r.returncode, r.stdout)
        assert not (Path(docroot) / 'results' / f'{TOK}.json').exists()
        r = run_cli(['result'], env)
        assert r.returncode == 0 and 'No result pending' in r.stdout, r.stdout


def test_result_encodes_delimiter_and_unicode_tab_id(tmp):
    """The result query addresses the exact tab rather than splitting its id."""
    tab_id = 'tab&branch#café'
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK,
            'tabId': tab_id,
            'id': 'encoded-tab-result',
            'result': 'exact tab',
            'error': None,
            'ts': 1,
        })
        assert status == 200, (status, body)
        result = run_cli(
            ['result', '--raw'],
            cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID=tab_id))
        assert result.returncode == 0, (result.returncode, result.stderr)
        assert 'encoded-tab-result' in result.stdout, result.stdout
        assert (Path(docroot) / 'results' / f'{TOK}_{tab_id}.json').exists()


def test_uploads_list_and_delete(tmp):
    with _util.bridge(tmp) as (base, docroot):
        _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'up9', 'filename': 'f.txt',
            'data': base64.b64encode(b'data').decode()})
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['uploads'], env)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert 'up9/f.txt' in r.stdout and '1 files' in r.stdout, r.stdout
        r = run_cli(['uploads', '--delete', '--id', 'up9'], env)
        assert r.returncode == 0 and 'Deleted' in r.stdout, (r.returncode, r.stdout)
        assert not (Path(docroot) / 'uploads' / TOK / 'up9').exists()
        r = run_cli(['uploads'], env)
        assert r.returncode == 0 and 'No uploads' in r.stdout, r.stdout


def test_upload_listing_encodes_delimiter_and_unicode_id(tmp):
    """An upload filter reaches the exact delimiter-bearing upload id."""
    upload_id = 'upload&branch#café'
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/upload', {
            'token': TOK,
            'id': upload_id,
            'filename': 'payload.txt',
            'data': base64.b64encode(b'query-safe').decode(),
        })
        assert status == 200, (status, body)
        result = run_cli(
            ['uploads', '--id', upload_id],
            cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert result.returncode == 0, (result.returncode, result.stderr)
        wanted = f'{upload_id}/payload.txt'
        assert wanted in result.stdout, (
            repr(wanted), repr(result.stdout))


def test_screenshot_download_encodes_delimiter_and_unicode_id(tmp):
    """The screenshot download query preserves the command/upload id exactly."""
    screenshot_id = 'shot&branch#café'
    output = Path(tmp) / 'captured.png'
    # The bridge's own log goes into the failure. This test times out on
    # Windows waiting for a result the test itself posted, and from a host
    # where it passes there is no way to tell whether the bridge stored the
    # result, stored it somewhere else, or never saw the request at all.
    served = []
    with _util.bridge(tmp, output=served) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['screenshot', '--id', screenshot_id,
                   '--output', str(output), '--timeout', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the screenshot command')
            command = json.loads(sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
            assert command['id'] == screenshot_id, command
            status, body = _util.post_json(base + '/upload', {
                'token': TOK,
                'id': screenshot_id,
                'filename': 'screenshot.png',
                'data': base64.b64encode(b'encoded-screenshot').decode(),
            })
            assert status == 200, (status, body)
            status, body = _util.post_json(base + '/result', {
                'token': TOK,
                'tabId': 'extension',
                'id': screenshot_id,
                'result': {'path': f'{TOK}/{screenshot_id}/screenshot.png',
                           'size': len(b'encoded-screenshot')},
                'error': None,
                'ts': 1,
                '_did': command['_did'],
            })
            assert status == 200, (status, body)
            stdout, stderr = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        assert proc.returncode == 0, (
            proc.returncode, repr(stdout), repr(stderr),
            repr(screenshot_id), ''.join(served))
        assert output.read_bytes() == b'encoded-screenshot'


def test_a_screenshot_download_ignores_a_later_capture_under_its_id(tmp):
    """The saved bytes are this capture's, not the next one's.

    `_ss` is the default screenshot id, so overlapping captures share an
    upload directory; a download that asked for the id got whichever file
    was newest when it asked, which is the other invocation's whenever one
    landed in between.
    """
    output = Path(tmp) / 'captured.png'
    served = []
    with _util.bridge(tmp, output=served) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['screenshot', '--output', str(output), '--timeout', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the screenshot command')
            command = json.loads(
                sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
            assert command['id'] == '_ss', command
            for name, payload in (('mine.png', b'this-invocation'),
                                  ('later.png', b'the-next-invocation')):
                status, body = _util.post_json(base + '/upload', {
                    'token': TOK, 'id': '_ss', 'filename': name,
                    'data': base64.b64encode(payload).decode()})
                assert status == 200, (status, body)
            # The overlapping capture is strictly newer on disk, so a fetch
            # by id would answer with it. Stamped rather than assumed: two
            # writes can share a timestamp.
            shot_dir = Path(docroot) / 'uploads' / TOK / '_ss'
            os.utime(shot_dir / 'mine.png', (1_700_000_000, 1_700_000_000))
            os.utime(shot_dir / 'later.png', (1_700_000_100, 1_700_000_100))
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': '_ss',
                'result': {'path': f'{TOK}/_ss/mine.png',
                           'size': len(b'this-invocation')},
                'error': None, 'ts': 1, '_did': command['_did'],
            })
            assert status == 200, (status, body)
            stdout, stderr = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    assert proc.returncode == 0, (
        proc.returncode, repr(stdout), repr(stderr), ''.join(served))
    assert output.read_bytes() == b'this-invocation', output.read_bytes()


def test_a_missing_stored_screenshot_names_what_the_bridge_said(tmp):
    """The raw download must report the refusal, not just its status.

    The command round trip succeeds and only the follow-up fetch of the
    stored image fails, so the status number is the operator's only clue
    unless the body travels with it.
    """
    screenshot_id = 'shot-' + uuid.uuid4().hex[:8]
    output = Path(tmp) / 'captured.png'
    served = []
    with _util.bridge(tmp, output=served) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['screenshot', '--id', screenshot_id,
                   '--output', str(output), '--timeout', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the screenshot command')
            command = json.loads(sorted(qdir.glob('*.json'))[0].read_text(encoding='utf-8'))
            # The result claims a stored file; nothing was uploaded, so the
            # download that follows it is refused.
            status, body = _util.post_json(base + '/result', {
                'token': TOK,
                'tabId': 'extension',
                'id': screenshot_id,
                'result': {'path': f'{TOK}/{screenshot_id}/screenshot.png',
                           'size': 18},
                'error': None,
                'ts': 1,
                '_did': command['_did'],
            })
            assert status == 200, (status, body)
            _stdout, stderr = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    assert proc.returncode != 0, (proc.returncode, ''.join(served))
    assert 'HTTP 404' in stderr, (stderr, ''.join(served))
    assert 'no screenshot' in stderr, (stderr, ''.join(served))


def test_segment_job_subcommand_prints_a_working_capability(tmp):
    job = 'clijob-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp) as (base, _docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['segment-job', job], env)
        assert r.returncode == 0, (r.returncode, r.stderr)
        sig = r.stdout.strip()
        assert sig, 'segment-job printed nothing'
        # The printed sig authorizes a segment post.
        status, _ = _util.request(
            base + f'/segment?job={job}&seg=0&total=1&sig={sig}', 'POST',
            body=b'\x47', headers={'Content-Type': 'application/octet-stream'})
        assert status == 200, status
        # Re-running re-fetches the same capability (idempotent for the owner).
        r = run_cli(['segment-job', job], env)
        assert r.returncode == 0 and r.stdout.strip() == sig, (r.returncode, r.stdout)


def test_segment_status_subcommand(tmp):
    job = 'cliseg-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/segment-job',
                                       {'token': TOK, 'job': job})
        assert status == 200, (status, body)
        status, _ = _util.request(
            base + f'/segment?job={job}&seg=0&total=2&sig={body["sig"]}', 'POST',
            body=b'\x47', headers={'Content-Type': 'application/octet-stream'})
        assert status == 200, status
        r = run_cli(['segment-status', job],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert f'Job: {job}' in r.stdout and 'Segments: 1' in r.stdout, r.stdout


def test_segment_status_subcommand_encodes_job_and_capability(tmp):
    job = 'cliseg & hash# caf\u00e9-' + uuid.uuid4().hex[:12]
    sig = 'sig&part#tail'
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/segment-job',
                                       {'token': TOK, 'job': job})
        assert status == 200, (status, body)
        record_path = docroot / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record['sig'] = sig
        record_path.write_text(json.dumps(record))

        r = run_cli(['segment-status', job],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        wanted = f'Job: {job}  Segments: 0'
        assert wanted in r.stdout, (repr(wanted), repr(r.stdout))


def test_segment_status_subcommand_reports_a_foreign_job_cleanly(tmp):
    """A job owned by another token is the CLI's own sentence, not the bare
    'HTTP 409: ...' that the generic api() error path would have exited with.
    """
    job = 'cliforeign-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp) as (base, docroot):
        segment_root = Path(docroot) / 'segments'
        (segment_root / job).mkdir()
        (segment_root / f'{job}.json').write_text(json.dumps({
            'token': 'earlierconfigured',
            'sig': 'persistedforeigncapability',
            'max_segment_index': 10,
            'max_segment_count': 10,
            'max_bytes': 100,
        }))
        r = run_cli(['segment-status', job],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'owned by a different token' in r.stderr, r.stderr
        assert 'HTTP 409' not in r.stderr, r.stderr


class _RefusingFrontEndHandler(http.server.BaseHTTPRequestHandler):
    """Answer every request the way a proxy that never reached the bridge does.

    The body is HTML rather than JSON on purpose: that is the shape a front
    end in front of the bridge produces, and the CLI has to carry its text
    through rather than reporting a status with nothing after it.
    """

    BODY = b'<html><body>502 upstream is not answering</body></html>'

    def _refuse(self):
        declared = self.headers.get('Content-Length')
        if declared:
            self.rfile.read(int(declared))
        self.send_response(502)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(self.BODY)))
        self.end_headers()
        self.wfile.write(self.BODY)

    do_GET = _refuse
    do_PUT = _refuse
    do_POST = _refuse
    do_DELETE = _refuse

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def _refusing_front_end():
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), _RefusingFrontEndHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_a_non_json_error_body_reaches_the_operator(tmp):
    """An error the bridge did not write must still arrive with its text.

    Every one of these paths read the body once to try JSON and then read it
    again for the fallback; the second read of a consumed response is empty,
    so the operator saw "HTTP 502:" and nothing else.
    """
    del tmp
    with _refusing_front_end() as base:
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        commands = (
            ['tabs'],                               # api
            ['uploads', '--delete', '--id', 'x'],   # api_delete
            ['segment-status', 'somejob'],          # the segment-job mint
        )
        for command in commands:
            r = run_cli(command, env)
            assert r.returncode != 0, (command, r.returncode, r.stdout)
            assert 'HTTP 502' in r.stderr, (command, r.stderr)
            assert 'upstream is not answering' in r.stderr, (command, r.stderr)


def test_connection_failure_is_a_clean_error(tmp):
    port = _util.free_port()  # nothing listens here
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL=f'http://127.0.0.1:{port}',
                                  DAEDALUS_TOKEN=TOK))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'Connection failed' in r.stderr, r.stderr


def _answer_one_ext_command(base, docroot, argv, result, env):
    """Run one typed subcommand and answer the command it enqueues.

    Returns (returncode, stdout, stderr, the payload the bridge received).
    The payload is the point: every one of these subcommands is a wire
    contract: the `type` the extension dispatches on, and the fields it reads
    off the command. The repository already carries a guard for confusing
    `tab` with a browser `tabId`, and this pins the senders themselves.
    """
    # Nothing consumes this queue — there is no extension here — so a command
    # from an earlier case is still sitting in it. Clearing first is what makes
    # the file this case waits for unambiguously its own.
    qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
    if qdir.is_dir():
        for stale in qdir.glob('*.json'):
            stale.unlink()
    proc = subprocess.Popen(
        CLI + argv, cwd=str(_util.ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                  what=f'the command {argv[0]} enqueues')
        queued_file = sorted(qdir.glob('*.json'))[0]
        queued = json.loads(queued_file.read_text(encoding='utf-8'))
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': queued['id'],
            'result': result, 'error': None, 'ts': 1, '_did': queued['_did']})
        assert status == 200, (argv, status)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    return proc.returncode, out, err, queued


def test_every_typed_subcommand_sends_its_documented_command(tmp):
    """Each typed subcommand reaches the extension as the command it claims.

    These handlers were the largest unexercised region of the CLI, and what
    goes wrong in them is not a crash — it is a payload that names the wrong
    `type` or routes a browser tab id through `tab`, which the bridge would
    deliver to a queue nobody reads.
    """
    hotfix = Path(tmp) / 'fix.js'
    hotfix.write_text('console.log(1)\n', encoding='utf-8')
    cases = (
        (['cookies'], 'cookies', {}, []),
        (['set-cookie', 'https://example.com', 'sid', 'abc'], 'set-cookie',
         {'url': 'https://example.com', 'name': 'sid', 'value': 'abc'}, {}),
        (['remove-cookie', 'https://example.com', 'sid'], 'remove-cookie',
         {'url': 'https://example.com', 'name': 'sid'}, {}),
        (['clear-cookies', '-d', 'example.com'], 'clear-cookies',
         {'domain': 'example.com'}, {}),
        (['cdp', 'Page.enable'], 'cdp',
         {'method': 'Page.enable', 'params': {}}, {}),
        (['close-tab', '5'], 'close-tab', {'tabId': 5}, {}),
        (['close-tab', '5', '6'], 'close-tab', {'tabIds': [5, 6]}, {}),
        (['open-tab', 'https://example.com'], 'open-tab',
         {'url': 'https://example.com'}, {}),
        (['open-tabs', 'https://example.com/a', 'https://example.com/b'],
         'open-tabs',
         {'urls': ['https://example.com/a', 'https://example.com/b']}, {}),
        (['focus-tab', '7'], 'focus-tab', {'tabId': 7}, {}),
        (['ext-navigate', 'https://example.com'], 'navigate',
         {'url': 'https://example.com'}, {}),
        (['ext-reload'], 'reload', {}, {}),
        (['ext-self-reload'], 'ext-reload', {}, {}),
        (['inject-css', '--css', 'a{color:red}'], 'inject-css',
         {'css': 'a{color:red}'}, {}),
        (['remove-css', '--css', 'a{color:red}'], 'remove-css',
         {'css': 'a{color:red}'}, {}),
        (['block-requests', '*.example/*'], 'block-requests',
         {'pattern': '*.example/*'}, {}),
        (['unblock-requests'], 'unblock-requests', {}, {}),
        (['list-block-rules'], 'list-block-rules', {}, {}),
        (['net-capture'], 'net-capture', {}, {}),
        (['net-capture-stop'], 'net-capture-stop', {}, {}),
        (['net-capture-get'], 'net-capture-get', {}, {}),
        (['store-hotfix', 'fix1', '--file', str(hotfix)], 'store-hotfix',
         {'fixId': 'fix1', 'code': 'console.log(1)\n'}, {}),
        (['store-hotfix', 'fix2', '--code', 'console.log(2)'], 'store-hotfix',
         {'fixId': 'fix2', 'code': 'console.log(2)'}, {}),
        (['clear-hotfix', 'fix1'], 'clear-hotfix', {'fixId': 'fix1'}, {}),
        (['clear-hotfixes'], 'clear-all-hotfixes', {}, {}),
        (['list-hotfixes'], 'list-hotfixes', {}, {}),
        # `found` is what this one branches on: without it the CLI reports
        # that no such hotfix exists and exits nonzero.
        (['set-permanent', 'fix1', 'true'], 'set-permanent',
         {'fixId': 'fix1', 'permanent': True}, {'found': True}),
        (['fetch-timings'], 'fetch-timings', {}, {}),
    )
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        for argv, cmd_type, fields, result in cases:
            code, out, err, queued = _answer_one_ext_command(
                base, docroot, argv, result, env)
            assert code == 0, (argv, code, out, err)
            assert queued.get('type') == cmd_type, (argv, queued)
            # Routing is not in the payload: the bridge consumes `tab` and
            # `token` when it enqueues, so what proves a typed command reached
            # the extension worker is the queue it landed in, which is the
            # directory _answer_one_ext_command read it from.
            assert 'tab' not in queued, (argv, queued)
            for key, value in fields.items():
                assert queued.get(key) == value, (argv, key, queued)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
