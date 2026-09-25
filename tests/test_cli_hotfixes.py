#!/usr/bin/env python3
"""The CLI's hotfix subcommands, against a recorded extension.

A fix's site scope is a Chrome match pattern the extension validates when
the fix is stored. Nothing here re-decides that grammar: the surface under
test forwards the value and renders whatever the record carries, so what
these pin is the forwarding and the rendering.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

run_cli = _cli_dispatch.run_cli

SCOPE = '*://*.example.com/*'
STORED = {'stored': 'fix', 'total': 1, 'permanent': False, 'match': None}


def test_store_hotfix_sends_the_scope_it_was_given(tmp):
    """C9, store direction: `--match` reaches the extension as `match`."""
    del tmp
    recorded, _out = run_cli(
        ['store-hotfix', 'fix', '--code', 'console.log(1)',
         '--match', SCOPE], [dict(STORED, match=SCOPE)])

    assert recorded.calls == [
        ('_store_hf', 'store-hotfix',
         {'fixId': 'fix', 'code': 'console.log(1)', 'match': SCOPE}),
    ], recorded.calls


def test_store_hotfix_leaves_an_unstated_scope_unsaid(tmp):
    """C11: absent travels as absent, never as an empty pattern.

    The extension refuses a pattern it cannot parse, so `--match ''` would
    be answered with a refusal, and a field sent as anything but absent
    would be read as the operator having set a scope.
    """
    del tmp
    recorded, _out = run_cli(
        ['store-hotfix', 'fix', '--code', 'console.log(1)'], [STORED])

    assert recorded.calls == [
        ('_store_hf', 'store-hotfix',
         {'fixId': 'fix', 'code': 'console.log(1)'}),
    ], recorded.calls


def test_list_hotfixes_prints_the_scope_it_read_back(tmp):
    """C9, list direction: the row names the scope the record carried."""
    del tmp
    _recorded, out = run_cli(['list-hotfixes'], [{'version': '1.0', 'fixes': [
        {'id': 'scoped', 'ts': 1750000000000, 'code': 'console.log(1)',
         'match': SCOPE},
    ]}])

    assert SCOPE in [line for line in out.splitlines()
                     if 'scoped' in line][0], repr(out)


def test_list_hotfixes_prints_an_absent_scope_as_absent(tmp):
    """C11: a fix with no scope does not print an empty pattern cell.

    This is the anti-vacuity half of the control above: one fixture with a
    scope and one without, so neither assertion can be met by a listing
    that renders the same thing either way.
    """
    del tmp
    _recorded, out = run_cli(['list-hotfixes'], [{'version': '1.0', 'fixes': [
        {'id': 'scoped', 'ts': 1750000000000, 'code': 'console.log(1)',
         'match': SCOPE},
        {'id': 'bare', 'ts': 1750000000000, 'code': 'console.log(2)'},
    ]}])

    rows = [line for line in out.splitlines() if 'scoped' in line
            or ' bare ' in line]
    assert len(rows) == 2, repr(out)
    scoped = [line for line in rows if 'scoped' in line][0]
    bare = [line for line in rows if ' bare ' in line][0]
    assert SCOPE in scoped, repr(out)
    assert '(none)' in bare, repr(out)
    assert 'None' not in bare and 'null' not in bare, repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clihotfix_')


if __name__ == '__main__':
    raise SystemExit(main())
