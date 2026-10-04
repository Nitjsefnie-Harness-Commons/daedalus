#!/usr/bin/env python3
"""Contracts for the per-thread rows a journey measurement carries.

The journey budget records ONE instruction count per journey, and two runs
of unchanged code have produced counts about seven percent apart. The
measurement already reads every thread's own total out of the callgrind
out-files and then sums them, so the data exists and is thrown away; these
rows are what it read, kept so a later run can say WHICH THREAD carries the
difference.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
import _journey_profile_fixture  # noqa: E402
from _journey_contract import (  # noqa: E402
    ROOT,
    _util,
    counter_facts,
    environment,
    journeys,
    planting,
)

# The name is spelled HERE rather than read out of the module that owns it,
# because a control that read the name would pin only that the two agree —
# which they would by construction — and a rename in either subject is a red.
FLAG = 'DAEDALUS_JOURNEY_THREAD_ROWS'

ROWS_SOURCE = ROOT / 'scripts' / 'ci' / 'journey_thread_rows.py'


def rows_module():
    """The rows module, loaded by path like every other CI module here."""
    return _util.load(ROWS_SOURCE, 'journey_thread_rows_contract')


FUNCTION_FLAG = 'DAEDALUS_JOURNEY_FUNCTION_ROWS'

FUNCTIONS_SOURCE = ROOT / 'scripts' / 'ci' / 'journey_function_rows.py'


def functions_module():
    """The function-rows module, loaded by path like its neighbour."""
    return _util.load(FUNCTIONS_SOURCE, 'journey_function_rows_contract')


def _profile_file(directory, name, text):
    """One out-file with the text given, written where a round leaves one."""
    path = Path(directory) / name
    path.write_text(text, encoding='utf-8')
    return path


def _profile(main, imported=3_000, served=5_000, request=2_000):
    """One profile's rows, through the shared two-process fixture."""
    return _journey_contract.bridge_profile(
        main=main, request=request, imported=imported, served=served)


def _measured(rows):
    return {'rows': rows, 'unread': None}


def _stubbed(main_by_journey, startup=7_000, bridge=100_000):
    """A callgrind stand-in that hands back a profile for every journey."""
    def counting(name, root, workdir):
        del root, workdir
        if name == 'startup-only':
            return _measured(_profile(startup, imported=0, served=0,
                                      request=0)), None
        if name == 'bridge-only':
            return _measured(_profile(bridge)), None
        return _measured(_profile(main_by_journey[name])), None
    return counting


def _even_counts(value=200_000):
    return {name: value for name in journeys().NAMES}


def _run_measure(value, counting, rounds=1, shapes=None, flag=FLAG,
                 counter='valgrind-callgrind'):
    """One `measure()` over a stubbed counter, with the flag as `value`.

    `value` of `None` removes the variable, which is a state a setting has
    and a dict that only ever assigns cannot express.
    """
    counters = _journey_contract.counters()
    shape = shapes or (lambda names, root, r: (
        {name: ['sha'] * r for name in names}, None))
    with environment(flag, value), planting(
            counters,
            shapes=shape,
            COUNTERS=(counter,),
            COUNTERS_BY_NAME={counter: (counting, True)}):
        return counters.measure(root=ROOT, rounds=rounds,
                                found=counter_facts())


def test_a_measurement_the_classifier_refuses_carries_no_rows(tmp):
    """No rows rather than rows with a guessed role."""
    del tmp
    rows = rows_module()
    floor = _journey_contract.threads().REQUEST_FROM
    thin = _profile(100_000)
    thin[0] = dict(thin[0], ir=floor - 1)
    kept, why = rows.kept_rows(_measured(thin), 'mcp-exec')
    assert kept is None, kept
    assert why is not None and str(floor - 1) in why, why

    # And a journey whose samples cannot all be read carries NO entry
    # rather than one of them: a `rounds` list shorter than the run is a
    # reader comparing the wrong two samples.
    whole, round_why = rows.journey_rows(
        'mcp-exec', _measured(_profile(100_000)),
        [_measured(_profile(200_000)), _measured(thin)])
    assert whole is None, whole
    assert round_why is not None, round_why


