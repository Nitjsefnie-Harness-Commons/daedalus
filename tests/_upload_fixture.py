"""The fixture plumbing the upload-route contract suites share.

`test_upload_routes.py` and `test_upload_races.py` drive the same route
module against a temporary upload root, and both need the module by path
and a way to place a file in the namespace without going through a route.

Not a suite itself — `run_tests.py` only loads `test_*.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROUTES = _util.ROOT / 'daedalus_bridge' / 'upload_routes.py'


def _load(name):
    return _util.load(ROUTES, name)


def _place_upload(root, token, upload_id, name, data=b'x'):
    """Put a file into the upload namespace without going through a route."""
    target = Path(root) / token / upload_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    path.write_bytes(data)
    return path
