#!/usr/bin/env python3
"""Repository invariants that protect the public release.

Most tests read the tree. The storage-relay tests execute the shipped content
and page scripts in a Node VM with browser API fakes. The properties pinned
here are the ones a private deployment tends to leak: version drift, console
scripts that point at nothing, a default server URL baked into the extension,
and deployment-specific strings in shipped files.
"""
import ast
import contextlib
import http.server
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
sys.path.insert(0, str(_util.ROOT))
CLI_MODULE = importlib.import_module('daedalus_cli.cli')

ROOT = _util.ROOT
EXTENSION_ROOT = ROOT / 'extension'


def test_gitignore_postcondition_detects_an_ignored_tracked_file(tmp):
    """The generator's final check must inspect paths already in the index."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_mutation')
    baseline = generator.main(repo)
    assert baseline == 0, (
        'the postcondition rejected the generated tracked-file allow rule')
    real_run = generator.subprocess.run

    def mutate_before_postcondition(args, **kwargs):
        if 'check-ignore' in args:
            (repo / '.gitignore').write_text('*\n', encoding='utf-8')
        return real_run(args, **kwargs)

    generator.subprocess.run = mutate_before_postcondition
    try:
        result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result == 1, (
        'the postcondition accepted a tracked file ignored by the mutation')


def test_gitignore_postcondition_rejects_a_fatal_check_error(tmp):
    """A failed Git check cannot report that every tracked file is named."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_fatal')
    real_run = generator.subprocess.run

    def inject_fatal_check_error(args, **kwargs):
        if 'check-ignore' in args:
            return subprocess.CompletedProcess(
                args, 128, stdout='',
                stderr='fatal: injected check failure')
        return real_run(args, **kwargs)

    generator.subprocess.run = inject_fatal_check_error
    error = io.StringIO()
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result != 0, 'a fatal check error was reported as release-safe'
    assert 'fatal: injected check failure' in error.getvalue(), (
        'the fatal check diagnostic was hidden from the operator')


def test_gitignore_rejects_match_status_without_matched_paths(tmp):
    """Git match status without paths is an inconsistent failed guard."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_empty_match')
    real_run = generator.subprocess.run

    def inject_empty_match(args, **kwargs):
        if 'check-ignore' in args:
            return subprocess.CompletedProcess(
                args, 0, stdout='', stderr='')
        return real_run(args, **kwargs)

    generator.subprocess.run = inject_empty_match
    error = io.StringIO()
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result == 1, 'Git match status without paths was release-safe'
    assert 'reported matches without naming paths' in error.getvalue(), (
        'the inconsistent Git result was hidden from the operator')


def _gitignore_exception_result(tmp, exception_type, failing_command):
    """Run the generator with one Git command raising an injected exception."""
    repo = Path(tmp) / f'{exception_type.__name__}-{failing_command}'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    module_name = f'gen_gitignore_{exception_type.__name__}_{failing_command}'
    generator = _util.load(ROOT / 'scripts' / 'gen_gitignore.py', module_name)
    real_run = generator.subprocess.run

    def inject_exception(args, **kwargs):
        if failing_command in args:
            if exception_type is FileNotFoundError:
                raise FileNotFoundError('injected launch failure')
            raise subprocess.TimeoutExpired(args, kwargs.get('timeout'))
        return real_run(args, **kwargs)

    generator.subprocess.run = inject_exception
    error = io.StringIO()
    result = None
    caught = None
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    except (OSError, subprocess.SubprocessError) as failure:
        caught = failure
    finally:
        generator.subprocess.run = real_run
    return result, caught, error.getvalue()


def test_gitignore_reports_launch_failures_from_both_git_calls(tmp):
    """A Git launch failure at either boundary returns an operator failure."""
    for command in ('ls-files', 'check-ignore'):
        result, caught, error = _gitignore_exception_result(
            tmp, FileNotFoundError, command)
        assert caught is None, (command, caught)
        assert result == 1, command
        assert f'git {command} failed' in error, (command, error)


def test_gitignore_reports_timeouts_from_both_git_calls(tmp):
    """A Git timeout at either boundary returns an operator failure."""
    for command in ('ls-files', 'check-ignore'):
        result, caught, error = _gitignore_exception_result(
            tmp, subprocess.TimeoutExpired, command)
        assert caught is None, (command, caught)
        assert result == 1, command
        assert f'git {command} failed' in error, (command, error)


def _gitignore_invalid_output_result(tmp, failing_command):
    """Run one Git boundary against a child that writes invalid UTF-8."""
    repo = Path(tmp) / f'invalid-output-{failing_command}'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py',
        f'gen_gitignore_invalid_output_{failing_command}')
    real_run = generator.subprocess.run

    def emit_invalid_bytes(args, **kwargs):
        # Raise the decode failure rather than trying to provoke one from a
        # real child. subprocess decodes with the locale, and the byte that
        # is undecodable as UTF-8 is an ordinary character under the Windows
        # code page — so the previous spelling of this injection simulated
        # nothing there and the generator succeeded. What the generator
        # contracts is that a decode failure at either Git boundary becomes
        # an operator-shaped result, and that is exactly what this asks it.
        if failing_command in args:
            raise UnicodeDecodeError(
                'utf-8', b'\xff', 0, 1, 'invalid start byte')
        return real_run(args, **kwargs)

    generator.subprocess.run = emit_invalid_bytes
    error = io.StringIO()
    result = None
    caught = None
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    except UnicodeDecodeError as failure:
        caught = failure
    finally:
        generator.subprocess.run = real_run
    return result, caught, error.getvalue()


def test_gitignore_reports_invalid_output_from_both_git_calls(tmp):
    """Invalid output at either Git boundary returns an operator failure."""
    for command in ('ls-files', 'check-ignore'):
        result, caught, error = _gitignore_invalid_output_result(tmp, command)
        assert caught is None, (command, caught)
        assert result == 1, command
        assert f'git {command} failed' in error, (command, error)


def test_gitignore_bounds_both_git_calls(tmp):
    """Both local Git operations receive an explicit finite timeout."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_timeouts')
    real_run = generator.subprocess.run
    timeouts = []

    def record_timeout(args, **kwargs):
        timeouts.append(kwargs.get('timeout'))
        return real_run(args, **kwargs)

    generator.subprocess.run = record_timeout
    try:
        result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result == 0, result
    assert len(timeouts) == 2, timeouts
    assert all(isinstance(item, (int, float)) and item > 0
               for item in timeouts), timeouts


def test_gitignore_names_a_tracked_path_containing_a_space(tmp):
    """A space in a tracked filename must survive enumeration as ONE path.

    `git ls-files` without `-z` separates paths on whitespace, so
    'has space.txt' became two tokens; the generator then named and
    postcondition-checked the FRAGMENTS and reported success while the real
    file stayed ignored — a fail-open postcondition on the most ordinary
    filename shape there is.
    """
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    (repo / 'plain.txt').write_text('published\n', encoding='utf-8')
    (repo / 'has space.txt').write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'plain.txt', 'has space.txt'],
        check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_space')
    result = generator.main(repo)
    assert result == 0, 'the generator rejected its own space-named allow rule'
    rules = (repo / '.gitignore').read_text(encoding='utf-8').splitlines()
    assert '!/has space.txt' in rules, rules
    ignored = subprocess.run(
        ['git', '-C', str(repo), 'check-ignore', '--no-index', '--',
         'has space.txt'],
        capture_output=True, check=False)
    assert ignored.returncode != 0, (
        'the generated .gitignore still ignores the tracked space-named file')


def test_every_served_page_declares_its_encoding(tmp):
    """An HTML page without a charset is decoded as windows-1252.

    A browser given no declaration falls back to a legacy code page, and the
    fallback applies to every classic script the document loads as well as to
    the markup — so a single em dash in a sibling .js file renders as three
    wrong characters. The extension's options page shipped that way and
    displayed its own success message mangled.

    Cheap to state and cheap to keep: any page added later inherits the same
    requirement instead of rediscovering it in a screenshot.
    """
    del tmp
    pages = [p for p in _iter_tree_files() if p.suffix == '.html']
    assert pages, 'no HTML pages found to check'
    missing = [
        str(p.relative_to(ROOT)) for p in pages
        if not re.search(r'<meta[^>]*charset', p.read_text(encoding='utf-8'),
                         re.IGNORECASE)]
    assert not missing, f'HTML without a charset declaration: {missing}'


_ALL_SKIPPED_SUITE = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util


def test_needs_something_absent(d):
    _util.skip('nothing to run against here')


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
"""

_PASSING_SUITE = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util


def test_arithmetic(d):
    assert 1 + 1 == 2


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
"""


def _runner_tree(tmp, suites):
    """A copy of run_tests.py over fabricated suites, run where it stands."""
    root = Path(tmp) / 'tree'
    (root / 'tests').mkdir(parents=True)
    shutil.copy2(ROOT / 'run_tests.py', root / 'run_tests.py')
    shutil.copy2(ROOT / 'tests' / '_util.py', root / 'tests' / '_util.py')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run(
        [sys.executable, 'run_tests.py'], cwd=str(root), env=env,
        capture_output=True, text=True, timeout=300)


def test_a_suite_that_ran_no_coverage_is_not_an_overall_pass(tmp):
    """A run that executed nothing must not read as a verified one.

    Per-suite lines carried the skip counts, but the aggregate was a boolean
    over exit codes, so a suite whose every test skipped — no browser, no
    dependencies — was indistinguishable from a verified one at exactly the
    line a reader and CI both key on.
    """
    result = _runner_tree(tmp, {
        'test_all_skipped.py': _ALL_SKIPPED_SUITE,
        'test_passing.py': _PASSING_SUITE,
    })
    assert 'OVERALL: PASS' not in result.stdout, result.stdout
    assert 'test_all_skipped.py' in result.stdout, result.stdout
    assert result.returncode != 0, (result.returncode, result.stdout)


def test_the_aggregate_carries_the_totals_it_verified(tmp):
    """A pass says how much was run and how much was skipped."""
    result = _runner_tree(tmp, {'test_passing.py': _PASSING_SUITE})
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'OVERALL: PASS' in result.stdout, result.stdout
    assert '1 passed' in result.stdout.rsplit('OVERALL', 1)[-1], result.stdout


def test_the_overlap_harness_bound_outlasts_its_inner_waits(tmp):
    """The subprocess bound is a backstop, not the first thing to fire.

    Every wait inside the harness is bounded and names what it was waiting
    for; the bound around the whole child says only that a command timed
    out, and carries the entire harness source with it. A Windows leg
    reported exactly that. So the outer bound has to outlast the worst path
    through the inner ones: one wait for the handlers to start, one per
    result, and one per gap when the caller asks to wait between them.
    """
    del tmp
    inner = re.search(r'timeoutMs = (\d+)', _util._BACKGROUND_OVERLAP_HARNESS)
    assert inner, 'the harness no longer declares a per-wait bound'
    inner_ms = int(inner.group(1))
    for order, wait_between in (
            (['a', 'b'], True), (['a', 'b'], False), (['a'], False)):
        waits = 1 + len(order) + (len(order) - 1 if wait_between else 0)
        worst = waits * inner_ms / 1000
        bound = _util.overlap_child_timeout(order, wait_between)
        assert bound > worst, (
            f'{len(order)} commands, wait_between={wait_between}: the child '
            f'is killed at {bound}s while its own waits can run to {worst}s, '
            'so the failure names the whole command instead of the step')


def test_the_runner_reports_a_failure_a_console_cannot_encode(tmp):
    """A failure the console cannot spell must still be reported.

    The detail carries whatever the test was comparing, and on a legacy code
    page `print` raises rather than degrading. One failing assertion holding
    an arrow used to abort the whole file with UnicodeEncodeError, so the
    report was lost AND every test after it in that file never ran. A runner
    that cannot say what went wrong is worse than the thing that went wrong.
    """
    del tmp
    program = (
        'import sys\n'
        'sys.path.insert(0, "tests")\n'
        'import _util\n'
        'def test_arrow(d):\n'
        '    assert False, "wanted \u2192 got \u2190 caf\u00e9"\n'
        'raise SystemExit(_util.runner([test_arrow]))\n')
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'cp1252'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    result = subprocess.run(
        [sys.executable, '-c', program], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding='cp1252', timeout=60)
    assert 'UnicodeEncodeError' not in result.stderr, result.stderr
    assert 'Traceback' not in result.stderr, result.stderr
    assert 'FAIL  test_arrow' in result.stdout, result.stdout
    assert '0/1 passed' in result.stdout, result.stdout
    assert result.returncode == 1, (result.returncode, result.stdout)


def test_gitignore_survives_an_undecodable_byte_in_the_repo_path(tmp):
    """A repository path carrying a raw byte must not crash the generator.

    argv arrives via surrogateescape, so a byte 0xff in the path becomes
    U+DCFF in the value the diagnostics print; under
    PYTHONIOENCODING=utf-8:strict both the success line and the failure
    lines raised UnicodeEncodeError — the success one AFTER a correct
    .gitignore had already been written. Run as a subprocess because the
    strict encoding has to be in force before the interpreter starts.
    """
    _util.require_undecodable_names(tmp)
    repo = os.fsencode(tmp) + b'/\xffrepo'
    os.mkdir(repo)
    subprocess.run(['git', '-C', repo, 'init', '-q'], check=True)
    with open(repo + b'/tracked.txt', 'wb') as handle:
        handle.write(b'published\n')
    subprocess.run(['git', '-C', repo, 'add', '-f', 'tracked.txt'],
                   check=True)

    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8:strict'}
    script = str(ROOT / 'scripts' / 'gen_gitignore.py')
    ok = subprocess.run([sys.executable, script, os.fsdecode(repo)],
                        capture_output=True, text=True, env=env, check=False)
    assert ok.returncode == 0, (ok.returncode, ok.stdout, ok.stderr)

    not_a_repo = os.fsencode(tmp) + b'/\xffplain'
    os.mkdir(not_a_repo)
    fail = subprocess.run(
        [sys.executable, script, os.fsdecode(not_a_repo)],
        capture_output=True, text=True, env=env, check=False)
    assert fail.returncode == 1, (fail.returncode, fail.stdout, fail.stderr)
    assert 'UnicodeEncodeError' not in fail.stderr, fail.stderr
    assert 'FAIL' in fail.stderr, fail.stderr


def test_gitignore_log_safe_never_raises_and_stays_useful(tmp):
    """The generator's copy of the log safeguard must not itself raise.

    Same finding as the server copy: str(value) and the encode step ran
    outside any fallback, so a conversion-limited huge int, an exception
    object with a failing __str__, or a str subclass with a raising encode
    or a non-string decode turned the diagnostic into the failure. The
    copies cannot share an implementation (importing server.py requires its
    env and runs module-level config), so they are kept behavior-identical —
    this test holds the generator's copy to the same shared contract.
    """
    del tmp
    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_log_safe')

    for value, expected in _util.log_safe_cases():
        assert generator._log_safe(value) == expected, (
            f'_log_safe({type(value).__name__}) disagrees')
    # Ordinary values pass through in full, ASCII and non-ASCII alike.
    assert generator._log_safe('plain ascii') == 'plain ascii'
    assert generator._log_safe('héllo — 世界') == 'héllo — 世界'


def test_extension_same_id_overlap_keeps_each_delivery_id(tmp):
    """Both completion orders preserve each command's server delivery id."""
    del tmp
    commands = [
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-a',
         '_did': 'did-a'},
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-b',
         '_did': 'did-b'},
    ]
    actual = {
        'a-first': _util.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-a', 'owner-b']),
        'b-first': _util.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-b', 'owner-a']),
    }
    expected = {
        'a-first': [
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
        ],
        'b-first': [
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
        ],
    }
    assert actual == expected, actual


_CDP_CALL_HARNESS = r"""
const [target, method, paramsText] = process.argv.slice(1);
const socket = new WebSocket(target);
const timer = setTimeout(() => {
  process.stderr.write('CDP response timed out\n');
  socket.close();
  process.exitCode = 1;
}, 10000);

socket.addEventListener('open', () => {
  socket.send(JSON.stringify({ id: 1, method, params: JSON.parse(paramsText) }));
});
socket.addEventListener('message', (event) => {
  const message = JSON.parse(String(event.data));
  if (message.id !== 1) return;
  clearTimeout(timer);
  if (message.error) {
    process.stderr.write(JSON.stringify(message.error) + '\n');
    process.exitCode = 1;
  } else {
    process.stdout.write(JSON.stringify(message.result || {}));
  }
  socket.close();
});
socket.addEventListener('error', () => {
  clearTimeout(timer);
  process.stderr.write('CDP websocket failed\n');
  process.exitCode = 1;
});
"""

_HOSTILE_EVAL_SCRIPT = r"""
(() => {
  try {
    const FORGED = 'FORGED-BY-PAGE';
    const forged = function () { return 'FORGED'; };
    // Descriptors are null-prototype: once `Object.prototype` carries a
    // `value` accessor, an ordinary descriptor literal inherits it and
    // `defineProperty` rejects the whole poison.
    const define = (target, key, descriptor) => Object.defineProperty(
      target, key, Object.assign({ __proto__: null }, descriptor));

    // Evaluator bindings: `eval`, `Function`, the four function-constructor
    // prototypes, both same-origin iframe access routes and `Worker`.
    const constructors = [
      Function,
      (async function () {}).constructor,
      (function* () {}).constructor,
      (async function* () {}).constructor,
    ];
    for (const constructor of constructors) {
      define(constructor.prototype, 'constructor', {
        configurable: true,
        value: forged,
        writable: true,
      });
    }
    const fakeWindow = { eval: forged, Function: forged };
    define(HTMLIFrameElement.prototype, 'contentWindow', {
      configurable: true,
      get() { return fakeWindow; },
    });
    define(HTMLIFrameElement.prototype, 'contentDocument', {
      configurable: true,
      get() { return { defaultView: fakeWindow }; },
    });
    const contentWindowFrame = document.createElement('iframe');
    const defaultViewFrame = document.createElement('iframe');
    document.body.append(contentWindowFrame, defaultViewFrame);
    void contentWindowFrame.contentWindow;
    void defaultViewFrame.contentDocument.defaultView;
    globalThis.eval = forged;
    globalThis.Function = forged;
    globalThis.Worker = forged;
    document.title = 'Hostile eval page';

    // Retrieval bindings. Promise resolution reads `constructor` and `then`
    // off page-writable prototypes and assimilates anything callable it finds
    // there, so an evaluator whose value rides back through page promise
    // machinery is forgeable even when its compilation is not.
    const poisonedThen = function (resolve) {
      if (typeof resolve === 'function') resolve(FORGED);
      return this;
    };
    function Poisoned() {}
    Poisoned[Symbol.species] = function (executor) {
      executor(function () {}, function () {});
      return this;
    };
    define(Promise.prototype, 'constructor', {
      configurable: true,
      value: Poisoned,
      writable: true,
    });
    const valueProtos = [Object.prototype, Number.prototype, String.prototype,
      Boolean.prototype, Array.prototype, Function.prototype, Error.prototype];
    for (const proto of [Promise.prototype].concat(valueProtos)) {
      define(proto, 'then', {
        configurable: true,
        value: poisonedThen,
        writable: true,
      });
    }
    for (const proto of valueProtos) {
      define(proto, Symbol.toPrimitive, {
        configurable: true,
        value: function () { return FORGED; },
        writable: true,
      });
    }
    define(Array.prototype, Symbol.iterator, {
      configurable: true,
      writable: true,
      value: function () {
        let spent = false;
        return {
          next() {
            const done = spent;
            spent = true;
            return { value: done ? undefined : FORGED, done };
          },
        };
      },
    });
    JSON.parse = function () { return FORGED; };
    JSON.stringify = function () { return '"' + FORGED + '"'; };

    // Accessors on every property name a result envelope is read through,
    // then `defineProperty` itself, both last so the poison above still ran
    // with working primitives.
    for (const name of ['r', 'e', 'ok', 'message', 'csp', 'ms',
      'value', 'title', 'result', 'world', 'code']) {
      define(Object.prototype, name, {
        configurable: true,
        get() { return FORGED; },
        set() {},
      });
    }
    Object.defineProperty = function (target) { return target; };
    Object.freeze = function (target) { return target; };
  } catch (error) {
    globalThis.__poisonError = (error && error.message) || 'poison failed';
  }
  globalThis.__evalPageReady = true;
})();
"""

_STRICT_CSP_EVAL_SCRIPT = r"""
globalThis.__dataUrlBlocks = 0;
globalThis.__evalBlocks = 0;
globalThis.__userSideEffects = 0;
document.addEventListener('securitypolicyviolation', (event) => {
  if (event.blockedURI === 'data') globalThis.__dataUrlBlocks++;
  if (event.blockedURI === 'eval') globalThis.__evalBlocks++;
});
document.title = 'Strict CSP eval page';
globalThis.__evalPageReady = true;
"""

_PERFORMANCE_POISON_EVAL_SCRIPT = r"""
performance.now = function () {
  throw new Error('page killed performance.now');
};
document.title = 'Performance poison eval page';
globalThis.__evalPageReady = true;
"""

_PLAIN_EVAL_SCRIPT = r"""
document.title = 'Plain eval page';
globalThis.__evalPageReady = true;
"""


class _EvalPageHandler(http.server.BaseHTTPRequestHandler):
    """Serve the real-page evaluator fixtures over one loopback origin."""

    def do_GET(self):
        pages = {
            '/hostile.html': (
                b'<title>loading</title><body><script src="/hostile.js"></script></body>',
                'text/html', None),
            '/hostile.js': (
                _HOSTILE_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/strict.html': (
                b'<title>loading</title><body><script src="/strict.js"></script></body>',
                'text/html', "default-src 'self'; script-src 'self'"),
            '/strict.js': (
                _STRICT_CSP_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/performance-poison.html': (
                b'<title>loading</title><body><script src="/performance-poison.js"></script></body>',
                'text/html', None),
            '/performance-poison.js': (
                _PERFORMANCE_POISON_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/plain.html': (
                b'<title>loading</title><body><script src="/plain.js"></script></body>',
                'text/html', None),
            '/plain.js': (
                _PLAIN_EVAL_SCRIPT.encode(), 'text/javascript', None),
        }
        fixture = pages.get(urllib.parse.urlsplit(self.path).path)
        if fixture is None:
            self.send_error(404)
            return
        body, content_type, csp = fixture
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if csp:
            self.send_header('Content-Security-Policy', csp)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def _eval_page_server():
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), _EvalPageHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _cdp_call(node, target, method, params):
    result = subprocess.run(
        [node, '-e', _CDP_CALL_HARNESS, target, method, json.dumps(params)],
        cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout or '{}')


