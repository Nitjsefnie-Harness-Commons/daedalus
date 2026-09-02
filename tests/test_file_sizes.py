#!/usr/bin/env python3
"""The module-size policy, and the ratchet that keeps its numbers honest.

The policy itself lives in scripts/ci/size_baseline.py, because CI rewrites it
when a file shrinks. What is tested here is that the tree satisfies it, and
that the rewriting only ever moves a number down.
"""
import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'size_baseline.py'

# Wordings that offer a bigger number as the way past a refusal, each a
# claim no correct statement of the rule can make.
GROWTH_SANCTIONING_CLAIMS = (
    # Growth presented as a supported move rather than a refusal.
    'growing a listed file is allowed',
    # Raising the recorded number offered as how that growth is done.
    'editing its recorded number',
    # The refusal message's old second option, beside splitting the file.
    'record the new size',
    # A first crossing excused by a fresh entry instead of being split;
    # both spellings, because either sentence reads as a licence.
    'add a baseline entry',
    'adding a baseline entry',
)

# Deleted rather than spaced out, so a claim spanning one - a backtick, or
# a string literal broken across two lines - stays one phrase to the pin.
MARKERS = ('#', "'", '"', '`', '*', '(', ')')

# The unit a negation governs. Finer than a sentence, so that "growing a
# listed file is allowed, but only by shrinking it" is refused on its first
# clause rather than excused by the second.
CLAUSE_BREAK = re.compile(r'[.;,:]')

# Every claim above is about what a person may do by hand, so a clause is
# not offering one where a negator falls anywhere in it, or where the tool
# that does the writing is its subject.
NOT_BY_HAND = re.compile(
    r'\b(?:never|not|no|none|nothing|nobody|dont|cannot|rather than'
    r'|instead)\b|--tighten')

# The rule stated positively, so deleting it is as visible as contradicting
# it. An alternation because the claim is a family, and a pin that refuses a
# synonym of it is a pin the next author deletes.
ACCEPTED_RULE_SPELLINGS = (
    r'(?:never|not|no)\b[a-z -]{0,40}?(?:raised|increased|edited upward)',
    r'only ever go(?:es)? down',
)
STATES_THE_RULE = re.compile('|'.join(ACCEPTED_RULE_SPELLINGS))
RULE_HELP = ('a negated raising verb ("is never raised by hand", "is not '
             'edited upward by hand"), or "only ever go down"')


def _policy():
    return _util.load(POLICY_SOURCE, 'size_baseline')


def _normalised(text):
    """`text` lowercased, as one line, with prose markers removed."""
    text = text.lower()
    for marker in MARKERS:
        text = text.replace(marker, '')
    return ' '.join(text.split())


def _policy_prose():
    """The whole policy source, normalised.

    Whole source rather than the docstring alone: a comment or a printed
    string carries the advice to a reader just as the docstring does.
    """
    return _normalised(POLICY_SOURCE.read_text(encoding='utf-8'))


def _drive_main(policy, baseline, sizes):
    """`main()` over a fabricated tree, as (status, stderr)."""
    captured = io.StringIO()
    argv, table, sizer = sys.argv, policy.BASELINE, policy.tracked_sizes
    sys.argv = ['size_baseline.py']
    policy.BASELINE, policy.tracked_sizes = baseline, lambda: sizes
    try:
        with contextlib.redirect_stderr(captured):
            status = policy.main()
    finally:
        sys.argv = argv
        policy.BASELINE, policy.tracked_sizes = table, sizer
    return status, captured.getvalue()


def test_every_tracked_module_satisfies_the_size_policy(tmp):
    """No file grew past its record, and none is over the ceiling unexcused."""
    del tmp
    policy = _policy()
    found = policy.violations(policy.tracked_sizes())
    assert not found['grown'], (
        f"these files grew past their recorded size: {found['grown']}\n"
        f'{policy.GROWTH_REMEDY}')
    assert not found['over'], (
        'these files are over the ceiling with no BASELINE entry: '
        f"{found['over']}\n{policy.GROWTH_REMEDY}")


def test_the_baseline_names_only_files_that_still_need_it(tmp):
    """A stale entry is a ceiling nobody is held to.

    Two ways an entry rots: the file is gone, or it has been split back under
    its ceiling and the entry now excuses a file that needs no excuse.
    """
    del tmp
    policy = _policy()
    found = policy.violations(policy.tracked_sizes())
    assert not found['missing'], (
        'BASELINE names files that are not tracked any more: '
        f"{found['missing']}\n{policy.STALE_ENTRY_REMEDY}")
    assert not found['graduated'], (
        f"these files are back under their ceiling: {found['graduated']}\n"
        f'{policy.STALE_ENTRY_REMEDY}')


