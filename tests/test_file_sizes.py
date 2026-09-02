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

# Claims this file must never make. Each fills its own subject slot - the
# act itself in the first, a named actor in the other two - so a prohibition
# does not contain the claim it refuses and no reading of negation is needed
# to tell them apart. Two costs: a permission addressed to somebody else
# ("A first crossing may add a BASELINE entry.") is missed, and a claim
# quoted to be disowned is refused; the rule reads positively here, so
# neither belongs in the file anyway.
FORBIDDEN_CLAIMS = (
    # The licence the policy carried, verbatim. Growth is not bought by
    # editing the number.
    'growing a listed file is allowed',
    # A first crossing excused by a fresh entry instead of being split.
    'you may add a baseline entry',
    # Untrue: an entry naming a file that is gone is deleted by hand.
    '--tighten is the only writer of this table',
)

# Deleted rather than spaced out, so a claim spanning one - a backtick, or
# a string literal broken across two lines - stays one phrase to the pin.
MARKERS = ('#', "'", '"', '`', '*', '(', ')')

# The rule stated positively, so deleting it is as visible as contradicting
# it. An alternation because the claim is a family, and a pin that refuses a
# synonym of it is a pin the next author deletes. Stems, so one entry covers
# a verb's forms; each is witnessed below. Growth is spelled "grow past"
# rather than bare: this module's subject is files growing, and a bare stem
# reads any negator near that subject as the rule.
RAISING_VERBS = ('rais', 'increas', 'upward', 'bump', 'rise',
                 'go up', 'goes up', 'grow past', 'grows past')
_RAISING = r'\b(?:' + '|'.join(RAISING_VERBS) + ')'
# A comma inside the window, because "never, ever raised" is a sentence
# somebody writes; the span itself is pinned by the far-negator negative.
ACCEPTED_RULE_SPELLINGS = (
    r'(?:never|nobody|not|no)\b[a-z ,-]{0,40}?' + _RAISING,
    _RAISING + r'[a-z ,-]{0,40}?(?:forbidden|prohibited)',
    r'only ever go(?:es)? down',
)
STATES_THE_RULE = re.compile('|'.join(ACCEPTED_RULE_SPELLINGS))
RULE_HELP = ('a negated raising verb ("is never raised by hand", "never '
             'goes up", "is not edited upward by hand"), a prohibited one '
             '("raising it is forbidden"), or "only ever go down"')

# Texts the spelling regex must tell apart, so a regex that stopped
# discriminating - one matching everything, nothing, or the rule inverted -
# is visible here rather than in the file it guards. The first two negatives
# invert the rule, because negatives that all lack a raising verb leave the
# negator - the half that makes it this rule - unmeasured.
RULE_STATED = (
    'a recorded number is never raised by hand',
    'no recorded number is ever increased by hand',
    'the number is not edited upward by hand',
    'a listed file never grows past its record',
    'no change may grow past a recorded number',
    'the recorded number is never bumped to make room',
    'a recorded number never goes up',
    'a recorded number must not go up',
    'a recorded number must not rise',
    'nobody raises a recorded number by hand',
    'a recorded number is never, ever raised by hand',
    'raising a recorded number is forbidden',
    'the numbers in this table only ever go down',
)
RULE_NOT_STATED = (
    'a recorded number is raised by hand when a file grows',
    'the numbers in this table go down and up',
    'growing a listed file is allowed by editing its recorded number',
    'the ratchet keeps the numbers in this table honest',
    # The window: a negator this far from a raising verb negates something
    # else, so widening the span reads this licence as the rule.
    'not a line limit, and a file over its ceiling is listed at the size '
    'it was measured, and a recorded number is raised by hand',
    '',
)