def _cdp_eval(node, target, expression):
    response = _cdp_call(node, target, 'Runtime.evaluate', {
        'expression': expression,
        'awaitPromise': True,
        'returnByValue': True,
    })
    assert not response.get('exceptionDetails'), response
    return response.get('result', {}).get('value')


def _browser_version(browser):
    """What the browser calls itself, for a skip that has to be actionable.

    A leg that skips the real-browser tests says nothing useful unless it
    says which browser refused: the fixture works on one Chromium and not on
    whatever a runner image happens to ship, and that difference is the
    whole question.
    """
    try:
        reported = subprocess.run(
            [browser, '--version'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as why:
        return f'{browser} (version unreadable: {type(why).__name__})'
    return f'{browser} ({reported.stdout.strip() or reported.stderr.strip()})'


def _browser_requirements():
    node = shutil.which('node')
    browser = next((path for name in (
        'chromium', 'chromium-browser', 'google-chrome',
        'google-chrome-stable', 'chrome')
        if (path := shutil.which(name))), None)
    if not node or not browser:
        _util.skip('Chromium and Node are required for the real-page eval test')
    websocket = subprocess.run(
        [node, '-e',
         "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    if websocket.returncode != 0:
        _util.skip('this Node runtime has no WebSocket client for CDP')
    return node, browser


_WORKER_READY_PROBE = (
    '(() => { try { return typeof loadConfig === "function" '
    '&& typeof ensureKeepAlive === "function" '
    '&& typeof startStream === "function"; } '
    'catch (_) { return false; } })()')

_WORKER_STATE_PROBE = (
    '(() => { try { return JSON.stringify({'
    'id: (typeof chrome !== "undefined" && chrome.runtime)'
    ' ? chrome.runtime.id : null,'
    'loadConfig: typeof loadConfig,'
    'ensureKeepAlive: typeof ensureKeepAlive,'
    'startStream: typeof startStream,'
    'version: typeof VERSION !== "undefined" ? VERSION '
    ': null}); } catch (e) { return "probe failed: " '
    '+ (e && e.message); } })()')


def _devtools_targets(port):
    with urllib.request.urlopen(
            f'http://127.0.0.1:{port}/json/list', timeout=2) as reply:
        return json.load(reply)


def _worker_targets(targets):
    """Every service worker that could be an extension's background worker.

    More than one extension can be loaded at once, and a CI runner's browser
    carries one of its own, so this is a list rather than the first match:
    which of them is this extension's is a question only the worker's own
    declarations answer, and DevTools happens to list the other one first.
    """
    return [item for item in targets
            if item.get('type') == 'service_worker'
            and item.get('url', '').endswith('/background.js')]


def _wait_for_devtools(profile, process):
    """Wait for the DevTools endpoint, the page, and a background worker.

    Everything this waits on is the browser starting, not the extension
    behaving, so an environment where it does not arrive skips rather than
    fails — see _real_extension_page for where that boundary sits.
    """
    port_file = Path(profile) / 'DevToolsActivePort'
    # A cold runner's first browser start is slower than every later one: the
    # ubuntu legs timed out here on the first browser test of the run and
    # reached the same browser without trouble in the ones after it.
    deadline = time.time() + 30
    seen = 'it never wrote a DevTools port'
    while time.time() < deadline:
        if process.poll() is not None:
            _util.skip('Chromium exited before DevTools became available')
        if port_file.exists():
            lines = port_file.read_text(encoding='utf-8').splitlines()
            if lines:
                port = lines[0]
                try:
                    targets = _devtools_targets(port)
                    page = next((item for item in targets
                                 if item.get('type') == 'page'), None)
                    workers = _worker_targets(targets)
                    if page and workers:
                        return page, workers, port
                    seen = (f'{len(targets)} targets on port {port}, '
                            f'a page: {page is not None}, '
                            f'background workers: {len(workers)}')
                except (OSError, ValueError) as why:
                    seen = f'listing its targets failed: {why}'
        time.sleep(0.05)
    _util.skip('this browser never exposed the fixture page and an '
               f'extension service worker over DevTools — {seen}')


def _ready_worker(node, workers):
    """The worker among `workers` whose script declares this extension.

    Returns (target, reached, error). `reached` says whether any of them
    could be evaluated in at all, which is the line between a machine that
    cannot talk to a worker and an extension that did not load.

    What is asserted is that the worker's own script ran to the end: a script
    that threw defines none of these, and neither does another extension's
    worker. Waiting on keepaliveTimer instead waited on the async boot chain,
    and a worker that answers with the timer still unset is a different thing
    from a worker that never loaded — the caller configures the extension
    explicitly, so it does not need that chain to have finished.
    """
    reached = False
    error = 'no service worker target is listed'
    for candidate in workers:
        target = candidate['webSocketDebuggerUrl']
        try:
            if _cdp_eval(node, target, _WORKER_READY_PROBE) is True:
                return target, True, None
            reached = True
            error = 'the worker answered without its declarations'
        except AssertionError as failure:
            error = f'evaluating in the worker failed: {failure}'
    return None, reached, error


def _worker_state(node, target):
    """What one worker says about itself, for a failure that names which."""
    try:
        return _cdp_eval(node, target, _WORKER_STATE_PROBE)
    except AssertionError as why:
        return f'could not be read back: {why}'


@contextlib.contextmanager
def _real_extension_page(tmp, bridge_url, token, page_url,
                         extension_root=None, extra_extensions=()):
    """Yield (node, page target, tab id) for a real page under the extension.

    Every test that reaches a real browser comes through here, so this is
    where the one boundary lives. Getting a usable browser is an environment
    question: the binaries have to exist, and Chromium has to start, expose
    DevTools and run the unpacked extension's service worker. None of that
    is a claim about this repository, so where it does not happen the test
    skips with the reason — a browser that cannot be launched, cannot run an
    MV3 service worker, or is refused a profile is a property of the machine.

    From the configuration step on, the browser has demonstrably worked and
    everything asserted is the extension's own behaviour, so those stay hard
    failures. Loading the fixture page is on that side of the line: the page
    and the script that sets __evalPageReady are files in this repository,
    served by this suite's own origin, so a page that never reports ready is
    a defect here rather than a property of the machine. Skipping the environment costs no coverage of the extension
    source itself: this suite also runs background.js, content.js and page.js
    under Node, which does not need a browser and fails outright if that
    source is broken.
    """
    node, browser = _browser_requirements()
    profile = Path(tmp) / 'chromium-profile'
    extension = (extension_root or EXTENSION_ROOT).resolve()
    # Ours last: a browser that carries an extension of its own lists that
    # one first, which is the order the CI legs see.
    loaded = ','.join(str(Path(item).resolve())
                      for item in (*extra_extensions, extension))
    process = subprocess.Popen(
        [
            browser,
            '--headless=new',
            '--no-sandbox',
            '--disable-gpu',
            '--disable-dev-shm-usage',
            '--no-first-run',
            '--no-default-browser-check',
            '--remote-allow-origins=*',
            '--remote-debugging-port=0',
            '--disable-extensions-except=' + loaded,
            '--load-extension=' + loaded,
            '--user-data-dir=' + str(profile),
            'about:blank',
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        page, workers, devtools_port = _wait_for_devtools(profile, process)
        page_target = page['webSocketDebuggerUrl']
        # Load the page before waiting on the worker. An MV3 worker goes
        # dormant on its own after about thirty idle seconds, and evaluating
        # in it over CDP does not wake it — on a slow machine the worker
        # that DevTools listed a moment ago can already be gone, and the
        # wait then polls a target nothing will ever answer. The content
        # script's keepalive port is an event the worker listens for, so
        # loading the page is what revives it, exactly as an ordinary
        # browsing session does.
        _cdp_call(node, page_target, 'Page.navigate', {'url': page_url})
        deadline = time.time() + 30
        last_error = 'no evaluation was attempted'
        answered = False
        worker_target = None
        while time.time() < deadline:
            worker_target, reached, error = _ready_worker(node, workers)
            answered = answered or reached
            if worker_target:
                break
            last_error = error
            try:
                workers = _worker_targets(_devtools_targets(devtools_port))
            except (OSError, ValueError) as why:
                last_error = f'listing DevTools targets failed: {why}'
            # Every attempt spawns one node process per candidate to
            # speak CDP, so this polls twice a second rather than twenty
            # times. Polling faster bought nothing on the runner that was
            # failing here, and a two-core machine pays for every spawn.
            time.sleep(0.5)
        if worker_target is None:
            # Two different states wear the same timeout. A worker that
            # answers is one this machine can reach, so what it says about
            # itself is the extension's own behaviour and stays a failure —
            # that is the case the injected-fault test drives. A worker that
            # never answers at all has not been reached: the browser lists a
            # target and refuses every debugger connection to it, which is a
            # property of the machine and skips like the launch steps do.
            if answered:
                states = [_worker_state(node, item['webSocketDebuggerUrl'])
                          for item in workers]
                raise AssertionError(
                    'the extension service worker never finished loading: '
                    'DevTools exposed its target and the fixture page was '
                    f'loaded to wake it. Last: {last_error}. Worker states: '
                    f'{states}')
            # Never reached at all: the target vanished, or every
            # debugger connection to it was refused. Both say the browser
            # did not get as far as running the extension, which is where
            # the environment boundary sits.
            _util.skip(
                'this browser never let the extension worker be reached '
                f'over the debugger: {_browser_version(browser)} — '
                f'{last_error}')

        storage = json.dumps({
            'daedalus-token': token,
            'daedalus-server': bridge_url,
        })
        configure = (
            '(async () => { await chrome.storage.local.set(' + storage
            + '); await loadConfig(); ensureKeepAlive(); stopStream(); '
            + 'startStream(); '
            + 'return config.token === ' + json.dumps(token)
            + ' && config.serverUrl === ' + json.dumps(bridge_url)
            + '; })()')
        deadline = time.time() + 30
        while True:
            try:
                configured = _cdp_eval(node, worker_target, configure)
            except AssertionError as failure:
                configured = f'the call failed: {failure}'
            if configured is True or time.time() >= deadline:
                break
            # An MV3 worker stops when it goes idle and the next event starts
            # a fresh one, so the worker that answered a moment ago can be
            # gone by the time it is configured. Look it up again rather than
            # reporting the browser's own lifecycle as this extension's
            # failure to take its configuration.
            time.sleep(0.5)
            try:
                workers = _worker_targets(_devtools_targets(devtools_port))
            except (OSError, ValueError):
                workers = []
            replacement, _reached, _error = _ready_worker(node, workers)
            if replacement:
                worker_target = replacement
        assert configured is True, (
            f'extension worker did not load test configuration ({configured!r}'
            f'). Worker state: {_worker_state(node, worker_target)}')
        _cdp_call(node, page_target, 'Page.navigate', {'url': page_url})

        deadline = time.time() + 15
        while time.time() < deadline:
            if _cdp_eval(
                    node, page_target,
                    'globalThis.__evalPageReady === true') is True:
                break
            time.sleep(0.25)  # one node process per attempt; see above
        else:
            raise AssertionError(
                'the fixture page never set __evalPageReady: ' + page_url)

        _cdp_eval(node, worker_target, 'registerAllTabs()')
        deadline = time.time() + 15
        last_tabs = None
        while time.time() < deadline:
            status, tabs = _util.get_json(
                bridge_url + '/tabs?' + urllib.parse.urlencode({'token': token}))
            last_tabs = (status, tabs)
            match = next((item for item in tabs
                          if page_url in item.get('url', '')), None) \
                if status == 200 and isinstance(tabs, list) else None
            if match:
                yield node, page_target, match['tabId']
                return
            time.sleep(0.05)
        raise AssertionError(
            f'extension did not register the eval fixture tab: {last_tabs!r}')
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _real_ext_command(bridge_url, token, cmd_id, payload):
    """Send a typed extension command and return its delivered result."""
    body = {'token': token, 'tab': 'extension', 'id': cmd_id, **payload}
    status, raw = _util.request(bridge_url + '/command', 'PUT', body=body)
    assert status == 200, (status, raw)
    sent = json.loads(raw)
    deadline = time.time() + 20
    query = urllib.parse.urlencode({'token': token, 'tab': 'extension'})
    while time.time() < deadline:
        result_status, result = _util.get_json(bridge_url + '/result?' + query)
        if (result_status == 200 and isinstance(result, dict)
                and result.get('deliveryId') == sent.get('did')):
            return result
        time.sleep(0.05)
    raise AssertionError(f'{cmd_id!r} did not return its delivery result')


def _real_eval(bridge_url, token, tab_id, cmd_id, code):
    status, raw = _util.request(
        bridge_url + '/command', 'PUT', body={
            'token': token,
            'tab': tab_id,
            'id': cmd_id,
            'code': code,
        })
    assert status == 200, (status, raw)
    sent = json.loads(raw)
    deadline = time.time() + 20
    query = urllib.parse.urlencode({'token': token, 'tab': tab_id})
    while time.time() < deadline:
        result_status, body = _util.get_json(
            bridge_url + '/result?' + query)
        if (result_status == 200 and isinstance(body, dict)
                and body.get('deliveryId') == sent.get('did')):
            generation = body.get('resultGeneration')
            if generation:
                consume = urllib.parse.urlencode({
                    'token': token,
                    'tab': tab_id,
                    'consume': '1',
                    'expected': generation,
                })
                consumed_status, _consumed = _util.get_json(
                    bridge_url + '/result?' + consume)
                assert consumed_status == 200, consumed_status
            return body
        time.sleep(0.05)
    raise AssertionError(f'eval {cmd_id!r} did not return its delivery result')


def _hostile_eval_matrix(tmp):
    """Return five eval shapes from the fully poisoned real page."""
    token = 'cdpevaltok'
    matrix = {}
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            page_url = pages + '/hostile.html'
            with _real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                poison = _cdp_eval(
                    node, page, 'globalThis.__poisonError || "none"')
                assert poison == 'none', poison
                cases = (
                    ('expression', '2 + 2'),
                    ('function-body', 'const value = 4; return value'),
                    ('top-level-await', 'await 0, 4'),
                    ('object-result', '({ value: 4 })'),
                    ('page-promise', 'await Promise.resolve(4)'),
                )
                for label, code in cases:
                    actual = _real_eval(
                        bridge_url, token, tab_id, 'cdp-' + label, code)
                    matrix[label] = actual
    return matrix


def test_hostile_page_eval_matrix_has_descriptive_channels_only(tmp):
    """A page-selected value never gains a trust claim from its channel.

    The fixture poisons eval, Function, all four function-constructor
    prototypes, both same-origin iframe routes, Worker, and the page's Promise
    machinery. The last case deliberately routes a primitive through that
    hostile Promise rather than merely placing `await` beside a direct value.
    It does not claim that Promise-prototype poison alone changes a direct
    object; that narrower reproduction does not occur.
    """
    matrix = _hostile_eval_matrix(tmp)
    for label, actual in matrix.items():
        assert 'result' in actual, (label, actual)
        world = actual.get('world')
        assert isinstance(world, str) and world, (label, actual)
        rendered = CLI_MODULE._format_eval_world(world)
        assert rendered == f'channel={world}', (label, actual, rendered)
        assert 'privileged' not in rendered, (label, actual, rendered)
        assert 'untrusted' not in rendered, (label, actual, rendered)
    assert matrix['page-promise'].get('result') == 'FORGED-BY-PAGE', matrix


def test_main_world_transport_failure_and_genuine_null_are_distinct(tmp):
    """A failed injection is an error while evaluated `null` is a value."""
    token = 'mainworldtok'
    actual = {}
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            cases = (
                ('performance-poison', '/performance-poison.html', '2 + 2'),
                ('genuine-null', '/plain.html', 'null'),
            )
            for label, path, code in cases:
                case_tmp = Path(tmp) / label
                case_tmp.mkdir()
                with _real_extension_page(
                        case_tmp, bridge_url, token,
                        pages + path) as (_node, _page, tab_id):
                    actual[label] = _real_eval(
                        bridge_url, token, tab_id, label, code)

    poisoned = actual['performance-poison']
    assert poisoned.get('result') is None, poisoned
    assert 'page killed performance.now' in (poisoned.get('error') or ''), poisoned
    assert poisoned.get('world') == 'page-main', poisoned

    genuine_null = actual['genuine-null']
    assert 'result' in genuine_null, genuine_null
    assert genuine_null['result'] is None, genuine_null
    assert genuine_null.get('error') is None, genuine_null
    assert genuine_null.get('world') == 'page-main', genuine_null


def test_a_worker_that_loads_broken_is_a_failure_not_a_skip(tmp):
    """A broken extension must not be reported as a broken machine.

    The fixture skipped when the worker did not come up ready, so a real MV3
    defect passed CI in silence. The Node-based tests do not cover what that
    skip hides: they run the same source against fakes with no
    chrome.runtime.id, so a fault conditioned on being a real worker is
    invisible there too — which is why this mutation is exactly that fault.

    A worker that answers is one the browser has reached, so what it says
    about itself is the extension's own behaviour and fails. A worker that
    cannot be reached at all is still the machine's business and skips.
    """
    _browser_requirements()  # skips honestly where no browser exists
    broken = Path(tmp) / 'broken-extension'
    shutil.copytree(EXTENSION_ROOT, broken)
    worker = broken / 'background.js'
    # Appended, and conditioned on being a real MV3 worker: the script still
    # installs and answers, and what breaks is the extension's own state.
    # A top-level throw instead makes Chrome retire the registration, which
    # is indistinguishable from a machine that cannot reach the worker at
    # all — the fault has to be one the extension survives loading.
    worker.write_text(
        worker.read_text(encoding='utf-8')
        + "\nif (chrome.runtime.id) { startStream = undefined; }\n",
        encoding='utf-8')

    token = 'workerboottok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            reported = None
            try:
                with _real_extension_page(
                        tmp, bridge_url, token, pages + '/plain.html',
                        extension_root=broken):
                    raise AssertionError(
                        'the fixture yielded with a worker that cannot boot')
            except _util.Skipped as skipped:
                raise AssertionError(
                    'a broken extension was reported as an environment skip: '
                    + str(skipped)) from skipped
            except AssertionError as failure:
                reported = str(failure)
            assert reported and 'service worker' in reported, reported


def test_a_page_that_never_reports_ready_is_a_failure_not_a_skip(tmp):
    """Past the fixture's own boundary, a page that will not load is a bug.

    By the time readiness is awaited, the browser has started, exposed
    DevTools, booted the extension's service worker and taken its
    configuration — the fixture's own docstring says everything from there
    on is the extension's behaviour and stays a hard failure. The fixture
    page and the script that sets __evalPageReady are repository files, so a
    skip there hides a defect in them behind an environment excuse.

    The check itself has to distinguish the two skips: a machine with no
    browser skips honestly, and only a skip raised AT the readiness step is
    the defect under test.
    """
    token = 'readyboundarytok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            # Served as a 404, so nothing ever sets __evalPageReady.
            page_url = pages + '/never-ready.html'
            reported = None
            try:
                with _real_extension_page(
                        tmp, bridge_url, token, page_url):
                    raise AssertionError(
                        'the fixture yielded a page that never reported ready')
            except _util.Skipped as skipped:
                if 'never finished loading the fixture page' in str(skipped):
                    raise AssertionError(
                        'page readiness was reported as an environment skip: '
                        + str(skipped)) from skipped
                raise
            except AssertionError as failure:
                reported = str(failure)
            assert reported and '__evalPageReady' in reported, reported


def test_the_fixture_reaches_its_own_worker_past_another_extension(tmp):
    """A second extension's background worker is not mistaken for ours.

    Every ubuntu CI leg runs a browser that carries an extension of its own,
    so DevTools lists two service workers whose URL ends in /background.js —
    and it lists the other one first. A fixture that took the first match
    attached to it and polled it for declarations it does not have, which is
    what the legs reported: a worker answering with none of them, or nothing
    at all once that worker's target had stopped.
    """
    _browser_requirements()  # skips honestly where no browser exists
    decoy = Path(tmp) / 'decoy-extension'
    decoy.mkdir()
    (decoy / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3,
        'name': 'decoy',
        'version': '1.0',
        'background': {'service_worker': 'background.js'},
    }), encoding='utf-8')
    (decoy / 'background.js').write_text(
        'globalThis.__decoyWorker = true;\n', encoding='utf-8')

    token = 'decoyworkertok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            with _real_extension_page(
                    tmp, bridge_url, token, pages + '/plain.html',
                    extra_extensions=(decoy,)) as (_node, _page, tab_id):
                # Reaching a value back through the bridge is what proves the
                # configured worker was this extension's: the decoy has no
                # stream to carry the command.
                answer = _real_eval(bridge_url, token, tab_id, 'decoy-eval',
                                    'return 1 + 1')
                assert answer.get('error') is None, answer
                assert answer.get('result') == 2, answer


def test_a_hotfix_replays_on_a_page_that_forbids_eval_and_blob(tmp):
    """A stored hotfix reaches a page whose CSP refuses the page relay.

    Replay used to run in the page: the page's own `eval`, then a blob
    <script>. A CSP with neither `unsafe-eval` nor `blob:` — github.com's,
    and this fixture's strict page — refuses both, and the blocked blob load
    reported nothing back, so the fix simply never applied. The background
    can reach the page by the same route ordinary eval uses when the page
    refuses dynamic compilation, so replay goes through it.
    """
    token = 'hotfixcsptok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            with _real_extension_page(
                    tmp, bridge_url, token,
                    pages + '/plain.html') as (node, page, _tab_id):
                stored = _real_ext_command(bridge_url, token, 'store-csp-fix', {
                    'type': 'store-hotfix',
                    'fixId': 'csp-fix',
                    'code': 'globalThis.__hotfixApplied = true;',
                    'permanent': True,
                })
                assert stored.get('error') is None, stored

                # A load of the strict page replays it: script-src 'self',
                # so neither page-side path the old relay had is available.
                _cdp_call(node, page, 'Page.navigate',
                          {'url': pages + '/strict.html'})
                deadline = time.time() + 20
                applied = None
                while time.time() < deadline:
                    if _cdp_eval(node, page,
                                 'globalThis.__evalPageReady === true') is True:
                        applied = _cdp_eval(
                            node, page, 'globalThis.__hotfixApplied === true')
                        if applied is True:
                            break
                    time.sleep(0.1)
                assert applied is True, (
                    'the hotfix never applied on a page whose CSP forbids '
                    f'eval and blob scripts (last read: {applied!r})')


def test_strict_csp_page_uses_cdp_once_after_source_free_preflight(tmp):
    """A source-free CSP probe falls back before the command runs once."""
    token = 'cspevaltok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            page_url = pages + '/strict.html'
            with _real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                actual = _real_eval(
                    bridge_url, token, tab_id, 'csp-eval',
                    'globalThis.__userSideEffects++; '
                    'return globalThis.__userSideEffects')
                state = _cdp_eval(node, page, '({'
                                  'blocks: globalThis.__dataUrlBlocks,'
                                  'evalBlocks: globalThis.__evalBlocks,'
                                  'sideEffects: globalThis.__userSideEffects'
                                  '})')
                assert actual.get('error') is None, actual
                assert actual.get('result') == 1, actual
                assert actual.get('world') == 'cdp', actual
                # The constant probe is the only page evaluation CSP rejects;
                # submitted source goes to CDP once and no data URL is tried.
                assert state['blocks'] == 0, state
                assert state['evalBlocks'] == 1, state
                assert state['sideEffects'] == 1, state


def test_cdp_eval_throw_is_terminal(tmp):
    """An exception is returned once and never retried on another evaluator."""
    token = 'cdpthrowtok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            page_url = pages + '/strict.html'
            with _real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                actual = _real_eval(
                    bridge_url, token, tab_id, 'cdp-throw',
                    'globalThis.__throwSideEffects = '
                    '(globalThis.__throwSideEffects || 0) + 1; '
                    'throw new Error("callable failed")')
                side_effects = _cdp_eval(
                    node, page, 'globalThis.__throwSideEffects')
                assert 'callable failed' in (actual.get('error') or ''), actual
                assert actual.get('world') == 'cdp', actual
                assert side_effects == 1, side_effects


_EVAL_RELAY_OVERLAP_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, contentPath, pagePath, orderText, mode = 'overlap',
  relayHostname = '', cdpText = ''] = process.argv.slice(1);
const cdpEnabled = cdpText === '1' || cdpText === 'midflight';
const cdpFailsMidFlight = cdpText === 'midflight';
let cdpSideEffects = 0;
const completionOrder = JSON.parse(orderText);
let scriptingCalls = 0;
let injectionShape = '';
const backgroundListeners = [];
const contentListeners = [];
const windowListeners = [];
const windowMessages = [];
const postedResults = [];
const evalResolvers = {};
let relaySequence = 0;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function eventTarget(listeners = null) {
  return {
    addListener(listener) {
      if (listeners) listeners.push(listener);
    },
  };
}

const backgroundChrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'eval-token',
        'daedalus-server': 'test-bridge',
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
    get: async (tabId) => ({
      id: tabId,
      url: '',
      title: 'Page',
    }),
    async sendMessage(_tabId, message) {
      for (const listener of contentListeners) listener(message);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {
      if (!cdpEnabled) throw new Error('debugger unavailable in relay test');
    },
    detach: async () => {},
    // Stand-in for the V8 inspector channel. The marker records that channel;
    // it makes no claim about a value the submitted source obtained from page
    // state or page promise machinery.
    sendCommand: async (_target, method, params) => {
      if (method !== 'Runtime.evaluate') return {};
      if (cdpFailsMidFlight) {
        // The inspector started the source and then went away. Nothing can
        // prove the side effect did not happen, so no other evaluator may run.
        cdpSideEffects++;
        throw new Error('inspector detached mid-evaluation');
      }
      try {
        return { result: { value: await vm.runInNewContext(params.expression, {}) } };
      } catch (error) {
        return { exceptionDetails: { exception: { description: String(error) } } };
      }
    },
  },
  scripting: {
    async executeScript(injection) {
      scriptingCalls++;
      if (mode === 'injection-shapes') {
        if (injection.func.name === '_canUseMainWorldEval') {
          return [{ result: true }];
        }
        if (injectionShape === 'reject') {
          throw new Error('executeScript rejected');
        }
        if (injectionShape === 'empty') return [];
        if (injectionShape === 'frame-error') {
          return [{ error: 'frame exception' }];
        }
        if (injectionShape === 'missing-result') return [{}];
        if (injectionShape === 'bare-null') return [{ result: null }];
        if (injectionShape === 'genuine-null') {
          return [{ result: { r: null, ms: 1 } }];
        }
        if (injectionShape === 'eval-exception') {
          return [{ result: { e: 'operator exception', ms: 1 } }];
        }
        if (injectionShape === 'page-substitution') {
          return [{ result: 'PAGE-SUBSTITUTED' }];
        }
        throw new Error('unknown injection shape ' + injectionShape);
      }
      if (mode !== 'preemption' && mode !== 'poisoned') {
        throw new Error('scripting unavailable in relay overlap test');
      }
      relayContext.__injectionArgs = injection.args || [];
      const source = '(' + injection.func.toString()
        + ')(...__injectionArgs)';
      const result = await vm.runInContext(source, relayContext);
      delete relayContext.__injectionArgs;
      return [{ result }];
    },
  },
  runtime: {
    onMessage: eventTarget(backgroundListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

const backgroundContext = vm.createContext({
  chrome: backgroundChrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init.method === 'POST') {
      postedResults.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    if (url.includes('/stream?')) return response(503, { error: 'disabled' });
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'relay-' + (++relaySequence) },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});

const windowObject = {
  addEventListener(type, listener) {
    if (type === 'message') windowListeners.push(listener);
  },
  postMessage(data) {
    windowMessages.push(data);
    for (const listener of [...windowListeners]) {
      listener({ source: windowObject, data });
    }
  },
};

const relayChrome = {
  runtime: {
    lastError: null,
    onMessage: eventTarget(contentListeners),
    sendMessage(message) {
      for (const listener of backgroundListeners) {
        listener(message, { tab: { id: 7 } }, () => {});
      }
    },
    connect() {
      return {
        name: 'keepalive',
        postMessage() {},
        disconnect() {},
        onDisconnect: eventTarget(),
      };
    },
    getManifest: () => ({ version: '0.18.0' }),
  },
  storage: {
    local: {
      get(_keys, callback) { callback({}); },
      set(_data, callback) { if (callback) callback(); },
      remove(_keys, callback) { if (callback) callback(); },
    },
  },
};

const documentObject = {
  head: { appendChild() {} },
  documentElement: { appendChild() {} },
  addEventListener() {},
  removeEventListener() {},
  createElement() {
    return {
      remove() {},
      set onload(_listener) {},
      set onerror(_listener) {},
    };
  },
};

const relayContext = vm.createContext({
  window: windowObject,
  chrome: relayChrome,
  document: documentObject,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: relayHostname },
  performance,
  evalResolvers,
  Blob,
  URL,
  Uint8Array,
  ArrayBuffer,
  TextEncoder,
  atob,
  btoa,
  setTimeout: () => 1,
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, error() {} },
});

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate, label) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return;
    await delay();
  }
  throw new Error('timed out waiting for ' + label);
}

