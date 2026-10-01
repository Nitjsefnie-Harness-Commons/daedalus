#!/usr/bin/env python3
"""The two ratchet files are gate-defining, and what that does to a head.

Its own module because both gate-freshness suites were at the 700-line
ceiling when this control was written and neither could hold it: what it
proves is about the files a ratchet MOVES, which is a question neither the
decision core nor the run's orchestration owns.

Nothing here asserts the spelling of `GATE_PATTERNS` — that is the decision
core's control. This drives the module's own enumeration and per-head flow
and reads the verdict each produces, so what is proved is that a ratchet
commit on main turns an older head red and leaves a head that carries it
green. Its stand-in for `gh` answers only the four endpoints that flow
reads, rather than the run suite's fuller one, because this control reaches
no other one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _gate_freshness_fixtures import _encode  # noqa: E402
from _repo import ROOT  # noqa: E402

G1 = 'a' * 40
HEAD = 'c' * 40
RATCHET = 'e' * 40
RUN = 'https://github.com/o/r/actions/runs/1'

# The two files a ratchet commit moves rather than a hand editing: one gate
# raises a coverage floor, the other lowers a journey count, and neither is a
# configuration file.
RATCHETED = ('.github/ci-thresholds.json', '.github/journey-budget.json')


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_ratchet')


def _reader(ratcheted, stale):
    """The per-head flow, with `ratcheted` resolving to the ratchet commit.

    Asking through the module's own path resolution is the point: it is what
    proves the pattern reaches the file carrying the numbers, rather than a
    hand-built gate list agreeing with itself. `stale` reports a merge base
    that is not the gate commit for that one compare, which is what an open
    head that never received the ratchet looks like to GitHub.
    """
    published = []

    def read(argv):
        target = next(t for t in argv if t.startswith('repos/'))
        if '/commits?' in target:
            asked = target.split('path=')[1]
            return _encode([{'sha': RATCHET if ratcheted in asked else G1}])
        if '/compare/' in target:
            gate = target.split('compare/')[1].split('...')[0]
            if stale and gate == RATCHET:
                return _encode({'status': 'diverged',
                                'merge_base_commit': {'sha': 'f' * 40}})
            return _encode({'status': 'ahead',
                            'merge_base_commit': {'sha': gate}})
        if target.rsplit('/', 1)[1].isdigit():
            return _encode({'head': {'sha': HEAD}})
        published.append(target)
        return '{}'
    return read, published


def _heads(m):
    return m.select_heads([{'number': 7, 'base': {'ref': 'main'},
                            'head': {'sha': HEAD, 'ref': 'feature',
                                     'repo': {'owner': {'login': 'octo'}}}}])


def test_a_ratchet_commit_on_main_publishes_open_heads_red_until_they_rebase(
        tmp):
    """A head predating a ratchet commit is red; one carrying it is green.

    `.github/ci-thresholds.json` and `.github/journey-budget.json` are not
    configurations. Every other gate file decides what a check computes FOR A
    FIXED TREE; these two are computed BY their gates and committed to main by
    them, and a ratchet move LOWERS them. So a head predating such a commit
    was checked against the old, higher numbers and can carry a regression in
    within them, merging green and leaving main's own post-merge gate red.

    The cost is the shape of the fix, and it is the point: every open head is
    published red until it rebases and is measured again, because a rebased
    head IS re-measured against the numbers that replaced the old ones.
    """
    del tmp
    m = _mod()
    for ratcheted in RATCHETED:
        read, stale_published = _reader(ratcheted, stale=True)
        enumerated = m.enumerate_gates(read, 'o/r')
        assert (ratcheted, RATCHET) in enumerated, (
            f'{ratcheted} is not enumerated as a gate, so a ratchet commit '
            'lands on main with every open pull request still published '
            f'green against the numbers it replaced: {enumerated}')
        code, verdicts = m.process(read, 'o/r', _heads(m), enumerated, RUN)
        assert code == 0, 'a stale head is a verdict, not a script failure'
        assert verdicts[0].conclusion == 'failure', (
            f'a head predating the {ratcheted} commit is published green, '
            'which is the stale-base hole the gate exists to close')
        assert stale_published, 'the red verdict was never published'

        read, fresh_published = _reader(ratcheted, stale=False)
        code, verdicts = m.process(read, 'o/r', _heads(m), enumerated, RUN)
        assert code == 0, verdicts
        assert verdicts[0].conclusion == 'success', (
            f'a head already carrying the {ratcheted} commit is published '
            'red, so gating it would never let anything through')
        assert fresh_published, 'the green verdict was never published'


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='gatefreshratchet_')


if __name__ == '__main__':
    raise SystemExit(main())
