#!/usr/bin/env python3
"""Contracts for how a journey is COUNTED: which counter this runner
allows, what a measurement carries, and every way a measurement can
refuse rather than answer."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ROOT,
    _util,
    counter_facts,
    journeys, launch_refusal, planting,
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
    record = 'noise\n' + counters.MARKER + '{"journey": "x", "sha256": "y"}\n'
    assert counters.journey_record(record) == {'journey': 'x', 'sha256': 'y'}
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
    # unavailable without being run; this is about what happens after.
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

    perf exits 0 whether or not it counted, so its text is the only evidence.
    """
    del tmp
    counters = _journey_contract.counters()
    machine = '1234567000123,,instructions:u,1000000,100.00,\n'
    assert counters._perf_instruction_count(machine) == 1234567000123
    text = '  1,234,567,000      instructions:u      99.99      '
    assert counters._perf_instruction_count(text) == 1234567000
    assert counters._perf_instruction_count('not permitted\n') is None
    assert counters._perf_instruction_count('') is None


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


# ─── the probe, and the children every counter launches ──────────────────


def _toolbox(**present):
    """`shutil` as the probe finds it: the named tools, and nothing else."""
    return SimpleNamespace(which=present.get)


def _profile(directory, name, slot, thread, ir, signature=()):
    return _journey_contract.callgrind_profile(
        directory, name, slot, thread, ir, signature)


def test_a_child_that_cannot_start_is_an_answer_not_an_exception(tmp):
    """A launch that never happened is data, not an exception: a `perf`
    missing from a runner's PATH is a state the counters report.

    The refusal is the platform's own, so it is compared with the platform's
    own answer for the same argv; a Windows refusal names no program at all.
    """
    del tmp
    counters = _journey_contract.counters()
    code, out, err = counters._run(
        [sys.executable, '-c', 'import sys; sys.stderr.write("e")'])
    assert (code, out, err) == (0, '', 'e'), (code, out, err)
    code, _out, _err = counters._run(
        [sys.executable, '-c', 'raise SystemExit(3)'])
    assert code == 3, ('the returncode every counter refuses on must be '
                       "the child's own, not a normalised zero")
    absent = str(ROOT / 'tests' / 'no-counter-binary-here')
    code, out, err = counters._run([absent])
    assert code is None, code
    assert out == '', out
    assert err == launch_refusal([absent]), (
        'a launch that never happened must report the refusal the platform '
        f'gave it verbatim, not a normalised one: {err!r}')


def test_the_probe_says_what_each_tool_is_and_which_counter_gates(tmp):
    """Every tool the probe finds is measured, never assumed, and each is
    asked with the arguments that decide what it can do."""
    del tmp
    counters = _journey_contract.counters()
    asked = []

    def answering(argv):
        asked.append(list(argv))
        if argv[1] == 'stat':
            return 0, '', '7,,instructions:u,4242,100.00,\n'
        if argv[1] == '--version':
            return 0, 'valgrind-3.24.0\n', ''
        return 0, '', ''

    with planting(counters,
                  shutil=_toolbox(perf='/usr/bin/perf',
                                  valgrind='/usr/bin/valgrind',
                                  strace='/usr/bin/strace'),
                  _run=answering):
        found = counters.facts()
    assert found['perf_path'] == '/usr/bin/perf', found
    assert found['perf_stat'] == {
        'event': 'instructions:u', 'returncode': 0, 'counts': True,
        'stderr': '7,,instructions:u,4242,100.00,'}, found['perf_stat']
    assert found['valgrind_path'] == '/usr/bin/valgrind', found
    assert found['valgrind_version'] == 'valgrind-3.24.0', found
    assert found['strace_path'] == '/usr/bin/strace', found
    assert found['strace_usable'] is True, found
    assert found['python'] == sys.version, found
    assert found['selected'] == 'perf-instructions', found
    assert [argv[1] for argv in asked] == ['stat', '--version', '-c'], asked


def test_a_probe_that_finds_no_tool_gates_on_nothing_and_says_so(tmp):
    """Each absent tool is reported absent in its own field rather than
    defaulted to something that measures, and a runner with none of the
    three has no gate candidate — a state the job still passes in."""
    del tmp
    counters = _journey_contract.counters()
    asked = []

    def launched(argv):
        asked.append(argv)

    with planting(counters, shutil=_toolbox(), _run=launched):
        found = counters.facts()
    assert asked == [], 'a tool the probe never found was launched anyway'
    assert found['perf_path'] is None, found
    assert found['perf_stat'] is None, found
    assert found['valgrind_path'] is None, found
    assert found['valgrind_version'] is None, found
    assert found['strace_path'] is None, found
    assert found['strace_usable'] is False, found
    assert found['selected'] is None, found
    assert isinstance(found['perf_event_paranoid'], (int, type(None))), found