async function run() {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), backgroundContext);
  await vm.runInContext('loadConfig()', backgroundContext);
  vm.runInContext(fs.readFileSync(contentPath, 'utf8'), relayContext);
  vm.runInContext(fs.readFileSync(pagePath, 'utf8'), relayContext);

  if (mode === 'injection-shapes') {
    const shapes = ['reject', 'empty', 'frame-error', 'missing-result',
      'bare-null', 'genuine-null', 'eval-exception', 'page-substitution'];
    const outcomes = {};
    for (const shape of shapes) {
      injectionShape = shape;
      backgroundContext.command = {
        id: '_eval',
        type: 'eval',
        code: shape === 'genuine-null' ? 'null' : '2 + 2',
        chromeTab: 7,
        _did: 'did-' + shape,
      };
      const before = postedResults.length;
      await vm.runInContext('dispatchCommand(command)', backgroundContext);
      await waitFor(
        () => postedResults.length === before + 1,
        'injection result for ' + shape);
      const posted = postedResults[before];
      outcomes[shape] = {
        hasResult: Object.prototype.hasOwnProperty.call(posted, 'result'),
        result: posted.result === undefined ? null : posted.result,
        error: posted.error === undefined ? null : posted.error,
        world: posted.world || null,
      };
    }
    return outcomes;
  }

  if (mode === 'poisoned') {
    // A hostile page replaces both evaluator primitives before the command
    // arrives. Everything the injected MAIN-world function resolves — `eval`
    // and `Function` alike — comes from these page-owned globals.
    relayContext.eval = (source) => 'FORGED-EVAL:' + source;
    relayContext.Function = function () {
      return function () { return 'FORGED-FUNCTION'; };
    };
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: '2 + 2',
      chromeTab: 7,
      _did: 'did-poisoned',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'poisoned eval result');
    return {
      result: postedResults[0].result,
      world: postedResults[0].world,
      deliveryId: postedResults[0]._did || null,
      scriptingCalls,
    };
  }

  if (mode === 'midflight') {
    relayContext.eval = (source) => 'FORGED-EVAL:' + source;
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: '2 + 2',
      chromeTab: 7,
      _did: 'did-midflight',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'mid-flight eval result');
    return {
      result: postedResults[0].result === undefined
        ? null : postedResults[0].result,
      error: postedResults[0].error,
      world: postedResults[0].world || null,
      cdpSideEffects,
      scriptingCalls,
    };
  }

  if (mode === 'marker') {
    windowObject.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || message.direction !== 'daedalus-eval') return;
      windowObject.postMessage({
        direction: 'daedalus-eval-result',
        id: message.id,
        relayId: message.relayId,
        r: 'FORGED',
        world: 'scripting',
        hostname: 'cdp',
      });
    });
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise(() => {})',
      _did: 'did-marker',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'forged page result');
    return {
      result: postedResults[0].result,
      world: postedResults[0].world,
      deliveryId: postedResults[0]._did || null,
    };
  }

  if (mode === 'preemption') {
    windowObject.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || message.direction !== 'daedalus-eval') return;
      windowObject.postMessage({
        direction: 'daedalus-eval-result',
        id: message.id,
        relayId: message.relayId,
        r: 'FORGED',
      });
    });
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise((resolve) => {'
        + ' evalResolvers.legit = () => resolve("LEGIT");'
        + ' })',
      _did: 'did-legit',
    };
    const execution = vm.runInContext(
      'dispatchCommand(command)', backgroundContext);
    await waitFor(() => Boolean(evalResolvers.legit), 'evaluation to start');
    evalResolvers.legit();
    await execution;
    await delay();
    return {
      pageEvalMessages: windowMessages.filter(
        (message) => message.direction === 'daedalus-eval').length,
      results: postedResults.map((item) => ({
        result: item.result,
        deliveryId: item._did || null,
      })),
    };
  }

  const commands = ['owner-a', 'owner-b'].map((owner) => ({
    id: '_eval',
    type: 'eval',
    code: 'await new Promise((resolve) => {'
      + ' evalResolvers["' + owner + '"] = () => resolve("' + owner + '");'
      + ' })',
    _did: owner === 'owner-a' ? 'did-a' : 'did-b',
  }));
  backgroundContext.commands = commands;
  vm.runInContext('dispatchCommand(commands[0])', backgroundContext);
  vm.runInContext('dispatchCommand(commands[1])', backgroundContext);
  await waitFor(
    () => Object.keys(evalResolvers).length === 2,
    'both page evaluations to start');

  const evalMessages = windowMessages.filter(
    (message) => message.direction === 'daedalus-eval');
  const firstRelay = evalMessages[0] && evalMessages[0].relayId;
  for (const listener of backgroundListeners) {
    listener({
      type: 'result', id: '_eval', relayId: firstRelay,
      result: 'wrong-tab', error: null, world: '',
    }, { tab: { id: 8 } }, () => {});
  }
  await delay();

  for (const owner of completionOrder) {
    evalResolvers[owner]();
    await waitFor(
      () => postedResults.some((item) => item.result === owner),
      'page result for ' + owner);
  }

  windowObject.postMessage({
    direction: 'daedalus-eval-result',
    id: '_eval',
    relayId: 'not-pending',
    r: 'unrecognised',
  });
  await delay();

  return {
    relayIds: evalMessages.map((message) => message.relayId || null),
    results: postedResults.map((item) => ({
      result: item.result,
      deliveryId: item._did || null,
    })),
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


_CDP_HANDLE_LIFECYCLE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const backgroundPath = process.argv[1];
const released = [];
const postedResults = [];
const finalAwaitPromise = [];
const timers = [];
let pendingResolve;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function eventTarget() {
  return { addListener() {} };
}

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'lifecycle-token',
        'daedalus-server': 'test-bridge',
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {},
    detach: async () => {},
    sendCommand: async (_target, method, params) => {
      if (method === 'Runtime.releaseObject') {
        released.push(params.objectId);
        if (params.objectId === 'pending-original' && pendingResolve) {
          const resolve = pendingResolve;
          pendingResolve = null;
          setImmediate(() => resolve({ result: { objectId: 'pending-late' } }));
        }
        return {};
      }
      if (method === 'Runtime.evaluate') {
        if (params.expression.startsWith('typeof (function')) {
          return {
            result: { objectId: 'compile-result' },
            exceptionDetails: {
              text: 'compile failed',
              exception: {
                objectId: 'compile-exception',
                description: 'compile failed',
              },
            },
          };
        }
        finalAwaitPromise.push(params.awaitPromise);
        if (params.expression.includes('throw-case')) {
          return {
            result: { objectId: 'throw-result' },
            exceptionDetails: {
              text: 'throw failed',
              exception: {
                objectId: 'throw-exception',
                description: 'throw failed',
              },
            },
          };
        }
        if (params.expression.includes('reject-case')) {
          return {
            result: {
              objectId: 'reject-original',
              subtype: 'promise',
            },
          };
        }
        return { result: { value: 1 } };
      }
      if (method === 'Runtime.awaitPromise') {
        if (params.promiseObjectId === 'reject-original') {
          return {
            result: { objectId: 'reject-result' },
            exceptionDetails: {
              text: 'promise rejected',
              exception: {
                objectId: 'reject-exception',
                description: 'promise rejected',
              },
            },
          };
        }
        if (params.promiseObjectId === 'pending-original') {
          return new Promise((resolve) => { pendingResolve = resolve; });
        }
      }
      if (method === 'Runtime.callFunctionOn') {
        return { result: { value: 'settled' } };
      }
      return {};
    },
  },
  scripting: { executeScript: async () => [{ result: false }] },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget(), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init.method === 'POST') {
      postedResults.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    if (url.includes('/stream?')) return new Promise(() => {});
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'lifecycle-id' },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout(callback, ms) {
    const timer = { callback, ms, active: true };
    timers.push(timer);
    return timers.length;
  },
  clearTimeout(id) {
    if (timers[id - 1]) timers[id - 1].active = false;
  },
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function runEval(id, code) {
  context.command = { id, code, tabId: '7', _did: id };
  await vm.runInContext(
    '_evalViaCdp({...command, _execution: _executionContext(command)}, 7)',
    context);
}

(async () => {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  await delay();
  vm.runInContext('_cdpSessions[7] = true', context);

  await runEval('compile', 'return compile-case');
  await runEval('throw', 'throw-case');
  await runEval('reject', 'reject-case');

  context.pendingRemote = {
    objectId: 'pending-original',
    subtype: 'promise',
  };
  const pending = vm.runInContext('_cdpSettle(7, pendingRemote)', context);
  await delay();
  const timer = timers.find((item) => item.active && item.ms === 10000);
  const pendingHasTimeout = Boolean(timer);
  if (timer) {
    timer.active = false;
    timer.callback();
    try { await pending; } catch (_) {}
    await delay();
    await delay();
  }

  process.stdout.write(JSON.stringify({
    released: [...new Set(released)].sort(),
    finalAwaitPromise,
    pendingHasTimeout,
    resultWorlds: postedResults.map((item) => item.world),
  }));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def _run_cdp_handle_lifecycle():
    node = shutil.which('node')
    assert node, 'node is required to execute the CDP lifecycle harness'
    result = subprocess.run(
        [node, '-e', _CDP_HANDLE_LIFECYCLE_HARNESS,
         str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_relay_overlap(order):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval relay'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(ROOT / 'extension' / 'background.js'),
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'), json.dumps(order)],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_same_tab_preemption():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'preemption'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_relay_marker(hostname):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'marker', hostname],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_after_cdp_fails_mid_flight():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'midflight', '', 'midflight'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_with_poisoned_page_globals(cdp_available):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'poisoned', '',
         '1' if cdp_available else '0'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_main_world_injection_shapes():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'injection-shapes'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_main_world_injection_result_shapes_are_explicit(tmp):
    """Every transport shape differs from a valid evaluated `null`."""
    del tmp
    actual = _run_main_world_injection_shapes()
    assert actual == {
        'reject': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: executeScript rejected',
            'world': 'page-main'},
        'empty': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: no result frame',
            'world': 'page-main'},
        'frame-error': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: frame exception',
            'world': 'page-main'},
        'missing-result': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: result frame has no result',
            'world': 'page-main'},
        'bare-null': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: no result envelope',
            'world': 'page-main'},
        'genuine-null': {
            'hasResult': True, 'result': None, 'error': None,
            'world': 'page-main'},
        'eval-exception': {
            'hasResult': False, 'result': None,
            'error': 'operator exception', 'world': 'page-main'},
        'page-substitution': {
            'hasResult': True, 'result': 'PAGE-SUBSTITUTED', 'error': None,
            'world': 'page-main'},
    }, actual


def test_page_replaced_evaluators_use_injection_before_cdp(tmp):
    """A source-free probe keeps ordinary eval on the injection channel."""
    del tmp
    without_cdp = _run_eval_with_poisoned_page_globals(False)
    assert without_cdp == {
        'result': 'FORGED-EVAL:2 + 2',
        'world': 'page-main',
        'deliveryId': 'did-poisoned',
        'scriptingCalls': 2,
    }, without_cdp

    with_cdp = _run_eval_with_poisoned_page_globals(True)
    assert with_cdp == {
        'result': 'FORGED-EVAL:2 + 2',
        'world': 'page-main',
        'deliveryId': 'did-poisoned',
        'scriptingCalls': 2,
    }, with_cdp


def test_cdp_failure_after_dispatch_never_reruns_the_source(tmp):
    """Once the inspector has the source, no other evaluator may run it.

    Falling back after a dispatched evaluation would execute a command's side
    effects a second time, so a mid-flight inspector failure has to surface as
    an error rather than as a page-influenced answer.
    """
    del tmp
    actual = _run_eval_after_cdp_fails_mid_flight()
    assert actual['cdpSideEffects'] == 1, actual
    assert actual['scriptingCalls'] == 1, actual
    assert actual['result'] is None, actual
    # The error still names the channel that executed the command.
    assert actual['world'] == 'cdp', actual
    assert 'inspector detached mid-evaluation' in (actual['error'] or ''), actual


def test_cdp_eval_releases_every_remote_handle_in_held_sessions(tmp):
    """Compile, throw, reject, and pending paths release every CDP handle."""
    del tmp
    actual = _run_cdp_handle_lifecycle()
    assert actual == {
        'released': [
            'compile-exception',
            'compile-result',
            'pending-late',
            'pending-original',
            'reject-exception',
            'reject-original',
            'reject-result',
            'throw-exception',
            'throw-result',
        ],
        'finalAwaitPromise': [False, False, False],
        'pendingHasTimeout': True,
        'resultWorlds': ['cdp', 'cdp', 'cdp'],
    }, actual


def test_eval_relay_same_id_overlap_uses_bounded_invocation_ids(tmp):
    """Eval results retain delivery ids and unknown relay ids are ignored."""
    del tmp
    actual = {
        'a-first': _run_eval_relay_overlap(['owner-a', 'owner-b']),
        'b-first': _run_eval_relay_overlap(['owner-b', 'owner-a']),
    }
    assert actual == {
        'a-first': {
            'relayIds': ['relay-1', 'relay-2'],
            'results': [
                {'result': 'owner-a', 'deliveryId': 'did-a'},
                {'result': 'owner-b', 'deliveryId': 'did-b'},
            ],
        },
        'b-first': {
            'relayIds': ['relay-1', 'relay-2'],
            'results': [
                {'result': 'owner-b', 'deliveryId': 'did-b'},
                {'result': 'owner-a', 'deliveryId': 'did-a'},
            ],
        },
    }, actual


def test_same_tab_page_cannot_preempt_direct_eval_result(tmp):
    """A page-forged relay result cannot win a direct eval invocation."""
    del tmp
    actual = _run_eval_same_tab_preemption()
    assert actual == {
        'pageEvalMessages': 0,
        'results': [{'result': 'LEGIT', 'deliveryId': 'did-legit'}],
    }, actual


def test_page_eval_relay_world_is_namespaced_and_not_page_overridable(tmp):
    """Reserved hostnames and a forged marker stay in the page namespace."""
    del tmp
    hostnames = ('cdp', 'page-main', 'extension', 'page', 'relay.test')
    actual = {
        hostname: _run_eval_relay_marker(hostname)
        for hostname in hostnames
    }
    assert actual == {
        hostname: {
            'result': 'FORGED',
            'world': f'page:{hostname}',
            'deliveryId': 'did-marker',
        }
        for hostname in hostnames
    }, actual


_EXTENSION_RESULT_BOUNDARY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, scenario] = process.argv.slice(1);
const changeListeners = [];
const detachListeners = [];
const sentMessages = [];
const timers = [];
const requests = [];
const resultPayloads = [];
const rules = [];
const createdTabs = [];
const uploadedData = [];
const windowTabs = [
  { id: 7, windowId: 3, active: true, url: 'about:blank#active' },
  { id: 8, windowId: 3, active: false, url: 'about:blank#target' },
];
const activations = [];
const cookieJar = [];
const removeCalls = [];
const storageStore = {
  'daedalus-token': 'initial-token',
  'daedalus-server': 'https://initial.example.com',
};
let captureResolver;
let tabQueryResolver;
let nextTimerId = 0;
let resultAttempts = 0;
let attachCalls = 0;
let detachCalls = 0;