def test_a_baseline_the_classifier_refuses_carries_no_entry(tmp):
    """The BASELINE's refusal drops the journey, exactly as a round's does.

    A guard that fires for one of the two and not the other hands back an
    entry whose `baseline` is null -- a half-measured journey presented as
    a whole one.
    """
    del tmp
    rows = rows_module()
    names = journeys().NAMES
    floor = _journey_contract.threads().REQUEST_FROM
    sample = _measured(_profile(200_000))
    thin = _profile(100_000)
    thin[0] = dict(thin[0], ir=floor - 1)

    samples = {name: [sample] for name in names[:2]}
    with environment(FLAG, '1'):
        readable = rows.for_counter(_measured(_profile(100_000)), samples)
        entries = rows.for_counter(_measured(thin), samples)
    assert readable is not None and set(readable) == set(samples), (
        'the fixture must reach the rows for the refusal to mean anything: '
        f'{readable}')
    assert entries is None, (
        f'a baseline the classifier refused drops the entry whole: {entries}')


def test_a_counter_that_hands_back_a_number_separates_no_threads(tmp):
    """`syscalls` (and perf) report one number and get no rows.

    A counter that counts a process tree whole hands back a number, and
    that number IS the count the report carries: the arm that hands it back
    reads it rather than building one out of it.
    """
    del tmp
    counters = _journey_contract.counters()
    ran = {}

    def answering(name, root, workdir):
        del root, workdir
        # A count that rises, because both fixed backgrounds come off the
        # top of it, so every journey nets a positive residual.
        ran[name] = 1000 * len(ran) + 1000
        return ran[name], None

    def shape(names, root, rounds):
        del root
        return {name: ['sha'] * rounds for name in names}, None

    with environment(FLAG, '1'), planting(
            counters,
            shapes=shape,
            COUNTERS=('syscalls',),
            COUNTERS_BY_NAME={'syscalls': (answering, True)}):
        report = counters.measure(root=ROOT, rounds=1,
                                  found=counter_facts())
    assert ran, ran
    row = report['counters']['syscalls']
    assert row['available'] is True, row
    assert 'thread_rows' not in row, row
    assert 'function_rows' not in row, row
    assert row['startup_only'] == ran['startup-only'], row
    assert set(row['bridge_only'].values()) == {ran['bridge-only']}, row


def test_the_rows_change_no_recorded_count(tmp):
    """The assertion that says the instrumentation measured nothing:
    the reports must be the same document, byte for byte."""
    del tmp
    counting = _stubbed(_even_counts())
    without = copy.deepcopy(_run_measure(None, counting))
    with_rows = copy.deepcopy(_run_measure('1', counting))
    recorded = with_rows['counters']['valgrind-callgrind']
    assert recorded.pop('thread_rows'), 'this run recorded no rows'
    assert 'function_rows' not in recorded, (
        'the thread switch turns no function rows on')
    assert json.dumps(with_rows, sort_keys=True) == json.dumps(
        without, sort_keys=True), 'the rows moved a recorded number'


def test_the_function_switch_is_the_exact_string_one(tmp):
    """Only the exact string `1` turns the rows on, so a workflow that
    sets the variable to `0` turns them OFF rather than on."""
    del tmp
    functions = functions_module()
    with environment(FUNCTION_FLAG, '1'):
        assert functions.enabled() is True
    for value in ('0', 'true', ' 1', ''):
        with environment(FUNCTION_FLAG, value):
            assert functions.enabled() is False, value
    with environment(FUNCTION_FLAG, None):
        assert functions.enabled() is False


