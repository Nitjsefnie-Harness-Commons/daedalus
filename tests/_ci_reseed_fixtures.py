"""The committed-repository builders the tests-line re-seed pins drive.

Not a suite itself — `run_tests.py` only loads `test_*.py`. The pins
stay in `tests/test_ci_thresholds.py` and drive these builders.
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


def _ci_reseed_rowless():
    """The shipped document minus the re-seeded row."""
    document = _ci_reseed_document(True)
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
    (clone / 'scripts' / 'ci').mkdir(parents=True)
    for source in (THRESHOLDS_SCRIPT, RESEED_SCRIPT):
        shutil.copy2(source, clone / 'scripts' / 'ci' / source.name)
    return clone


def _ci_reseed_order_subject(tmp, marker):
    """A subject where parent order decides. The first-parent limb ends
    in a row-present tip, so the pop order gates the whole limb: the
    mandated ordering visits the marked limb first and finds the marker
    at the cap's tenth visit, while dropping the reversal pops the
    row-present tip first, whose ``continue`` skips the limb and leaves
    the marker at the eleventh visit — past the cap."""
    repo = _ci_reseed_repo(tmp, 'order')
    _ci_reseed_commit(repo, 'base', row=True)
    named = subprocess.run(
        ('git', '-C', str(repo), 'branch', '--show-current'),
        check=True, capture_output=True,
        env=_util.child_coverage('scrub'))
    base = named.stdout.decode().strip()
    _git(repo, 'checkout', '-qb', 'line')
    _ci_reseed_commit(repo, f'delete {marker}', row=False)
    for depth in range(1, 9):
        _ci_reseed_commit(repo, f'limb {depth}', row=False)
    _git(repo, 'checkout', '-q', base)
    for _ in range(9):
        _ci_reseed_commit(repo, 'filler', row=True)
    _ci_reseed_commit(repo, 'publisher lands', row=True)
    _git(repo, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #2',
         'line')
    named = subprocess.run(
        ('git', '-C', str(repo), 'rev-parse', 'HEAD^1', 'HEAD^2'),
        check=True, capture_output=True,
        env=_util.child_coverage('scrub'))
    return repo, *named.stdout.decode().split()


def _ci_reseed_force_true():
    """Point the tree probe at a True answer for one in-process leg;
    the return value restores it."""
    import reseed
    real = reseed.in_flight
    setattr(reseed, 'in_flight', lambda root=None: True)
    return real


def _ci_reseed_restore_true(real):
    """Put the tree probe back after ``_ci_reseed_force_true``."""
    import reseed
    setattr(reseed, 'in_flight', real)


def _ci_reseed_visit_order(repo):
    """The revisions whose messages the walk reads, in visit order."""
    observed = []
    probe = _ci_reseed_module()
    real = probe._revision_message

    def spy(root, rev):
        observed.append(rev)
        return real(root, rev)

    setattr(probe, '_revision_message', spy)
    try:
        probe.clear_cache()
        probe.in_flight(repo)
    finally:
        setattr(probe, '_revision_message', real)
    probe.clear_cache()
    return observed


def _ci_reseed_diamond(tmp, marker):
    """A merge whose parents share a base commit: the base is queued
    twice and the second visit takes the seen-continue on the way down
    to the marker below it."""
    repo = _ci_reseed_repo(tmp, 'diamond')
    _ci_reseed_commit(repo, f'root {marker}', row=False)
    _ci_reseed_commit(repo, 'base', row=False)
    named = subprocess.run(
        ('git', '-C', str(repo), 'branch', '--show-current'),
        check=True, capture_output=True,
        env=_util.child_coverage('scrub'))
    base_branch = named.stdout.decode().strip()
    _git(repo, 'checkout', '-qb', 'side')
    _ci_reseed_commit(repo, 'side work', row=False)
    _git(repo, 'checkout', '-q', base_branch)
    _ci_reseed_commit(repo, 'main work', row=False)
    _git(repo, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #3',
         'side')
    return repo


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
