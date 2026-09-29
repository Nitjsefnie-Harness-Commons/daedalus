"""Which workflow jobs reach the test suites, read off what the jobs run.

Shared, because `run_tests.py` gives every suite its own process: a
control that imported a sibling suite for this would re-execute that
suite's whole body and read a private copy, which is the defect
`tests/test_suite_import_boundaries.py` names. Two controls read it from
here, so they cannot disagree about which jobs run suites.

`SUITE_RUNNERS` recognises the sanctioned runners by a substring of each
step's `run:` text, and the complement — every other route into the suite
tree — is claimed explicitly by the control that owns it. A predicate over
two basenames is a fingerprint of that property rather than the property:
a third runner, a `.yaml` workflow, a `uses:` step, a shell loop over
`tests/*.py` and `unittest discover -s tests` are all routes the
basenames cannot see, so `_door_jobs` reads the step's resolved inputs and
follows the tracked file a step runs, which is where the mechanism is
written down whatever the runner is called.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_names  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

ROOT = _util.ROOT
WORKFLOW_DIR = ROOT / '.github' / 'workflows'
SUITE_RUNNERS = ('run_tests.py', 'coverage_suites.py')
# A job whose suites it FINDS: a tracked module that enumerates the suite
# tree and launches what it enumerates. A suite added tomorrow walks
# through such a job, so it has to install what the suites skip on.
RUNNER = 'runner'
# A job whose suites it NAMES: the step carries the paths. A suite added
# tomorrow cannot reach it, so the only obligation is keeping the list.
NAMES = 'names'
# A reference to the suite tree, as a step's own text spells one. `tests/`
# covers a path and a glob; the trailing alternative covers the directory
# named on its own at the end of a command, which is how `unittest
# discover -s tests` and `for f in tests/*.py` write it. A bare `tests` in
# the middle of an English sentence is not a path, so the reference has to
# reach the end of the line or a closing quote.
SUITE_TREE = re.compile(r"""(?<![\w/.-])tests(?:/|[\s'")\\]*(?:#.*)?$)""")
# A tracked file a step's own text names, by an extension worth reading.
SCRIPT_PATH = re.compile(
    r'(?<![\w./$-])[\w./-]+\.(?:py|sh|bash|json|yml|yaml|toml|ini|cfg|mk)')
# The build entry points a step may name without an extension at all.
BUILD_FILES = ('Makefile', 'makefile', 'tox.ini', 'justfile', 'Justfile')
# How a module looks into a directory: a glob, a walk, a listing.
_DISCOVERS = ('glob', 'rglob', 'iterdir', 'walk', 'listdir')
# How a module starts something: a process, or an entry point it calls.
_LAUNCHES = ('run', 'Popen', 'check_call', 'check_output', 'call', 'system',
             'run_path', 'run_module', 'open', 'main', 'load', 'execute')
_RUNNER_CACHE = {}


def _job_steps(workflow, job):
    """Every complete step mapping in one workflow job."""
    mapping = complete_job_mapping(workflow, job)
    assert mapping is not None, f'the workflow has no {job} job'
    return mapping['steps']


def _ordered_job_runs(workflow, job):
    """The ordered `run:` values of every step in one workflow job."""
    return [step.get('run', '') for step in _job_steps(workflow, job)]


def _workflow_jobs(marker):
    """Every job in every workflow with a step whose `run:` names `marker`.

    Read off what the jobs run, not off a list of their names: a job that
    globs the suites belongs to this control whichever workflow file it was
    added to, and a control that could only see one file was exactly the
    defect a third job in a second file walked past.
    """
    found = []
    for source in sorted(WORKFLOW_DIR.glob('*.yml')):
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            runs = _ordered_job_runs(workflow, job)
            if any(marker in run for run in runs):
                found.append((source.name, job, runs))
    return found


def _step_inputs(step):
    """Everything a step is given: its command, its action, its settings.

    `run:` is not the only input. A step that reaches the suites through a
    composite action says so in `uses:`, and one that passes the path in
    `with:` says it there; reading `run:` alone is a walk that cannot see
    either.
    """
    parts = [str(step.get(key) or '') for key in ('run', 'uses', 'with')]
    return '\n'.join(parts)


def _names_suite_tree(text):
    """Whether the text names a path under `tests/`."""
    return any(SUITE_TREE.search(line) for line in text.splitlines())


def _tracked(token):
    """The repository file a step's own token names, or None.

    A path is tried as written and then with leading components dropped,
    because the `timed` job checks its two trees out as `base/` and `head/`
    and names `head/scripts/ci/time_tests.py` — the same tracked file, under
    a directory that exists only on the runner. Dropping components stops
    at the first tracked file, so `tests/test_x.py` never resolves to the
    repository's top-level `test_x.py`.
    """
    parts = [part for part in token.lstrip('./').split('/') if part]
    while parts:
        candidate = ROOT.joinpath(*parts)
        if candidate.is_file():
            return candidate
        parts = parts[1:]
    return None


def _named_files(text):
    """Every tracked file the text names, directly."""
    found = set()
    for token in SCRIPT_PATH.findall(text):
        path = _tracked(token)
        if path is not None:
            found.add(path)
    for name in BUILD_FILES:
        if re.search(rf'(?<![\w./-]){re.escape(name)}(?![\w.-])', text):
            found.add(ROOT / name)
    return found


def _runs_suites(path):
    """Whether a tracked module FINDS the suites and runs what it finds.

    The two facts are read separately and required together, because
    either alone is common and neither is a suite runner: a type checker
    enumerates `tests/` and never runs anything, and a script runs
    processes without looking at the tree. What a runner does is enumerate
    the tree and start processes, and the conjunction is a property of the
    module rather than of the name its author gave it — which is the whole
    reason a third `scripts/ci/` runner cannot hide from this walk.
    """
    if path in _RUNNER_CACHE:
        return _RUNNER_CACHE[path]
    enumerates = launches = False
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        _RUNNER_CACHE[path] = False
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name in _LAUNCHES:
            launches = True
        elif name in _DISCOVERS and _mentions_suite_tree(node):
            enumerates = True
    _RUNNER_CACHE[path] = enumerates and launches
    return _RUNNER_CACHE[path]


def _called_name(node):
    """The method or function a call names, or ''."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else ''


def _mentions_suite_tree(node):
    """Whether a call looks into the suite tree rather than merely at it.

    The receiver counts as well as the arguments, because the shape is
    `(ROOT / 'tests').glob('test_*.py')`: the directory is the thing being
    globbed, and reading only the arguments sees a glob over
    `test_*.py` and concludes the module never looked at `tests`.
    """
    for part in ast.walk(node):
        if (isinstance(part, ast.Constant) and isinstance(part.value, str)
                and SUITE_TREE.search(part.value)):
            return True
    return False


def _door_jobs():
    """`(source, job, runs, mechanism)` for every job reaching the suites.

    A step's resolved inputs are its `run:`, its `uses:` and its `with:`
    values, and the step reaches the suite tree when they name a path under
    it, or name a tracked module that discovers the tree and launches what
    it finds. Reading a step's own text is not enough — a job whose only
    step is `python scripts/ci/third_runner.py` says nothing about suites —
    so the file a step runs is read for what it does.

    What this cannot see: a step whose reach is decided outside the
    repository. A `uses:` composite action is a remote string with no
    source here to read, and neither is a container image's entry point. A
    route through one of those is a claim this walk makes no effort to
    falsify, and the control that owns the residue says so where a reader
    will meet it.
    """
    sources = sorted(WORKFLOW_DIR.glob('*.yml')) + sorted(
        WORKFLOW_DIR.glob('*.yaml'))
    found = []
    for source in sources:
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            runs = _ordered_job_runs(workflow, job)
            for step in _job_steps(workflow, job):
                inputs = _step_inputs(step)
                if _names_suite_tree(inputs):
                    found.append((source.name, job, runs, NAMES))
                    break
                if any(_runs_suites(path)
                       for path in _named_files(inputs)):
                    found.append((source.name, job, runs, RUNNER))
                    break
    return found
