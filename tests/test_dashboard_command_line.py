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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashcmdline_')


if __name__ == '__main__':
    raise SystemExit(main())
