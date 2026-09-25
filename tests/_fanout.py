"""Shared fixtures for the dashboard fan-out stream suites.

Not a suite itself — run_tests.py only loads `test_*.py`. The dashboard
fan-out controls span two suites (the core fan-out behaviour and the drain's
blocking/stop-reason behaviour); the by-path stream-service loader, the
dashboard queue helpers, and the stdout capture live here so both use one
copy of the wiring rather than each keeping a private one.
"""
import contextlib
import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _service_loader import _load_service  # noqa: E402


DASHBOARD = 'dashboard'


def service_pair(name):
    """The path-loaded stream service and a drain bound to that copy.

    `stream_service` is loaded by path under a private name, so it is a
    different module object from `daedalus_bridge.stream_service`; the drain
    has to see THIS copy's registry or `register` and the cursor queries
    would not meet.
    """
    service = _load_service(name)
    drain = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'dashboard_drain.py',
        name=f'{name}_drain')
    # setattr, not `drain.stream_service = service`: pyright refuses a
    # direct attribute assignment on a ModuleType.
    setattr(drain, 'stream_service', service)
    return service, drain


def dashboard_queue(service, tmp, token):
    """The dashboard event queue directory for `token`."""
    cq = service.command_queue
    name, _ = cq.command_target_names(token, DASHBOARD)
    qdir = Path(tmp) / 'commands' / name
    qdir.mkdir(parents=True, exist_ok=True)
    return qdir


def write_event(qdir, stem, **fields):
    """Write one dashboard event under an explicit, sortable stem."""
    document = {'id': stem, 'kind': 'event'}
    document.update(fields)
    path = qdir / f'{stem}.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


@contextlib.contextmanager
def captured_stdout():
    """Capture what the module printed while the block ran."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


class DescendingUuid:
    """A uuid4 whose hex strictly decreases, forcing a name inversion.

    With a random-hex stem two events published in one millisecond order by
    this value, so the second can sort below the first. The monotonic
    naming never calls uuid at all, so the mock is inert there and the stems
    come from the counter instead.
    """

    def __init__(self):
        self.calls = 0

    def uuid4(self):
        self.calls += 1
        hexid = 'ffffffff' if self.calls == 1 else '00000000'
        return type('U', (), {'hex': hexid})()
