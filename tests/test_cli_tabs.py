#!/usr/bin/env python3
"""Suite for daedalus_cli — the tabs subcommand and token resolution.

The tabs listing against a real bridge, the query encoding every
accepted custom token survives, and the one-off TOKEN override's
precedence. The CLI is always run as a subprocess, the way a shell
would run it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_helpers import BRIDGE_ENV, TOK, cli_env, run_cli  # noqa: E402


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
