"""Fabricate isolated repositories for the two suite launchers' controls.

The runners themselves and the helpers a control needs to read what a
launcher did are both here rather than in either suite: `tests/` forbids a
suite importing another suite, so a helper both of them want has to live
in a module under `tests/` that is not one.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import _util
from _repo import ROOT


_FAKE_COVERAGE = r"""import json, os, runpy, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
suite = Path(sys.argv[-1])
stdin_byte = sys.stdin.buffer.read(1)
record = (root / 'coverage-invocations'
          / f'{suite.name}.{os.getpid()}.json')
record.write_text(json.dumps({
    'argv': sys.argv[1:],
    'coverage_process_start': os.environ.get('COVERAGE_PROCESS_START'),
    'pid': os.getpid(),
    'stdin_byte': stdin_byte.decode('ascii', errors='replace'),
}), encoding='utf-8')
runpy.run_path(sys.argv[-1], run_name='__main__')
"""


_FAKE_COVERAGE_INIT = """def process_startup(**_kwargs):
    pass
"""


SYNTHETIC_PROCESS_START = 'fabricated coverage startup'

# Which kill the launcher will reach on THIS host. Read here rather than
# from the module under test, but the HONEST account of what that buys is
# narrower than it looks: `sys.platform` is a host fact and not a decision
# either side makes, so this predicate agrees with the launcher's by
# spelling, and it cannot drift without the host changing. What a control
# is really independent ON is the strings below -- they are spelled here and
# asserted against the launcher's OWN output, so a control cannot pass by
# agreeing with a copy of the thing it is checking. The predicate only
# decides which of them the control expects to find.
FORCES_WITHOUT_ASKING = sys.platform.startswith('win')
# The half of a record that is true on every platform: the tree was ended
# and a signal of some kind reached it.
TREE_WAS_KILLED = 'taskkill' if FORCES_WITHOUT_ASKING else 'process group'
# The clause that names the POSIX half's request, and the clause that says
# the Windows half sent none. A control asserts the one its platform makes
# and REFUSES the other, so a route that silently changed platform is red.
REQUESTED_THEN_GRACED = 'asked to stop'
FORCED_WITHOUT_GRACE = 'no request was sent and no grace was given'
# The two sentences an operator reads, spelled here and nowhere else, for
# the same reason as the clauses above: a control asserts the one its
# platform selects and REFUSES the other, so a conditional that sends the
# wrong platform's sentence to stderr is red. Inverting the conditional
# used to move no assertion at all, which is the whole of finding E2.
OPERATOR_ASKS_FIRST = ('a suite that took the request to stop keeps what it '
                       'had measured, and one that did not contributes '
                       'nothing')
OPERATOR_FORCES = 'a forced kill leaves a suite nothing to flush'

# The whole timeout record, not a prefix of it. A pin that stops before
# the suite name is satisfied by a sibling occurrence the launcher printed
# for any suite, so a control that wants the name must match the line: the
# name has to sit where the record puts it, or it is not in the record.
# The two numbers are the ones a reader cannot know in advance, and each
# is required to be a number rather than anything at all.
RECORD = re.compile(
    r'SUITE TIMED OUT after (?P<bound>\S+) s '
    r'\(returncode (?P<returncode>-?\d+)\) in (?P<name>\S+?); '
    r'(?P<cleanup>[^\n]*); its last lines are above\n')


def records(text):
    """Every timeout record in `text`, parsed whole."""
    return list(RECORD.finditer(text))


def coverage_group(stdout, name):
    """The block the coverage launcher printed for `name`, '' if none.

    Empty rather than raising, because the call that has no block is a
    launcher that never got far enough to print one — an import that did
    not resolve, a name that does not exist — and a control that reports
    "the group was never printed" names that, where a `ValueError` from
    `.index` names nothing.
    """
    start = stdout.find(f'::group::tests/{name}\n')
    if start < 0:
        return ''
    end = stdout.index('::endgroup::\n', start)
    return stdout[start:end]


def kill_recorded(path):
    """SIGKILL a pid a planted suite wrote down, so a red control leaks none.

    The fixture's own outer bound is `subprocess.run(timeout=...)` on the
    launcher's pid, and it kills that pid and nothing else -- each suite
    is in a session of its own, which is what lets the launcher's kill
    reach the tree and also what puts the suite out of the outer bound's
    reach. So a control that goes red before the launcher ended its suite
    leaves that suite running, and this is the other half of the contract.
    """
    try:
        pid = int(path.read_text(encoding='ascii'))
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, 9)
    except OSError:
        return


def pid_alive(pid):
    """Whether this host can still signal `pid`.

    Signal 0 is the POSIX probe and has no meaning on Windows, where
    `os.kill(pid, 0)` is a request to terminate with exit code 0 -- a
    probe that killed what it measured would report a survivor gone.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def settle_gone(pid, seconds):
    """Wait out a kill's own settling, on a deadline, not a margin."""
    deadline = time.monotonic() + seconds
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not pid_alive(pid)


