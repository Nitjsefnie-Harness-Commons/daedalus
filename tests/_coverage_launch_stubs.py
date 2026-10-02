"""Stand-ins for `scripts/ci/coverage_suites.py`'s launch boundary.

The end-to-end controls in `test_coverage_suites.py` copy the runner into a
fabricated tree and run it there, so what they execute is recorded against a
temporary path that dies with the tree -- the runner's own lines are measured
only where something runs it in this checkout. `run_main()` runs it here, and
these stand-ins stand in for the one boundary that cannot be crossed
in-process without: the launch.

Every launch RECORDS what it was handed -- the argv, the cwd, the output
path, the bound, and the platform it was called under -- because the record
is the evidence, and the string the runner then prints is its own account of
the same event. Nothing here starts a process and nothing waits on a clock:
a suite that overran its bound is stood in for by a stand-in that reports
one, never by one that takes the time.
"""
import contextlib
import io
import sys
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# The launcher binds `suite_bound` by the FLAT name, because it is run by
# path, so its own directory is the only import that resolves there. Putting
# that directory on the path is this helper's business, not each control's.
sys.path.insert(0, str(_util.ROOT / 'scripts' / 'ci'))

RUNNER = Path('scripts') / 'ci' / 'coverage_suites.py'
SUITE = 'test_alpha.py'
# What a kill of the suite's tree leaves behind, in the launcher's words.
KILLED = 'the tree was asked to stop and it stopped'
MEASURED = 'measured\n'


class Launches:
    """`launch_suite`'s shape, recording each call instead of running one.

    `scripts` is keyed by suite stem, the name the runner derives the output
    path from. A stem it does not name writes `output` and passes. `error`
    raises out of the launch instead, which is the one failure the runner
    reports as a suite it could not even start.

    The record names the directory `launched_from` rather than repeating the
    launcher's own `cwd` parameter: this call DOCUMENTS what a launch was
    handed, and nothing here starts a process.
    """

    def __init__(self, scripts=None, output=MEASURED):
        self.scripts = scripts or {}
        self.output = output
        self.calls = []

    def __call__(self, argv, *, cwd, output_path, timeout):
        target = Path(output_path)
        self.calls.append(SimpleNamespace(
            suite=target.stem, argv=list(argv), launched_from=cwd,
            timeout=timeout, platform=sys.platform))
        script = self.scripts.get(target.stem, {})
        if 'error' in script:
            raise script['error']
        target.write_text(script.get('output', self.output),
                          encoding='utf-8')
        return script.get('result', (0, ''))


def run_main(tmp, *, scripts=None, platform=None, argv=(),
             suites=(SUITE,), output=MEASURED):
    """Run the runner's `main()` here, over a tree holding `suites`.

    `platform` is what `sys.platform` reads as for the length of the call,
    and it is the only thing that selects the Windows half of the timeout
    sentence. Setting it proves the SELECTION -- that the value the runner
    read is the one that chose the sentence; what a Windows run does at its
    own bound stays proven by the windows-latest cells, which run the real
    launcher on that host.
    """
    policy = _util.load(_util.ROOT / RUNNER, 'coverage_suites_in_process')
    root = Path(tmp) / 'tree'
    (root / 'tests').mkdir(parents=True, exist_ok=True)
    for name in suites:
        (root / 'tests' / name).write_text("print('measured')\n",
                                           encoding='utf-8')
    setattr(policy, 'ROOT', root)
    launch = Launches(scripts, output)
    setattr(policy, 'launch_suite', launch)
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        if platform is not None:
            stack.enter_context(unittest.mock.patch.object(
                sys, 'platform', platform))
        stack.enter_context(contextlib.redirect_stdout(stdout))
        stack.enter_context(contextlib.redirect_stderr(stderr))
        status = policy.main(list(argv))
    return SimpleNamespace(status=status, stdout=stdout.getvalue(),
                           stderr=stderr.getvalue(), launch=launch)


