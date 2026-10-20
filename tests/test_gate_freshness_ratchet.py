#!/usr/bin/env python3
"""Ratchet commits make older heads stale until rebased."""
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

RATCHETED = ('.github/ci-thresholds.json', '.github/journey-budget.json')


def _mod():
    return _util.load(ROOT / 'scripts' / 'ci' / 'gate_freshness.py',
                      'gate_freshness_ratchet')


def _reader(ratcheted, stale):
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
        if '/check-runs?' in target:
            assert '--method' in argv and 'GET' in argv
            return ''
        assert target == 'repos/o/r/check-runs' and 'POST' in argv
        published.append(target)
        return '{}'
    return read, published


def _heads(m):
    return m.select_heads([{'number': 7, 'base': {'ref': 'main'},
                            'head': {'sha': HEAD, 'ref': 'feature',
                                     'repo': {'owner': {'login': 'octo'}}}}])


def test_ratchet_commit_blocks_old_heads_until_they_rebase(tmp):
    del tmp
    m = _mod()
    for ratcheted in RATCHETED:
        read, stale_published = _reader(ratcheted, stale=True)
        enumerated = m.enumerate_gates(read, 'o/r')
        assert (ratcheted, RATCHET) in enumerated, enumerated
        code, verdicts = m.process(read, 'o/r', _heads(m), enumerated, RUN, G1)
        assert code == 0, 'a stale head is a verdict, not a script failure'
        assert verdicts[0].conclusion == 'failure', verdicts
        assert stale_published, 'the red verdict was never published'

        read, fresh_published = _reader(ratcheted, stale=False)
        code, verdicts = m.process(read, 'o/r', _heads(m), enumerated, RUN, G1)
        assert code == 0, verdicts
        assert verdicts[0].conclusion == 'success', verdicts
        assert fresh_published, 'the green verdict was never published'


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='gatefreshratchet_')


if __name__ == '__main__':
    raise SystemExit(main())