def _launch_failure_site(unlaunchable):
    return f"""import subprocess
from pathlib import Path

_real_run = subprocess.run
_real_popen = subprocess.Popen
_unlaunchable = {tuple(unlaunchable)!r}


def _unlaunchable_command(command):
    if (isinstance(command, (list, tuple)) and command
            and Path(str(command[-1])).name in _unlaunchable):
        command = list(command)
        command[0] = str(Path(__file__).resolve().parent / 'missing-python')
    return command


def run(command, *args, **kwargs):
    return _real_run(_unlaunchable_command(command), *args, **kwargs)


def Popen(command, *args, **kwargs):
    return _real_popen(_unlaunchable_command(command), *args, **kwargs)


subprocess.run = run
subprocess.Popen = Popen
"""


def _suite_bound_site(bound):
    """Rewrite the one definition of the per-suite bound, in the child.

    The launcher reads the bound when it starts, so setting the constant
    the module publishes is what a control that shrinks the real default
    must do: a value captured when the module was written would not see
    it, and the control would go green against an unbounded launcher.

    It patches the FLAT name, under this tree's own `scripts/ci`, because
    that is the module the launcher binds: the fixture runs it by path,
    so `sys.path[0]` is `scripts/ci` and `scripts.ci.suite_bound` is not
    what it imports. `site` reads the path the interpreter has built
    before it imports one, and a script's own directory is put on the
    path after that, so the directory is inserted here rather than left
    to the launch.
    """
    return ('import sys\n'
            'from pathlib import Path\n'
            '_tree = Path(__file__).resolve().parent\n'
            "sys.path.insert(0, str(_tree / 'scripts' / 'ci'))\n"
            'import suite_bound as _suite_bound\n'
            f'_suite_bound.DEFAULT_SUITE_TIMEOUT_S = {bound!r}\n')


def _cpu_count_site(cpu_count):
    return f"""import os

os.cpu_count = lambda: {cpu_count}
"""


