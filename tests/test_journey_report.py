#!/usr/bin/env python3
"""What a journey run SAYS: the prose every verdict and remedy is rendered
through.

Split out of `test_journey_counters.py`, which owns the counting these
blocks report, and which was at its size ceiling. The split is along the
module under test and nothing else: every control below was written
against `journey_report.py` and none of them loads the counters module,
so moving them neither changed what they drive nor where the fixtures
come from.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    IDENTITY,
    _util,
    budget_document,
    counter_facts,
    journeys,
    measurements_file,
    probe,
)


def test_every_summary_block_renders_and_names_its_remedy(tmp):
    """Each block is read by a person deciding whether to re-baseline, so
    each says what was not compared and what to do about it."""
    summaries = _journey_contract.summaries()
    gate = _journey_contract.policy()
    document = budget_document(toolchain=dict(IDENTITY))
    measurement = json.loads(measurements_file(
        Path(tmp) / 'counts.json', document).read_text('utf-8'))
    for lines in (summaries.probe_lines(probe()),
                  summaries.verdict_lines(
                      document, {'command-round-trip': 4000,
                                 'dashboard-fanout': 4000,
                                 'mcp-exec': 4000},
                      {'over': {'mcp-exec': (12000, 1100.0)},
                       'unmeasured': {}}),
                  summaries.verdict_lines(
                      document, {'mcp-exec': 4000},
                      {'over': {},
                       'unmeasured': {'mcp-exec': 'perf-instructions'}}),
                  summaries.rebaseline_lines(12345),
                  summaries.toolchain_lines(document, measurement, {},
                                            gate.TOOLCHAIN_REMEDY),
                  summaries.toolchain_lines(document, measurement,
                                            {'python': ('a', 'b')},
                                            gate.TOOLCHAIN_REMEDY),
                  summaries.toolchain_lines(document, measurement, {},
                                            gate.THREADS_REMEDY,
                                            subject='excluded threads')):
        assert lines, 'a block that renders to nothing is a block nobody reads'
        assert any(line.strip() for line in lines)
    # The two subjects must not read the same: a summary that said "toolchain
    # changed" for a set of threads sends the reader to the wrong remedy.
    moved = summaries.toolchain_lines(
        document, measurement, {'mcp-exec': (['front-end-import'], [])},
        gate.THREADS_REMEDY, subject='excluded threads')
    assert 'excluded threads changed, re-baseline' in moved[2], moved[2]
    assert 'toolchain' not in moved[2], moved[2]


def test_the_probe_row_says_whether_perf_ran_and_why_it_did_not(tmp):
    """The two probe states a runner can be in, neither of them a default.

    `perf_stat` is absent on a runner with no perf on PATH at all, and it
    carries stderr on one where perf ran and refused to count. Those are
    the two sentences a reader uses to decide whether a low count means
    anything, and they are told apart by the text: without perf there is
    no fenced block at all, and with it the block is what carries the
    refusal, since the returncode perf exits with is not evidence.
    """
    del tmp
    summaries = _journey_contract.summaries()
    absent = summaries.probe_lines(dict(probe(), perf_stat=None))
    joined = '\n'.join(absent)
    assert ('- `perf stat -e instructions:u -- true`: not run, perf is not on '
            'PATH') in joined, joined
    assert '```' not in joined, (
        'a runner with no perf at all fenced a block for it, so the summary '
        f'carries an output nothing produced: {joined}')
    refused = summaries.probe_lines(counter_facts())
    said = '\n'.join(refused)
    assert 'returncode `0`' in said, said
    assert '\n```\nnot permitted\n```' in said, (
        "perf's own stderr is what says it was refused, and the summary "
        f'drops it: {said}')
    assert '```\nnot permitted' not in '\n'.join(absent), absent


def test_the_verdict_table_pins_every_row_it_renders(tmp):
    """One row per recorded journey, each carrying its own numbers.

    The block loop above proves the table RENDERS; this proves each cell says
    what the gate decided. A row whose verdict read "within budget" over a
    count above its budget is the one contradiction a maintainer cannot act
    on, and nothing else in the tree notices it — the counts are right in the
    log and the artifact on disk is right too.
    """
    del tmp
    summaries = _journey_contract.summaries()
    names = journeys().NAMES
    # Recorded at 1000 with a 10% tolerance, so every budget is 1100.
    document = budget_document()
    counts = {names[0]: 1200, names[1]: 900}
    found = {'over': {names[0]: (1200, 1100.0)},
             'unmeasured': {names[2]: 'perf-instructions'}}
    rows = {line.split('|')[1].strip(): line
            for line in summaries.verdict_lines(document, counts, found)
            if line.startswith('|') and not line.startswith('|---')}
    assert sorted(rows) == sorted([*names, 'journey']), sorted(rows)
    assert rows[names[0]] == (
        f'| {names[0]} | 1200 | 1100 | +100 | OVER BUDGET |'), rows[names[0]]
    assert rows[names[1]] == (
        f'| {names[1]} | 900 | 1100 | -200 | within budget |'), rows[names[1]]
    assert rows[names[2]] == (
        f'| {names[2]} | not measured | 1100 | — | no count for '
        '`perf-instructions` |'), rows[names[2]]


def test_the_table_and_the_gate_are_held_to_the_same_number(tmp):
    """The summary's budget cell and the verdict above it are one number.

    A second copy of the arithmetic in `journey_report.py` is the one drift
    nothing else in the tree can see: the counts are right in the log and on
    disk, the module's own `budget_of` is right, and the table under the
    verdict is a number a second implementation produced. So the cell is
    compared with the gate's own number, the gate is driven to both sides of
    that same number, and the module is read for a tolerance of its own at
    all — which is what a second copy would have to mention.
    """
    del tmp
    summaries = _journey_contract.summaries()
    policy = _journey_contract.policy()
    names = journeys().NAMES
    own, defaulted = names[0], names[1]
    document = budget_document(tolerance_pct=10, tolerances={own: 40})
    counts = {name: 0 for name in names}
    counts[own] = 1400
    counts[defaulted] = 1100
    found = policy.violations(counts, document, names)
    assert not any(found.values()), found
    rows = {line.split('|')[1].strip(): line
            for line in summaries.verdict_lines(document, counts, found)
            if line.startswith('|') and not line.startswith('|---')}
    assert rows[own] == f'| {own} | 1400 | 1400 | 0 | within budget |', (
        rows[own])
    assert rows[defaulted] == (
        f'| {defaulted} | 1100 | 1100 | 0 | within budget |'), (
            rows[defaulted])
    for name in (own, defaulted):
        limit = policy.budget_of(document, name)
        assert f'| {limit:.0f} |' in rows[name], (name, limit, rows[name])
        # One instruction either side of the number the table printed, driven
        # through the gate: the table said `within budget` at it, and the gate
        # has to agree at both ends of the boundary.
        for measured, expected in ((limit, False), (limit + 1, True)):
            over = policy.violations(dict(counts, **{name: measured}),
                                     document, names)['over']
            assert (name in over) is expected, (name, measured, over)

    source = (Path(_journey_contract.ROOT) / 'scripts' / 'ci'
              / 'journey_report.py').read_text(encoding='utf-8')
    # The two FIELD names, not the word: the remedies below say in prose that
    # a re-baseline leaves the recorded tolerance alone, and that sentence is
    # not a second copy of anything. A second copy has to read a value.
    named = [line for line in source.splitlines()
             if 'tolerance_pct' in line or 'tolerances' in line]
    assert not named, (
        'journey_report.py reads a tolerance of its own rather than the one '
        f'the gate compares against: {named}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeyreport_')


if __name__ == '__main__':
    raise SystemExit(main())
