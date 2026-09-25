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
import _cli_parse  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

run_cli = _cli_dispatch.run_cli
refused = _cli_parse.refused

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


def test_store_hotfix_can_ask_for_the_scope_to_be_removed(tmp):
    """C12, CLI direction: `--clear-scope` reaches the extension as the
    clear, not as a pattern.

    The pattern an operator clears is the one they wrote, and the extension
    is the only place that can decide whether a value is a pattern, so the
    CLI states the instruction and forwards the code. Nothing here re-decides
    the grammar.
    """
    del tmp
    recorded, _out = run_cli(
        ['store-hotfix', 'fix', '--code', 'console.log(1)', '--clear-scope'],
        [STORED])

    assert recorded.calls == [
        ('_store_hf', 'store-hotfix',
         {'fixId': 'fix', 'code': 'console.log(1)', 'clearScope': True}),
    ], recorded.calls


def test_store_hotfix_refuses_a_clear_alongside_a_scope(tmp):
    """The two instructions are mutually exclusive at the parser, so there
    is no argv that sends both.

    Honouring either one would apply a decision the operator did not make,
    and the loser is a scope they believe is set.
    """
    del tmp
    code, err = refused(['store-hotfix', 'fix', '--code', 'console.log(1)',
                         '--match', SCOPE, '--clear-scope'])

    assert code == 2, err
    assert '--clear-scope' in err, err
    # The anti-vacuity half: the two flags on their own are each accepted, so
    # the refusal above is the pair and not either flag.
    assert _cli_parse.accepted(
        ['store-hotfix', 'fix', '--code', 'x', '--match', SCOPE])
    assert _cli_parse.accepted(
        ['store-hotfix', 'fix', '--code', 'x', '--clear-scope'])


def test_store_hotfix_prints_the_scope_the_fix_ended_up_with(tmp):
    """The confirmation names the scope, so a clear is not a plain store.

    `--clear-scope` and a store that changed nothing print the same line
    otherwise, which leaves the operator with no way to tell a clear from a
    swallowed one — the confusion this command's whole reason for existing.
    Both arms are asserted here because a print that renders one shape for
    either outcome satisfies either assertion alone.
    """
    del tmp
    _recorded, cleared = run_cli(
        ['store-hotfix', 'fix', '--code', 'console.log(1)', '--clear-scope'],
        [dict(STORED, match=None)])

    assert 'unscoped' in cleared, repr(cleared)
    assert SCOPE not in cleared, repr(cleared)

    _recorded, scoped = run_cli(
        ['store-hotfix', 'fix', '--code', 'console.log(1)',
         '--match', SCOPE], [dict(STORED, match=SCOPE)])

    assert SCOPE in scoped, repr(scoped)
    assert 'unscoped' not in scoped, repr(scoped)


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
