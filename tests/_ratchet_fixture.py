"""The fixture plumbing the JSON-owned ratchet contracts share.

`test_file_sizes.py`, `test_line_lengths.py` and `test_type_errors.py`
each drive one policy over a fixture repository built from the real
policy source, and each of those runs is the same three or four steps
wearing a different policy's name. The steps live here so a change to
one reaches all three. `test_js_coverage.py` takes only `_git` from it,
`test_ci_ratchets.py` the publisher scaffolding beside it, and both use
the scrubbed child environment the other three launches already used.

Not a suite itself — `run_tests.py` only loads `test_*.py`.
"""
import contextlib
import io
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