def test_the_ceilings_are_below_everything_they_excuse(tmp):
    """A ceiling above a baselined file would make that entry meaningless."""
    del tmp
    policy = _policy()
    for rel, recorded in policy.BASELINE.items():
        assert recorded > policy.ceiling_for(rel), (rel, recorded)


def test_tightening_follows_a_shrunk_file_down(tmp):
    """The bound tracks the file, so the lines cannot come back for free."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n}\n"
    rewritten = policy.tightened(
        text, {'server.py': 2000}, {'server.py': 2624})
    assert rewritten is not None, 'a 624-line shrink tightened nothing'
    assert "'server.py': 2000," in rewritten, rewritten


def test_tightening_never_raises_a_number(tmp):
    """The ratchet follows a file down and never back up."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n}\n"
    for current in (2624, 2625, 9999):
        rewritten = policy.tightened(
            text, {'server.py': current}, {'server.py': 2624})
        assert rewritten is None, (current, rewritten)


def test_tightening_deletes_an_entry_that_graduated(tmp):
    """Back under the ceiling, the entry goes rather than shrinking further."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n    'mcp_server.py': 939,\n}\n"
    rewritten = policy.tightened(
        text, {'server.py': 400, 'mcp_server.py': 939},
        {'server.py': 2624, 'mcp_server.py': 939})
    assert rewritten is not None, 'a graduated file changed nothing'
    assert 'server.py' not in rewritten.replace('mcp_server.py', ''), rewritten
    assert "'mcp_server.py': 939," in rewritten, rewritten


def test_tightening_leaves_a_file_it_has_no_entry_for(tmp):
    """A file outside the baseline is the ceiling's business, not the ratchet's."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n}\n"
    rewritten = policy.tightened(
        text, {'server.py': 2624, 'tests/_util.py': 10}, {'server.py': 2624})
    assert rewritten is None, rewritten


def test_the_policy_prose_never_sanctions_growth_by_hand(tmp):
    """A refused session must not read a bigger number as the fix."""
    del tmp
    offered = [(claim, clause)
               for clause in CLAUSE_BREAK.split(_policy_prose())
               for claim in GROWTH_SANCTIONING_CLAIMS
               if _normalised(claim) in clause
               and not NOT_BY_HAND.search(clause)]
    assert not offered, f'the size policy offers growth by hand: {offered}'


def test_the_policy_prose_still_states_the_rule(tmp):
    """Deleting the rule has to be as visible as contradicting it."""
    del tmp
    assert STATES_THE_RULE.search(_policy_prose()), (
        f'the size policy no longer states the rule; write one of: '
        f'{RULE_HELP}')


def test_the_docstring_carries_every_printed_remedy(tmp):
    """One text, so the reference cannot drift from what a refusal prints."""
    del tmp
    policy = _policy()
    doc = _normalised(policy.__doc__)
    for kind, remedy in sorted(policy.REMEDY_FOR.items()):
        assert _normalised(remedy) in doc, (
            f'the docstring no longer carries the {kind} remedy: {remedy}')


def test_a_refused_run_prints_the_remedy_for_every_kind(tmp):
    """Each kind gets the remedy that is true for it, and no other."""
    del tmp
    policy = _policy()
    over, under = policy.TEST_CEILING + 1, policy.TEST_CEILING
    fabricated = {
        'grown': ({'tests/big.py': over}, {'tests/big.py': over + 1}),
        'over': ({}, {'tests/big.py': over}),
        'missing': ({'tests/gone.py': over}, {}),
        'graduated': ({'tests/small.py': over}, {'tests/small.py': under}),
    }
    for kind, (baseline, sizes) in sorted(fabricated.items()):
        found = policy.violations(sizes, baseline)
        assert [k for k, v in found.items() if v] == [kind], (kind, found)
        status, printed = _drive_main(policy, baseline, sizes)
        assert status == 1, (kind, status)
        assert policy.REMEDY_FOR[kind] in printed, (kind, printed)
        wrong = [other for other in set(policy.REMEDY_FOR.values())
                 if other != policy.REMEDY_FOR[kind] and other in printed]
        assert not wrong, (kind, printed)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
