#!/usr/bin/env python3
"""The MCP front end's settings, environment and shared-helper contracts.

Split from tests/test_mcp_server.py.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402
import _util  # noqa: E402
import _mcp_load  # noqa: E402

DEPS = _mcp_load.DEPS
if DEPS:
    import logging
    logging.getLogger('httpx').setLevel(
        logging.WARNING)  # quiet per-request logs
    logging.getLogger('mcp').setLevel(logging.WARNING)  # quiet mcp INFO logs


def test_mcp_numeric_settings_fail_cleanly_at_startup(tmp):
    """A bad MCP setting names itself instead of raising a bare ValueError.

    Both were parsed with bare int(): a malformed value arrived as an
    import-time traceback and a negative body size refused every request.
    """
    _mcp_load._need_deps()
    cases = (
        ('DAEDALUS_MCP_PORT', 'not-an-integer', 'integer from 0 to 65535'),
        ('DAEDALUS_MCP_PORT', '70000', 'integer from 0 to 65535'),
        ('DAEDALUS_MCP_MAX_BODY_SIZE', 'bad', 'non-negative integer'),
        ('DAEDALUS_MCP_MAX_BODY_SIZE', '-1', 'non-negative integer'),
    )
    failures = []
    for name, value, requirement in cases:
        env = {name: value for name, value in os.environ.items()
               if not name.startswith('DAEDALUS_')}
        env.update({
            'DAEDALUS_DIR': str(Path(tmp) / name.lower()),
            'DAEDALUS_PORT': '0',
            'PYTHONDONTWRITEBYTECODE': '1',
            name: value,
        })
        Path(env['DAEDALUS_DIR']).mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, '-c', 'import daedalus_mcp.server'],
            cwd=_util.ROOT,
            env=env, capture_output=True, text=True, timeout=120)
        output = (proc.stdout + proc.stderr).strip()
        if (proc.returncode == 0 or 'Traceback' in output
                or name not in output or requirement not in output):
            failures.append(
                f'{name}={value!r}: exit={proc.returncode}, output={output!r}')
    assert not failures, '\n'.join(failures)


def test_a_poisoned_shell_cannot_reach_the_in_process_loads(tmp):
    """An invalid DAEDALUS_MCP_* value exported in the suite's own shell used
    to refuse the in-process load before any test ran: the loader parsed the
    suite process's environment. The load must see the caller's settings alone.
    """
    del tmp
    _mcp_load._need_deps()
    for name, value in (('DAEDALUS_MCP_PORT', 'abc'),
                        ('DAEDALUS_MCP_MAX_BODY_SIZE', 'bad')):
        previous = os.environ.get(name)
        os.environ[name] = value
        try:
            expected_env = dict(os.environ)
            plain = _mcp_load._load_mcp('http://127.0.0.1:1')
            ported = _mcp_load._load_mcp_at_port('http://127.0.0.1:1', 0)
            assert os.environ == expected_env, 'environment leaked'
            assert plain.MCP_PORT == 8086 and ported.MCP_PORT == 0
            assert plain.MAX_BODY_SIZE == 64 * 1024 * 1024
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


def test_mcp_and_bridge_config_use_one_env_parser(tmp):
    _mcp_load._need_deps()
    from daedalus_bridge import env_config

    mod = _mcp_load._load_mcp('http://127.0.0.1:1')
    with _daedalus_env.isolated({
            'DAEDALUS_DIR': str(Path(tmp) / 'envcontract'),
            'DAEDALUS_PORT': '0'}):
        bridge_config = _util.load(
            _util.ROOT / 'daedalus_bridge' / 'config.py')

    assert mod.env_int is bridge_config.env_int is env_config.env_int
    cases = (
        ('DAEDALUS_CONTRACT_A', 5, 0, None),
        ('DAEDALUS_CONTRACT_B', 8086, 0, 65535),
    )
    for name, default, minimum, maximum in cases:
        for value in (None, '7', 'nonsense', '-1', '70000'):
            previous = os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
            try:
                should_fail = (value == 'nonsense' or value == '-1'
                               or (maximum is not None and value == '70000'))
                try:
                    result = env_config.env_int(
                        name, default, minimum, maximum)
                except SystemExit as error:
                    assert should_fail, (name, value, error)
                else:
                    assert not should_fail, (name, value, result)
                    assert result == (default if value is None else int(value))
            finally:
                os.environ.pop(name, None)
                if previous is not None:
                    os.environ[name] = previous


def test_mcp_uses_the_shared_log_safe_function(tmp):
    """The MCP entry point must use the contract-tested shared renderer."""
    del tmp
    _mcp_load._need_deps()
    mod = _mcp_load._load_mcp('http://127.0.0.1:1')
    assert mod.log_safe is sys.modules['daedalus_bridge.log_safe'].log_safe


def test_the_shared_contract_catches_a_divergent_copy(tmp):
    """Proof the anti-drift control has teeth: a divergent helper must fail
    it. The generator keeps a standalone implementation, so it is that copy
    which can drift; the helper below diverges only on non-ASCII."""
    del tmp
    generator = _util.load(
        _util.ROOT / 'scripts' / 'gen_gitignore.py',
        'divergent_gen_gitignore_log_safe')

    def divergent(value):
        try:
            rendered = str(value).encode(
                'utf-8', 'backslashreplace').decode('ascii', 'replace')
        except Exception:
            return '<unprintable value>'
        if type(rendered) is not str:  # pylint: disable=unidiomatic-typecheck
            return '<unprintable value>'
        return rendered

    setattr(generator, '_log_safe', divergent)
    try:
        for value, expected in _util.log_safe_cases():
            assert generator._log_safe(value) == expected
        assert generator._log_safe('héllo — 世界') == 'héllo — 世界'
    except AssertionError:
        return
    raise AssertionError('a divergent _log_safe passed the shared contract')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
