"""The property that a worker loading broken fails rather than skips.

Not a suite itself — run_tests.py only loads `test_*.py`.

This is the mechanism tests/test_real_browser_eval.py's
`test_a_worker_that_loads_broken_is_a_failure_not_a_skip` and
tests/test_real_browser_harness.py's two wrapper controls all ran, moved out
of the first of them. A test function cannot live in a helper — being
collected is the point — so the mechanism came here and each suite kept a
test that calls it: the real-browser suite against a real browser, the
browser-free one against a page fixture that raises each of the three
verdicts the fixture can produce.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _realbrowser import (BrowserEnvironmentSkipped,  # noqa: E402
                          browser_requirements, real_extension_page)
from _repo import EXTENSION_ROOT  # noqa: E402

BROKEN_WORKER_TOKEN = 'workerboottok'
# The verdict is certain - the ready probe can never go true - so the short
# patience only skips waiting for it.
BROKEN_WORKER_PATIENCE = 2.0


def broken_worker_extension(tmp):
    """The extension copied and broken the way a real MV3 worker breaks.

    Appended, and conditioned on being a real MV3 worker: the script still
    installs and answers, and what breaks is the extension's own state. A
    top-level throw instead makes Chrome retire the registration, which is
    what the control extension now tells apart from a machine that cannot
    reach a worker at all.
    """
    broken = Path(tmp) / 'broken-extension'
    shutil.copytree(EXTENSION_ROOT, broken)
    worker = broken / 'background.js'
    worker.write_text(
        worker.read_text(encoding='utf-8')
        + "\nif (chrome.runtime.id) { startStream = undefined; }\n",
        encoding='utf-8')
    return broken


def assert_broken_worker_is_a_failure(tmp, bridge_url, pages):
    """Enter the fixture on a broken extension and require a failure.

    A worker that answers is one the browser has reached, so what it says
    about itself is the extension's own behaviour and fails. A worker that
    cannot be reached at all is decided by the control extension: the machine
    skips only when the control fails to load too. Any other skip is a broken
    extension reported as a broken machine, and is itself the failure.
    """
    browser_requirements()  # skips honestly where no browser exists
    reported = None
    try:
        with real_extension_page(
                tmp, bridge_url, BROKEN_WORKER_TOKEN, pages + '/plain.html',
                extension_root=broken_worker_extension(tmp),
                worker_ready_patience=BROKEN_WORKER_PATIENCE):
            raise AssertionError(
                'the fixture yielded with a worker that cannot boot')
    except BrowserEnvironmentSkipped:
        raise
    except _util.Skipped as skipped:
        raise AssertionError(
            'a broken extension was reported as an environment skip: '
            + str(skipped)) from skipped
    except AssertionError as failure:
        reported = str(failure)
    assert reported and 'service worker' in reported, reported