def test_every_probe_answer_is_read_from_what_the_tool_printed(tmp):
    """perf exits 0 whether or not it counted, so its own stderr is what
    is read; a silent valgrind and a failed strace are each reported as
    what the tool did, not as what the probe hoped for."""
    del tmp
    counters = _journey_contract.counters()

    def answering(argv):
        if argv[1] == '-c':
            return 1, '', 'strace: -c failed\n'
        return 0, '\n', 'not permitted\n'

    with planting(counters,
                  shutil=_toolbox(perf='/usr/bin/perf',
                                  valgrind='/usr/bin/valgrind',
                                  strace='/usr/bin/strace'),
                  _run=answering):
        found = counters.facts()
    assert found['perf_stat']['returncode'] == 0, found['perf_stat']
    assert found['perf_stat']['counts'] is False, (
        'perf reported no count and the probe still counted with it')
    assert 'not permitted' in found['perf_stat']['stderr'], found['perf_stat']
    assert found['valgrind_version'] is None, (
        'a valgrind that printed no version is no version, not an empty one')
    assert found['strace_usable'] is False, found
    assert found['selected'] == 'valgrind-callgrind', found


def test_the_shape_is_a_plain_run_of_each_journey(tmp):
    """The shape is settled in the cheapest child there is, so every
    journey is run plainly and the record is read from wherever in the
    stream it landed. A journey that printed none is refused by name: a
    shape nobody can read is not a count of zero."""
    del tmp
    counters = _journey_contract.counters()
    names = journeys().NAMES
    shas = {name: name[::-1] * 4 for name in names}
    asked = []

    def answering(argv):
        asked.append(list(argv))
        name = argv[argv.index('--journey') + 1]
        return 0, (f'the bridge said something\n{counters.MARKER}'
                   f'{{"sha256": "{shas[name]}"}}\n'), ''

    with planting(counters, _run=answering):
        seen, failure = counters.shapes(names, ROOT, 2)
    assert failure is None, failure
    assert seen == {name: [shas[name], shas[name]] for name in names}, seen
    assert len(asked) == 2 * len(names), asked
    assert all(Path(argv[1]).name == '_journeys.py' for argv in asked), asked
    assert all(argv[-1] == str(ROOT) for argv in asked), asked
    loud = 'the bridge refused, and said so at length: ' + 'x' * 500
    with planting(counters, _run=lambda argv: (1, '', loud)):
        seen, failure = counters.shapes(names, ROOT, 1)
    assert seen is None, seen
    assert failure == (f'the {names[0]} journey printed no record '
                       f'(returncode 1): {loud[-400:]}'), (
        'a refusal must name the journey and carry the tail of what it '
        f'said: {failure}')


def test_a_callgrind_round_counts_the_last_one_not_the_sum_of_them(tmp):
    """Every round writes into the same workdir under the same prefix, so
    a round that did not clear the previous round's files would report a
    count that grows with the round number.

    The out-file argument is asserted whole and built from the workdir this
    test hands the module, which is how the module builds it: a `Path`
    interpolated plainly, so the platform spells the separator.
    """
    counters = _journey_contract.counters()
    threads = _journey_contract.threads()
    _profile(tmp, 'mcp-exec', 9, 1, 5_000_000)
    asked = []

    def answering(argv):
        asked.append(list(argv))
        _profile(tmp, 'mcp-exec', 0, 1, 9_000)
        _profile(tmp, 'mcp-exec', 1, 2, 2_000_000_000,
                 threads.SIGNATURES[threads.IMPORT])
        return 0, '', ''

    with planting(counters, shutil=_toolbox(valgrind='/usr/bin/valgrind'),
                  _run=answering):
        kept, why = counters._callgrind('mcp-exec', ROOT, tmp)
    assert why is None, why
    assert kept == 9_000, f'the previous round was summed in: {kept}'
    assert asked[0][:2] == ['/usr/bin/valgrind', '--tool=callgrind'], asked
    tail = Path(tmp) / 'callgrind.mcp-exec.%p'
    out_file = next(part for part in asked[0] if '--callgrind-out-file='
                    in part)
    assert out_file == f'--callgrind-out-file={tail}', out_file


