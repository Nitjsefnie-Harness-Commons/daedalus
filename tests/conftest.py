"""The rules run_tests.py and _util.runner held, as pytest fixtures and hooks.

A test module may define `suite_scope()` (entered around its tests),
`narrowed_suite()` (None, or the test names to keep), `ALLOWED_SKIPS` (the
`_util.Skipped` kinds that may skip) and `REQUIRES` (what a file that passes
nothing needs, so the run reports it as not run rather than failing).
"""
import collections
import contextlib
import faulthandler
import os
import sys
import sysconfig
import threading
import time
from pathlib import Path

import pytest

import _util
from _teardown import suite_root

BOUND = _util.SUITE_BOUND.suite_timeout()
SPENT = collections.defaultdict(float)
OUTCOMES = collections.defaultdict(collections.Counter)
NEEDS = {}
SKIP_ALLOWED = pytest.StashKey[bool]()
CONFINED = collections.defaultdict(dict)
INSTALLED = tuple(Path(sysconfig.get_path(key)).resolve() for key in (
    'stdlib', 'platstdlib', 'purelib', 'platlib'))


def pytest_configure(config):
    # A returned value is a body the call may not have run, as
    # _completion.py refuses it; pytest itself only warns.
    config.addinivalue_line(
        'filterwarnings', 'error::pytest.PytestReturnNotNoneWarning')


@pytest.fixture(scope='module')
def module_tmp_root():
    with suite_root('daedalustests_') as root:
        yield root


@pytest.fixture
def tmp(module_tmp_root, request):
    path = os.path.join(module_tmp_root, request.node.name)
    os.makedirs(path, exist_ok=True)
    return path


@pytest.fixture(name='_tmp')
def tmp_alias(tmp):
    return tmp


def confine(path, before):
    # A checkout module first imported for one test file is that file's
    # alone, as under the runner's process per file; CONFINED keeps it.
    for name in sys.modules.keys() - before:
        source = Path(getattr(sys.modules[name], '__file__', None) or '/')
        source = source.resolve()
        if (source.is_relative_to(_util.ROOT) and source != path.resolve()
                and not any(source.is_relative_to(i) for i in INSTALLED)):
            CONFINED[path][name] = sys.modules.pop(name)


@pytest.fixture(autouse=True, scope='module')
def module_scope(request):
    before = set(sys.modules)
    sys.modules.update(CONFINED[request.path])
    try:
        with getattr(request.module, 'suite_scope', contextlib.nullcontext)():
            yield
    finally:
        confine(request.path, before - CONFINED[request.path].keys())


@pytest.fixture(autouse=True)
def process_state():
    environ, cwd, path = dict(os.environ), os.getcwd(), sys.path[:]
    os.chdir(_util.ROOT)
    yield
    os.environ.clear()
    os.environ.update(environ)
    os.chdir(cwd)
    sys.path[:] = path


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    if not isinstance(collector, pytest.Module):
        return (yield)
    environ, cwd, before = dict(os.environ), os.getcwd(), set(sys.modules)
    with bounded(collector.config, collector.path, collector.nodeid):
        report = yield
    confine(collector.path, before)
    changed = sorted(key for key in environ.keys() | os.environ.keys()
                     if environ.get(key) != os.environ.get(key))
    if report.failed:
        return report
    if changed:
        problem = 'importing it changed os.environ: ' + ', '.join(changed)
    elif os.getcwd() != cwd:
        problem = 'importing it changed the working directory'
    elif report.skipped:
        problem = 'it skipped at collection'
    elif not report.result:
        problem = 'it collected no test'
    else:
        return report
    report.outcome, report.longrepr = 'failed', problem
    return report


def pytest_collection_modifyitems(config, items):
    kept, dropped, wanted = [], [], {}
    for item in items:
        module = getattr(item, 'module', None)
        if module not in wanted:
            narrowed = getattr(module, 'narrowed_suite', None)
            before = set(sys.modules)
            with bounded(config, item.path, item.nodeid.split('::')[0]):
                wanted[module] = narrowed() if narrowed else None
            confine(item.path, before)
        requires = getattr(module, 'REQUIRES', None)
        if requires:
            item.user_properties.append(('requires', requires))
        names = wanted[module]
        (kept if names is None or item.name in names else dropped).append(
            item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


@pytest.hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem):
    allowed = getattr(pyfuncitem.module, 'ALLOWED_SKIPS', (_util.Skipped,))
    try:
        return (yield)
    except allowed as why:
        pyfuncitem.stash[SKIP_ALLOWED] = True
        raise pytest.skip.Exception(str(why)) from why
    except _util.Skipped as why:
        raise AssertionError(
            f'{type(why).__name__} is not in ALLOWED_SKIPS: {why}') from why


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.skipped and not item.stash.get(SKIP_ALLOWED, False):
        report.outcome = 'failed'
        report.longrepr = ('a skip outside ALLOWED_SKIPS fails: '
                           f'{getattr(call.excinfo, "value", None)}')
    return report


def expire(config, nodeid):
    capture = config.pluginmanager.get_plugin('capturemanager')
    with getattr(capture, 'global_and_fixture_disabled',
                 contextlib.nullcontext)():
        faulthandler.dump_traceback(sys.stderr, all_threads=True)
        sys.stderr.write(_util.SUITE_BOUND.timeout_record(nodeid, BOUND, None))
        sys.stderr.flush()
    os._exit(1)


@contextlib.contextmanager
def bounded(config, path, nodeid):
    # The runner's per-file bound, spent across this process's work on it.
    started = time.monotonic()
    timer = threading.Timer(BOUND - SPENT[path], expire, (config, nodeid))
    timer.daemon = True
    if SPENT[path] < BOUND:
        timer.start()
    try:
        yield
    finally:
        timer.cancel()
        SPENT[path] += time.monotonic() - started


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item):
    with bounded(item.config, item.path, item.nodeid):
        return (yield)


def pytest_runtest_setup(item):
    if SPENT[item.path] >= BOUND:
        pytest.fail(_util.SUITE_BOUND.timeout_record(item.nodeid, BOUND, None),
                    pytrace=False)


def pytest_runtest_logreport(report):
    path = report.nodeid.split('::')[0]
    if report.when == 'call' or not report.passed:
        OUTCOMES[path][report.outcome] += 1
    NEEDS[path] = dict(report.user_properties).get('requires')


def pytest_sessionfinish(session):
    reporter = session.config.pluginmanager.get_plugin('terminalreporter')
    write = getattr(reporter, 'write_line', print)
    for path, counts in sorted(OUTCOMES.items()):
        if counts['passed'] or counts['failed']:
            continue
        if NEEDS[path]:
            write(f"NOT RUN HERE: {path} ({counts['skipped']} skipped, "
                  f'needs {NEEDS[path]})')
        else:
            write(f"NO COVERAGE: {path} ({counts['skipped']} skipped)")
            session.exitstatus = session.exitstatus or 1
