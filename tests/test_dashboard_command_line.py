#!/usr/bin/env python3
"""The dashboard harness must keep its program off the command line.

The launcher hands node a program the size of the prelude plus the scenario
body. `node --eval SRC` carries all of it in ONE argv element, so the command
line grows with everything the child reads, and Windows caps one command line
at 32,767 characters. Past the cap `CreateProcessW` refuses with
`ERROR_FILENAME_EXCED_RANGE` and Python raises a `FileNotFoundError` naming
the FILENAME, so a suite whose program outgrew the cap fails as though node
were missing.

That failure cannot be reproduced on the machine reading it: `Popen` accepts
an over-cap command line on Linux. So this suite does not assert that a suite
passes -- it builds a program past the cap, measures the command line the
launcher ACTUALLY assembles, and asserts the program's own bytes are not in
it. The measurement is identical on every platform; only the Windows leg is
the end-to-end confirmation, and it was the red one.

It is its own file because the suite that owns the launcher's other
diagnostics had no headroom left, and a size baseline is never raised to make
room.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402

DashboardNodeHarness = _dashnode.DashboardNodeHarness
run_dashboard_node = _dashnode.run_dashboard_node

# The API's cap, not a measurement. The measurements beside it are per case.
WINDOWS_COMMAND_LINE_LIMIT = 32767
# The widest command line the stock launcher built at the head that grew the
# DOM surface, measured by intercepting `Popen` during real suite runs: 33,056
# in the block-rules section suite, 289 past the cap. A control that only
# measured today's size would pass while the next scenario entry went over, so
# the case below grows the program instead of trusting a number.
HEAD_WIDEST_COMMAND_LINE = 33056
# The same measurement at the base of that work: 30,728, 2,039 under the cap.
# The launcher is byte-identical at both refs; the payload grew, which is what
# made the ceiling reachable at all.
BASE_WIDEST_COMMAND_LINE = 30728


def _command_line_length(argv):
    """The length Windows measures: the arguments joined by one space."""
    return sum(len(argument) + 1 for argument in argv)


def _settled_child():
    return Mock(
        stdout='', stderr='',
        communicate=Mock(return_value=('', '')),
        wait=Mock(return_value=0), pid=4242, returncode=0, kill=Mock())


def test_the_program_never_rides_the_command_line(_tmp):
    """Three sizes, one measurement, and no platform in the assertion.

    The sizes are the base ref's widest real command line, the head's (over
    the cap, which is the state this control exists for), and a program grown
    to twice the cap. Each is also assembled the way the launcher used to
    assemble it, so the report says which sizes straddled the cap before the
    file route existed -- and that straddling is asserted, not assumed, so a
    stale number cannot quietly stop meaning what it says.
    """
    del _tmp
    node = shutil.which('node')
    assert node, 'node is required to measure the command line'
    captured = {}

    def launch(command, **_options):
        argv = [str(part) for part in command]
        captured['argv'] = argv
        # Read the program while the launcher still has the file -- it is
        # removed when the attempt returns, so a later read would find
        # nothing and the assertion would pass vacuously. A launcher that put
        # the program on the command line has no file, and the property
        # assertion below is what reports that, not this reader.
        path = Path(argv[1]) if len(argv) > 1 else None
        captured['program'] = (
            path.read_text(encoding='utf-8')
            if path is not None and path.is_file() else '')
        return _settled_child()

    # Every size is measured and every verdict collected before anything is
    # asserted, so a failure names all three rather than only the first.
    verdicts = {}
    for label, size in (('base', BASE_WIDEST_COMMAND_LINE),
                        ('head', HEAD_WIDEST_COMMAND_LINE),
                        ('grown', WINDOWS_COMMAND_LINE_LIMIT * 2)):
        program = 'x' * size
        captured.clear()
        with patch.object(_dashnode.subprocess, 'Popen', side_effect=launch):
            try:
                _dashnode.run_dashboard_node(
                    _dashnode.DashboardNodeHarness(program, 0, module=True))
            except (AssertionError, subprocess.TimeoutExpired):
                # The stub child cannot settle, so the run's own verdict is
                # not what this case reads; the command line it built is.
                pass
        argv = captured['argv']
        # Substring, not list membership: the launcher composes the prelude
        # and the timeout constant ONTO the program, so under `--eval` the
        # element is a superstring of it and `program in argv` would be False
        # for a launcher that had put the program right there.
        inline = _command_line_length(
            [node, '--input-type=module', '--eval', program])
        length = _command_line_length(argv)
        verdicts[label] = {
            'program_rode': any(program in part for part in argv),
            'line': length,
            'under_cap': length < WINDOWS_COMMAND_LINE_LIMIT,
            'file_carried_it': captured['program'].endswith(program),
            'inline_length': inline,
            'inline_under_cap': inline < WINDOWS_COMMAND_LINE_LIMIT,
        }
    assert not [label for label, seen in verdicts.items()
                if seen['program_rode']], (
        'the program rode the command line at: ' + ', '.join(
            f'{label} ({seen["line"]})' for label, seen
            in verdicts.items() if seen['program_rode'])
        + f'; measured {verdicts}')
    assert all(seen['under_cap'] for seen in verdicts.values()), (
        f'a command line is over the cap: {verdicts}')
    assert all(seen['file_carried_it'] for seen in verdicts.values()), (
        f'the file did not carry the program: {verdicts}')
    # The growth the file route removes: the replaced shape fits at the base
    # ref and is over the cap at the head and beyond.
    assert verdicts['base']['inline_under_cap'] is True, verdicts
    assert verdicts['head']['inline_under_cap'] is False, verdicts
    assert verdicts['grown']['inline_under_cap'] is False, verdicts


# A child that prints two of its own arguments and settles one bounded step,
# so the case reads real `process.argv` from a real launch.
_ARGV_PROBE = r"""
const show = (n) => process.stdout.write(n + ':' + process.argv[n] + '\n');
show(1);
show(2);
show(3);
"""


def test_the_child_still_sees_the_callers_arguments_at_their_own_indices(
        _tmp):
    """The file route puts a path where the program used to be.

    Under `node --eval SRC ARG` there is no script filename, so the caller's
    first argument lands at `process.argv[1]`. Under `node PROGRAM ARG` the
    program occupies index 1 and everything after it shifts up, so a launcher
    that did not drop that slot would move every reader at once. The
    prologue `process.argv.splice(1, 1)` is what restores the old shape, and
    this holds it against a real child: `_dashshell.py` and
    `_dashsection_transport.py` read `process.argv[at + 1]` for the module a
    caller declared, and the suites that call them read `[1]`, `[2]` and
    `[3]`. On Linux a wrong shift reads as a path that does not exist, so
    this would be caught here too -- but it is the case that says so.
    """
    del _tmp
    node = shutil.which('node')
    assert node, 'node is required to read a child argv'
    with tempfile.TemporaryDirectory() as directory:
        one = Path(directory) / 'one'
        two = Path(directory) / 'two'
        three = Path(directory) / 'three'
        for path in (one, two, three):
            path.write_text('export default 1;\n', encoding='utf-8')
        result = run_dashboard_node(DashboardNodeHarness(
            _ARGV_PROBE, 0, module=True, arguments=(one, two, three)))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert result.stdout == f'1:{one}\n2:{two}\n3:{three}\n', result


def _launch_records(run, *, returncode=0):
    """Run `run()` with `Popen` stubbed, recording what was written.

    Each record is `{'argv', 'program', 'directory'}`. The program is read
    INSIDE the launch, because the launcher removes the file when the attempt
    returns -- a test that read it afterwards would find nothing and could
    conclude anything from that. A launch the stub cannot serve is swallowed
    so a caller gets its records whether or not the run's own verdict passed.
    """
    records = []

    def launch(command, **_options):
        argv = [str(part) for part in command]
        path = Path(argv[1]) if len(argv) > 1 else None
        records.append({
            'argv': argv,
            'program': (path.read_text(encoding='utf-8')
                        if path is not None and path.is_file() else ''),
            'directory': path.parent if path is not None else None,
        })
        child = _settled_child()
        child.returncode = returncode
        return child

    with patch.object(_dashnode.subprocess, 'Popen', side_effect=launch):
        try:
            run()
        except (AssertionError, subprocess.TimeoutExpired):
            pass
    return records


# The per-attempt budget the child is handed, read out of what was written.
_STEP_BUDGET = re.compile(r'const _dashnodeStepTimeoutMs = (\d+);')


def _step_budget_ms(program):
    """The step timeout the program declares, in milliseconds."""
    found = _STEP_BUDGET.search(program)
    assert found, f'the program declares no step budget: {program[-200:]}'
    return int(found.group(1))


def _one_launch(attempt, *, module=True, returncode=0, bounded_steps=0):
    return _launch_records(
        lambda: _dashnode._run_dashboard_node_once(
            _dashnode.DashboardNodeHarness(
                'x' * 64, bounded_steps, module=module),
            attempt=attempt),
        returncode=returncode)


def test_the_childs_step_budget_escalates_with_the_attempt(_tmp):
    """The budget the CHILD is handed must scale with the attempt.

    This is a witness that was deleted and replaced by a comment, and the
    comment was not even true. The deleted pair read the program out of argv;
    the comment claimed the `timeout_s` pair "can only come from 5 s then
    10 s", but `timeout_s` is `attempt * (step + grace)` and that map is not
    injective in `step` -- 3 and 7 sum to the same 10. So nothing held the
    child-side value at all, and a launcher that stopped escalating left
    every suite green, the way a launcher past the Windows cap leaves them
    green on Linux.

    So the value is read out of what the launcher WROTE, and the property
    held is the one the deleted pair stood in for: the child's own budget
    doubles with the attempt. No unrelated step/grace split can satisfy that,
    and a launcher that held the step and grew the grace cannot either.
    """
    del _tmp
    first = _one_launch(1)
    second = _one_launch(2)
    assert len(first) == 1 and len(second) == 1, (first, second)
    one = _step_budget_ms(first[0]['program'])
    two = _step_budget_ms(second[0]['program'])
    assert one > 0, first[0]['program'][-200:]
    assert two == 2 * one, (
        f'the child budget did not double with the attempt: '
        f'attempt 1 {one} ms, attempt 2 {two} ms')


def test_the_scratch_directory_is_removed_after_every_launch(_tmp):
    """Nothing else holds the program once the child is gone, so a launcher
    that stopped removing its scratch tree would leak a directory per launch
    and nothing would say so: the failure this commit exists to prevent is
    invisible on the machine reading it, and a teardown nothing holds is the
    same class of that.

    Both paths are checked, because they are different code: a child that
    exits cleanly, and a child that fails and takes the launcher's
    `AssertionError` out through the same `finally`.
    """
    del _tmp
    clean = _one_launch(1)
    assert clean, 'no launch was recorded'
    for record in clean:
        assert not record['directory'].exists(), (
            f'the scratch tree survived a clean launch: '
            f'{record["directory"]}')
    failed = _one_launch(1, returncode=1)
    assert failed, 'no launch was recorded'
    for record in failed:
        assert not record['directory'].exists(), (
            f'the scratch tree survived a failed launch: '
            f'{record["directory"]}')


def test_the_extension_carries_the_module_type(_tmp):
    """`--input-type=module` may not be combined with a file, so the module
    harness is marked by its extension, and the extension is load-bearing
    rather than conventional.

    CI declares `node-version: "22"`, and Node 22.7 and later auto-detect
    module syntax in a `.js` file -- so renaming `.mjs` to `.js` is green on
    every platform this repository runs, and only an older runtime would see
    the difference, where a module harness would be parsed as CommonJS. That
    is the whole argument for pinning it: a tidy-up would be invisible here
    and wrong in the field.
    """
    del _tmp
    for module, expected in ((True, '.mjs'), (False, '.cjs')):
        records = _one_launch(1, module=module)
        assert len(records) == 1, (module, records)
        name = Path(records[0]['argv'][1]).name
        assert name.endswith(expected), (
            f'a module={module} harness was written as {name}, '
            f'expected {expected}')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashcmdline_')


if __name__ == '__main__':
    raise SystemExit(main())