// chrome.storage.local hands back a structured clone, so a reader that has not
// written yet cannot see another writer's in-flight mutation.
function copy(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function eventTarget(listeners = null) {
  return {
    addListener(listener) {
      if (listeners) listeners.push(listener);
    },
  };
}

function schedule(callback, delay) {
  const timer = {
    id: ++nextTimerId,
    callback,
    delay,
    cleared: false,
  };
  timers.push(timer);
  if (scenario === 'route' && (delay === 300 || delay === 600)) {
    setImmediate(() => {
      if (!timer.cleared) callback();
    });
  }
  return timer.id;
}

function clearScheduled(id) {
  const timer = timers.find((candidate) => candidate.id === id);
  if (timer) timer.cleared = true;
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const key of keys) {
          if (key in storageStore) out[key] = copy(storageStore[key]);
        }
        return out;
      },
      set: async (entries) => {
        for (const key of Object.keys(entries)) {
          storageStore[key] = copy(entries[key]);
        }
      },
      remove: async (keys) => {
        for (const key of keys) delete storageStore[key];
      },
    },
    onChanged: eventTarget(changeListeners),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    create: async (details) => {
      createdTabs.push(details);
      return { id: 100 + createdTabs.length, windowId: 1, url: details.url };
    },
    query: async (query) => {
      if (scenario === 'screenshot-target') {
        return windowTabs
          .filter((tab) =>
            (query.active === undefined || tab.active === query.active)
            && (query.windowId === undefined || tab.windowId === query.windowId))
          .map((tab) => ({ ...tab }));
      }
      if (scenario === 'route' && Object.keys(query).length === 0) {
        return new Promise((resolve) => {
          tabQueryResolver = resolve;
        });
      }
      return [{ id: 7, url: 'https://page.example.com' }];
    },
    get: async (tabId) => {
      const known = windowTabs.find((tab) => tab.id === tabId);
      if (known) return { ...known, title: 'Page' };
      return {
        id: tabId,
        windowId: 3,
        url: 'https://page.example.com',
        title: 'Page',
      };
    },
    update: async (tabId, changes) => {
      if (changes && changes.active) {
        activations.push(tabId);
        for (const tab of windowTabs) tab.active = tab.id === tabId;
      }
      const updated = windowTabs.find((tab) => tab.id === tabId);
      return updated ? { ...updated } : { id: tabId, windowId: 3 };
    },
    sendMessage: async (_tabId, message) => {
      sentMessages.push(message);
    },
    captureVisibleTab: async () => {
      if (scenario === 'screenshot-target') {
        // A capture returns whatever is ACTIVE in the window, which is the
        // whole point: naming a tab does not select it.
        const active = windowTabs.find((tab) => tab.active);
        return 'data:image/png;base64,' + btoa('captured:' + (active && active.id));
      }
      if (scenario !== 'route') return 'data:image/png;base64,AA==';
      return new Promise((resolve) => {
        captureResolver = resolve;
      });
    },
  },
  scripting: {
    executeScript: async () => {
      throw new Error('scripting unavailable in residual relay test');
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(detachListeners),
    attach: async () => {
      attachCalls++;
      if (scenario !== 'net-capture') {
        throw new Error('debugger unavailable in residual relay test');
      }
      // Attempt 1 models a tab another client already owns; attempt 2 attaches
      // but fails to enable the domain.
      if (attachCalls === 1) throw new Error('Another debugger is already attached');
    },
    detach: async () => {
      detachCalls++;
    },
    sendCommand: async (_target, method) => {
      if (method === 'Network.enable' && attachCalls === 2) {
        throw new Error('Network.enable failed');
      }
      return {};
    },
  },
  cookies: {
    getAll: async () => cookieJar.map((cookie) => ({ ...cookie })),
    remove: async (details) => {
      removeCalls.push(details);
      // Chrome matches a partitioned cookie only when the partition is named,
      // and answers null when nothing matched -- which is the whole bug: the
      // caller counted a removal that never happened.
      const partition = JSON.stringify(details.partitionKey || null);
      const at = cookieJar.findIndex((cookie) =>
        cookie.name === details.name
        && JSON.stringify(cookie.partitionKey || null) === partition);
      if (at === -1) return null;
      const [gone] = cookieJar.splice(at, 1);
      return { name: gone.name };
    },
  },
  declarativeNetRequest: {
    getSessionRules: async () => rules.map((rule) => ({ ...rule })),
    updateSessionRules: async (change) => {
      for (const rule of change.addRules) {
        if (rules.some((existing) => existing.id === rule.id)) {
          throw new Error('Duplicate rule ID ' + rule.id);
        }
      }
      // Removal is honoured, not ignored: what the unblock scenario asserts
      // is which rules are STILL installed afterwards.
      for (const id of change.removeRuleIds || []) {
        const at = rules.findIndex((existing) => existing.id === id);
        if (at !== -1) rules.splice(at, 1);
      }
      rules.push(...change.addRules);
    },
  },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.endsWith('/upload') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    requests.push({
      kind: 'upload', url, token: payload.token, id: payload.id,
    });
    uploadedData.push(payload.data);
    if (scenario === 'screenshot-reject') {
      return response(400, { error: 'invalid path component' });
    }
    return response(200, { path: 'capture.png', size: 4 });
  }
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    resultPayloads.push(payload);
    requests.push({
      kind: 'result', url, token: payload.token, id: payload.id,
      error: payload.error,
    });
    resultAttempts++;
    if (scenario === 'route' && resultAttempts === 1) {
      return response(503, { error: 'retry' });
    }
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  return response(200, { ok: true });
}

let relaySequence = 0;

// One contextified worker. A second one models the service worker Chrome
// restarts after idle suspension: fresh script state, same browser-side stores.
function makeContext() {
  return vm.createContext({
    chrome,
    fetch: bridgeFetch,
    crypto: { randomUUID: () => 'relay-' + (++relaySequence) },
    AbortController,
    TextDecoder,
    URL,
    performance,
    btoa,
    setTimeout: schedule,
    clearTimeout: clearScheduled,
    setInterval: schedule,
    clearInterval: clearScheduled,
    console: { log() {}, warn() {}, error() {} },
  });
}

const context = makeContext();

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate, label) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return;
    await delay();
  }
  throw new Error('timed out waiting for ' + label);
}

async function runCapacity() {
  context.prefill = Array.from({ length: 1000 }, (_unused, index) => ({
    id: 'existing-' + index,
    _did: 'did-existing-' + index,
  }));
  const relayIds = vm.runInContext(
    "prefill.map((command) => _registerEvalRelay("
      + "_executionContext(command), '7'))",
    context);
  context.nextCommand = {
    id: 'new-at-capacity',
    type: 'eval',
    code: '42',
    chromeTab: 7,
    _did: 'did-new-at-capacity',
  };
  await vm.runInContext('dispatchCommand(nextCommand)', context);
  context.firstRelay = relayIds[0];
  const first = vm.runInContext(
    "_takeEvalRelay(firstRelay, '7')", context);
  return {
    firstId: first && first.id,
    sentMessages: sentMessages.length,
    results: requests.filter((item) => item.kind === 'result'),
  };
}

async function runExpiry() {
  context.slowCommand = {
    id: 'slow-eval',
    _did: 'did-slow-eval',
  };
  const relayId = vm.runInContext(
    "_registerEvalRelay(_executionContext(slowCommand), '7')", context);
  const expiry = timers.find((timer) => timer.delay === 300000);
  if (!expiry) throw new Error('missing 300000 ms relay expiry');
  expiry.callback();
  expiry.callback();
  await delay();
  context.expiredRelay = relayId;
  return {
    stillPending: Boolean(vm.runInContext(
      "_takeEvalRelay(expiredRelay, '7')", context)),
    results: requests.filter((item) => item.kind === 'result'),
  };
}

async function runRouteSnapshot() {
  context.screenshotCommand = {
    id: 'route-snapshot',
    type: 'screenshot',
    _did: 'did-route-snapshot',
  };
  const execution = vm.runInContext(
    'dispatchCommand(screenshotCommand)', context);
  context.blockCommand = {
    id: 'block-route-snapshot',
    type: 'block-requests',
    pattern: '*://media.example.com/*',
    _did: 'did-block-route-snapshot',
  };
  const blockExecution = vm.runInContext(
    'dispatchCommand(blockCommand)', context);
  await waitFor(
    () => Boolean(captureResolver) && Boolean(tabQueryResolver),
    'side operations to start');
  for (const listener of changeListeners) {
    listener({
      'daedalus-token': { newValue: 'replacement-token' },
      'daedalus-server': {
        newValue: 'https://replacement.example.com',
      },
    }, 'local');
  }
  captureResolver('data:image/png;base64,AA==');
  await execution;
  tabQueryResolver([{ id: 7 }]);
  await blockExecution;
  return {
    requests,
    excludedRequestDomains: rules[0]
      ? rules[0].condition.excludedRequestDomains
      : null,
  };
}

async function runScreenshotTarget() {
  context.screenshotCommand = {
    id: 'targeted',
    type: 'screenshot',
    tabId: 8,
    _did: 'did-targeted',
  };
  await vm.runInContext('dispatchCommand(screenshotCommand)', context);
  return {
    captured: uploadedData.length
      ? Buffer.from(uploadedData[0], 'base64').toString() : null,
    activeAfter: (windowTabs.find((tab) => tab.active) || {}).id,
    activations,
    posted: resultPayloads.map((item) => ({
      tabUrl: item.result && item.result.tabUrl, error: item.error,
    })),
  };
}

async function runScreenshotReject() {
  context.screenshotCommand = {
    id: 'bad/id',
    type: 'screenshot',
    _did: 'did-bad-id',
  };
  await vm.runInContext('dispatchCommand(screenshotCommand)', context);
  return {
    uploads: requests.filter((item) => item.kind === 'upload').length,
    posted: resultPayloads.map((item) => ({
      result: item.result === undefined ? '<absent>' : item.result,
      error: item.error,
    })),
  };
}

async function runNetCapture() {
  const outcomes = [];
  for (const step of ['attach-fails', 'enable-fails', 'succeeds']) {
    context.captureCommand = {
      id: 'net-' + step,
      type: 'net-capture',
      tabId: 7,
      _did: 'did-net-' + step,
    };
    await vm.runInContext('dispatchCommand(captureCommand)', context);
    const posted = resultPayloads[resultPayloads.length - 1];
    outcomes.push({ step, result: posted.result, error: posted.error });
  }
  // Chrome detaches us (DevTools opened, target crashed): the capture is over
  // whether or not anything told the worker to stop it.
  for (const listener of detachListeners) listener({ tabId: 7 });
  context.captureCommand = {
    id: 'net-after-detach',
    type: 'net-capture',
    tabId: 7,
    _did: 'did-net-after-detach',
  };
  await vm.runInContext('dispatchCommand(captureCommand)', context);
  const posted = resultPayloads[resultPayloads.length - 1];
  outcomes.push({ step: 'after-detach', result: posted.result, error: posted.error });
  return { outcomes, attachCalls, detachCalls };
}

async function runHotfixRace() {
  context.storeCommands = ['fix-a', 'fix-b'].map((fixId) => ({
    id: 'store-' + fixId,
    type: 'store-hotfix',
    fixId,
    code: 'console.log("' + fixId + '")',
    _did: 'did-store-' + fixId,
  }));
  await vm.runInContext(
    'Promise.all([dispatchCommand(storeCommands[0]),'
    + ' dispatchCommand(storeCommands[1])])', context);
  const stored = storageStore['daedalus-hotfixes'] || { fixes: [] };
  return {
    posted: resultPayloads.map((item) => ({
      result: item.result, error: item.error,
    })),
    storedIds: stored.fixes.map((fix) => fix.id).sort(),
  };
}

async function runBlockRuleRestart() {
  context.blockCommand = {
    id: 'block-first',
    type: 'block-requests',
    pattern: '*://a.example.com/*',
    tabId: 7,
    _did: 'did-block-first',
  };
  await vm.runInContext('dispatchCommand(blockCommand)', context);

  // A restarted worker re-reads the shipped script with a zeroed counter while
  // the session rules it installed earlier are still present.
  const restarted = makeContext();
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), restarted);
  await vm.runInContext('loadConfig()', restarted);
  restarted.blockCommand = {
    id: 'block-after-restart',
    type: 'block-requests',
    pattern: '*://b.example.com/*',
    tabId: 7,
    _did: 'did-block-after-restart',
  };
  await vm.runInContext('dispatchCommand(blockCommand)', restarted);

  // Two adds in flight at once must not settle on one id either.
  restarted.concurrentCommands = ['c', 'd'].map((name) => ({
    id: 'block-' + name,
    type: 'block-requests',
    pattern: '*://' + name + '.example.com/*',
    tabId: 7,
    _did: 'did-block-' + name,
  }));
  await vm.runInContext(
    'Promise.all([dispatchCommand(concurrentCommands[0]),'
    + ' dispatchCommand(concurrentCommands[1])])', restarted);

  return {
    posted: resultPayloads.map((item) => ({
      ruleId: item.result && item.result.ruleId, error: item.error,
    })),
    installedIds: rules.map((rule) => rule.id),
  };
}

async function runUnblockZero() {
  // Three rules already installed, as an operator would have.
  rules.push({ id: 9001 }, { id: 9002 }, { id: 9003 });
  context.unblockCommand = {
    id: 'unblock-zero',
    type: 'unblock-requests',
    ruleId: 0,
    _did: 'did-unblock-zero',
  };
  await vm.runInContext('dispatchCommand(unblockCommand)', context);
  return {
    installedIds: rules.map((rule) => rule.id),
    posted: resultPayloads.map((item) => ({
      removed: item.result && item.result.removed, error: item.error,
    })),
  };
}

function settle() {
  // parseSSEChunk dispatches without awaiting, so let the real event loop
  // drain before looking at what the handler did.
  return new Promise((resolve) => setImmediate(resolve));
}

async function runDedupAcrossRestart() {
  const frame = 'event: command\ndata: ' + JSON.stringify({
    id: 'dedup-open', type: 'open-tab', url: 'about:blank',
    _did: 'did-dedup-1',
  }) + '\n\n';
  const deliver = 'parseSSEChunk(' + JSON.stringify(frame) + ')';

  vm.runInContext(deliver, context);
  for (let turn = 0; turn < 6; turn++) await settle();

  // A fresh worker instance over the SAME extension storage, which is what an
  // MV3 restart is.
  const restarted = makeContext();
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), restarted);
  await vm.runInContext('loadConfig()', restarted);
  vm.runInContext(deliver, restarted);
  for (let turn = 0; turn < 6; turn++) await settle();

  return {
    created: createdTabs.length,
    posted: resultPayloads.map((item) => item._did || null),
  };
}

async function runClearPartitioned() {
  cookieJar.push(
    { name: 'ordinary', domain: 'example.test', path: '/', secure: false },
    { name: 'chips', domain: 'example.test', path: '/', secure: false,
      partitionKey: { topLevelSite: 'http://example.test' } });
  context.clearCommand = {
    id: 'clear-partitioned',
    type: 'clear-cookies',
    url: 'http://example.test/',
    _did: 'did-clear-partitioned',
  };
  await vm.runInContext('dispatchCommand(clearCommand)', context);
  return {
    remaining: cookieJar.map((cookie) => cookie.name),
    posted: resultPayloads.map((item) => ({
      result: item.result, error: item.error,
    })),
    removeCalls: removeCalls.map((details) => ({
      name: details.name, partitionKey: details.partitionKey || null,
    })),
  };
}

async function run() {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  await vm.runInContext('loadConfig()', context);
  if (scenario === 'capacity') return runCapacity();
  if (scenario === 'expiry') return runExpiry();
  if (scenario === 'route') return runRouteSnapshot();
  if (scenario === 'screenshot-reject') return runScreenshotReject();
  if (scenario === 'screenshot-target') return runScreenshotTarget();
  if (scenario === 'net-capture') return runNetCapture();
  if (scenario === 'hotfix-race') return runHotfixRace();
  if (scenario === 'block-rule-restart') return runBlockRuleRestart();
  if (scenario === 'unblock-zero') return runUnblockZero();
  if (scenario === 'clear-partitioned') return runClearPartitioned();
  if (scenario === 'dedup-restart') return runDedupAcrossRestart();
  throw new Error('unknown scenario: ' + scenario);
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def _run_extension_result_boundary(scenario):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension result path'
    result = subprocess.run(
        [node, '-e', _EXTENSION_RESULT_BOUNDARY_HARNESS,
         str(EXTENSION_ROOT / 'background.js'), scenario],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_eval_relay_capacity_rejects_1001st_and_preserves_first(tmp):
    """The 1,001st relay fails while the first live relay remains valid."""
    del tmp
    actual = _run_extension_result_boundary('capacity')
    assert actual == {
        'firstId': 'existing-0',
        'sentMessages': 0,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'new-at-capacity',
            'error': 'Eval relay capacity exceeded',
        }],
    }, actual


def test_eval_relay_expiry_posts_one_timeout_at_300000_ms(tmp):
    """The exact relay TTL removes the entry and posts one terminal error."""
    del tmp
    actual = _run_extension_result_boundary('expiry')
    assert actual == {
        'stillPending': False,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'slow-eval',
            'error': 'Eval relay timed out after 300000 ms',
        }],
    }, actual


def test_result_route_snapshot_covers_retries_and_side_operations(tmp):
    """Config rotation cannot retarget result retries or side operations."""
    del tmp
    actual = _run_extension_result_boundary('route')
    assert actual == {
        'requests': [
            {
                'kind': 'upload',
                'url': 'https://initial.example.com/upload',
                'token': 'initial-token',
                'id': 'route-snapshot',
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'block-route-snapshot',
                'error': None,
            },
        ],
        'excludedRequestDomains': ['initial.example.com'],
    }, actual


def test_a_targeted_screenshot_captures_the_tab_it_names(tmp):
    """Naming a tab has to select it, because capture does not.

    captureVisibleTab captures whatever is active in the WINDOW it is given,
    so a screenshot aimed at an inactive tab returned the active sibling's
    pixels under the requested tab's url and title. Nothing in the answer said
    the image was of a different page.
    """
    del tmp
    actual = _run_extension_result_boundary('screenshot-target')
    assert actual['captured'] == 'captured:8', actual
    assert actual['posted'] == [
        {'tabUrl': 'about:blank#target', 'error': None}], actual
    # And the window is left as it was found.
    assert actual['activeAfter'] == 7, actual
    assert actual['activations'] == [8, 7], actual


def test_rejected_screenshot_upload_is_reported_as_an_error(tmp):
    """A 400 from /upload must not become a success envelope with no path."""
    del tmp
    actual = _run_extension_result_boundary('screenshot-reject')
    assert actual == {
        'uploads': 1,
        'posted': [{
            'result': None,
            'error': 'Screenshot upload failed: invalid path component',
        }],
    }, actual