def coverage_tree(
        tmp, suites, unlaunchable=(), cpu_count=None, args=(),
        real_coverage=False, suite_bound=None, outer_timeout=120,
        timeout_env=None, tree_on_path=True):
    """Copy the runner over fabricated suites and execute it there.

    `suite_bound` rewrites the one per-suite bound the copied launcher
    reads at startup, which is how a control shrinks the real default
    instead of carrying a second number of its own. `outer_timeout` is
    THIS fixture's limit on the launcher, independent of the bound the
    launcher itself applies: without one, a launcher that never ends its
    wedged suite reproduces the hang the control exists to catch, and the
    control's own verdict never arrives. What that produces is a
    `TimeoutExpired` naming the launcher, which is red and named and not
    an absence of findings -- a control that reports "the bound did not
    hold" instead has to look elsewhere.

    `tree_on_path` is whether the tree is on `PYTHONPATH`, and what that
    governs is the LOOKUP of this tree's `sitecustomize.py` -- nothing
    else. `site` reads the path the interpreter has already built, and a
    script's own directory is put on the path after that, so a
    `sitecustomize` beside the launcher is never found on its own. It
    does not choose which spelling the launcher imports its shared bound
    by: the launcher is run by path either way, so `__package__` is `''`
    and it takes the `else` branch in both cases. `_suite_bound_site`
    below puts `<tree>/scripts/ci` on the path itself and imports the
    flat name, so the patch lands on the same module object the
    launcher binds whichever way it is run.

    Turned off, the tree is on no path at all -- the shape a workflow step
    has, where no `sitecustomize` is found either. A control that wants
    that turns it off and drives the bound through `timeout_env`, because
    there is no `sitecustomize` left to carry the constant.
    """
    root = Path(tmp) / 'tree'
    (root / 'scripts' / 'ci').mkdir(parents=True, exist_ok=True)
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'coverage-invocations').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'coverage_suites.py',
                 root / 'scripts' / 'ci' / 'coverage_suites.py')
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                 root / 'scripts' / 'ci' / 'suite_bound.py')
    if real_coverage:
        shutil.copy2(ROOT / 'pyproject.toml', root / 'pyproject.toml')
    else:
        (root / 'coverage').mkdir(exist_ok=True)
        (root / 'coverage' / '__init__.py').write_text(
            _FAKE_COVERAGE_INIT, encoding='utf-8')
        (root / 'coverage' / '__main__.py').write_text(
            _FAKE_COVERAGE, encoding='utf-8')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    env = dict(os.environ)
    if timeout_env:
        env.update(timeout_env)
    if not real_coverage:
        env['COVERAGE_PROCESS_START'] = SYNTHETIC_PROCESS_START
    env['COVERAGE_FILE'] = str(root / '.coverage')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if tree_on_path:
        inherited_path = env.get('PYTHONPATH')
        env['PYTHONPATH'] = str(root)
        if inherited_path:
            env['PYTHONPATH'] += os.pathsep + inherited_path
    else:
        env.pop('PYTHONPATH', None)
    sitecustomize = ''
    if unlaunchable:
        sitecustomize += _launch_failure_site(unlaunchable)
    if cpu_count is not None:
        sitecustomize += _cpu_count_site(cpu_count)
    if suite_bound is not None:
        assert tree_on_path, (
            'the constant rides in a sitecustomize, and site looks for '
            'one on the path the interpreter has already built -- with '
            'the tree off PYTHONPATH neither this nor the launcher is '
            'found, so the launcher would enforce the unpatched default. '
            'Drive the bound through timeout_env instead.')
        sitecustomize += _suite_bound_site(suite_bound)
    if sitecustomize:
        (root / 'sitecustomize.py').write_text(
            sitecustomize, encoding='utf-8')
    # A generated `sitecustomize.py` is scaffolding, not repository source:
    # the coverage configuration measures the synthetic tree and attributes
    # what it measured to repository paths, so a child that imports one
    # hands `coverage report` a measured file with no source and the gate
    # refuses it. Scrubbing the collector from exactly these children stops
    # the RECORDING, on every platform -- an exclusion would only hide a
    # path shape that reads differently per platform. It also lowers the
    # declaration for the `unlaunchable` and `cpu_count` sites, which this
    # branch did not write; the lines they drive stay measured by the
    # children that have no sitecustomize.
    mode = 'scrub' if (root / 'sitecustomize.py').exists() else 'keep'
    result = subprocess.run(
        [sys.executable, 'scripts/ci/coverage_suites.py', *args],
        cwd=str(root), env=_util.child_coverage(mode, env, cwd=root),
        input='runner-only input\n', capture_output=True, text=True,
        timeout=outer_timeout)
    records = root / 'coverage-invocations'
    invocations = [json.loads(record.read_text(encoding='utf-8'))
                   for record in sorted(records.glob('*.json'))]
    return result, invocations
