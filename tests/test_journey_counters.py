#!/usr/bin/env python3
"""Contracts for how a journey is COUNTED: which counter this runner
allows, what a measurement carries, and every way a measurement can
refuse rather than answer."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    IDENTITY,
    ROOT,
    _util,
    budget_document,
    counter_facts,
    journeys,
    planting,
    measurements_file,
    probe,
)


def test_a_strace_row_with_an_errors_column_is_still_counted(tmp):
    """`strace -c` prints that column only for a syscall that FAILED.

    A parser that assumes four columns on every row drops exactly the failing
    rows — `futex` among them — and a table built that way is flat for a
    reason that has nothing to do with the program. The total comes from
    strace's own last line, which is the one row every rendering prints.
    """
    del tmp
    counters = _journey_contract.counters()
    with_errors = (
        '% time     seconds  usecs/call    calls    errors syscall\n'
        '------ ----------- ----------- --------- ------ ----------------\n'
        ' 0.00    0.000000           0        12       0 futex\n'
        ' 0.00    0.000123          61       2       1 poll\n'
        '100.00    0.000123          54        14      1 total\n')
    assert counters._strace_call_total(with_errors) == 14
    assert counters._strace_call_total('nothing here\n') is None


def test_the_probe_decides_which_counter_by_measurement(tmp):
    """Which counter gates is settled by what this machine allows, not by
    a preference list read on faith: perf it cannot count, callgrind it
    can, and with neither there is no counter and the job still passes.
    """
    del tmp
    counters = _journey_contract.counters()
    blind = {'perf_path': '/usr/bin/perf', 'perf_stat': {'counts': False},
             'valgrind_path': '/usr/bin/valgrind', 'strace_path': None,
             'strace_usable': False}
    assert counters._selected(blind) == 'valgrind-callgrind'
    blind['valgrind_path'] = None
    assert counters._selected(blind) is None
    blind['strace_path'] = '/usr/bin/strace'
    blind['strace_usable'] = True
    assert counters._usable('syscalls', blind) is True
    blind['strace_usable'] = False
    assert counters._usable('syscalls', blind) is False
    assert counters._usable('no-such-counter', blind) is False
    assert counters._usable('perf-instructions', blind) is False
    blind['perf_stat'] = {'counts': True}
    assert counters._selected(blind) == 'perf-instructions'


def test_the_identity_is_what_the_runner_reports_about_itself(tmp):
    del tmp
    counters = _journey_contract.counters()
    found = {'python': '3.13.0 (main)', 'valgrind_version': 'valgrind-3.22',
             'perf_event_paranoid': 4, 'perf_path': None, 'perf_stat': None,
             'valgrind_path': None, 'strace_path': None,
             'strace_usable': False}
    saved = {name: os.environ.get(name)
             for name in ('ImageOS', 'ImageVersion')}
    try:
        for name in saved:
            os.environ.pop(name, None)
        assert counters._runner_image() is None
        os.environ['ImageOS'] = 'ubuntu24'
        os.environ['ImageVersion'] = '20260920.314.1'
        assert counters._runner_image() == 'ubuntu24 20260920.314.1'
        assert counters.toolchain(found)['runner_image'] == (
            'ubuntu24 20260920.314.1')
        assert counters.toolchain(found)['python'] == '3.13.0 (main)'
    finally:
        for name, value in saved.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
    assert isinstance(counters._paranoid(), (int, type(None)))


def test_a_journey_record_is_read_behind_its_marker(tmp):
    """The bridge's own output shares the stream, so the record is looked
    for rather than taken from the last line."""
    del tmp
    counters = _journey_contract.counters()
    assert counters.journey_record('noise\n' + counters.MARKER
                                   + '{"journey": "x", "sha256": "y"}\n'
                                   ) == {'journey': 'x', 'sha256': 'y'}
    assert counters.journey_record('the bridge said something\n') is None
    assert counters.journey_record('') is None
    assert counters.child_argv('mcp-exec', '/r')[0] == sys.executable


def test_the_measurement_carries_the_median_the_check_compares(tmp):
    """A planted counter, so `measure` runs for real and nothing does: the
    leaves are replaced, not the control flow."""
    del tmp
    counters = _journey_contract.counters()
    names = journeys().NAMES
    seen = []

    def answering(name, root, workdir):
        del root, workdir
        seen.append(name)
        return 1000 + len(seen), None

    # `syscalls`, because a counter the probe would not use is reported
    # unavailable without being run, and this is about what happens once
    # one has been chosen.
    with planting(counters,
                  shapes=lambda names, root, rounds: (
                      {name: ['shape-' + name] for name in names}, None),
                  COUNTERS=('syscalls',),
                  COUNTERS_BY_NAME={'syscalls': (answering, True)}):
        report = counters.measure(root=ROOT, rounds=2,
                                  found=counter_facts())
    assert seen[0] == 'startup-only', seen
    assert seen.count('startup-only') == 1, seen
    assert len(seen) == 1 + 2 * len(names), seen
    assert report['shape_failure'] is None
    assert report['shas'] == {name: ['shape-' + name] for name in names}
    counts = counters.counts_of(report, 'syscalls')
    # An even number of rounds has a median halfway between two, so the
    # count is a number and not necessarily a whole one.
    assert all(isinstance(value, (int, float)) and value > 0
               for value in counts.values()), counts
    row = report['counters']['syscalls']['journeys'][names[0]]
    assert row['spread'] == row['max'] - row['min'], row
    # Reported both ways because only the net one is the budget: the raw
    # total carries the startup the child pays whatever is measured.
    startup = report['counters']['syscalls']['startup_only']
    assert len(row['raw']) == 2, row
    assert row['net'] == [value - startup for value in row['raw']], row
    assert row['min'] == min(row['net']) and row['max'] == max(row['net'])
    # What the count is, stated per journey, is the thing the artefact
    # records beside it.
    assert report['excluded_threads']['mcp-exec'] == ['front-end-import']
    assert report['excluded_threads']['dashboard-fanout'] == [
        'front-end-import', 'uvicorn-serve']


def test_a_counter_this_runner_cannot_produce_is_reported_not_assumed(tmp):
    """An unavailable counter is unavailable, and the row says so."""
    del tmp
    counters = _journey_contract.counters()
    with planting(counters,
                  shapes=lambda names, root, rounds: (
                      {name: ['s'] for name in names}, None),
                  COUNTERS=('syscalls',),
                  COUNTERS_BY_NAME={
                      'syscalls': (lambda name, root, workdir: (
                          None, 'this runner will not count this'), True)}):
        report = counters.measure(root=ROOT, rounds=1,
                                  found=counter_facts())
    row = report['counters']['syscalls']
    assert row['available'] is False, row
    assert 'will not count' in row['why'], row


def test_a_journey_that_prints_no_record_stops_the_measurement(tmp):
    """A shape nobody can read is a refusal, not a count of zero."""
    del tmp
    counters = _journey_contract.counters()
    with planting(counters,
                  shapes=lambda names, root, rounds: (
                      None, 'the dashboard-fanout journey printed no record '
                      '(returncode 1): boom')):
        report = counters.measure(root=ROOT, rounds=1,
                                  found=counter_facts())
    assert 'printed no record' in report['shape_failure']
    assert report['counters'] == {}


def test_the_step_summary_is_written_only_where_there_is_one_to_write(tmp):
    counters = _journey_contract.counters()
    saved = os.environ.get('GITHUB_STEP_SUMMARY')
    try:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        counters.write_summary(['nothing to write'])
        target = Path(tmp) / 'summary.md'
        os.environ['GITHUB_STEP_SUMMARY'] = str(target)
        counters.write_summary(['a line', 'another'])
        counters.write_summary([])
        assert target.read_text(encoding='utf-8') == 'a line\nanother\n'
    finally:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if saved is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = saved


# ─── the thread reader, against a profile the shape is taken from ─────────


def test_a_counter_that_stops_mid_measurement_stops_the_measurement(tmp):
    """A counter that answers for the first journey and then refuses has
    already produced rows, and those rows must not be reported: a partial
    set of counts is the shape a gate cannot compare in."""
    del tmp
    counters = _journey_contract.counters()
    names = journeys().NAMES
    answered = []

    def flaky(name, root, workdir):
        del root, workdir
        answered.append(name)
        if len(answered) > 1 + len(names):
            return None, 'this runner stopped counting'
        return 1000, None

    with planting(counters,
                  shapes=lambda names, root, rounds: (
                      {name: ['s'] for name in names}, None),
                  COUNTERS=('syscalls',),
                  COUNTERS_BY_NAME={'syscalls': (flaky, True)}):
        report = counters.measure(root=ROOT, rounds=2,
                                  found=counter_facts())
    row = report['counters']['syscalls']
    assert row['available'] is False, row
    assert 'stopped counting' in row['why'], row
    assert 'journeys' not in row, row


def test_a_counter_that_will_not_start_is_reported_before_it_is_run(tmp):
    """A counter the probe did not find usable is not run at all, and the
    row says which probe finding decided that."""
    del tmp
    counters = _journey_contract.counters()
    names = journeys().NAMES
    ran = []

    def answering(name, root, workdir):
        del root, workdir
        ran.append(name)
        return 1000, None

    found = counter_facts()
    found['valgrind_path'] = None
    with planting(counters,
                  shapes=lambda names, root, rounds: (
                      {name: ['s'] for name in names}, None),
                  COUNTERS=('syscalls', 'valgrind-callgrind'),
                  COUNTERS_BY_NAME={
                      'syscalls': (answering, True),
                      'valgrind-callgrind': (answering, True)}):
        report = counters.measure(root=ROOT, rounds=1, found=found)
    assert ran and set(ran) <= {'startup-only', *names}, ran
    row = report['counters']['valgrind-callgrind']
    assert row == {
        'available': False,
        'why': 'the probe did not find this counter usable here'}, row
    assert report['counters']['syscalls']['available'] is True


def test_perf_counts_are_read_from_both_of_the_shapes_it_prints(tmp):
    """perf prints two renderings and counts under either, never by status.

    perf exits 0 whether or not it was permitted to count, so a returncode
    is not evidence and the text is the only thing read.
    """
    del tmp
    counters = _journey_contract.counters()
    machine = '1234567000123,,instructions:u,1000000,100.00,\n'
    assert counters._perf_instruction_count(machine) == 1234567000123
    text = '  1,234,567,000      instructions:u      99.99      '
    assert counters._perf_instruction_count(text) == 1234567000
    assert counters._perf_instruction_count('not permitted\n') is None
    assert counters._perf_instruction_count('') is None


def test_every_summary_block_renders_and_names_its_remedy(tmp):
    """Each block is read by a person deciding whether to re-baseline, so
    each says what was not compared and what to do about it."""
    summaries = _journey_contract.summaries()
    gate = _journey_contract.policy()
    document = budget_document(toolchain=dict(IDENTITY))
    measurement = json.loads(measurements_file(
        Path(tmp) / 'counts.json', document).read_text('utf-8'))
    for lines in (summaries.probe_lines(probe()),
                  summaries.summary_lines(measurement),
                  summaries.rebaseline_lines(measurement),
                  summaries.toolchain_lines(document, measurement, {},
                                            gate.TOOLCHAIN_REMEDY),
                  summaries.toolchain_lines(document, measurement,
                                            {'python': ('a', 'b')},
                                            gate.TOOLCHAIN_REMEDY),
                  summaries.toolchain_lines(document, measurement, {},
                                            gate.THREADS_REMEDY,
                                            subject='excluded threads'),
                  # `refusal_lines` renders the policy module's remedies,
                  # so it lives there rather than beside the summaries.
                  gate.refusal_lines({'over': {'a': (1, 2.0)},
                                      'unmeasured': {'c': 'syscalls'}})):
        assert lines, 'a block that renders to nothing is a block nobody reads'
        assert any(line.strip() for line in lines)
    # The two subjects must not be able to read the same: a summary that
    # said "toolchain changed" for a set of threads would be a reader sent
    # to the wrong remedy.
    moved = summaries.toolchain_lines(document, measurement,
                                      {'mcp-exec': (['front-end-import'], [])},
                                      gate.THREADS_REMEDY,
                                      subject='excluded threads')
    assert 'excluded threads changed, re-baseline' in moved[2], moved[2]
    assert 'toolchain' not in moved[2], moved[2]


def test_a_perf_run_that_printed_no_count_says_so(tmp):
    """perf exits 0 whether or not it counted, so its exit status is not
    evidence — which makes a None count with no reason the shape that has to
    be refused rather than passed on.

    Without the guard a None reached `_row` and raised `TypeError` on
    `None - None`: an abort where this module promises a counter reports
    itself unavailable.
    """
    counters = _journey_contract.counters()
    with planting(counters,
                  _run=lambda argv: (0, '', 'perf: not permitted')):
        value, why = counters._perf('mcp-exec', ROOT, Path(tmp))
    assert value is None, value
    assert why is not None, 'a None with no reason reaches _row and aborts'
    assert why['returncode'] == 0, why
    assert 'perf printed no instruction count' in why['stderr'], why
    # The strace twin refuses in its own words, for the same reason.
    (Path(tmp) / 'strace.mcp-exec.txt').write_text(
        'nothing a total can be read from\n', encoding='utf-8')
    with planting(counters, _run=lambda argv: (0, '', '')):
        value, why = counters._syscalls('mcp-exec', ROOT, tmp)
    assert value is None, value
    assert 'no summary to read a total from' in why['stderr'], why


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