def test_failed_net_capture_setup_leaves_no_capture_and_no_attachment(tmp):
    """Attach and enable failures roll back; a detach ends the capture."""
    del tmp
    actual = _run_extension_result_boundary('net-capture')
    assert actual == {
        'outcomes': [
            {
                'step': 'attach-fails',
                'result': None,
                'error': 'Another debugger is already attached',
            },
            {
                'step': 'enable-fails',
                'result': None,
                'error': 'Network.enable failed',
            },
            {
                'step': 'succeeds',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
            {
                'step': 'after-detach',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
        ],
        # One attach per call — a failed setup never answers `already: true`.
        'attachCalls': 4,
        # Only the enable failure had an attachment to give back.
        'detachCalls': 1,
    }, actual


def test_concurrent_hotfix_stores_both_survive(tmp):
    """Two stores dispatched together must both be in the record afterwards."""
    del tmp
    actual = _run_extension_result_boundary('hotfix-race')
    assert actual == {
        'posted': [
            {
                'result': {'stored': 'fix-a', 'total': 1, 'permanent': False},
                'error': None,
            },
            {
                'result': {'stored': 'fix-b', 'total': 2, 'permanent': False},
                'error': None,
            },
        ],
        'storedIds': ['fix-a', 'fix-b'],
    }, actual


def test_a_delivery_id_is_spent_once_across_worker_restarts(tmp):
    """At-most-once has to survive the worker, or it is at-most-once per boot.

    The ledger of spent delivery ids was module state, so an MV3 restart
    emptied it. The bridge redelivers a command whose socket write succeeded
    but whose unlink did not, which is exactly the case dedup exists for — and
    a worker that restarted in between executed it a second time.
    """
    del tmp
    actual = _run_extension_result_boundary('dedup-restart')
    assert actual['created'] == 1, actual
    assert actual['posted'].count('did-dedup-1') == 1, actual


def test_clearing_cookies_removes_the_partitioned_ones_too(tmp):
    """A cookie the browser refused to remove must not be counted as removed.

    `chrome.cookies.remove` matches a partitioned cookie only when the
    partition is named, and the call dropped `partitionKey` — so a CHIPS
    cookie stayed readable while the count said it had gone. The count was
    incremented per iteration rather than per removal, which is what let the
    two disagree in the first place.
    """
    del tmp
    actual = _run_extension_result_boundary('clear-partitioned')
    assert actual['remaining'] == [], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'] is None, actual
    assert actual['posted'][0]['result']['removed'] == 2, actual
    assert actual['posted'][0]['result']['failed'] == [], actual
    partitioned = [call for call in actual['removeCalls']
                   if call['partitionKey']]
    assert len(partitioned) == 1, actual['removeCalls']


def test_rule_id_zero_is_refused_rather_than_removing_everything(tmp):
    """A specific id that is invalid must not widen into remove-all.

    `if (cmd.ruleId)` is false for 0, so `unblock-requests` with ruleId 0 fell
    through to the branch that removes every session rule and reported them as
    removed. The narrowest possible request destroyed the most.
    """
    del tmp
    actual = _run_extension_result_boundary('unblock-zero')
    assert actual['installedIds'] == [9001, 9002, 9003], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'], actual
    assert actual['posted'][0]['removed'] is None, actual


def test_block_rule_ids_survive_a_worker_restart(tmp):
    """Session rules outlive the worker, so ids must not restart at the base."""
    del tmp
    actual = _run_extension_result_boundary('block-rule-restart')
    assert actual == {
        'posted': [
            {'ruleId': 9001, 'error': None},
            {'ruleId': 9002, 'error': None},
            {'ruleId': 9003, 'error': None},
            {'ruleId': 9004, 'error': None},
        ],
        'installedIds': [9001, 9002, 9003, 9004],
    }, actual


_CONTENT_KEEPALIVE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const timers = [];
const intervals = [];
const ports = [];
let nextId = 0;

function scheduled(collection, callback, delay) {
  const item = { id: ++nextId, callback, delay, cleared: false };
  collection.push(item);
  return item.id;
}

function clearScheduled(collection, id) {
  const item = collection.find((candidate) => candidate.id === id);
  if (item) item.cleared = true;
}

function eventTarget(listeners) {
  return { addListener(listener) { listeners.push(listener); } };
}

const windowObject = {
  addEventListener() {},
  postMessage() {},
};
const chrome = {
  runtime: {
    lastError: null,
    onMessage: eventTarget([]),
    sendMessage() {},
    connect() {
      const disconnectListeners = [];
      const port = {
        messages: [],
        disconnectListeners,
        postMessage(message) { port.messages.push(message); },
        disconnect() {},
        onDisconnect: eventTarget(disconnectListeners),
      };
      ports.push(port);
      return port;
    },
    getManifest: () => ({ version: '0.18.0' }),
  },
  storage: {
    local: {
      get(_keys, callback) { callback({}); },
      set(_data, callback) { if (callback) callback(); },
      remove(_keys, callback) { if (callback) callback(); },
    },
  },
};
const context = vm.createContext({
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: '' },
  setTimeout: (callback, delay) => scheduled(timers, callback, delay),
  clearTimeout: (id) => clearScheduled(timers, id),
  setInterval: (callback, delay) => scheduled(intervals, callback, delay),
  clearInterval: (id) => clearScheduled(intervals, id),
  console: { log() {}, error() {} },
});

vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const firstProactive = timers.find((item) => item.delay === 4 * 60 * 1000);
firstProactive.callback();
const secondInterval = intervals[intervals.length - 1];
for (const listener of ports[0].disconnectListeners) listener();
secondInterval.callback();

process.stdout.write(JSON.stringify({
  portCount: ports.length,
  port2Pings: ports[1].messages.length,
  interval2Cleared: secondInterval.cleared,
  retryTimers: timers.filter((item) => item.delay === 500).length,
}));
"""


def test_stale_keepalive_disconnect_cannot_clobber_replacement_port(tmp):
    """A retired port callback cannot clear or replace the current port."""
    del tmp
    node = shutil.which('node')
    assert node, 'node is required to execute the content keep-alive lifecycle'
    result = subprocess.run(
        [node, '-e', _CONTENT_KEEPALIVE_HARNESS,
         str(ROOT / 'extension' / 'content.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    actual = json.loads(result.stdout)
    assert actual == {
        'portCount': 2,
        'port2Pings': 1,
        'interval2Cleared': False,
        'retryTimers': 0,
    }, actual


_DASHBOARD_CONSUME_HARNESS = r"""
const fs = require('fs');

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

(async () => {
  const tokenKey = 'daedalus-token';
  globalThis.localStorage = {
    getItem: key => key === tokenKey ? 'dashboard-token' : '',
    setItem: () => {},
  };
  globalThis.setTimeout = callback => { callback(); return 0; };

  let commandSent = false;
  globalThis.fetch = async (target, init = {}) => {
    const method = init.method || 'GET';
    if (method === 'PUT') {
      commandSent = true;
      return response(200, { ok: true, did: 'command-delivery' });
    }
    if (String(target).includes('consume=1')) {
      if (!commandSent) return response(200, { pending: true });
      return response(500, { error: 'consume failed' });
    }
    return response(200, {
      id: 'dashboard-command',
      deliveryId: 'command-delivery',
      resultGeneration: 'result-generation',
      result: 'fresh',
      error: null,
      world: 'page:cdp',
    });
  };

  const source = fs.readFileSync(process.argv[1], 'utf8');
  const moduleUrl = 'data:text/javascript;base64,'
    + Buffer.from(source).toString('base64');
  const dashboard = await import(moduleUrl);
  let rejected = false;
  try {
    await dashboard.runCommand({
      type: 'cookies', id: 'dashboard-command', timeout: 1000,
    });
  } catch (error) {
    rejected = true;
    if (!String(error.message).includes('HTTP 500')) throw error;
  }
  if (!rejected) throw new Error('failed consume surfaced as a successful read');
})().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""


_DASHBOARD_WORLD_HARNESS = r"""
const fs = require('fs');

(async () => {
  const source = fs.readFileSync(process.argv[1], 'utf8');
  const moduleUrl = 'data:text/javascript;base64,'
    + Buffer.from(source).toString('base64');
  const dashboard = await import(moduleUrl);
  process.stdout.write(JSON.stringify([
    dashboard.formatEvalWorld('cdp'),
    dashboard.formatEvalWorld('page-main'),
    dashboard.formatEvalWorld('page:cdp'),
    dashboard.formatEvalWorld('extension'),
    dashboard.formatEvalWorld('module-main'),
  ]));
})().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""


def _blank_js_comments(source):
    """Return `source` with comments blanked and string literals intact.

    A single forward walk, because a `//` inside a string literal does not
    start a comment. Regex literals are NOT modelled: one containing a quote
    or a comment opener would desynchronise this walk. The dashboard sources
    contain none, and the scanner below would report a violation rather than
    stay silent if that changed, which is the direction an unmodelled shape
    should fail in.
    """
    out = []
    index, end = 0, len(source)
    quote = None
    while index < end:
        char = source[index]
        if quote:
            out.append(char)
            if char == '\\' and index + 1 < end:
                out.append(source[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in '\'"`':
            quote = char
            out.append(char)
            index += 1
            continue
        if char == '/' and source[index + 1:index + 2] == '/':
            while index < end and source[index] != '\n':
                out.append(' ')
                index += 1
            continue
        if char == '/' and source[index + 1:index + 2] == '*':
            while index < end and source[index:index + 2] != '*/':
                out.append('\n' if source[index] == '\n' else ' ')
                index += 1
            out.append('  ')
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def _expression_after(source, start):
    """Return the text from `start` to the statement's terminating `;`."""
    index, end = start, len(source)
    depth = 0
    quote = None
    while index < end:
        char = source[index]
        if quote:
            if char == '\\':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in '\'"`':
            quote = char
        elif char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ';' and depth == 0:
            return source[start:index]
        index += 1
    return source[start:]


_CONSTANT_MARKUP = re.compile(
    r"^(?:'(?:[^'\\\n]|\\.)*'"
    r'|"(?:[^"\\\n]|\\.)*"'
    r'|`(?:[^`\\$]|\\.|\$(?!\{))*`)$')


def test_dashboard_never_builds_markup_from_a_value(tmp):
    """innerHTML in the dashboard is only ever a constant.

    Several catch paths concatenated an error string — or an
    extension-supplied reason — into innerHTML, so text the dashboard did not
    author could become markup. The rule enforced here is the one that keeps
    itself true: a value reaches the page through text nodes or the `h`
    helper, never through markup, so an assignment that is not a literal is a
    violation whatever the value happens to be today. `+=` never qualifies.
    """
    del tmp
    violations = []
    sources = sorted((ROOT / 'dashboard').rglob('*.js'))
    assert sources, 'no dashboard sources found'
    for path in sources:
        blanked = _blank_js_comments(path.read_text(encoding='utf-8'))
        for match in re.finditer(r'\.innerHTML\s*(\+?=)(?!=)', blanked):
            line = blanked.count('\n', 0, match.start()) + 1
            expression = _expression_after(blanked, match.end()).strip()
            if match.group(1) == '=' and _CONSTANT_MARKUP.match(expression):
                continue
            violations.append(
                f'{path.relative_to(ROOT)}:{line}: '
                f'innerHTML {match.group(1)} {expression[:80]}')
    assert not violations, '\n'.join(violations)


def test_dashboard_labels_eval_world_as_a_channel(tmp):
    """Dashboard text presents `world` only as execution-channel metadata."""
    del tmp
    node = shutil.which('node')
    assert node, 'node is required to execute the dashboard world formatter'
    result = subprocess.run(
        [node, '-e', _DASHBOARD_WORLD_HARNESS,
         str(ROOT / 'dashboard' / 'sections' / '_util.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert json.loads(result.stdout) == [
        'channel=cdp',
        'channel=page-main',
        'channel=page:cdp',
        'channel=extension',
        'channel=module-main',
    ]


def test_dashboard_failed_consume_is_not_a_success(tmp):
    """The dashboard must reject when its matching-result consume fails."""
    node = shutil.which('node')
    assert node, 'node is required to execute the dashboard API boundary'
    result = subprocess.run(
        [node, '-e', _DASHBOARD_CONSUME_HARNESS,
         str(ROOT / 'dashboard' / 'api.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


def test_console_scripts_resolve(tmp):
    data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    scripts = data['project']['scripts']
    assert scripts, 'pyproject declares no console scripts'
    sys.path.insert(0, str(ROOT))
    try:
        for name, spec in scripts.items():
            mod_name, _, attr = spec.partition(':')
            assert attr, f'{name}: {spec!r} is not a module:attr spec'
            mod = importlib.import_module(mod_name)
            target = getattr(mod, attr, None)
            assert callable(target), f'{name}: {spec} does not resolve to a callable'
    finally:
        sys.path.remove(str(ROOT))


def _copy_versioned_tree(dest):
    """Copy just the files check_versions.py reads, preserving layout."""
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py', 'check_versions_ro')
    rel_paths = {p for p, _, _ in checker.SITES}
    rel_paths.add('scripts/check_versions.py')
    for rel in rel_paths:
        src = ROOT / rel
        dst = Path(dest) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return checker


def _durations_tree(tmp, side, rounds):
    """Write one summary directory per round, as run_tests.py would."""
    dirs = []
    for index, tests in enumerate(rounds, start=1):
        d = Path(tmp) / f'{side}-{index}'
        d.mkdir(parents=True)
        (d / 'test_suite.json').write_text(json.dumps({
            'total': len(tests), 'passed': len(tests),
            'skipped': 0, 'failed': 0, 'tests': tests,
        }), encoding='utf-8')
        dirs.append(str(d))
    return dirs


def _compare_durations():
    return _util.load(ROOT / 'scripts' / 'ci' / 'compare_durations.py')


def test_speed_comparison_takes_each_test_s_minimum_across_rounds(tmp):
    """The floor is what moves when code gets slower, so rounds take a min.

    A single test can move by multiples between two runs of identical code.
    Averaging carries that noise into the verdict; the minimum estimates the
    quantity that actually changes.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 9.0}, {'a': 1.0}])
    head = _durations_tree(tmp, 'head', [{'a': 8.0}, {'a': 1.0}])
    assert compare.side_durations(base) == {'a': 1.0}
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_ignores_a_test_only_one_side_ran(tmp):
    """Adding or removing a test must not move the number.

    This is the whole reason the comparison is per-test rather than a suite
    total: a release that grew three tests would otherwise read as a
    regression, and one that deleted three as an improvement.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'shared': 1.0, 'gone': 50.0}])
    head = _durations_tree(tmp, 'head', [{'shared': 1.0, 'added': 50.0}])
    shared, base_total, head_total, _moves = compare.compare(
        compare.side_durations(base), compare.side_durations(head))
    assert shared == ['shared'], shared
    assert (base_total, head_total) == (1.0, 1.0), (base_total, head_total)
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_fails_only_past_its_budget(tmp):
    """A slowdown inside the budget passes; one past it fails."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 1.0}])
    within = _durations_tree(tmp, 'within', [{'a': 1.2}])
    past = _durations_tree(tmp, 'past', [{'a': 1.4}])
    assert compare.main(['--base', *base, '--head', *within,
                         '--max-regression', '0.30']) == 0
    assert compare.main(['--base', *base, '--head', *past,
                         '--max-regression', '0.30']) == 1


def test_speed_comparison_passes_when_a_side_was_never_measured(tmp):
    """An unmeasured side is not a fast side, and must not divide the total.

    The timing instrument belongs to the comparison rather than to either
    checkout, so an empty side means its timing step failed. A comparator that
    treated that as zero seconds would report an infinite speedup.
    """
    compare = _compare_durations()
    old = Path(tmp) / 'base-1'
    old.mkdir(parents=True)
    (old / 'test_suite.json').write_text(json.dumps({
        'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,
    }), encoding='utf-8')
    head = _durations_tree(tmp, 'head', [{'a': 1.0}])
    summary = Path(tmp) / 'summary.md'
    assert compare.main(['--base', str(old), '--head', *head,
                         '--summary-file', str(summary)]) == 0
    assert 'produced no per-test durations' in summary.read_text(encoding='utf-8')


def test_check_versions_passes_on_tree(tmp):
    r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'check_versions.py')],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert 'ok:' in r.stdout, r.stdout


def test_check_versions_site_list_covers_package(tmp):
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py', 'check_versions_sites')
    site_keys = {(p, d) for p, d, _ in checker.SITES}
    # The package __version__ is a version claim about the wire format; it must
    # be one of the checked sites, or the wheel can drift from the extension.
    assert ('daedalus_cli/__init__.py', 'package __version__') in site_keys, site_keys
    assert checker.CANONICAL in site_keys, checker.CANONICAL


def test_check_versions_detects_drift(tmp):
    copy_root = Path(tmp) / 'tree'
    _copy_versioned_tree(copy_root)
    # Break exactly one site, in the COPY — never the real files.
    init_copy = copy_root / 'daedalus_cli' / '__init__.py'
    text = init_copy.read_text(encoding='utf-8')
    new_text, n = re.subn(r'__version__\s*=\s*"[^"]+"',
                          '__version__ = "0.0.0-drift"', text)
    assert n == 1, text
    init_copy.write_text(new_text, encoding='utf-8')
    r = subprocess.run([sys.executable, str(copy_root / 'scripts' / 'check_versions.py')],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert 'FAIL' in r.stderr, r.stderr
    assert 'daedalus_cli/__init__.py' in r.stderr, r.stderr


def test_check_versions_sites_all_present_in_copy(tmp):
    """The drift test copies every file the checker reads — prove the copy is
    complete, so a missing file can never masquerade as a version mismatch."""
    copy_root = Path(tmp) / 'tree'
    checker = _copy_versioned_tree(copy_root)
    r = subprocess.run([sys.executable, str(copy_root / 'scripts' / 'check_versions.py')],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    expected = len({(p, d) for p, d, _ in checker.SITES})
    m = re.search(r'consistent across (\d+) sites', r.stdout)
    assert m and int(m.group(1)) == expected, r.stdout


# GM.info is metadata about the shim, not a capability it grants, so the
# install-time warning has nothing to say about it.
_GM_NON_CAPABILITIES = frozenset({'GM.info'})


def test_the_security_warning_names_every_capability_the_shim_grants(tmp):
    """The warning has to keep up with the surface it is warning about.

    It described the consequence as cross-origin requests, while the same
    page-facing relay also opened tabs, started downloads, raised
    notifications, wrote the clipboard and shared extension-wide storage
    between origins. Those were all in the API table and none of them in the
    warning, which is the half a reader makes an install decision from.
    """
    del tmp
    readme = (_util.ROOT / 'README.md').read_text(encoding='utf-8')
    _, _, after = readme.partition('## GM Bridge')
    table, _, _ = after.partition('## Architecture')
    granted = {f'GM.{name}' for name in re.findall(r'`GM\.([a-zA-Z]+)', table)}
    granted -= _GM_NON_CAPABILITIES
    assert len(granted) > 5, granted  # the table was found and parsed

    _, _, after = readme.partition('## Security')
    warning, _, _ = after.partition('**The bridge token and server URL')
    missing = sorted(name for name in granted if name not in warning)
    assert not missing, f'not named in the install-time warning: {missing}'


def test_the_extension_never_logs_the_bridge_token(tmp):
    """The token is a reusable browser-control credential, not a diagnostic.

    First-run bootstrap printed the whole generated token, which put it into
    extension DevTools output, screen recordings and any diagnostic bundle
    collected from them — all places a credential outlives the moment it was
    useful in. A truncated prefix is not what this pins: the version banner
    logs eight characters to say which bridge is configured, and that stays.
    """
    del tmp
    offenders = []
    for name in ('background.js', 'content.js', 'page.js', 'options.js'):
        path = _util.ROOT / 'extension' / name
        if not path.is_file():
            continue
        for number, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), 1):
            if 'console.' not in line or '.token' not in line:
                continue
            # A short prefix is a legitimate diagnostic — the version banner
            # prints one to identify which bridge this extension is talking
            # to. The whole value is the credential itself.
            if '.token.substring(' in line or '.token.slice(' in line:
                continue
            offenders.append(f'{name}:{number}: {line.strip()}')
    assert not offenders, offenders


def test_extension_ships_no_default_server(tmp):
    src = (ROOT / 'extension' / 'background.js').read_text(encoding='utf-8')
    # The constant exists and is empty: an unconfigured install must not dial
    # anything.
    m = re.search(r"const\s+DEFAULT_SERVER\s*=\s*'([^']*)'", src)
    assert m, 'DEFAULT_SERVER constant not found in background.js'
    assert m.group(1) == '', f'DEFAULT_SERVER ships a URL: {m.group(1)!r}'
    # No hardcoded bridge URL anywhere else in the service worker either.
    assert 'http://' not in src and 'https://' not in src, \
        'background.js contains a hardcoded URL'


def test_extension_startstream_stays_idle_without_url(tmp):
    src = (ROOT / 'extension' / 'background.js').read_text(encoding='utf-8')
    start = src.index('async function startStream()')
    rest = src[start:]
    nxt = rest.find('\nasync function ', 1)
    body = rest[:nxt] if nxt != -1 else rest
    guard = 'if (!config.serverUrl) return;'
    assert guard in body, 'startStream() lost its no-server-URL guard'
    # The guard must come before the first fetch the stream would make.
    assert body.index(guard) < body.index('fetch('), \
        'startStream() fetches before refusing an empty server URL'


def _iter_tree_files():
    """Yield every path Git tracks for the public release."""
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True,
        check=True, timeout=30)
    paths = [path for path in listed.stdout.split(b'\0') if path]
    assert paths, 'Git returned no tracked release paths'
    for path in paths:
        yield ROOT / os.fsdecode(path)


def test_release_scanners_reject_empty_git_enumeration(tmp):
    """Both scanners reject a successful empty tracked-file enumeration."""
    global ROOT
    release = Path(tmp) / 'empty-release'
    release.mkdir()
    subprocess.run(['git', '-C', str(release), 'init', '-q'], check=True)

    real_root = ROOT
    ROOT = release
    accepted = []
    try:
        scanners = (
            test_no_deployment_strings_in_tree,
            test_no_hardcoded_deployment_urls,
        )
        for scanner in scanners:
            try:
                scanner(tmp)
            except AssertionError as failure:
                message = str(failure)
                assert 'Git returned no tracked release paths' in message, failure
            else:
                accepted.append(scanner.__name__)
    finally:
        ROOT = real_root
    assert not accepted, f'scanners accepted an empty Git enumeration: {accepted}'


def test_release_scanner_enumeration_matches_tracked_files(tmp):
    """The scanner input is the non-empty set of 70 tracked release paths."""
    del tmp
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True,
        check=True, timeout=30)
    tracked = {
        ROOT / os.fsdecode(path)
        for path in listed.stdout.split(b'\0') if path
    }
    enumerated = set(_iter_tree_files())
    assert tracked, 'Git returned no tracked release paths'
    assert len(tracked) == 70, f'expected 70 tracked paths, found {len(tracked)}'
    assert tracked - enumerated == set(), (
        f'tracked paths omitted from scanner input: {tracked - enumerated}')
    assert enumerated - tracked == set(), (
        f'non-tracked paths included in scanner input: {enumerated - tracked}')


def test_no_deployment_strings_in_tree(tmp):
    """No shipped file may name a host or an absolute path off this machine.

    This asserts a PROPERTY rather than a list of forbidden strings. The
    previous version carried the private hostname and docroot as needles, split
    across a concatenation so the file would not match itself — which published
    the very strings the scrub existed to remove, reconstructible by anyone who
    read the test. A rule that can only be written by quoting the secret is the
    wrong rule.
    """
    # Hosts a public release may legitimately contact or document.
    allowed_hosts = {
        'example.com', 'daedalus.example.com', 'example.org',
        '127.0.0.1', 'localhost', 'github.com',
    }
    # Absolute paths that describe one machine's layout rather than a standard
    # location. /srv and /tmp are generic; a home directory or a webroot is not.
    private_roots = re.compile(r'(?<![\w.])/(?:var/www|root|home/[a-z])[\w./-]*')
    url_host = re.compile(r'https?://([a-zA-Z0-9._-]+(?::\d+)?)')

    violations = []
    for path in _iter_tree_files():
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        if path.name == os.path.basename(__file__):
            continue                      # the patterns above, not findings
        for match in url_host.finditer(text):
            host = match.group(1).split(':')[0]
            if host not in allowed_hosts:
                violations.append(f'{path}: non-allowlisted host {host}')
        for match in private_roots.finditer(text):
            violations.append(f'{path}: machine-specific path {match.group(0)}')
    assert not violations, 'deployment strings in the release tree:\n' + '\n'.join(violations)


def test_no_hardcoded_deployment_urls(tmp):
    # A public tree may reference documentation hosts only. Anything else in an
    # https:// URL is either a deployment endpoint or a call home, and neither
    # ships.
    #
    # The font hosts used to be allowed here, which meant this test would have
    # watched the dashboard fetch a webfont from Google on every load without
    # objecting. The stylesheet now uses a local font stack, so the allowance
    # goes with it: a re-introduced @import fails this test.
    allowed_exact = {'github.com'}
    violations = []
    for path in _iter_tree_files():
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            for host in re.findall(r'https://([A-Za-z0-9.-]+)', line):
                ok = (host in allowed_exact or host == 'example.com'
                      or host.endswith('.example.com'))
                if not ok:
                    violations.append(f'{rel}:{i}: https://{host}')
    assert not violations, '\n'.join(violations)


def test_release_scanners_ignore_caches_and_scan_published_files(tmp):
    """Both scanners reject tracked violations and ignore untracked caches."""
    global ROOT
    release = Path(tmp) / 'release'
    release.mkdir()
    published = release / 'published.txt'
    published.write_text('public release content\n', encoding='utf-8')
    unmanifested = release / 'unmanifested.txt'
    unmanifested.write_text('tracked release content\n', encoding='utf-8')
    (release / '.gitignore').write_text(
        '*\n!/.gitignore\n!/published.txt\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(release), 'init', '-q'], check=True)
    subprocess.run(
        ['git', '-C', str(release), 'add', '-f', '.gitignore',
         'published.txt', 'unmanifested.txt'], check=True)

    cache_url = 'https://' + 'cache-only' + '.invalid'
    for directory in ('.pytest_cache', '__pycache__'):
        cache = release / directory
        cache.mkdir()
        (cache / 'content.txt').write_text(cache_url, encoding='utf-8')

    real_root = ROOT
    ROOT = release
    try:
        scanners = (
            test_no_deployment_strings_in_tree,
            test_no_hardcoded_deployment_urls,
        )
        for scanner in scanners:
            scanner(tmp)

        for path in (unmanifested, published):
            path.write_text(
                'https://' + 'tracked-violation' + '.invalid', encoding='utf-8')
            for scanner in scanners:
                try:
                    scanner(tmp)
                except AssertionError as failure:
                    assert path.name in str(failure), failure
                else:
                    raise AssertionError(
                        f'{scanner.__name__} missed tracked {path.name}')
            path.write_text('tracked release content\n', encoding='utf-8')
    finally:
        ROOT = real_root


def test_actionlint_lints_every_workflow_extension_github_accepts(tmp):
    """The gate on the gates must not skip a workflow it triggered on.

    The job fires on every file under .github/workflows and zizmor scans the
    whole directory, but actionlint was handed `.github/workflows/*.yml`
    alone. A workflow using GitHub's other accepted extension would therefore
    start this gate and be skipped by it — the silent-stop failure mode the
    workflow's own header says the other gates cannot catch.
    """
    del tmp
    workflow = (_util.ROOT / '.github' / 'workflows' / 'actionlint.yml').read_text(
        encoding='utf-8')
    _, marker, after = workflow.partition('- name: actionlint\n')
    assert marker, 'the actionlint step is not named the way this test finds it'
    step, _, _ = after.partition('- name: zizmor')
    for pattern in ('.github/workflows/*.yml', '.github/workflows/*.yaml'):
        assert pattern in step, (pattern, step)
    # An extension nothing matches must not reach actionlint as a literal
    # pattern, and a directory holding no workflows at all must not read as a
    # clean lint — both would be the same silent pass in a different place.
    assert 'nullglob' in step, step
    assert 'exit 1' in step, step


def test_the_coverage_ratchet_records_what_it_was_calibrated_to(tmp):
    """The floor, the flag and the measurement it was set against agree.

    The ratchet is raised by hand, so nothing keeps it near the number it was
    calibrated against except someone remembering — and it had drifted 3.2
    points below measured coverage, which is a regression budget rather than
    a ratchet. Pinning the recorded measurement next to the floor makes
    raising one without the other fail here instead of silently widening it.
    """
    del tmp
    workflow = (_util.ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    measured = re.search(r'#\s*measured:\s*([0-9.]+)', workflow)
    floor = re.search(r'#\s*floor:\s*([0-9.]+)', workflow)
    assert measured and floor, 'the coverage gate records no calibration'
    measured, floor = float(measured.group(1)), float(floor.group(1))
    flag = re.search(r'--fail-under=([0-9.]+)', workflow)
    assert flag, workflow
    assert float(flag.group(1)) == floor, (flag.group(1), floor)
    assert floor < measured, (floor, measured)
    assert measured - floor <= 2.0, (
        f'the ratchet allows a {measured - floor:.1f} point regression')


def test_manifest_version_matches_package(tmp):
    # check_versions.py covers this, but the manifest translation rule
    # (0.16.0a -> 0.16.0.1) is subtle enough to pin directly.
    checker = _util.load(ROOT / 'scripts' / 'check_versions.py', 'check_versions_mv')
    found = checker.collect()
    versions = {(p, d): v for p, d, v in found}
    canonical = versions[checker.CANONICAL]
    assert versions[checker.MANIFEST] == checker.manifest_form(canonical)
    pkg = json.loads((ROOT / 'extension' / 'manifest.json').read_text(encoding='utf-8'))
    assert pkg['version'] == versions[checker.MANIFEST]


# ─── Typed-command routing guard: `tab` vs `tabId` ───
# `tab` routes to a server queue; `tabId` names a browser tab. A typed
# extension command routes to `tab: 'extension'`; sending a tab number as
# `tab` silently retargets it. Within the statically resolved shapes documented
# below, the scan is structural rather than value-shaped: Python senders are
# parsed with `ast` and JavaScript is read with a bracket-matching scanner, so
# the value expression and line wrapping do not weaken the check.


def _scope_nodes(scope):
    """Every node in `scope`, not descending into nested functions or lambdas:
    each function's locals are tracked as a separate scope."""
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _scope_nodes(child)


_OPAQUE_TAB_SPREAD = object()


def _merge_payload_keys(keys, spread, spread_node):
    """Apply one Python mapping spread to tracked payload keys.

    An unresolved spread makes the resulting `tab` value opaque. A later
    explicit `tab` write clears that uncertainty because dict construction is
    ordered and the later value wins.
    """
    if spread is None:
        keys[_OPAQUE_TAB_SPREAD] = (spread_node.lineno, spread_node)
        return
    if _OPAQUE_TAB_SPREAD in spread:
        keys[_OPAQUE_TAB_SPREAD] = (spread_node.lineno, spread_node)
    if 'tab' in spread:
        keys.pop(_OPAQUE_TAB_SPREAD, None)
    keys.update((key, value) for key, value in spread.items()
                if key is not _OPAQUE_TAB_SPREAD)


def _payload_keys(expr, dicts):
    """Tracked string keys, or None for a wholly opaque expression.

    `**spread` of a tracked name merges, `dict(k=v, ...)` is the same mapping
    spelled as a call, and an unresolved spread records an opaque `tab` value
    instead of trusting the literal keys around it.
    """
    if isinstance(expr, ast.Name):
        return dicts.get(expr.id)
    if isinstance(expr, ast.Dict):
        keys = {}
        for k, v in zip(expr.keys, expr.values):
            if k is None:
                _merge_payload_keys(keys, _payload_keys(v, dicts), v)
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                if k.value == 'tab':
                    keys.pop(_OPAQUE_TAB_SPREAD, None)
                keys[k.value] = (v.lineno, v)
        return keys
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
            and expr.func.id == 'dict'):
        if expr.args:
            return None                    # positional source is opaque
        keys = {}
        for kw in expr.keywords:
            if kw.arg is None:
                spread = _payload_keys(kw.value, dicts)
                _merge_payload_keys(keys, spread, kw.value)
            else:
                if kw.arg == 'tab':
                    keys.pop(_OPAQUE_TAB_SPREAD, None)
                keys[kw.arg] = (kw.value.lineno, kw.value)
        return keys
    return None


def test_positional_dict_copy_is_opaque_but_later_tab_write_is_tracked(tmp):
    """A positional dict source is opaque; later explicit keys are tracked."""
    del tmp
    tree = ast.parse(
        "cmd = dict(BASE)\n"
        "cmd['tab'] = tab_id\n"
        "api('PUT', '/command', cmd)\n")
    assert _payload_keys(tree.body[0].value, {}) is None
    keys = _dict_assignments(tree)['cmd']
    assert keys['tab'][0] == 2


def _update_keys(call, dicts):
    """The keys `d.update(...)` merges: the positional mapping when it
    resolves, plus keywords. None when any part is opaque."""
    keys = {}
    if call.args:
        merged = _payload_keys(call.args[0], dicts)
        if merged is None:
            return None
        keys.update(merged)
    for kw in call.keywords:
        if kw.arg is None:
            return None                    # d.update(**opaque)
        if kw.arg == 'tab':
            keys.pop(_OPAQUE_TAB_SPREAD, None)
        keys[kw.arg] = (kw.value.lineno, kw.value)
    return keys


def _apply_dict_statement(node, dicts):
    """Apply one assignment or mapping mutation to a tracked Python state."""
    if isinstance(node, ast.AugAssign):          # d |= {...}
        if isinstance(node.op, ast.BitOr) and isinstance(node.target, ast.Name):
            merged = _payload_keys(node.value, dicts)
            if merged is None:
                dicts.pop(node.target.id, None)
            else:
                _merge_payload_keys(
                    dicts.setdefault(node.target.id, {}), merged, node.value)
        return
    if isinstance(node, ast.Expr):               # d.update({...})
        call = node.value
        if (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'update'
                and isinstance(call.func.value, ast.Name)):
            merged = _update_keys(call, dicts)
            if merged is None:
                dicts.pop(call.func.value.id, None)
            else:
                _merge_payload_keys(
                    dicts.setdefault(call.func.value.id, {}), merged, call)
        return
    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            keys = _payload_keys(node.value, dicts)
            if keys is None:
                dicts.pop(target.id, None)
            else:
                dicts[target.id] = keys
        elif (isinstance(target, ast.Subscript)
              and isinstance(target.value, ast.Name)
              and isinstance(target.slice, ast.Constant)
              and isinstance(target.slice.value, str)):
            if target.slice.value == 'tab':
                dicts.setdefault(target.value.id, {}).pop(
                    _OPAQUE_TAB_SPREAD, None)
            dicts.setdefault(target.value.id, {})[target.slice.value] = (
                node.value.lineno, node.value)


def _dict_assignments(scope):
    """Map local names to their string keys: {name: {key: (lineno, value)}}.

    A literal `d = {...}` (annotated or not) or `d = dict(...)` resets the
    name; `d['k'] = v`, `d.update({...})` and `d |= {...}` add keys; last
    write wins, so `d = {'tab': 'extension'}` followed by `d['tab'] = tid`
    records `tid`. Only constant string keys are tracked. A wholly opaque
    rebinding or mutation (`d = f()`, `d.update(f())`, `d |= g()`) drops the
    name from tracking from that point rather than trusting keys it may have
    replaced; an unresolved `**` inside a dict retains an opaque-tab marker.
    """
    dicts = {}
    nodes = [n for n in _scope_nodes(scope)
             if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr))]
    for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset)):
        _apply_dict_statement(node, dicts)
    return dicts


