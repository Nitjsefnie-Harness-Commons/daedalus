"""The shared by-path loader for the SSE stream service module.

Not a suite itself — run_tests.py only loads `test_*.py`. The stream-service
suites load `stream_service.py` by path through this one definition instead
of each keeping a private copy. The command-queue loader is a different seam:
it lives in `tests/_command_candidates.py` as `_load_queue`, not here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_service(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'stream_service.py', name=name)
