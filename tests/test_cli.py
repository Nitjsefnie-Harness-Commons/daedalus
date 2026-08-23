#!/usr/bin/env python3
"""Suite for daedalus_cli — configuration resolution and subcommands.

Subcommands that talk to the bridge are driven against a real bridge() so the
CLI's request construction is checked against the server that answers it, not
against a model of it. The CLI is always run as a subprocess, the way a shell
would run it.
"""
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))
from daedalus_cli import __version__  # noqa: E402

# Keep bridge children off the fixed MCP port (see test_bridge_http.py).
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
    for k in ('DAEDALUS_URL', 'DAEDALUS_TOKEN', 'TOKEN', 'ID'):
        env.pop(k, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(overrides)
    return env


def run_cli(args, env, timeout=60):
    return subprocess.run(CLI + args, cwd=str(_util.ROOT), env=env,
                          capture_output=True, text=True, timeout=timeout)


def run_python(code, env, timeout=60):
    return subprocess.run([sys.executable, '-c', code], cwd=str(_util.ROOT),
                          env=env, capture_output=True, text=True,
                          timeout=timeout)


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
        'from daedalus_cli.cli import print_result\n'
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
        'from daedalus_cli.cli import print_result\n'
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


def test_missing_token_is_an_error(tmp):
    # No TOKEN and no DAEDALUS_TOKEN: required() must refuse before any HTTP.
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL='http://127.0.0.1:1'))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'DAEDALUS_TOKEN is not set' in r.stderr, r.stderr


def test_url_default_and_override(tmp):
    code = 'from daedalus_cli.cli import URL; print(URL)'
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
        data = json.loads(files[0].read_text())
        assert data['id'] == 'job1' and data['code'] == 'return 1+1', data


def test_exec_full_round_trip(tmp):
    """exec without --no-result waits; we play the extension over HTTP."""
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab9')
        proc = subprocess.Popen(
            CLI + ['exec', 'job7', 'document.title', '-t', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_tab9'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the enqueued command file')
            queued = json.loads(sorted(qdir.glob('*.json'))[0].read_text())
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
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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


def test_waiter_skips_a_foreign_result_and_finds_its_own(tmp):
    """A foreign result seen mid-wait is neither returned as ours nor fatal:
    the waiter keeps polling and completes when its own result arrives."""
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the enqueued command file')
            queued = json.loads(sorted(qdir.glob('*.json'))[0].read_text())
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
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the fresh cookies command')
            queued = json.loads(sorted(qdir.glob('*.json'))[0].read_text())
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
                CLI + ['cookies', '--domain', owner],
                cwd=str(_util.ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for owner in owners
        }
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(
                lambda: qdir.is_dir()
                and len(list(qdir.glob('*.json'))) == len(owners),
                what='both same-id client commands')
            queued = [json.loads(path.read_text())
                      for path in sorted(qdir.glob('*.json'))]
            by_owner = {command['domain']: command for command in queued}
            assert set(by_owner) == set(owners), by_owner
            commands = [by_owner[owner] for owner in owners]
            _util.run_background_overlap(
                _util.ROOT / 'extension' / 'background.js', commands,
                completion_order, result_base=base, token=TOK,
                wait_between=True)
            results = {}
            for owner, proc in processes.items():
                out, err = proc.communicate(timeout=20)
                foreign = owners[1] if owner == owners[0] else owners[0]
                results[owner] = {
                    'returncode': proc.returncode,
                    'ownResult': owner in out,
                    'foreignResult': foreign in out,
                    'stderr': err.strip(),
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
        data = json.loads(files[0].read_text())
        assert data['id'] == 'pid1' and data['code'] == '1 + 2;', data


def test_navigate_constructs_location_href(tmp):
    with _util.bridge(tmp) as (base, docroot):
        r = run_cli(['navigate', 'https://example.com/x?a="b"'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        files = sorted((Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(files) == 1, files
        data = json.loads(files[0].read_text())
        assert data['id'] == '_nav'
        assert data['code'] == 'location.href = "https://example.com/x?a=\\"b\\""', data


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
    with _util.bridge(tmp) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['screenshot', '--id', screenshot_id,
                   '--output', str(output), '--timeout', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the screenshot command')
            command = json.loads(sorted(qdir.glob('*.json'))[0].read_text())
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
                'result': {'path': f'{screenshot_id}/screenshot.png',
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
            repr(screenshot_id))
        assert output.read_bytes() == b'encoded-screenshot'


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
        record = json.loads(record_path.read_text())
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


def test_connection_failure_is_a_clean_error(tmp):
    port = _util.free_port()  # nothing listens here
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL=f'http://127.0.0.1:{port}',
                                  DAEDALUS_TOKEN=TOK))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'Connection failed' in r.stderr, r.stderr


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