def _is_extension_constant(node):
    return isinstance(node, ast.Constant) and node.value == 'extension'


def _py_call_violations(node, dicts, rel, allowed_opaque_names=frozenset()):
    """Violations from one Call node, given the scope's tracked dicts."""
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else '')
    if name in ('ext_cmd', '_ext_cmd'):
        found = []
        for kw in node.keywords:
            if kw.arg == 'tab' and not _is_extension_constant(kw.value):
                found.append(f'{rel}:{kw.value.lineno}: ext_cmd keyword `tab`')
            elif kw.arg is None:
                keys = _payload_keys(kw.value, dicts)
                if keys is None or _OPAQUE_TAB_SPREAD in keys:
                    found.append(
                        f'{rel}:{kw.value.lineno}: opaque **'
                        f'{ast.unparse(kw.value)} passed to ext_cmd; `tab` '
                        'cannot be verified')
                elif 'tab' in keys:
                    lineno, value = keys['tab']
                    if not _is_extension_constant(value):
                        found.append(
                            f'{rel}:{lineno}: `tab` in '
                            f'**{ast.unparse(kw.value)} passed to ext_cmd')
        return found
    cmd_at = next((i for i, a in enumerate(node.args)
                   if isinstance(a, ast.Constant) and a.value == '/command'),
                  None)
    if cmd_at is None or cmd_at + 1 >= len(node.args):
        return []
    keys = _payload_keys(node.args[cmd_at + 1], dicts)
    if keys and 'type' in keys and _OPAQUE_TAB_SPREAD in keys:
        lineno, spread = keys[_OPAQUE_TAB_SPREAD]
        allowed = (isinstance(spread, ast.Name)
                   and spread.id in allowed_opaque_names)
        if not allowed:
            return [f'{rel}:{lineno}: opaque spread may replace `tab` on a '
                    'typed /command payload']
    if keys and 'type' in keys and 'tab' in keys:
        lineno, value = keys['tab']
        if not _is_extension_constant(value):
            return [f'{rel}:{lineno}: `tab` on a typed /command payload']
    return []


def _copy_dict_state(dicts):
    return {name: keys.copy() for name, keys in dicts.items()}


def _dedupe_dict_states(states):
    """Collapse flow states that are equivalent for this routing contract."""
    found = {}
    for state in states:
        signature = []
        for name, keys in sorted(state.items()):
            if _OPAQUE_TAB_SPREAD in keys:
                tab = 'opaque'
            elif 'tab' not in keys:
                tab = 'absent'
            elif _is_extension_constant(keys['tab'][1]):
                tab = 'extension'
            else:
                tab = 'other'
            signature.append((name, 'type' in keys, tab))
        found.setdefault(tuple(signature), state)
    return list(found.values())


def _py_calls_in(node):
    nodes = [node, *_scope_nodes(node)]
    return [child for child in nodes if isinstance(child, ast.Call)]


def _py_flow_violations(statements, states, rel, allowed_opaque_names):
    """Walk statements in order, retaining alternate `if` branch states."""
    violations = []

    def check_calls(node, current_states):
        for call in _py_calls_in(node):
            for state in current_states:
                violations.extend(_py_call_violations(
                    call, state, rel, allowed_opaque_names))

    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
            continue
        if isinstance(statement, ast.If):
            check_calls(statement.test, states)
            incoming = [_copy_dict_state(state) for state in states]
            body, body_states = _py_flow_violations(
                statement.body,
                [_copy_dict_state(state) for state in incoming], rel,
                allowed_opaque_names)
            violations.extend(body)
            if statement.orelse:
                other, other_states = _py_flow_violations(
                    statement.orelse,
                    [_copy_dict_state(state) for state in incoming], rel,
                    allowed_opaque_names)
                violations.extend(other)
            else:
                other_states = incoming
            states = _dedupe_dict_states([*body_states, *other_states])
            continue
        check_calls(statement, states)
        for state in states:
            _apply_dict_statement(statement, state)
        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return violations, []
    return violations, states