# The move each kind's remedy has to name, as (--tighten clears it, the
# phrase the printed advice must carry, the phrase it must not). Written
# out here rather than read from REMEDY_FOR, which is the thing being pinned.
REMEDY_NAMES_THE_MOVE = {
    'grown': (False, 'relocate the code into a new module', '--tighten'),
    'over': (False, 'relocate the code into a new module', '--tighten'),
    'missing': (False, 'deleted by hand', 'relocate the code'),
    'graduated': (True, '--tighten', 'relocate the code'),
}

# Correct statements of the policy, which the claim check must accept; a
# guard refusing correct prose is deleted rather than worked around.
STATES_THE_POLICY = (
    'Never add a BASELINE entry to excuse growth.',
    'Adding a BASELINE entry is forbidden.',
    'Editing its recorded number is off the table.',
    'You may not record the new size by hand.',
    'Split the file rather than editing its recorded number.',
    'A reviewer who sees you add a BASELINE entry will reject the change.',
    'Neither raising the number nor adding a BASELINE entry is a fix.',
    'The old advice, record the new size, was withdrawn.',
    "You shouldn't add a BASELINE entry.",
    "You can't record the new size by hand.",
    "Growing a listed file isn't allowed.",
    # Growth refused and shrinking permitted in one wording, so a claim
    # broadened to the permission alone is refused here.
    'Shrinking a listed file is allowed and rewarded.',
    'A recorded number is never raised by hand.',
    'The numbers in this table only ever go down.',
    'Relocate the code into a new module instead of growing the file.',
    'A file crossing its ceiling for the first time is split rather '
    'than excused.',
    'Shrink the file; the table is not where growth is negotiated.',
    # Prohibitions with a negative quantifier for a subject: the shape
    # that contains a claim built on a bare modal.
    'Nobody may add a BASELINE entry.',
    'Neither an author nor a reviewer may add a BASELINE entry.',
    'No tool is the only writer of this table.',
    'Neither --tighten nor CI is the only writer of this table.',
    'No change may grow a listed file past its record.',
    'Under no circumstances may a listed file grow.',
)

# Licences to grow, which it must refuse: the sentence the policy carried, a
# marker-carrying variant of it, then one per remaining claim. This half is a
# backstop over free prose and does not pretend to catch a paraphrase - a
# permissive sentence written in none of these wordings is caught only where
# it displaces the rule paragraph and loses the rule's spelling with it. An
# exception clause appended to an intact rule keeps that spelling and passes,
# and the containment pin below moves with a reworded constant.
SANCTIONS_GROWTH = (
    'Growing a listed file is allowed, because some fixes genuinely belong '
    'in it, but only by editing its recorded number in the same commit.',
    'Growing a listed file is allowed (edit the number).',
    'You may add a BASELINE entry when the file has to grow.',
    '`--tighten` is the only writer of this table.',
)


def _policy():
    return _util.load(POLICY_SOURCE, 'size_baseline')


def _normalised(text):
    """`text` lowercased, as one line, with prose markers removed."""
    text = text.lower()
    for marker in MARKERS:
        text = text.replace(marker, '')
    return ' '.join(text.split())


def _offered(text):
    """Every forbidden claim `text` makes, in the order they are listed."""
    prose = _normalised(text)
    return [claim for claim in FORBIDDEN_CLAIMS
            if _normalised(claim) in prose]


def _policy_prose():
    """The whole source normalised, since a comment or a printed string
    carries the advice to a reader just as the docstring does."""
    return _normalised(POLICY_SOURCE.read_text(encoding='utf-8'))


def _baseline_text(baseline):
    """`baseline` written out the way the module carries its own table."""
    entries = ''.join(
        f"    '{rel}': {size},\n" for rel, size in baseline.items())
    return f'BASELINE = {{\n{entries}}}\n'


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
    """No wording of the three licences to grow survives in the source."""
    del tmp
    offered = _offered(POLICY_SOURCE.read_text(encoding='utf-8'))
    assert not offered, f'the size policy offers growth by hand: {offered}'


