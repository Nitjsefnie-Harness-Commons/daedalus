#!/usr/bin/env python3
"""Suite for daedalus_cli — the eval-family subcommands on the wire.

exec, put, navigate, reload and the tabs listing build an eval command
and queue it or wait for its result, the result subcommand reads and
consumes the shared slot, and the token each request carries is resolved
in a documented precedence. Everything runs against a real bridge, so
what is checked is the command the bridge receives, not a model of it.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
from _cli_helpers import BRIDGE_ENV, CLI, TOK, cli_env, run_cli  # noqa: E402
from _queueread import queued_command  # noqa: E402

# The CLI picks its decorative markers from what the console can encode, so a
# test that pinned the arrow would pass here and fail on a Windows code page
# for a CLI that was behaving correctly. What is contracted is that a marker
# immediately precedes the id, not which glyph carries it.
OUT_MARKS = ('\u2192', '->')
IN_MARKS = ('\u2190', '<-')


def test_exec_no_result_enqueues_broadcast(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK, ID='tab9')
        proc = subprocess.Popen(
            CLI + ['exec', 'job7', 'document.title', '-t', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_tab9'
            queued = queued_command(qdir, 'the enqueued command file')
            assert queued['id'] == 'job7' and (
                queued['code'] == 'document.title')
            # The extension's answer, posted the way the extension posts it.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'tab9', 'id': 'job7',
                'result': 'Hello Title', 'error': None, 'ts': 1,
                'world': 'page:cdp', '_did': queued['_did']})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert any(f'{m} job7' in out for m in IN_MARKS), out
        assert '@channel=page:cdp' in out, out
        assert 'Hello Title' in out, out
        # The CLI consumed its own result.
        assert not (Path(docroot) / 'results' / f'{TOK}_tab9.json').exists()


def test_put_reads_code_from_file(tmp):
    src = Path(tmp) / 'snippet.js'
    src.write_text('  1 + 2;\n')
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        r = run_cli(['put', 'pid1', str(src), '--no-result', '-b'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        files = sorted((Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == 'pid1' and data['code'] == '1 + 2;', data


def test_navigate_constructs_location_href(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        r = run_cli(['navigate', 'https://example.com/x?a="b"'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        files = sorted((Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == '_nav'
        assert data['code'] == (
            'location.href = "https://example.com/x?a=\\"b\\""'), data


def test_reload_dispatches_to_the_tab_and_to_every_tab(tmp):
    """`reload` reached its handler and died there, in every invocation.

    The handler reads `args.broadcast` the way `put` and `exec` do, but the
    subparser declared no arguments at all, so the attribute never existed and
    the command raised AttributeError before building a request. Nothing
    caught it because no test dispatched `reload`: the eval-style commands are
    tested one at a time, and this was the one nobody wrote.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
        assert r.returncode == 0 and '"a": 1' in r.stdout, (
            r.returncode, r.stdout)
        assert not (Path(docroot) / 'results' / f'{TOK}.json').exists()
        r = run_cli(['result'], env)
        assert r.returncode == 0 and 'No result pending' in r.stdout, r.stdout


def test_result_encodes_delimiter_and_unicode_tab_id(tmp):
    """The result query addresses the exact tab rather than
    splitting its id."""
    tab_id = 'tab&branch#café'
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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


def test_tabs_against_real_bridge(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
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
    """Ampersand, fragment, and Unicode tokens survive the CLI
    query boundary."""
    tokens = ('alpha&beta', 'alpha#beta', 'tökén')
    for index, custom_token in enumerate(tokens):
        case = Path(tmp) / f'token-{index}'
        with _util.bridge(
                case,
                env={'DAEDALUS_TOKEN': custom_token,
                     'TOKEN': ''}) as (base, _docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': '11', 'url': 'https://example.com/override',
             'title': 'O'}]})
        # TOKEN shadows DAEDALUS_TOKEN: the request must go out with TOK even
        # though DAEDALUS_TOKEN names a token with no tabs.
        r = run_cli(['tabs'], cli_env(DAEDALUS_URL=base,
                                      DAEDALUS_TOKEN='tokghost', TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert 'https://example.com/override' in r.stdout, r.stdout


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
