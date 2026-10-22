"""Shared fixtures for the JSON-owned ratchet contracts."""
import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

THRESHOLDS_SOURCE = _util.ROOT / '.github' / 'ci-thresholds.json'
THRESHOLDS_SCRIPT = _util.ROOT / 'scripts' / 'ci' / 'thresholds.py'


def _normalised(text):
    return ' '.join(text.lower().split())


def _captured_main(policy, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with (contextlib.redirect_stdout(stdout),
          contextlib.redirect_stderr(stderr)):
        status = policy.main(argv)
    return status, stdout.getvalue(), stderr.getvalue()


def _document():
    return _util.load(THRESHOLDS_SCRIPT, 'ratchet_thresholds').load(
        THRESHOLDS_SOURCE)


def _git(repo, *args):
    subprocess.run(('git', '-C', str(repo)) + args, check=True,
                   capture_output=True, env=_util.child_coverage('scrub'))


def _budget_repo(tmp, files, budget, name):
    """Build a small committed test tree with an optional line budget."""
    repo = Path(tmp) / name
    (repo / '.github').mkdir(parents=True)
    (repo / 'scripts' / 'ci').mkdir(parents=True)
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for source in (THRESHOLDS_SCRIPT,
                   THRESHOLDS_SCRIPT.with_name('tests_lines.py')):
        shutil.copy2(source, repo / 'scripts' / 'ci' / source.name)
    candidate = _document()
    if budget is None:
        candidate.pop('tests_line_baseline', None)
    else:
        candidate['tests_line_baseline'] = budget
    target = repo / '.github' / 'ci-thresholds.json'
    _util.load(THRESHOLDS_SCRIPT, 'budget_thresholds').write(target, candidate)
    _git(repo, 'init', '-q')
    _git(repo, 'add', '.')
    _git(repo, '-c', 'user.email=tests@example.invalid',
         '-c', 'user.name=Tests',
         '-c', 'commit.gpgsign=false', 'commit', '-qm', 'base')
    return repo, target
