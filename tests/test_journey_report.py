#!/usr/bin/env python3
"""What a journey run SAYS: the prose every verdict and remedy is rendered
through.

Every control below is written against `journey_report.py` and none of
them loads the counters module.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _journey_contract import (  # noqa: E402
    IDENTITY,
    ROOT,
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
    # The same row once the counter's own reason is threaded in, so the cell
    # is pinned in both states rather than the reason only being absent.
    rows = {line.split('|')[1].strip(): line
            for line in summaries.verdict_lines(
                document, counts, found, 'the probe did not find it usable')
            if line.startswith('|') and not line.startswith('|---')}
    assert rows[names[2]] == (
        f'| {names[2]} | not measured | 1100 | — | no count for '
        '`perf-instructions`: the probe did not find it usable |'), (
            rows[names[2]])


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


def test_the_table_never_reads_a_refused_journey_as_within_budget(tmp):
    """A journey the check refuses must not render green in the table.

    `verdict_lines` decides its verdict from three branches, and a journey
    this run refused arrives at none of the first two — `budget_of` answers
    a number, `unmeasured` does not carry it — so it fell through to
    "within budget" for a journey that was never measured. That is the same
    false green the check refuses, arriving through the report layer instead
    of the exit status, and the table is the surface a person reads.
    """
    del tmp
    summaries = _journey_contract.summaries()
    names = journeys().NAMES
    refused = names[0]
    document = budget_document()
    counts = {name: 1000 for name in names}
    del counts[refused]
    refusal = 'the journey measured [2000] net [-9000]'
    found = {'over': {}, 'unmeasured': {},
             'unresolved': {refused: refusal}}
    lines = summaries.verdict_lines(document, counts, found)
    row = next(line for line in lines
               if line.startswith(f'| {refused} '))
    assert 'within budget' not in row, (
        f'the table calls a journey within budget that this run refused to '
        f'measure: {row}')
    assert 'OVER BUDGET' not in row, row
    assert 'no count for' not in row, row
    assert 'could not resolve' in row, row
    # And the same document with the journey recorded at null reads as the
    # other thing it is, rather than the same false green by another route.
    dropped = budget_document()
    dropped['journeys'] = dict(document['journeys'], **{refused: None})
    row = next(line for line in summaries.verdict_lines(
        dropped, counts,
        {'over': {}, 'unmeasured': {}, 'unresolved': {}})
        if line.startswith(f'| {refused} '))
    assert 'within budget' not in row, row


def test_the_unmeasured_row_carries_the_reason_the_counter_gave(tmp):
    """The counter's own sentence has to reach a reader of a failed run.

    `boundary_refusal()` exists because "the refusal is what a reader of a
    failed run gets and it has to name the variable that was missing", and
    the row reporting an unmeasured journey named only the counter: the
    sentence survived inside the uploaded `journey-counts.json` and nowhere
    a person reads. So the reason is rendered into the row.

    The remedy beside it has to name the step that FAILED. It named the
    probe step, which succeeded, while the build step is the one whose
    missing output produces this refusal -- and the name is cross-checked
    against the job, so a step renamed in one place and not the other is
    red rather than a remedy pointing at nothing.
    """
    del tmp
    summaries = _journey_contract.summaries()
    gate = _journey_contract.policy()
    names = journeys().NAMES
    document = budget_document()
    counts = {name: 1000 for name in names}
    unmeasured = names[0]
    del counts[unmeasured]
    refusal = ('DAEDALUS_CALLGRIND_BOUNDARY names no compiled boundary '
               'helper, so a counted child would keep its interpreter '
               'startup in the recorded count')
    row = next(line for line in summaries.verdict_lines(
        document, counts,
        {'over': {}, 'unmeasured': {unmeasured: 'valgrind-callgrind'}},
        refusal)
        if line.startswith(f'| {unmeasured} '))
    assert 'valgrind-callgrind' in row, row
    assert refusal in row, (
        f'the counter named no reason on the row a reader meets: {row}')
    # The other shape a counter reports its reason in: a tool that would not
    # run carries a returncode and its own stderr rather than a sentence.
    tool = {'returncode': 255, 'stderr': 'perf: no permission'}
    row = next(line for line in summaries.verdict_lines(
        document, counts,
        {'over': {}, 'unmeasured': {unmeasured: 'perf-instructions'}}, tool)
        if line.startswith(f'| {unmeasured} '))
    assert 'returncode 255' in row and 'perf: no permission' in row, row
    # And the tool's own stderr verbatim, which is what `journey_counters`
    # records. The wording is not a real tool's; the SHAPE is what this row
    # is for, and it carries all four of it: a newline, a bare pipe, a
    # backslash abutting one pipe, and two backslashes abutting another.
    # The cell this renders into is one line of one table, so a newline
    # would split the row out of it and an unescaped pipe would add a
    # phantom column -- and a backslash the sanitiser does not account for
    # leaves an EVEN run before a pipe, which the table grammar reads as a
    # column boundary again, so the escape undoes itself.
    noisy = {'returncode': 1, 'stderr': 'warn \\| and \\\\| too | here\nend'}
    row = next(line for line in summaries.verdict_lines(
        document, counts,
        {'over': {}, 'unmeasured': {unmeasured: 'perf-instructions'}}, noisy)
        if line.startswith(f'| {unmeasured} '))
    # The WHOLE row, written out. Counting pipes and subtracting a count of
    # escaped pipes agrees with the correct rendering here and with the
    # corrupt one everywhere except where the reason carried a backslash --
    # the input this fixture is built around -- so the count is blind to
    # exactly the defect it stands in for, and a literal is not. It also
    # restates no column count, so a table that legitimately grows a
    # column leaves this assertion green.
    assert row == (
        r'| command-round-trip | not measured | 1100 | — | no count for '
        r'`perf-instructions`: returncode 1: warn \\\| and '
        r'\\\\\| too \| here end |'), row

    # A journey NAME is the other value interpolated into a row, and
    # `journey_artifact` validates a key only as a key -- it bounds the
    # recorded COUNT and never the name -- so a name carrying a pipe reaches
    # this table carrying it. It is the FIRST cell, which is why an
    # unescaped pipe is worse here than in the reason: it moves the count,
    # the delta and the verdict each out of the column a reader reads them
    # from. The same escape as the cell above, and a literal for the same
    # reason.
    piped = 'dashboard | fanout'
    piped_document = budget_document()
    piped_document['journeys'] = {piped: 1000}
    lines = summaries.verdict_lines(
        piped_document, {piped: 900}, {'over': {}, 'unmeasured': {}})
    # Anchored on the separator rather than a line number or a column count,
    # so a table that legitimately grows a column leaves this green.
    separator = next(line for line in lines if line.startswith('|---'))
    rows = lines[lines.index(separator) + 1:]
    assert rows == [
        r'| dashboard \| fanout | 900 | 1100 | -200 | within budget |'], rows

    remedy = gate.UNMEASURED_REMEDY
    assert 'DAEDALUS_CALLGRIND_BOUNDARY' in remedy, remedy
    step = 'Build the counted-boundary helper'
    assert step in remedy, remedy
    job = complete_job_mapping(
        (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
            encoding='utf-8'), 'journey-budget')
    assert job is not None, 'the journey-budget job is not in tests.yml'
    declared = {one.get('name') for one in job['steps'] if one.get('name')}
    assert step in declared, (
        'the remedy sends the reader to a step the job does not declare: '
        f'{sorted(declared)}')


def test_an_unresolved_row_carries_the_refusal_the_gate_gave(tmp):
    """A journey the run REFUSED is reported under its own sentence.

    `journey_gates` fills `unresolved[name]` with the refusal the run
    produced, and the table rendered a bare "this run could not resolve
    it" without ever reading it: the sentence was computed, carried, and
    dropped at the last step, so a reader of a failed run learned THAT a
    journey was unresolved and nothing about WHY. The reason is rendered
    into the row.

    This control fails for the opposite reason to the one above: that case
    is about a reason rendered UNSAFE, this one about a reason not
    rendered at all, and neither can stand in for the other. It also pins
    THIS call site to the sanitiser, which nothing else did: replacing the
    `_reason` around `unresolved[name]` with the bare value left every suite
    green, because today's refusals are minted from a journey name and two
    int lists and carry neither a pipe nor a newline. So the refusal here
    carries both -- no backslash, which is the one shape the sibling
    control above owns, and the two controls stay disjoint.
    """
    del tmp
    summaries = _journey_contract.summaries()
    names = journeys().NAMES
    unresolved = names[0]
    document = budget_document()
    counts = {name: 1000 for name in names}
    del counts[unresolved]
    refusal = 'the journey measured [2000] net [-9000]\nand it said | perf'
    row = next(line for line in summaries.verdict_lines(
        document, counts,
        {'over': {}, 'unmeasured': {}, 'unresolved': {unresolved: refusal}})
        if line.startswith(f'| {unresolved} '))
    assert row == (
        r'| command-round-trip | not measured | 1100 | — | this run could '
        r'not resolve it: the journey measured [2000] net [-9000] and it '
        r'said \| perf |'), row


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeyreport_')


if __name__ == '__main__':
    raise SystemExit(main())
