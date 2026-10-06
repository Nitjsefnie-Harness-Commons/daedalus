"""The committed-repository builders the tests-line re-seed pins drive.

Not a suite itself — `run_tests.py` only loads `test_*.py`.

The walk pins need real committed histories: a builder that commits the
thresholds document with or without the row, a merge standing over the
marked lineage, and a shallow clone cutting below the marker. They live
here rather than in the suite because the suite sits at its own size
ceiling; the pins stay in `tests/test_ci_thresholds.py` and drive these
builders, so the ruling's test location is preserved.
"""
import shutil
import subprocess
from pathlib import Path

import _util
from _ratchet_fixture import _git
from _repo import ROOT

RESEED_SCRIPT = ROOT / 'scripts' / 'ci' / 'reseed.py'
THRESHOLDS_SCRIPT = ROOT / 'scripts' / 'ci' / 'thresholds.py'
TESTS_LINES_SCRIPT = ROOT / 'scripts' / 'ci' / 'tests_lines.py'


def _ci_reseed_module():
    """reseed.py loaded under this module's own contract name."""
    return _util.load(RESEED_SCRIPT, 'ci_reseed_walk_contract')


def _ci_reseed_thresholds():
    """thresholds.py loaded under the builders' own contract name."""
    return _util.load(THRESHOLDS_SCRIPT, 'ci_reseed_build_thresholds')


def _ci_reseed_document(row):
    """The shipped document, complete or minus the re-seeded row."""
    thresholds = _ci_reseed_thresholds()
    document = thresholds.load(thresholds.THRESHOLDS)
    if not row:
        del document['tests_line_baseline']
    return document


def _ci_reseed_repo(tmp, name):
    """A committed git repository holding only the thresholds document."""
    repo = Path(tmp) / name
    (repo / '.github').mkdir(parents=True)
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    return repo


def _ci_reseed_commit(repo, message, row):
    """One commit; complete minus the row exactly when ``row`` is false."""
    _ci_reseed_thresholds().write(
        repo / '.github' / 'ci-thresholds.json',
        _ci_reseed_document(row), True)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', message)


def _ci_reseed_merge(repo, marker):
    """A merge whose own message carries no marker and whose second
    parent is the marked lineage."""
    scrubbed = _util.child_coverage('scrub')
    named = subprocess.run(
        ('git', '-C', str(repo), 'branch', '--show-current'),
        check=True, capture_output=True, env=scrubbed)
    base = named.stdout.decode().strip()
    _git(repo, 'checkout', '-qb', 'line')
    _ci_reseed_commit(repo, f'delete {marker}', row=False)
    _git(repo, 'checkout', '-q', base)
    _ci_reseed_commit(repo, 'unrelated fill', row=False)
    _git(repo, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #1',
         'line')


def _ci_reseed_shallow(tmp, marker):
    """A depth-one clone of a repo whose marker sits below its tip."""
    repo = _ci_reseed_repo(tmp, 'source')
    _ci_reseed_commit(repo, f'delete {marker}', row=False)
    _ci_reseed_commit(repo, 'publisher lands', row=False)
    clone = Path(tmp) / 'shallow'
    _git(tmp, 'clone', '--no-local', '--depth', '1', '-q',
         str(repo), str(clone))
    return clone


def _ci_reseed_budget_repo(tmp, files, budget, name):
    """A committed repository of ``files`` with a budgeted document.

    ``budget`` None builds the re-seed window itself: the row is deleted
    and the commit message carries the marker, so the walk the child's
    own loader runs reads the window open.
    """
    repo = _ci_reseed_repo(tmp, name)
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for source in (TESTS_LINES_SCRIPT, THRESHOLDS_SCRIPT, RESEED_SCRIPT):
        shutil.copy2(source, repo / 'scripts' / 'ci' / source.name)
    candidate = _ci_reseed_document(True)
    marker = ''
    if budget is None:
        del candidate['tests_line_baseline']
        marker = f' {_ci_reseed_module().MARKER}'
    else:
        candidate['tests_line_baseline'] = budget
    target = repo / '.github' / 'ci-thresholds.json'
    _ci_reseed_thresholds().write(target, candidate, True)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', f'base{marker}')
    return repo, target