def group(outcome, name=SUITE):
    """The block the runner printed for `name`, group markers included."""
    start = outcome.stdout.find(f'::group::tests/{name}\n')
    assert start >= 0, outcome.stdout
    end = outcome.stdout.index('::endgroup::\n', start)
    return outcome.stdout[start:end]


class Stream:
    """A stream that can be reconfigured, and can refuse to be.

    `run_main()` hands the runner a `StringIO`, which has no `reconfigure` at
    all, so the safety net the runner puts on its own output gives up before
    it starts. This is the shape that reaches the rest of it: `tty` picks
    which reconfigure the runner asks for, and `error` is the refusal its
    `except` exists to survive.
    """

    def __init__(self, tty=False, error=None):
        self.tty = tty
        self.error = error
        self.reconfigured = []
        self.written = []

    def isatty(self):
        return self.tty

    def reconfigure(self, **options):
        self.reconfigured.append(options)
        if self.error is not None:
            raise self.error

    def write(self, text):
        self.written.append(text)

    def flush(self):
        pass

    @property
    def text(self):
        return ''.join(self.written)


class Sys:
    """`sys` as the runner reads it: its streams, and the interpreter."""

    def __init__(self, stdout, stderr):
        self.stdout, self.stderr = stdout, stderr
        self.executable = sys.executable
        self.platform = sys.platform
        self.argv = ['coverage_suites.py']


def run_main_on_streams(tmp, *, tty=False, error=None, suites=(SUITE,)):
    """`main()` over a `sys` whose stdout can be reconfigured.

    The runner's OWN `sys.stdout` is what the safety net reads, so it is the
    module's name for `sys` that is replaced; the group blocks `print()`
    writes go to the real stdout, which is redirected away here because the
    runner never reads them back.
    """
    policy = _util.load(_util.ROOT / RUNNER, 'coverage_suites_streams')
    root = Path(tmp) / 'streams'
    (root / 'tests').mkdir(parents=True, exist_ok=True)
    for name in suites:
        (root / 'tests' / name).write_text("print('measured')\n",
                                           encoding='utf-8')
    setattr(policy, 'ROOT', root)
    launch = Launches()
    setattr(policy, 'launch_suite', launch)
    setattr(policy, 'sys', Sys(Stream(tty, error), Stream(tty, error)))
    with contextlib.redirect_stdout(io.StringIO()):
        status = policy.main([])
    return SimpleNamespace(status=status, stdout=policy.sys.stdout,
                           stderr=policy.sys.stderr, launch=launch)


def assert_timed_out(outcome, cleanup, suite=SUITE):
    """The whole refusal a suite ended at its bound produces.

    Every part the runner owes an operator is asserted here: the verdict,
    the suite by name, the record appended to that suite's OWN output, and
    the bound the launch was handed. The bound is a parameter of the call,
    not a margin on elapsed time -- nothing in this path waited for a suite
    to overrun, and nothing here would fail on a slower machine.
    """
    assert outcome.status == 1, (outcome.stdout, outcome.stderr)
    assert f'TIMED OUT: tests/{suite}' in outcome.stderr, outcome.stderr
    record = group(outcome, suite)
    assert 'SUITE TIMED OUT after' in record, record
    assert cleanup in record, record
    assert outcome.launch.calls[0].timeout > 0, outcome.launch.calls


def assert_launch_failed(outcome, suite, marker):
    """A suite the launcher never started, in its own group and on its own.

    The marker is the caller's: it is the runner's own account of a suite
    that did not pass, and the caller is where that sentence is spelled.
    Asserting it here is what says the failure was COUNTED rather than
    merely printed -- a launch failure that did not increment the tally
    would leave every other part of this block intact.
    """
    assert outcome.status == 0, (outcome.stdout, outcome.stderr)
    failed = group(outcome, suite)
    assert failed.startswith(f'::group::tests/{suite}\nLAUNCH FAILED: '), (
        failed)
    assert 'FileNotFoundError' in failed, failed
    assert marker in failed, failed