def test_self_totals_sum_to_the_summary_and_calls_cost_lands_nowhere(tmp):
    """THE numeric control: over files that carry cost lines, the self
    totals of the declared functions sum to the files' own `summary:`
    values -- every cost line accounted -- and the inclusive cost a
    `calls=` line records at a call site lands in NO self total. The cost
    lines after that arc are the caller's self again, no new `fn=` line
    announcing them; the format's compressed bare `fn=(id)` re-selects
    the function the id was declared for, and an id the file never
    declared leaves the file out whole rather than bill its costs to a
    neighbour. The torn companion is the count reader's refusal shape,
    contributing no row and no failure."""
    functions = functions_module()
    _profile_file(tmp, 'callgrind.x.torn', (
        'pid: 5\nthread: 1\ncmd: python3 x.py\nfn=(1) carried\n1 9\n'))
    _profile_file(tmp, 'callgrind.x.1', (
        'version: 1\npid: 11\npart: 1\nthread: 1\ncmd: python3 x.py\n'
        'positions: line\nevents: Ir\n'
        'fn=(1) caller\n5 30\n9 20\n'
        'cfn=(2) callee\ncalls=1 0\n5 100\n'
        '15 40\n'
        'fn=(3) other\n2 7\n'
        'summary: 97\n'))
    _profile_file(tmp, 'callgrind.x.2', (
        'version: 1\npid: 12\npart: 1\nthread: 1\ncmd: python3 x.py\n'
        'positions: line\nevents: Ir\n'
        'fn=(1) alpha\n5 30\n'
        'fn=(2) gamma\n7 10\n'
        'fn=(1)\n5 40\n'
        'cfn=(2)\ncalls=1 0\n* 25\n'
        '17 6\n'
        'summary: 86\n'))
    _profile_file(tmp, 'callgrind.x.3', (
        'version: 1\npid: 13\npart: 1\nthread: 1\ncmd: python3 x.py\n'
        'positions: line\nevents: Ir\n'
        'fn=(1) alpha\n5 30\n'
        'fn=(9)\n5 40\n'
        'summary: 70\n'))
    rows = functions.read(Path(tmp), 'callgrind.x')
    assert [r['pid'] for r in rows] == [11, 12], rows
    row = rows[0]
    assert row['pid'] == 11 and row['thread'] == 1 and (
        row['cmd'] == 'python3 x.py'), row
    assert row['functions'] == [
        {'fn': 'caller', 'ir': 90},
        {'fn': 'other', 'ir': 7},
        {'fn': 'callee', 'ir': 0}], row
    assert sum(f['ir'] for f in row['functions']) == 97, row
    mixed = rows[1]
    assert mixed['functions'] == [
        {'fn': 'alpha', 'ir': 76},
        {'fn': 'gamma', 'ir': 10}], mixed
    assert sum(f['ir'] for f in mixed['functions']) == 86, mixed


def test_every_preserved_profile_parses_whole(tmp):
    """The control over the REAL profiles, all three preserved runs. The
    excerpts carry no cost lines -- no line of the 27 files starts with a
    digit, measured -- so the numeric control is the synthetic one beside
    it, and the whole-file claim here is per file: the same slots the
    count reader accepts, the same pid/thread/cmd, the same declared set."""
    del tmp
    functions = functions_module()
    threads = _journey_contract.threads()
    prefix = _journey_profile_fixture.PREFIX
    for journey, directory in _journey_profile_fixture.runs().items():
        mine = functions.read(directory, prefix)
        theirs, unread = threads.read(directory, prefix)
        assert unread is None, unread
        assert len(mine) == len(theirs), journey
        slots = {(row['pid'], row['thread']): row for row in mine}
        for row in theirs:
            mine_row = slots[(row['pid'], row['thread'])]
            assert mine_row['cmd'] == row['cmd'], (journey, row['pid'])
            assert ({f['fn'] for f in mine_row['functions']}
                    == row['names']), (journey, row['pid'])


