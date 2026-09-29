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
basenames cannot see, so `_suite_step` reads the step's resolved inputs and
follows the tracked file a step runs, which is where the mechanism is
written down whatever the runner is called.

Two bounds, and they are bounds on the WALK rather than on this control's
reach. A step that merely NAMES a runner reaches the tree here even where
it runs nothing — `time_tests.py --help` is a step that reaches the suite
tree and not one that runs a suite — because the alternative is encoding
which invocations of which runners do what, which is the basename
fingerprint this module exists to refuse. And a step running a tracked
file the walk cannot read — a shell wrapper — reaches nothing here, which
would drop its job out of the door set in silence. That second one is
surfaced rather than fixed: `_unclassifiable_steps` names every step in it,
`tests/test_ci_lint_tools.py` holds that set to files no interpreter can
execute, and the walk still reads no shell. What that refusal is about is
`_PATH_EXTENSIONS` and not every language there is: a tracked file whose
extension the pattern does not resolve is not a step the walk can see at
all, so nothing downstream of here answers for it.
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
# The list is the whole universe this walk can resolve, and it is one
# tuple rather than a set spelled into the pattern: a step naming a
# tracked file whose extension is not on it names nothing this module
# can classify, so every control downstream of the walk is silent about
# it rather than refusing it. The shells are on it because the `suites`
# matrix runs `windows-latest` beside two POSIX runners, so the
# interpreter a step's `run:` reaches for is not one language either.
_PATH_EXTENSIONS = ('py', 'sh', 'bash', 'mk', 'ps1', 'bat', 'cmd',
                    'json', 'yml', 'yaml', 'toml', 'ini', 'cfg')
SCRIPT_PATH = re.compile(
    rf'(?<![\w./$-])[\w./-]+\.(?:{"|".join(_PATH_EXTENSIONS)})')
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


def _suite_step(workflow, job):
    """`(index, mechanism)` for the first step reaching the suites, or None.

    A step's resolved inputs are its `run:`, its `uses:` and its `with:`
    values, and the step reaches the suite tree when they name a path under
    it, or name a tracked module that discovers the tree and launches what
    it finds. Reading a step's own text is not enough — a job whose only
    step is `python scripts/ci/third_runner.py` says nothing about suites —
    so the file a step runs is read for what it does.

    The INDEX is half the answer, because where a step sits in a job decides
    what its siblings could count on: a `setup-node` step after
    `python run_tests.py` declares nothing, and only the position tells that
    apart from a step that declares it first. Every question about a suite
    job's ordering asks this function rather than re-walking the steps, so
    two controls cannot disagree about which step is the one that reaches.

    What this cannot see: a step whose reach is decided outside the
    repository. A `uses:` composite action is a remote string with no
    source here to read, and neither is a container image's entry point. A
    route through one of those is a claim this walk makes no effort to
    falsify, and the control that owns the residue says so where a reader
    will meet it.
    """
    for index, step in enumerate(_job_steps(workflow, job)):
        inputs = _step_inputs(step)
        if _names_suite_tree(inputs):
            return index, NAMES
        if any(_runs_suites(path) for path in _named_files(inputs)):
            return index, RUNNER
    return None


def _action_name(step):
    """The `owner/repo` a step's `uses:` names, with the ref dropped.

    A pin is a property of the workflow, not of the action: the same
    `actions/setup-node` is a declaration whatever commit it is pinned at,
    and comparing the full `uses:` would make every bump a second control to
    update.

    `''` for a step that runs a command rather than an action. The empty
    string is a step's own `run:` text and is not an action, so a reader of
    the set this feeds is shown the step's text and never a blank entry.
    """
    uses = str(step.get('uses') or '').strip()
    return uses.split('@', 1)[0] if uses else ''


def _declarations_before(workflow, job, index):
    """`(action, condition)` per action step before `index`, in order.

    The condition is the step's `if:`, or `''` when it has none. It is
    carried because whether such a step RUNS is not a property of its
    `uses:`: a step guarded by `if: matrix.python == '3.99'` names an
    action and executes on no cell, and a control that read the action name
    alone would count a declaration nobody ever gets. A caller asking which
    actions a job declares filters these out; a caller writing the message
    for a refusal needs the condition it filtered.
    """
    return [(_action_name(step), str(step.get('if') or ''))
            for step in _job_steps(workflow, job)[:index]
            if _action_name(step)]


def _actions_before(workflow, job, index):
    """Every action name an UNGATED step before `index` uses."""
    steps = _declarations_before(workflow, job, index)
    return {name for name, condition in steps if not condition}


def _readable(path):
    """Whether this walk can read a tracked file's own source.

    Python and only Python: `_runs_suites` is `ast.parse` over the file, so
    a `.sh` wrapper classifies to "not a runner" by the same path as a
    Python file that genuinely is not one. That silence is what lets a job
    reach the suites through a shell script and leave the door set without
    anything saying so.
    """
    if path.suffix != '.py':
        return False
    try:
        ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        return False
    return True


# A tracked file an interpreter RUNS rather than reads. The walk reads
# Python and nothing else, so a step naming one of these reaches nothing
# here, and whether it reached the suites is a question this walk cannot
# answer. The shape is what makes that a refusal rather than a shrug: a
# step naming a manifest is inert whatever the manifest says, and a step
# naming a shell wrapper is not.
_SCRIPT_SHAPES = ('.sh', '.bash', '.mk', '.ps1', '.bat', '.cmd')
# The rest of `_PATH_EXTENSIONS`: a tracked file a step READS, which is
# what a manifest is. The two sets and `.py` partition the pattern, and a
# control holds them to it, so an extension the walk learns to resolve is
# classified here or that control is red.
_INERT_SHAPES = ('.json', '.yml', '.yaml', '.toml', '.ini', '.cfg')


def _executable_shape(path):
    """Whether a tracked file is one an interpreter would run."""
    return path.suffix in _SCRIPT_SHAPES or path.name in BUILD_FILES


def _unclassifiable_steps():
    """`(source, job, step, path)` for every step the walk cannot classify.

    A step naming a tracked file the walk cannot read reaches nothing here.
    That is correct for a data file — a step that names a manifest runs no
    suite — and silent for a shell wrapper, which runs exactly the suites
    this module exists to find. The set is returned whole so a control can
    hold the difference rather than have it assumed, and so a reader sees
    the names rather than an absence.
    """
    found = []
    for source in sorted(WORKFLOW_DIR.glob('*.yml')) + sorted(
            WORKFLOW_DIR.glob('*.yaml')):
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            for index, step in enumerate(_job_steps(workflow, job)):
                for path in sorted(_named_files(_step_inputs(step))):
                    if not _readable(path):
                        found.append((source.name, job, index, path))
    return found


def _door_jobs():
    """`(source, job, runs, mechanism)` for every job reaching the suites."""
    sources = sorted(WORKFLOW_DIR.glob('*.yml')) + sorted(
        WORKFLOW_DIR.glob('*.yaml'))
    found = []
    for source in sources:
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            reach = _suite_step(workflow, job)
            if reach is not None:
                runs = _ordered_job_runs(workflow, job)
                found.append((source.name, job, runs, reach[1]))
    return found