def _py_tab_routing_violations(path, rel):
    """`tab` set to a non-'extension' value on a typed command sent from `path`.

    Typed means: routed through ext_cmd/_ext_cmd (which themselves inject
    `tab: 'extension'`), or sent to /command carrying a `type` key. Eval
    payloads carry `code` instead of `type` and route BY tab legitimately —
    `_send_eval` sets `payload['tab'] = tab_id` and is correct — so they are
    exempt by structure, not by naming convention.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    violations = []
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        statements = scope.body
        allowed_opaque = frozenset()
        if (isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                and scope.name in ('ext_cmd', '_ext_cmd')
                and scope.args.kwarg is not None):
            allowed_opaque = frozenset({scope.args.kwarg.arg})
        found, _ = _py_flow_violations(
            statements, [{}], rel, allowed_opaque)
        violations.extend(found)
    return list(dict.fromkeys(violations))


def _js_mask(text):
    """Blank string and comment contents, preserving positions and newlines,
    so structure (brackets, commas, colons) can be read without false hits
    from literal text."""
    out = []
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == '//':
            j = text.find('\n', i)
            j = n if j == -1 else j
            out.append(' ' * (j - i))
            i = j
        elif two == '/*':
            j = text.find('*/', i + 2)
            j = n if j == -1 else j + 2
            out.append(re.sub(r'[^\n]', ' ', text[i:j]))
            i = j
        elif text[i] in '\'"`':
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                elif text[j] == text[i]:
                    j += 1
                    break
                else:
                    j += 1
            out.append(re.sub(r'[^\n]', ' ', text[i:j]))
            i = j
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def _js_bracket_end(mask, open_pos):
    """Offset just past the bracket matching the one at `open_pos`."""
    depth = 0
    for i in range(open_pos, len(mask)):
        if mask[i] in '([{':
            depth += 1
        elif mask[i] in ')]}':
            depth -= 1
            if depth == 0:
                return i + 1
    return len(mask)


def _js_split_top_level(mask, text, start, end):
    """Split mask[start:end] on depth-0 commas. Emptiness is judged on the
    ORIGINAL text: a blanked string is a real argument, not a gap."""
    spans, depth, seg = [], 0, start
    for i in range(start, end):
        c = mask[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ',' and depth == 0:
            spans.append((seg, i))
            seg = i + 1
    spans.append((seg, end))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def _js_top_level(mask, start, end, char):
    """Offset of the first `char` at bracket depth 0 in the span, or None."""
    depth = 0
    for i in range(start, end):
        c = mask[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth < 0:
                return None
        elif c == char and depth == 0:
            return i
    return None


def _js_statement_end(mask, pos):
    """Offset of the `;` ending the statement that starts at `pos`.

    Falls back to the end of the line at depth 0, for the rare unterminated
    statement, and to the end of the file when neither is found.
    """
    depth = 0
    for i in range(pos, len(mask)):
        c = mask[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ';' and depth == 0:
            return i
        elif c == '\n' and depth == 0 and mask[pos:i].strip():
            return i
    return len(mask)


def _js_object_entries(mask, text, obj_start):
    """Top-level entries of the object literal opening at `obj_start`:
    [(key, value_text_or_None_for_shorthand, key_offset)]. A spread has a
    None key and its expression as the value. Destructuring defaults
    (`tab = 'extension'` in a parameter object) are not entries."""
    obj_end = _js_bracket_end(mask, obj_start)
    entries = []
    for s, e in _js_split_top_level(mask, text, obj_start + 1, obj_end - 1):
        seg_text = text[s:e]
        stripped = seg_text.strip()
        if stripped.startswith('...'):
            spread_at = s + seg_text.index('...') + 3
            while spread_at < e and text[spread_at].isspace():
                spread_at += 1
            entries.append((None, text[spread_at:e].strip(), spread_at))
            continue
        seg_mask = mask[s:e]
        depth, colon, equals = 0, None, None
        for i, c in enumerate(seg_mask):
            if c in '([{':
                depth += 1
            elif c in ')]}':
                depth -= 1
            elif depth == 0 and c == ':' and colon is None:
                colon = i
            elif depth == 0 and c == '=' and equals is None:
                equals = i
        if equals is not None and (colon is None or equals < colon):
            continue
        if colon is None:
            m = re.match(r'\s*([\w$]+)', seg_text)
            if m:
                entries.append((m.group(1), None, s + m.start(1)))
            continue
        key_text = seg_text[:colon].strip()
        quoted = re.fullmatch(r'["\']([^"\']+)["\']', key_text)
        computed = re.fullmatch(
            r'\[\s*(["\'])([^"\']+)\1\s*\]', key_text)
        key = quoted.group(1) if quoted else (
            computed.group(2) if computed else key_text)
        entries.append((key,
                        seg_text[colon + 1:].strip(), s))
    return entries


# A name whose object exists but whose contents this scanner cannot prove.
# Distinct from an unknown name (a parameter, an import): unknown is silence,
# unprovable is a claim that something happened here and was not followed.
_JS_UNPROVABLE = object()


def _js_function_body(mask, name):
    """`(kind, start, end)` of a same-file function's body, or None.

    `kind` is 'block' for a braced body (start is the `{`) and 'expr' for a
    concise arrow body (the span is the expression). Only the first
    declaration of a name is read; a file that declares one twice gets the
    first, which is the conservative half of a shape nothing here uses.
    """
    patterns = (
        r'\bfunction\s+' + re.escape(name) + r'\s*\(',
        r'\b(?:const|let|var)\s+' + re.escape(name)
        + r'\s*=\s*(?:async\s+)?function\s*[\w$]*\s*\(',
        r'\b(?:const|let|var)\s+' + re.escape(name)
        + r'\s*=\s*(?:async\s*)?\(',
    )
    for pattern in patterns:
        match = re.search(pattern, mask)
        if not match:
            continue
        paren = match.end() - 1
        after = _js_bracket_end(mask, paren)
        arrow = re.match(r'\s*=>\s*', mask[after:])
        if arrow:
            body = after + arrow.end()
            if mask[body:body + 1] == '{':
                return ('block', body, _js_bracket_end(mask, body))
            if mask[body:body + 1] == '(':
                return ('expr', body + 1, _js_bracket_end(mask, body) - 1)
            semi = mask.find(';', body)
            return ('expr', body, len(mask) if semi == -1 else semi)
        brace = mask.find('{', after)
        if brace != -1:
            return ('block', brace, _js_bracket_end(mask, brace))
    return None


def _js_tab_routing_violations(path, rel):
    """The dashboard side of the same contract: the fields object of an
    extCmd call, and a runCommand object carrying `type`, may contain `tab`
    only as the literal 'extension'. runCommand objects carrying `code` are
    eval sends and route by tab legitimately.

    Resolved before the call: inline object literals, names initialized by
    object literals, aliases of such names, ternary initializers whose
    branches both resolve, `Object.assign` writes, same-name direct property
    writes, tracked object spreads, literal computed keys, the third extCmd
    options argument, and a call to a same-file helper that returns an
    object it built.

    An object that escapes into another call is marked unprovable from that
    point, and an unprovable object reaching a sender is a violation: a
    helper that writes `target.tab` through a parameter cannot be followed,
    so it is reported rather than trusted. An unresolvable sender argument
    is reported for the same reason. A name this scanner never saw assigned
    — a parameter, an import — stays unknown rather than unprovable, which
    is the one silence left.
    """
    text = path.read_text(encoding='utf-8')
    mask = _js_mask(text)
    violations = []
    senders = ('extCmd', 'extcmd', 'runCommand')

    def line_of(pos):
        return text.count('\n', 0, pos) + 1

    def is_extension_literal(value):
        return value is not None and re.fullmatch(r'["\']extension["\']', value)

    def object_state(obj_start, named, depth):
        """Resolve relevant state from a literal and tracked object spreads."""
        state = {}
        for key, value, off in _js_object_entries(mask, text, obj_start):
            if key is None:
                spread = value.strip()
                if spread.startswith('{'):
                    merged = object_state(mask.index('{', off), named, depth)
                elif re.fullmatch(r'[\w$]+', spread):
                    merged = named.get(spread)
                else:
                    merged = None
                if merged is _JS_UNPROVABLE:
                    return _JS_UNPROVABLE
                if merged is not None:
                    state.update(merged)
            else:
                state[key] = (line_of(off), value)
        return state

    def helper_state(name, depth):
        """What a same-file helper returns, when it returns what it built."""
        if depth > 3:
            return _JS_UNPROVABLE
        body = _js_function_body(mask, name)
        if body is None:
            return _JS_UNPROVABLE
        kind, body_start, body_end = body
        inner = names_before(body_end, depth + 1, floor=body_start)
        if kind == 'expr':
            return resolve(body_start, body_end, inner, depth + 1)
        returns = list(re.finditer(r'\breturn\b',
                                   mask[body_start:body_end]))
        if not returns:
            return _JS_UNPROVABLE
        merged = {}
        for m in returns:
            expr_start = body_start + m.end()
            semi = mask.find(';', expr_start)
            expr_end = body_end if semi == -1 or semi > body_end else semi
            state = resolve(expr_start, expr_end, inner, depth + 1)
            if state is _JS_UNPROVABLE:
                return _JS_UNPROVABLE
            if state is None:
                continue
            merged.update(state)
        return merged

    def resolve(span_start, span_end, named, depth):
        """Resolve an expression span to a key state, None, or unprovable."""
        expr = mask[span_start:span_end].strip()
        raw = text[span_start:span_end].strip()
        if not expr:
            return None
        if expr.startswith('{'):
            return object_state(span_start + mask[span_start:span_end].index('{'),
                                named, depth)
        if re.fullmatch(r'[\w$]+', expr):
            if raw in ('null', 'undefined'):
                return None
            return named.get(expr)
        question = _js_top_level(mask, span_start, span_end, '?')
        if question is not None:
            colon = _js_top_level(mask, question + 1, span_end, ':')
            if colon is not None:
                left = resolve(question + 1, colon, named, depth)
                right = resolve(colon + 1, span_end, named, depth)
                if left is _JS_UNPROVABLE or right is _JS_UNPROVABLE:
                    return _JS_UNPROVABLE
                merged = dict(left or {})
                merged.update(right or {})
                return merged
        call = re.fullmatch(r'([\w$]+)\s*\(.*\)', expr, re.DOTALL)
        if call:
            return helper_state(call.group(1), depth)
        return _JS_UNPROVABLE

    events = []  # (pos, kind, match)
    for m in re.finditer(r'\b(?:const|let|var)\s+([\w$]+)\s*=', mask):
        events.append((m.start(), 'init', m))
    for m in re.finditer(r'\b([\w$]+)\s*\.\s*tab(?![\w$])\s*=', mask):
        events.append((m.start(), 'prop', m))
    for m in re.finditer(r'\bObject\s*\.\s*assign\s*\(', mask):
        events.append((m.start(), 'assign', m))
    # The bracket form is found in the raw text because the mask blanks
    # string contents, making `['tab']` unreadable there — but then a match
    # that begins inside a blanked span is a mention in a string or comment,
    # not code. The mask preserves positions, so the two diverge at the very
    # first character of the name exactly when the mention is not real code.
    for m in re.finditer(r'\b([\w$]+)\s*\[\s*["\']tab["\']\s*\]\s*=', text):
        if mask[m.start()] == text[m.start()]:
            events.append((m.start(), 'prop', m))
    # A tracked object handed to any other call escapes: a helper that writes
    # through its parameter is invisible from here, so the object stops being
    # provable at that point rather than being trusted on its literal.
    for m in re.finditer(r'\b([\w$]+)\s*\(', mask):
        if m.group(1) in senders or m.group(1) in ('if', 'for', 'while',
                                                   'switch', 'catch',
                                                   'function', 'return'):
            continue
        # Object.assign's target is modelled above, so it is not an escape;
        # every other call taking the object is one, member calls included.
        if re.search(r'Object\s*\.\s*$', mask[:m.start()]):
            continue
        events.append((m.start(), 'escape', m))
    events.sort(key=lambda e: e[0])

    def names_before(limit, depth, floor=0):
        """Tracked state of named objects from the assignments before `limit`.

        Judging every call against end-of-file state let a later, unrelated
        `const f = {...}` erase an earlier violation, so the walk stops at
        the call. `floor` restricts the walk to one function body.
        """
        named = {}
        for start, kind, m in events:
            if start >= limit:
                break
            if start < floor:
                continue
            if kind == 'init':
                name = m.group(1)
                stmt_end = _js_statement_end(mask, m.end())
                named[name] = resolve(m.end(), stmt_end, named, depth)
            elif kind == 'assign':
                open_paren = mask.index('(', m.start())
                call_end = _js_bracket_end(mask, open_paren)
                args = _js_split_top_level(mask, text, open_paren + 1,
                                           call_end - 1)
                if not args:
                    continue
                target = mask[args[0][0]:args[0][1]].strip()
                if not re.fullmatch(r'[\w$]+', target):
                    continue
                state = named.get(target)
                if state is _JS_UNPROVABLE:
                    continue
                if state is None:
                    state = {}
                for span in args[1:]:
                    merged = resolve(span[0], span[1], named, depth)
                    if merged is _JS_UNPROVABLE:
                        state = _JS_UNPROVABLE
                        break
                    if merged:
                        state.update(merged)
                named[target] = state
            elif kind == 'escape':
                open_paren = mask.index('(', m.start())
                call_end = _js_bracket_end(mask, open_paren)
                for span in _js_split_top_level(mask, text, open_paren + 1,
                                                call_end - 1):
                    arg = mask[span[0]:span[1]].strip()
                    if re.fullmatch(r'[\w$]+', arg) and arg in named:
                        named[arg] = _JS_UNPROVABLE
            else:
                name = m.group(1)
                eq = mask.index('=', m.start())
                semi = mask.find(';', eq)
                semi = len(mask) if semi == -1 else semi
                state = named.get(name)
                if state is _JS_UNPROVABLE:
                    continue
                if state is None:
                    state = {}
                state['tab'] = (line_of(m.start()), text[eq + 1:semi].strip())
                named[name] = state
        return named

    def argument_state(span, named):
        start, end = span
        return resolve(start, end, named, 0)

    for m in re.finditer(r'\b(?:extCmd|extcmd|runCommand)\s*\(', mask):
        # `function extCmd(type, fields = {}, opts = {})` is where a sender is
        # declared, not where one is called; its parameter list is not a
        # payload and resolving it would report the definition of every
        # sender as unprovable.
        if mask[:m.start()].rstrip().endswith('function'):
            continue
        call_name = re.match(r'[\w$]+', mask[m.start():]).group(0)
        open_paren = mask.index('(', m.start())
        call_end = _js_bracket_end(mask, open_paren)
        args = _js_split_top_level(mask, text, open_paren + 1, call_end - 1)
        tab_entries = []
        unprovable = []
        named = names_before(m.start(), 0)
        if call_name == 'runCommand':
            if not args:
                continue
            keys = argument_state(args[0], named)
            if keys is _JS_UNPROVABLE:
                unprovable.append(line_of(m.start()))
            # A `type` key makes this a typed send; eval sends carry `code`.
            elif keys and 'type' in keys and 'tab' in keys:
                tab_entries.append(keys['tab'])
        else:  # extCmd(type, fields, ...)
            if len(args) < 2:
                continue
            fields = argument_state(args[1], named)
            if fields is _JS_UNPROVABLE:
                unprovable.append(line_of(m.start()))
            elif fields and 'tab' in fields:
                tab_entries.append(fields['tab'])
            if len(args) >= 3:
                opts = argument_state(args[2], named)
                if opts is _JS_UNPROVABLE:
                    unprovable.append(line_of(m.start()))
                elif opts and 'tab' in opts:
                    tab_entries.append(opts['tab'])
        for lineno, value in tab_entries:
            if not is_extension_literal(value):
                violations.append(
                    f'{rel}:{lineno}: `tab` in a typed command send')
        for lineno in unprovable:
            violations.append(
                f'{rel}:{lineno}: a command send whose fields this guard '
                'cannot resolve — inline the object or build it in a helper '
                'that returns one')
    return list(dict.fromkeys(violations))


def test_no_client_sends_the_browser_target_as_the_routing_field(tmp):
    r"""`tab` routes to a server queue; `tabId` names a browser tab.

    Overloading them is not hypothetical: screenshot, CDP and the tabs panel
    all sent the browser target as `tab`, which the server strips for routing,
    so those buttons silently captured the active tab instead of the selected
    one. One sender also wrote the target over the routing value and sent the
    command to a queue nothing drains.

    This checks the SENDERS rather than the wire. A test that builds the
    payload itself passes while a sender that builds it wrongly ships — which
    is exactly what happened to the first version of this fix: the wire test
    was green and `dashboard/sections/tabs.js` was still wrong.

    What is enforced: in statically resolved sender shapes in mcp_server.py,
    daedalus_cli/ and dashboard/, a typed extension command (one routed
    through ext_cmd/_ext_cmd/extCmd/runCommand, or sent to /command with a
    visible `type` key) may carry `tab` only as the literal 'extension'. The
    eval path is exempt by structure: eval payloads carry `code` instead of
    `type`, and eval genuinely routes by tab. Python payloads are followed
    through literals (annotated or not), `dict(...)`, subscript assignments,
    `update({...})`, `|= {...}`, source order and `if`/`else` branches.
    JavaScript inline literals, names initialized by object literals,
    aliases of those names, ternary initializers whose branches resolve,
    `Object.assign` writes, tracked object spreads, literal computed keys,
    same-name direct `tab` property assignments, calls to same-file helpers
    that return the object they built, and the third extCmd argument are
    checked in source order. An object handed to any other call stops being
    provable there, and an unprovable object reaching a sender is reported
    rather than trusted — a helper that writes through its parameter cannot
    be followed, so it is not silently believed.

    What is NOT enforced:
    - A name this scanner never saw assigned — a parameter, an import — is
      unknown rather than unprovable, and unknown stays silent. That is what
      keeps `extCmd('fetch-timings', opts)` and the `...opts` spread inside
      `extCmd` itself quiet; the third-argument check covers the override
      those spreads could carry.
    - A Python /command payload built by a non-`dict(...)` call or by
      `dict(...)` with a positional argument is skipped. An untracked
      `**spread` after a visible `type` is rejected, but an untracked spread
      with no visible `type` could introduce both `type` and `tab` without
      being recognized as a typed payload.
    - Python control flow other than straight-line code and `if`/`else` is
      not modeled. Assignments inside loops, `try`, `with`, or `match` may be
      inspected against incomplete state.
    - A Python dict rebound to or mutated through an opaque expression
      (`d = f()`, `d.update(f())`, `d |= g()`) is dropped from tracking from
      that point rather than trusted on stale keys; a later `d['tab'] = ...`
      is tracked again.
    - Computed keys other than string literals are skipped. Inline object
      spreads and spreads of tracked names, plus literal computed keys such
      as `['tab']`, are checked; a spread of an unknown name is skipped.
    - For a named runCommand object, `type`/`code` are read from its literal
      initializer; later property assignments to those two keys are not
      tracked. `.tab` and `['tab']` assignments are tracked through the name
      and through an alias of it.
    - A helper is resolved from its first declaration in the file, and only
      through three levels of helper calls.
    - JavaScript name state is file-wide and source-ordered, not
      block-scoped or execution-ordered: a call is judged against every
      same-named assignment that precedes it in the file — including ones in
      other functions — and an assignment written after the call is
      invisible to it even when it runs first.
    - The JavaScript mask does not understand regex literals: a literal
      containing a quote character (e.g. `s.replace(/['"]/g, '')`) would be
      read as the start of a string, blanking real code up to the next quote
      and under-reporting everything after it. The dashboard's regex literal
      patterns today are `/\s+/g`, `/^\./`, `/\s+/`,
      `/\.(png|jpe?g|gif|webp)$/i` and `/\.(png|jpe?g)$/i`; none contains a
      quote, so nothing is masked wrongly — but adding one with a quote
      silently weakens this guard.
    """
    senders_py = [ROOT / 'mcp_server.py', *(ROOT / 'daedalus_cli').glob('*.py')]
    senders_js = sorted((ROOT / 'dashboard').rglob('*.js'))
    scanned_py = [p for p in senders_py if p.is_file()]
    # A floor, not a glob of whatever happens to exist: with the senders
    # moved aside the scan above finds nothing and passes vacuously.
    assert len(scanned_py) >= 3, (
        f'found {len(scanned_py)} Python senders (mcp_server.py + '
        'daedalus_cli/*.py), expected at least 3 — the senders moved and '
        'this guard is stale')
    assert len(senders_js) >= 10, (
        f'found {len(senders_js)} dashboard .js files, expected at least '
        '10 — the senders moved and this guard is stale')
    violations = []
    for path in scanned_py:
        violations.extend(_py_tab_routing_violations(
            path, path.relative_to(ROOT)))
    for path in senders_js:
        violations.extend(_js_tab_routing_violations(
            path, path.relative_to(ROOT)))
    assert not violations, (
        'these senders pass a browser tab as the routing field `tab`; the '
        "browser target is `tabId` and a typed command routes to "
        "`tab: 'extension'`:\n" + '\n'.join(violations))

    # The scan above passing on a correct tree proves nothing by itself — the
    # previous two versions of this guard passed on the real tree while
    # missing every reversion an auditor tried. Each shape below is checked
    # against the same scanner functions the tree scan uses.
    reversions = [
        ('py', "async def f(chrome_tab):\n"
               "    fields = {}\n"
               "    fields['tab'] = str(chrome_tab)\n"
               "    return await _ext_cmd('_ss', 'screenshot', **fields)\n"),
        ('py', "async def f(tab_id):\n"
               "    fields = {}\n"
               "    fields['tab'] = tab_id\n"
               "    return await _ext_cmd('_ss', 'screenshot', **fields)\n"),
        ('py', "def f(args):\n"
               "    cmd = {'id': '_ss', 'type': 'screenshot', 'tab': 'extension'}\n"
               "    cmd[\"tab\"] = int(args.chrome_tab)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "async def f(t):\n"
               "    extra = {'tab': str(t)}\n"
               "    return await _ext_cmd('_cdp', 'cdp', **extra)\n"),
        ('js', "async function f() {\n"
               "  const fields = {};\n"
               "  fields.tab = Number(tabSel.value);\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "async function f(m, tid) {\n"
               "  await extCmd('cdp', { method: m.trim(), params: {}, tab: tid });\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  const f = { tab: tid };\n"
               "  await extCmd('cdp', f);\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  await extCmd('net-capture', { method: 'Network.enable',"
               " params: { maxTotalBufferSize: 10000000, maxResourceBufferSize:"
               " 5000000, maxPostDataSize: 65536 }, note: 'padding padding"
               " padding padding padding padding padding padding', tab: tid });\n"
               "}\n"),
        # The shapes a mutation sweep found this guard missing open on:
        # annotated assignments (cli.py and mcp_server.py spell every payload
        # `fields: dict = {...}` / `cmd: dict = {...}`) ...
        ('py', "def f(args):\n"
               "    cmd: dict = {'id': '_x', 'type': 'close-tab', 'tab': 'extension'}\n"
               "    cmd['tab'] = int(args.chrome_tab)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "async def f(t):\n"
               "    fields: dict = {'css': 'x'}\n"
               "    fields['tab'] = t\n"
               "    return await _ext_cmd('_css', 'inject-css', **fields)\n"),
        # ... payloads assembled without a literal in scope ...
        ('py', "def f(tid):\n"
               "    cmd = dict(id='_x', type='close-tab', tab=tid)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "def f(tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
               "    cmd.update({'tab': tid})\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "def f(tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
               "    cmd |= {'tab': tid}\n"
               "    api('PUT', '/command', cmd)\n"),
        # ... a later unrelated re-declaration erasing an earlier violation
        # (the live shape in dashboard/sections/cookies.js) ...
        ('js', "async function load() {\n"
               "  const fields = {};\n"
               "  fields.tab = Number(tabSel.value);\n"
               "  await extCmd('cookies', fields);\n"
               "}\n"
               "function later(q) {\n"
               "  const fields = { url: q };\n"
               "  extCmd('set-cookie', fields);\n"
               "}\n"),
        # ... and the bracket form, which must still be caught now that
        # comment/string mentions of it are filtered out.
        ('js', "async function f(tid) {\n"
               "  const fields = {};\n"
               "  fields['tab'] = tid;\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        # An opaque spread after the visible routing value can replace it at
        # runtime; visible keys must not make that spread look safe.
        ('py', "def f(tid):\n"
               "    spread = build_fields(tid)\n"
               "    cmd = {'id': '_x', 'type': 'close-tab',"
               " 'tab': 'extension', **spread}\n"
               "    api('PUT', '/command', cmd)\n"),
        # Calls are checked against the state that reaches the call, not the
        # flattened final assignment from a mutually exclusive branch.
        ('py', "def f(flag, tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab',"
               " 'tab': 'extension'}\n"
               "    if flag:\n"
               "        cmd['tab'] = tid\n"
               "        api('PUT', '/command', cmd)\n"
               "    else:\n"
               "        cmd['tab'] = 'extension'\n"),
        # Object spreads and literal computed keys have ordinary runtime
        # object semantics and therefore must participate in the scan.
        ('js', "async function f(tid) {\n"
               "  await extCmd('cdp', { ...{ ['tab']: tid } });\n"
               "}\n"),
        # runCommand accepts named objects as well as inline literals.
        ('js', "async function f(tid) {\n"
               "  const command = { type: 'cdp', tab: tid };\n"
               "  await runCommand(command);\n"
               "}\n"),
        # api.js applies opts after `tab: 'extension'`, so the third argument
        # can really retarget a typed command.
        ('js', "async function f(target) {\n"
               "  await extCmd('cdp', {}, { tab: target });\n"
               "}\n"),
        # Shapes that used to be disclosed gaps, each promoted here when it
        # started being caught: an alias, a helper writing through its
        # parameter, a ternary initializer, an Object.assign write, and a
        # helper that returns the object it built.
        ('js', "async function f(tid) {\n"
               "  const fields = {};\n"
               "  const alias = fields;\n"
               "  alias.tab = tid;\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "function addTab(target, tid) {\n"
               "  target.tab = tid;\n"
               "}\n"
               "async function f(tid) {\n"
               "  const fields = {};\n"
               "  addTab(fields, tid);\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "async function f(flag, tid) {\n"
               "  const fields = flag ? { tab: tid } : {};\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  const fields = {};\n"
               "  Object.assign(fields, { tab: tid });\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "function build(tid) {\n"
               "  const f = {};\n"
               "  f.tab = tid;\n"
               "  return f;\n"
               "}\n"
               "async function g(tid) {\n"
               "  await extCmd('screenshot', build(tid));\n"
               "}\n"),
    ]

    legitimate = [
        ('py', "async def f(cmd_id, code, tab_id):\n"
               "    payload = {'id': cmd_id, 'code': code}\n"
               "    payload['tab'] = tab_id\n"
               "    await _put('/command', payload)\n"),
        ('py', "async def f(chrome_tab):\n"
               "    return await _ext_cmd('_focus', 'focus-tab',"
               " tabId=int(chrome_tab))\n"),
        ('js', "async function f(tabId, code) {\n"
               "  await runCommand({ tab: tabId, code });\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  await extCmd('screenshot', { tabId: Number(tid) });\n"
               "}\n"),
        # An annotated assignment used correctly: the annotation changes
        # nothing, and tabId was never the routing field.
        ('py', "async def f(chrome_tab):\n"
               "    fields: dict = {'css': 'x'}\n"
               "    fields['tabId'] = int(chrome_tab)\n"
               "    return await _ext_cmd('_css', 'inject-css', **fields)\n"),
        # A bracket-form mention inside a comment is not code: it used to
        # crash the scanner (no later `=`) or invent a violation (a later
        # `let done = false;` supplied a garbage value span).
        ('js', "async function f(fields) {\n"
               "  // never do fields['tab'] = Number(tabSel.value) here\n"
               "  await extCmd('cookies', fields);\n"
               "}\n"),
        ('js', "async function f(q) {\n"
               "  const fields = { url: q };\n"
               "  // fields['tab'] = Number(tabSel.value) would be wrong\n"
               "  let done = false;\n"
               "  await extCmd('cookies', fields);\n"
               "}\n"),
    ]

    disclosed_js_limits = [
        # Name state is file-wide and source-ordered rather than scoped and
        # execution-ordered: an assignment written after the call is invisible
        # to it even when it runs first.
        ('assignment after the call that runs before it',
         "async function send() {\n"
         "  await extCmd('screenshot', fields);\n"
         "}\n"
         "const fields = { tab: 'not-extension' };\n"),
        # A name this scanner never saw assigned — a parameter, an import —
        # is unknown rather than unprovable, and unknown stays silent.
        ('fields arriving as a parameter',
         "async function f(fields) {\n"
         "  await extCmd('screenshot', fields);\n"
         "}\n"),
    ]
    fixture = Path(tmp) / 'sender'
    for i, (lang, src) in enumerate(reversions):
        path = fixture.with_suffix('.py' if lang == 'py' else '.js')
        path.write_text(src, encoding='utf-8')
        found = _py_tab_routing_violations(path, f'reversion-{i}') if lang == 'py' \
            else _js_tab_routing_violations(path, f'reversion-{i}')
        assert found, (
            f'reversion {i} was NOT caught — the guard asserts a contract it '
            f'does not enforce:\n{src}')
    for i, (lang, src) in enumerate(legitimate):
        path = fixture.with_suffix('.py' if lang == 'py' else '.js')
        path.write_text(src, encoding='utf-8')
        found = _py_tab_routing_violations(path, f'legitimate-{i}') if lang == 'py' \
            else _js_tab_routing_violations(path, f'legitimate-{i}')
        assert not found, (
            f'legitimate shape {i} was flagged — eval routing and `tabId` are '
            f'correct:\n{src}\n{found}')
    for i, (label, src) in enumerate(disclosed_js_limits):
        path = fixture.with_suffix('.js')
        path.write_text(src, encoding='utf-8')
        found = _js_tab_routing_violations(path, f'disclosed-{i}')
        assert not found, (
            f'disclosed JavaScript limit {label!r} is now caught — remove or '
            f'narrow its docstring disclosure and promote this fixture to a '
            f'reversion:\n{src}\n{found}')


_STORAGE_RELAY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [contentPath, pagePath] = process.argv.slice(1);
const listeners = {};
const messages = [];
const posted = [];
const storageCalls = [];
const store = Object.create(null);

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

function storedValues(keys) {
  const values = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(store, key)) values[key] = store[key];
  }
  return values;
}

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage() {},
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
      get(keys, callback) {
        storageCalls.push('get');
        callback(keys === null ? { ...store } : storedValues(keys));
      },
      set(values, callback) {
        storageCalls.push('set');
        Object.assign(store, values);
        callback();
      },
      remove(keys, callback) {
        storageCalls.push('remove');
        for (const key of keys) delete store[key];
        callback();
      },
    },
  },
};

const context = {
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-test.invalid' },
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
};
vm.runInNewContext(fs.readFileSync(contentPath, 'utf8'), context);
vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), context);

function reset(initial = {}) {
  for (const key of Reflect.ownKeys(store)) delete store[key];
  Object.assign(store, initial);
  messages.length = 0;
  posted.length = 0;
  storageCalls.length = 0;
}

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

function responseFor(reqId) {
  return posted.find((message) =>
    message.direction === 'daedalus-bg-to-page' && message.reqId === reqId);
}

let directReqId = 1000;
function dispatch(handler, key, initial = {}) {
  reset(initial);
  const reqId = ++directReqId;
  const data = {
    direction: 'daedalus-page-to-bg',
    reqId,
    handler,
    key,
    value: 'attacker',
    defaultValue: 'default',
  };
  for (const listener of listeners.message) {
    listener({ source: windowObject, data });
  }
  flushMessages();
  const response = responseFor(reqId);
  return {
    error: response && response.error || null,
    value: response && response.value,
    calls: [...storageCalls],
    storedKeys: Object.keys(store).sort(),
    protectedValue: store['daedalus-server'],
  };
}

async function gmSet(label, key) {
  reset();
  const pending = windowObject.GM.setValue(key, 'attacker');
  flushMessages();
  let status = 'resolved';
  let error = null;
  try {
    await pending;
  } catch (caught) {
    status = 'rejected';
    error = caught && caught.message || String(caught);
  }
  return {
    label,
    status,
    error,
    calls: [...storageCalls],
    storedKeys: Object.keys(store).sort(),
  };
}

async function main() {
  const gmSetCases = [];
  for (const key of ['daedalus-server', 'daedalus-hotfixes', 'daedalus-token']) {
    gmSetCases.push(await gmSet(`array:${key}`, [key]));
  }
  gmSetCases.push(await gmSet('string:daedalus-server', 'daedalus-server'));
  gmSetCases.push(await gmSet('string:ordinary', 'ordinary'));

  const invalidHandlers = {};
  for (const handler of ['getValue', 'setValue', 'deleteValue']) {
    invalidHandlers[handler] = dispatch(
      handler, ['daedalus-server'], { 'daedalus-server': 'protected' });
  }

  const coercibleKeys = {
    number: 7,
    object: { toString() { return 'daedalus-server'; } },
    nestedArray: [['daedalus-server']],
    symbol: Symbol('daedalus-server'),
  };
  const coercible = {};
  for (const [label, key] of Object.entries(coercibleKeys)) {
    coercible[label] = dispatch(
      'setValue', key, { 'daedalus-server': 'protected' });
  }

  const ordinaryHandlers = {
    getValue: dispatch('getValue', 'ordinary', { ordinary: 'kept' }),
    setValue: dispatch('setValue', 'ordinary'),
    deleteValue: dispatch('deleteValue', 'ordinary', { ordinary: 'remove-me' }),
  };

  reset({
    ordinary: 'visible',
    'daedalus-server': 'hidden',
    'daedalus-hotfixes': 'hidden',
    'daedalus-token': 'hidden',
  });
  const listPending = windowObject.GM.listValues();
  flushMessages();
  const listed = await listPending;

  process.stdout.write(JSON.stringify({
    gmSetCases,
    invalidHandlers,
    coercible,
    ordinaryHandlers,
    listValues: { keys: listed, calls: [...storageCalls] },
  }));
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
"""