def test_the_claim_check_accepts_a_correct_statement_of_the_policy(tmp):
    """No listed correct statement of the policy contains a licence."""
    del tmp
    assert STATES_THE_POLICY, 'the tolerance battery is empty'
    refused = [(text, _offered(text)) for text in STATES_THE_POLICY
               if _offered(text)]
    assert not refused, f'the pin refuses correct prose: {refused}'


def test_the_claim_check_refuses_a_licence_to_grow(tmp):
    """Each listed licence to grow by hand is read as one."""
    del tmp
    assert SANCTIONS_GROWTH, 'the strictness battery is empty'
    missed = [text for text in SANCTIONS_GROWTH if not _offered(text)]
    assert not missed, f'the pin passes a licence to grow: {missed}'


def test_the_policy_prose_still_states_the_rule(tmp):
    """Deleting the rule has to be as visible as contradicting it."""
    del tmp
    assert STATES_THE_RULE.search(_policy_prose()), (
        f'the size policy no longer states the rule; write one of: '
        f'{RULE_HELP}')


def test_the_rule_spellings_tell_a_statement_from_its_absence(tmp):
    """Every listed statement of the rule is seen; no listed absence is."""
    del tmp
    assert RULE_STATED, 'the rule-statement battery is empty'
    assert RULE_NOT_STATED, 'the rule-absence battery is empty'
    assert any(verb in text for text in RULE_NOT_STATED
               for verb in RAISING_VERBS), (
        'no listed negative states the rule inverted, so a regex that lost '
        'its negator would pass here')
    missed = [text for text in RULE_STATED
              if not STATES_THE_RULE.search(text)]
    assert not missed, f'the rule is stated here and not seen: {missed}'
    matched = [text for text in RULE_NOT_STATED
               if STATES_THE_RULE.search(text)]
    assert not matched, f'the rule is read into prose lacking it: {matched}'


def test_the_docstring_carries_every_printed_remedy(tmp):
    """One text, so the reference cannot drift from what a refusal prints."""
    del tmp
    policy = _policy()
    doc = _normalised(policy.__doc__)
    for kind, remedy in sorted(policy.REMEDY_FOR.items()):
        # A blank or one-word remedy is carried by every docstring there
        # is, so containment on its own would prove nothing about it.
        assert len(_normalised(remedy)) > 40, (
            f'the {kind} remedy is too short to advise anyone: {remedy!r}')
        assert _normalised(remedy) in doc, (
            f'the docstring no longer carries the {kind} remedy: {remedy}')


def test_a_refused_run_prints_the_remedy_for_every_kind(tmp):
    """Each kind's remedy names the move that clears it, and not the other.

    Whether `--tighten` clears the kind is established by running it, and
    the printed advice is read for the words a refused author acts on.
    """
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
        clears, names, never = REMEDY_NAMES_THE_MOVE[kind]
        rewritten = policy.tightened(
            _baseline_text(baseline), sizes, baseline)
        cleared = rewritten is not None and not any(
            rel in rewritten for rel in baseline)
        assert cleared == clears, (kind, rewritten)
        status, printed = _drive_main(policy, baseline, sizes)
        assert status == 1, (kind, status)
        assert names in printed, (kind, names, printed)
        assert never not in printed, (kind, never, printed)


def test_a_mixed_refusal_prints_each_remedy_once(tmp):
    """Three kinds, two answers: both printed, neither twice."""
    del tmp
    policy = _policy()
    over = policy.TEST_CEILING + 1
    baseline = {'tests/gone.py': over, 'tests/big.py': over}
    sizes = {'tests/big.py': over + 1, 'tests/other.py': over}
    found = policy.violations(sizes, baseline)
    assert sorted(k for k, v in found.items() if v) == [
        'grown', 'missing', 'over'], found
    status, printed = _drive_main(policy, baseline, sizes)
    assert status == 1, status
    assert printed.count('relocate the code into a new module') == 1, printed
    assert printed.count('deleted by hand') == 1, printed


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
