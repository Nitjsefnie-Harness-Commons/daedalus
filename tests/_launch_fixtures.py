"""Fixtures the launch-path suites share, beside them rather than in one.

`tests/test_harness_launch_bounds.py` and `tests/test_launch_path.py` both
need to plant a small source tree and both need a launcher-shaped module to
plant it against. Held here for the reason `tests/_processtree.py` exists:
two suites needing one thing is a shared module, not two copies. It is not in
`tests/_util.py` because that file is at its size ceiling, and copying a
two-line helper between two suites is exactly what a duplicate-code check
exists to refuse.

Carries no scenario state and no configuration of its own, so it binds
nothing and asserts nothing.
"""
from pathlib import Path

# A launcher-shaped module, written as the source a control plants. It hangs
# forever, so a bound handed to its `wait` is a bound that fires; and it
# catches the expiry and raises a class of its own, so it is the PERMITTED
# shape the census's permission rule admits rather than a margin.
HANG_DETECTOR_PROGRAM = """
import subprocess
import sys

from _processtree import cleanup_process_tree

SAMPLES = (1.0, 1.5, 2.0)
SLOWEST_S = max(SAMPLES)
MULTIPLE = 10
DEADLINE_S = round(SLOWEST_S * MULTIPLE)
CLEANUP_SHARE = 0.05
CLEANUP_S = round(DEADLINE_S * CLEANUP_SHARE)


class ChildDeadlineExceeded(Exception):
    pass


def launch(argv):
    process = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != 'win32')
    try:
        returncode = process.wait(timeout=DEADLINE_S)
    except subprocess.TimeoutExpired:
        cleanup_process_tree(process, CLEANUP_S)
        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None
    return returncode
"""


def write_source_tree(root, files):
    """Write `{relative path: source}` under `root` and return the directory.

    The planted subdirectory a control needs — `pkg/child.py` — is created
    on the way, because the point of several controls is a module below the
    top level of the suite directory.
    """
    root = Path(root)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    return root