def test_the_breakdown_is_a_cap_not_a_partition(tmp):
    """`functions` keeps the top 64 by self total; the cap claims nothing
    about what it left out, and ties order by name, so the names are read
    in name order whatever order the file declared them in."""
    functions = functions_module()
    _profile_file(tmp, 'callgrind.c.1', (
        'pid: 3\nthread: 1\ncmd: c\npositions: line\nevents: Ir\n'
        + ''.join(f'fn=({n}) fun{n}\n{n + 1} {70 - n}\n'
                  for n in range(70))
        + 'summary: 2485\n'))
    _profile_file(tmp, 'callgrind.c.2', (
        'pid: 4\nthread: 1\ncmd: c\npositions: line\nevents: Ir\n'
        + ''.join(f'fn=({n}) tie{(n + 7) % 66:02d}\n1 7\n'
                  for n in range(66))
        + 'summary: 462\n'))
    rows = functions.read(Path(tmp), 'callgrind.c')
    kept = next(r for r in rows if r['pid'] == 3)['functions']
    assert len(kept) == 64, len(kept)
    irs = [f['ir'] for f in kept]
    assert irs == list(range(70, 6, -1)), irs
    assert kept[0]['fn'] == 'fun0', kept[0]
    assert kept[-1]['fn'] == 'fun63', kept[-1]
    # 66 functions of one self total, declared out of name order: the cap
    # keeps the first 64 NAMES, not the first 64 the file happened to
    # declare, which is what makes two rounds diff-able.
    tied = next(r for r in rows if r['pid'] == 4)
    assert [f['fn'] for f in tied['functions']] == [
        f'tie{n:02d}' for n in range(64)], tied


def _writing(counting):
    """A counter stub that also leaves the out-file a callgrind round
    would, in the workdir the wiring reads at the round's end."""
    def writing(name, root, workdir):
        measured, why = counting(name, root, workdir)
        if name not in ('startup-only', 'bridge-only'):
            _profile_file(workdir, f'callgrind.{name}.1', (
                'pid: 7\nthread: 1\ncmd: python3 x.py\n'
                'positions: line\nevents: Ir\n'
                'fn=(1) hot\n1 123\nsummary: 123\n'))
        return measured, why
    return writing


def test_function_rows_are_wired_beside_the_count_only_when_on(tmp):
    """`function_rows` sits under the counter entry beside `thread_rows`,
    and only its own switch puts it there. A counter that counts the tree
    whole -- `syscalls` -- leaves the same stale callgrind files in the
    shared workdir and still carries no function rows: the breakdown is
    the profile counter's."""
    writing = _writing(_stubbed(_even_counts()))
    with environment(FLAG, '1'):
        entry = _run_measure(None, writing, flag=FUNCTION_FLAG)[
            'counters']['valgrind-callgrind']
    assert 'function_rows' not in entry, entry
    assert 'thread_rows' in entry, entry
    entry = _run_measure('1', writing, flag=FUNCTION_FLAG)[
        'counters']['valgrind-callgrind']
    assert 'thread_rows' not in entry, entry
    assert set(entry['function_rows']) == set(_even_counts()), entry
    assert entry['function_rows']['mcp-exec']['rounds'][0] == [
        {'pid': 7, 'thread': 1, 'cmd': 'python3 x.py',
         'functions': [{'fn': 'hot', 'ir': 123}]}], entry

    def numbering(name, root, workdir):
        del root, workdir
        return 100_000 + len(name), None

    entry = _run_measure('1', _writing(numbering), flag=FUNCTION_FLAG,
                         counter='syscalls')['counters']['syscalls']
    assert 'function_rows' not in entry, entry
    with environment(FLAG, '1'):
        entry = _run_measure('1', writing, flag=FUNCTION_FLAG)[
            'counters']['valgrind-callgrind']
    assert 'thread_rows' in entry and 'function_rows' in entry, entry


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeythreadrows_')


if __name__ == '__main__':
    raise SystemExit(main())
