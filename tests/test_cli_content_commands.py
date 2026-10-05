#!/usr/bin/env python3
"""Suite for daedalus_cli — the content commands' refusals and payloads.

The refusals that must land before anything is sent (an uploads delete
without an id, rule id zero, an unreadable set-permanent value), the
spellings _boolean_argument reads, and what store-hotfix puts on the
wire for a missing file, an ambiguous positional, and --code vs --file.
The CLI is always run as a subprocess, the way a shell would run it.
"""
import base64
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
from _cli_helpers import BRIDGE_ENV, CLI, TOK, cli_env, run_cli  # noqa: E402
from _queueread import queued_command  # noqa: E402


def run_python(code, env, timeout=60):
    return subprocess.run([sys.executable, '-c', code], cwd=str(_util.ROOT),
                          env=env, capture_output=True, text=True,
                          encoding='utf-8', timeout=timeout)


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
        return queued_command(qdir, what)
    finally:
        _drain.kill_and_drain(proc)


def test_uploads_delete_refuses_a_filename_without_an_id(tmp):
    """Naming one file must not become deleting the token's whole namespace."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, _ = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'alpha', 'filename': 'one.txt',
            'data': base64.b64encode(b'keep me').decode()})
        assert status == 200, status
        r = run_cli(['uploads', '--delete', '--filename', 'one.txt'],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert '--id' in r.stderr, r.stderr
        assert (Path(docroot) / 'uploads' / TOK / 'alpha'
                '/one.txt').is_file()


def test_unblock_refuses_rule_id_zero_before_sending_it(tmp):
    """Zero is not a rule id, and it must not reach the extension as one.

    The extension read a present-but-false ruleId as absent and removed every
    session rule, so the CLI refusing it here is the outer half of that fix.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    code = ('from daedalus_cli.commands_content import '
            '_boolean_argument as b\n'
            'print([b(v) for v in ("true", "1", "yes", "y", "on", "TRUE")])\n'
            'print([b(v) for v in ("false", "0", "no", "n", "off", "OFF")])\n')
    r = run_python(code, cli_env())
    assert r.returncode == 0, (r.returncode, r.stderr)
    lines = r.stdout.strip().splitlines()
    assert lines[0] == str([True] * 6), lines
    assert lines[1] == str([False] * 6), lines


def test_store_hotfix_refuses_a_file_that_is_not_there(tmp):
    """A mistyped path is an error, not a hotfix whose source is that path.

    The single positional this replaced decided between a path and inline
    source by asking whether the path existed, so a typo stored the literal
    string as persistent page code and reported success.
    """
    missing = Path(tmp) / 'not-here.js'
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        r = run_cli(['store-hotfix', 'typo', '--file', str(missing)],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'File not found' in r.stderr, r.stderr
        queue = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(queue.glob('*.json')) if queue.is_dir() else []
        assert queued == [], queued


def test_store_hotfix_refuses_the_positional_that_meant_either(tmp):
    """The ambiguous form fails loudly rather than picking a meaning."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        queued = _queued_extension_command(
            base, docroot, ['store-hotfix', 'realfix', '--file', str(src)],
            'the store-hotfix command')
    assert queued['code'] == '/* real hotfix */\n', queued
    assert queued['fixId'] == 'realfix', queued


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