def test_a_callgrind_child_that_failed_is_reported_in_its_own_words(tmp):
    """A counter that will not run reports the returncode and what the
    tool said, because a refusal naming nothing is one a maintainer
    cannot act on."""
    counters = _journey_contract.counters()
    loud = 'valgrind could not start, and said so at length: ' + 'y' * 500
    with planting(counters, shutil=_toolbox(valgrind='/usr/bin/valgrind'),
                  _run=lambda argv: (255, '', loud)):
        kept, why = counters._callgrind('mcp-exec', ROOT, tmp)
    assert kept is None, kept
    assert why == {'returncode': 255, 'stderr': loud[-400:]}, why


def test_a_profile_the_gate_cannot_read_comes_back_as_a_sentence(tmp):
    """The two refusals are told apart by their shape: a child that would
    not run carries a returncode, and a profile this gate cannot read
    carries a sentence, because there is no returncode to report."""
    counters = _journey_contract.counters()
    _profile(tmp, 'mcp-exec', 0, 1, 900)
    with planting(counters, shutil=_toolbox(valgrind='/usr/bin/valgrind'),
                  _run=lambda argv: (0, '', '')):
        kept, why = counters._callgrind('mcp-exec', ROOT, tmp)
    assert kept is None, kept
    assert isinstance(why, str), why
    assert "'front-end-import'" in why, (
        'the sentence must name the role the profile has no thread for: '
        f'{why}')


def test_a_perf_run_that_failed_and_one_that_counted_are_told_apart(tmp):
    """`-x,` is what makes perf's output machine-readable, so the run
    that counted is read out of that line rather than out of a status."""
    counters = _journey_contract.counters()
    asked = []

    def failed(argv):
        asked.append(list(argv))
        return 255, '', 'perf: no permission'

    def counted(argv):
        asked.append(list(argv))
        return 0, '', '4242,,instructions:u,100.00,\n'

    with planting(counters, shutil=_toolbox(perf='/usr/bin/perf'),
                  _run=failed):
        value, why = counters._perf('mcp-exec', ROOT, Path(tmp))
    assert value is None, value
    assert why == {'returncode': 255, 'stderr': 'perf: no permission'}, why
    assert asked[0][:6] == ['/usr/bin/perf', 'stat', '-e', 'instructions:u',
                            '-x,', '--'], asked
    with planting(counters, shutil=_toolbox(perf='/usr/bin/perf'),
                  _run=counted):
        value, why = counters._perf('mcp-exec', ROOT, Path(tmp))
    assert (value, why) == (4242, None), (value, why)


def test_a_strace_run_that_wrote_no_readable_summary_is_refused(tmp):
    """Three refusals, each naming what was wrong: the tool would not
    run, the summary cannot be read, and no total in it. The middle one is
    a directory, which is the one a root run can reach too."""
    counters = _journey_contract.counters()
    asked = []

    def failed(argv):
        asked.append(list(argv))
        return 1, '', 'strace: cannot start'

    with planting(counters, shutil=_toolbox(strace='/usr/bin/strace'),
                  _run=failed):
        value, why = counters._syscalls('mcp-exec', ROOT, tmp)
    assert value is None, value
    assert why == {'returncode': 1, 'stderr': 'strace: cannot start'}, why
    assert '-o' in asked[0] and '--' in asked[0], asked[0]
    summary = Path(tmp) / 'strace.mcp-exec.txt'
    summary.mkdir()
    with planting(counters, shutil=_toolbox(strace='/usr/bin/strace'),
                  _run=lambda argv: (0, '', '')):
        value, why = counters._syscalls('mcp-exec', ROOT, tmp)
    assert value is None, value
    assert why['returncode'] == 0, why
    assert 'strace.mcp-exec.txt' in why['stderr'], why
    summary.rmdir()
    summary.write_text(
        ' 0.00    0.000000           0        12       0 futex\n'
        ' 0.00    0.000123          61        2       1 poll\n'
        '100.00    0.000123          54        14      1 total\n',
        encoding='utf-8')
    with planting(counters, shutil=_toolbox(strace='/usr/bin/strace'),
                  _run=lambda argv: (0, '', '')):
        value, why = counters._syscalls('mcp-exec', ROOT, tmp)
    assert (value, why) == (14, None), (value, why)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