_FETCH_RELAY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [contentPath, pagePath, responseText] = process.argv.slice(1);
const backgroundResponse = JSON.parse(responseText);
const listeners = {};
const messages = [];
const posted = [];
const sent = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage(payload, callback) {
      sent.push(payload);
      // content.js asks for a hotfix replay at load with no callback at all.
      if (typeof callback === 'function') callback(backgroundResponse);
    },
    getManifest() { return { version: '0.0.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
      get(keys, callback) { callback({}); },
      set(values, callback) { callback(); },
      remove(keys, callback) { callback(); },
    },
  },
};

const context = {
  window: windowObject,
  document: { documentElement: {}, addEventListener() {} },
  chrome,
  location: { hostname: 'page.invalid', href: 'about:blank' },
  setTimeout, clearTimeout, setInterval, clearInterval,
  performance,
  console: { log() {}, error() {} },
};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync(contentPath, 'utf8'), context);
vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), context);

function flushMessages() {
  let guard = 0;
  while (messages.length && guard++ < 100) {
    const data = messages.shift();
    for (const listener of listeners.message || []) {
      listener({ source: windowObject, data });
    }
  }
}

const events = [];
let loadDetail = null;
windowObject.GM.xmlhttpRequest({
  url: 'about:blank#slow',
  timeout: 50,
  onload: (detail) => { loadDetail = detail; events.push('load'); },
  onerror: (detail) => events.push('error:' + (detail && detail.error)),
  ontimeout: () => events.push('timeout'),
});
flushMessages();

// The content script arms a keepalive timer that would hold the event loop
// open forever; exit once the answer has actually been flushed.
process.stdout.write(JSON.stringify({
  events,
  loadDetail,
  relayed: posted
    .filter((m) => m.direction === 'daedalus-bg-to-page')
    .map((m) => m.event),
  requestedTimeout: (sent.find((m) => m.type === 'fetch') || {}).timeout,
}), () => process.exit(0));
"""


_CLIPBOARD_RELAY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [contentPath, pagePath, mode] = process.argv.slice(1);
const listeners = {};
const messages = [];
const posted = [];
const writes = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

const navigatorObject = {
  clipboard: {
    writeText(text) {
      writes.push(text);
      return mode === 'reject'
        ? Promise.reject(new Error('NotAllowedError'))
        : Promise.resolve();
    },
  },
};

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage(payload, callback) {
      if (typeof callback === 'function') callback({});
    },
    getManifest() { return { version: '0.0.0' }; },
    connect() {
      return {
        disconnect() {}, postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: { local: {
    get(keys, cb) { cb({}); }, set(v, cb) { cb(); }, remove(k, cb) { cb(); },
  } },
};

const context = {
  window: windowObject,
  document: { documentElement: {}, addEventListener() {} },
  navigator: navigatorObject,
  chrome,
  location: { hostname: 'page.invalid', href: 'about:blank' },
  setTimeout, clearTimeout, setInterval, clearInterval,
  performance,
  console: { log() {}, error() {} },
};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync(contentPath, 'utf8'), context);
vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), context);

function flushMessages() {
  let guard = 0;
  while (messages.length && guard++ < 100) {
    const data = messages.shift();
    for (const listener of listeners.message || []) {
      listener({ source: windowObject, data });
    }
  }
}

(async () => {
  let settled = 'pending';
  let reported = null;
  const returned = windowObject.GM.setClipboard('replacement');
  const isPromise = !!(returned && typeof returned.then === 'function');
  if (isPromise) {
    returned.then(() => { settled = 'resolved'; },
      (error) => { settled = 'rejected'; reported = String(error && error.message); });
  }
  // Let the clipboard promise settle, then deliver whatever the content
  // script posted back in response to it.
  for (let turn = 0; turn < 10; turn++) {
    await Promise.resolve();
    flushMessages();
  }
  await Promise.resolve();

  process.stdout.write(JSON.stringify({
    isPromise, settled, reported, writes,
    acknowledged: posted
      .filter((m) => m.direction === 'daedalus-bg-to-page'
        && m.handler === 'setClipboard')
      .map((m) => ({ error: m.error || null })),
  }), () => process.exit(0));
})();
"""


def _run_clipboard_relay_harness(mode):
    """Drive GM.setClipboard through content.js and page.js under Node."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension clipboard relay'
    result = subprocess.run(
        [node, '-e', _CLIPBOARD_RELAY_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'), mode],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_refused_clipboard_write_reaches_the_caller(tmp):
    """A clipboard write that the browser refuses must not report success.

    The content script called writeText, swallowed the rejection with an empty
    catch, and posted its acknowledgement without waiting for the promise at
    all — so a page without user activation, where Chromium rejects the write
    with NotAllowedError, was told the clipboard had been set.
    """
    del tmp
    refused = _run_clipboard_relay_harness('reject')
    assert refused['writes'] == ['replacement'], refused
    assert refused['isPromise'] is True, refused
    assert refused['settled'] == 'rejected', refused
    assert refused['acknowledged'] == [{'error': 'NotAllowedError'}], refused

    accepted = _run_clipboard_relay_harness('resolve')
    assert accepted['settled'] == 'resolved', accepted
    assert accepted['acknowledged'] == [{'error': None}], accepted


def test_the_extension_declares_the_permission_its_clipboard_write_needs(tmp):
    """A documented operation must ship the permission it depends on."""
    del tmp
    manifest = json.loads(
        (EXTENSION_ROOT / 'manifest.json').read_text(encoding='utf-8'))
    assert 'clipboardWrite' in manifest.get('permissions', []), manifest


def _run_fetch_relay_harness(background_response):
    """Drive GM.xmlhttpRequest through content.js and page.js under Node."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension fetch relay'
    result = subprocess.run(
        [node, '-e', _FETCH_RELAY_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'),
         json.dumps(background_response)],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_fetch_timeout_reaches_ontimeout_and_not_onerror(tmp):
    """A timeout is its own event, not an error that happens to say so.

    page.js has had an `ontimeout` branch all along, and nothing could reach
    it: the background flattened an aborted fetch into an error string, and
    content.js relays anything with an `error` as `event: 'error'`. A caller
    that distinguished the two saw every timeout as a generic failure.
    """
    del tmp
    timed_out = _run_fetch_relay_harness(
        {'error': 'fetch timeout after 50ms', 'timedOut': True})
    assert timed_out['events'] == ['timeout'], timed_out
    assert timed_out['relayed'] == ['timeout'], timed_out
    assert timed_out['requestedTimeout'] == 50, timed_out

    # An ordinary failure still arrives as one.
    failed = _run_fetch_relay_harness({'error': 'network unreachable'})
    assert failed['events'] == ['error:network unreachable'], failed
    assert failed['relayed'] == ['error'], failed


def test_a_redirected_fetch_reports_where_the_body_came_from(tmp):
    """finalUrl is the response's URL, not the one the caller asked for.

    The relay filled finalUrl in from the request, so a caller following a
    redirect chain was told no redirect had happened — and statusText was
    never carried at all, so the page API always reported an empty one.
    """
    del tmp
    loaded = _run_fetch_relay_harness({
        'status': 200, 'statusText': 'OK', 'data': 'body', 'headers': {},
        'finalUrl': 'https://redirected.example.com/final'})
    assert loaded['events'] == ['load'], loaded
    detail = loaded['loadDetail']
    assert detail['finalUrl'] == 'https://redirected.example.com/final', detail
    assert detail['statusText'] == 'OK', detail

    # A background that reports neither still works: the request URL is the
    # fallback it always was, rather than the answer it used to be.
    plain = _run_fetch_relay_harness(
        {'status': 200, 'data': 'body', 'headers': {}})
    assert plain['loadDetail']['finalUrl'] == 'about:blank#slow', plain
    assert plain['loadDetail']['statusText'] == '', plain


def test_the_background_relays_the_response_url_and_status_text(tmp):
    """The other half of the same contract, at its source.

    The relay test above starts from what the background answered. This one
    pins that the background actually reads them off the Response rather than
    off the request it was handed.
    """
    del tmp
    source = (_util.ROOT / 'extension' / 'background.js').read_text(
        encoding='utf-8')
    _, marker, after = source.partition('sendResponse({ status: resp.status,')
    assert marker, 'the fetch success response is not shaped as this test finds it'
    response, _, _ = after.partition('}\n')
    for field in ('statusText: resp.statusText', 'finalUrl: resp.url'):
        assert field in response, (field, response)


_STORAGE_FAILURE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

// Every chrome.storage call fails the way Chrome fails one: the callback is
// invoked exactly as on success, the store is left alone, and the only trace
// is chrome.runtime.lastError — which Chrome clears once the callback
// returns, so it is set around the call and cleared after it.
const [contentPath, pagePath] = process.argv.slice(1);
const FAILURE = 'QUOTA_BYTES quota exceeded';
const listeners = {};
const messages = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    messages.push(message);
  },
};

function failing(callback, value) {
  chrome.runtime.lastError = { message: FAILURE };
  try {
    callback(value);
  } finally {
    chrome.runtime.lastError = null;
  }
}

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage() {},
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
      get(keys, callback) { failing(callback, {}); },
      set(values, callback) { failing(callback); },
      remove(keys, callback) { failing(callback); },
    },
  },
};

const context = {
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-failure.invalid' },
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
};
vm.runInNewContext(fs.readFileSync(contentPath, 'utf8'), context);
vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), context);

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

const outcomes = {};
const settled = [];
for (const [name, call] of [
  ['getValue', () => windowObject.GM.getValue('ordinary', 'fallback')],
  ['setValue', () => windowObject.GM.setValue('ordinary', 'value')],
  ['deleteValue', () => windowObject.GM.deleteValue('ordinary')],
  ['listValues', () => windowObject.GM.listValues()],
]) {
  settled.push(call().then(
    (value) => { outcomes[name] = { settled: 'resolved', value: value ?? null }; },
    (error) => { outcomes[name] = { settled: 'rejected', error: String(error && error.message) }; },
  ));
}
flushMessages();

Promise.all(settled).then(() => {
  process.stdout.write(JSON.stringify(outcomes), () => process.exit(0));
});
"""


def _run_storage_failure_harness():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension storage boundary'
    result = subprocess.run(
        [node, '-e', _STORAGE_FAILURE_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_failed_storage_write_rejects_instead_of_resolving(tmp):
    """Chrome reports a storage failure only through lastError.

    The callbacks fire on failure exactly as on success, with the store
    unchanged, so a relay that did not read chrome.runtime.lastError could
    not tell the two apart — GM.setValue resolved successfully having stored
    nothing, and the page had no way to find out.
    """
    del tmp
    outcomes = _run_storage_failure_harness()
    assert set(outcomes) == {
        'getValue', 'setValue', 'deleteValue', 'listValues'}, outcomes
    for name, outcome in sorted(outcomes.items()):
        assert outcome['settled'] == 'rejected', (name, outcome)
        assert 'QUOTA_BYTES quota exceeded' in outcome['error'], (name, outcome)


def _run_storage_relay_harness():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension storage boundary'
    result = subprocess.run(
        [node, '-e', _STORAGE_RELAY_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_page_storage_rejects_coercible_reserved_keys(tmp):
    """GM.setValue rejects coercible reserved keys before storage is called."""
    result = _run_storage_relay_harness()
    cases = {case['label']: case for case in result['gmSetCases']}
    for key in ('daedalus-server', 'daedalus-hotfixes', 'daedalus-token'):
        assert cases[f'array:{key}'] == {
            'label': f'array:{key}',
            'status': 'rejected',
            'error': 'invalid key',
            'calls': [],
            'storedKeys': [],
        }, cases[f'array:{key}']
    assert cases['string:daedalus-server'] == {
        'label': 'string:daedalus-server',
        'status': 'rejected',
        'error': 'reserved key',
        'calls': [],
        'storedKeys': [],
    }, cases['string:daedalus-server']
    assert cases['string:ordinary'] == {
        'label': 'string:ordinary',
        'status': 'resolved',
        'error': None,
        'calls': ['set'],
        'storedKeys': ['ordinary'],
    }, cases['string:ordinary']


def test_page_storage_validates_every_keyed_handler(tmp):
    """Every keyed relay rejects non-strings without touching storage."""
    result = _run_storage_relay_harness()
    for handler in ('getValue', 'setValue', 'deleteValue'):
        case = result['invalidHandlers'][handler]
        assert case['error'] == 'invalid key', (handler, case)
        assert case['calls'] == [], (handler, case)
        assert case['storedKeys'] == ['daedalus-server'], (handler, case)
        assert case['protectedValue'] == 'protected', (handler, case)
    for label, case in result['coercible'].items():
        assert case['error'] == 'invalid key', (label, case)
        assert case['calls'] == [], (label, case)
        assert case['protectedValue'] == 'protected', (label, case)


def test_page_storage_allows_string_keys_and_filters_list_values(tmp):
    """Ordinary strings work and listValues omits every reserved string key."""
    result = _run_storage_relay_harness()
    handlers = result['ordinaryHandlers']
    assert handlers['getValue']['value'] == 'kept', handlers['getValue']
    assert handlers['getValue']['calls'] == ['get'], handlers['getValue']
    assert handlers['setValue']['storedKeys'] == ['ordinary'], handlers['setValue']
    assert handlers['setValue']['calls'] == ['set'], handlers['setValue']
    assert handlers['deleteValue']['storedKeys'] == [], handlers['deleteValue']
    assert handlers['deleteValue']['calls'] == ['remove'], handlers['deleteValue']
    assert result['listValues'] == {
        'keys': ['ordinary'],
        'calls': ['get'],
    }, result['listValues']


# ─── Relay contract: content.js → background.js ───
# Self-contained guard; other tests in this file may be rewritten without
# touching this block.


def _relay_sent_types(content):
    """Runtime `type` value for each inline content-script send, or None."""
    mask = _js_mask(content)
    sent_types = []
    for match in re.finditer(r'chrome\.runtime\.sendMessage\s*\(', mask):
        open_paren = mask.index('(', match.start())
        call_end = _js_bracket_end(mask, open_paren)
        args = _js_split_top_level(
            mask, content, open_paren + 1, call_end - 1)
        if not args:
            sent_types.append(None)
            continue
        start, end = args[0]
        if not mask[start:end].strip().startswith('{'):
            sent_types.append(None)
            continue
        obj_start = start + mask[start:end].index('{')
        runtime_type = None
        for key, value, _ in _js_object_entries(mask, content, obj_start):
            # A spread after the last explicit type can replace it, so the
            # runtime value is no longer statically readable.
            if key is None:
                runtime_type = None
            elif key == 'type':
                found = re.fullmatch(r"'([^'\\]+)'", value or '')
                runtime_type = found.group(1) if found else None
        sent_types.append(runtime_type)
    return sent_types


def _relay_handled_types(background):
    """Unmasked single-quoted msg.type comparisons inside the listener."""
    mask = _js_mask(background)
    listener_start = mask.index('chrome.runtime.onMessage.addListener')
    listener_end = _js_bracket_end(mask, mask.index('(', listener_start))
    listener = background[listener_start:listener_end]
    handled = set()
    for match in re.finditer(r"msg\.type\s*===\s*'([^'\\]+)'", listener):
        start = listener_start + match.start()
        # Comments and strings are blank at the identifier's position. The
        # raw source supplies the literal value only after this code check.
        if mask[start:start + len('msg.type')] == 'msg.type':
            handled.add(match.group(1))
    return handled


def _relay_coverage_violations(content, background):
    """Return relay message types that the background listener cannot answer."""
    # One result per call makes an unreadable type fail closed. Object entries
    # are processed in source order, so a duplicate later `type` is the value
    # JavaScript sends at runtime.
    extracted = _relay_sent_types(content)
    sent_types = [item for item in extracted if item is not None]
    send_count = len(extracted)
    if len(sent_types) != send_count:
        return [
            f'content.js has {send_count} chrome.runtime.sendMessage call(s) '
            f'but only {len(sent_types)} readable single-quoted type(s) — '
            'the relay shape changed and this guard is stale']
    sent = set(sent_types)
    if not sent:
        return [
            'found no chrome.runtime.sendMessage types in content.js — '
            'the relay shape changed and this guard is stale']
    # Only unmasked branches inside the onMessage listener count: comparisons
    # in comments, strings, helpers, or code after the listener are excluded.
    handled = _relay_handled_types(background)
    missing = sorted(sent - handled)
    if missing:
        return [
            'extension/content.js sends message type(s) '
            + ', '.join(repr(item) for item in missing)
            + ' but extension/background.js onMessage listener has no branch '
            'for them — the send resolves undefined silently. Add the branch '
            'in extension/background.js or remove the send in '
            'extension/content.js.']
    return []


def test_every_content_script_message_type_has_a_background_branch(tmp):
    """Every type content.js sends must have a branch in the background.

    `content.js` relays page-context calls to the service worker with
    `chrome.runtime.sendMessage({ type: ... })`. If the `onMessage` listener
    in `background.js` has no branch for a type, the callback fires with
    `undefined` and the page-side promise resolves to `undefined` with no
    error logged anywhere. The `GM.cookie.list()` relay shipped exactly like
    that — documented in the README, wired in content.js, and dead from the
    day it was written because no `cookies` branch ever existed. (The
    page-facing surface has since been removed; this guard keeps any future
    relay from regressing the same way.) Duplicate object keys are evaluated
    in source order, so the last `type` is checked. Unreadable send shapes
    fail closed, and only unmasked comparisons inside the listener count as
    handlers. What is NOT enforced: `_js_mask` does not parse regex literals,
    so a regex literal containing a full `msg.type === 'value'` comparison
    could still look like code to the handler scan.
    """
    del tmp
    content = (ROOT / 'extension' / 'content.js').read_text(encoding='utf-8')
    background = (ROOT / 'extension' / 'background.js').read_text(encoding='utf-8')
    violations = _relay_coverage_violations(content, background)
    assert not violations, '\n'.join(violations)

    reversions = [
        (
            'duplicate type whose last value wins at runtime',
            "chrome.runtime.sendMessage({ type: 'handled',"
            " type: 'runtimeOnly' });",
            "chrome.runtime.onMessage.addListener((msg) => {"
            " if (msg.type === 'handled') {} });",
            'runtimeOnly',
        ),
        (
            'comparison that exists only in a comment',
            "chrome.runtime.sendMessage({ type: 'commentOnly' });",
            "chrome.runtime.onMessage.addListener((msg) => {"
            " // if (msg.type === 'commentOnly') {}\n});",
            'commentOnly',
        ),
    ]
    for label, content_mutation, background_mutation, missing_type in reversions:
        found = _relay_coverage_violations(
            content_mutation, background_mutation)
        assert any(missing_type in item for item in found), (
            f'{label} was NOT caught — the guard asserts a contract it does '
            f'not enforce:\ncontent: {content_mutation}\n'
            f'background: {background_mutation}\nviolations: {found}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
