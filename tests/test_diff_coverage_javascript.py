#!/usr/bin/env python3
"""Patch coverage across two languages: Python and shipped JavaScript."""
import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
import diff_coverage  # noqa: E402

_SCRIPT = ROOT / 'scripts' / 'ci' / 'diff_coverage.py'

_PYTHON_XML = (
    '<?xml version="1.0" ?>\n'
    '<coverage><packages><package name="pkg"><classes>\n'
    '  <class filename="pkg/mod.py"><lines>\n'
    '    <line number="1" hits="1"/>\n'
    '  </lines></class>\n'
    '</classes></package></packages></coverage>\n')

_JAVASCRIPT_XML = (
    '<?xml version="1.0" ?>\n'
    '<coverage><packages><package name="javascript"><classes>\n'
    '  <class filename="extension/content.js"><lines>\n'
    '    <line number="1" hits="1"/>\n'
    '    <line number="2" hits="0"/>\n'
    '  </lines></class>\n'
    '</classes></package></packages></coverage>\n')

_BOTH_LANGUAGES_NOTE = (
    'This number covers added Python and JavaScript lines.')
_PYTHON_ONLY_NOTE = (
    'Only the Python report was given, so added JavaScript lines are '
    'not measured.')
_JAVASCRIPT_ONLY_NOTE = (
    'Only the JavaScript report was given, so added Python lines are '
    'not measured.')
_NO_LANGUAGE_NOTE = (
    'The report measured neither Python nor JavaScript, so added lines '
    'in both languages are not measured.')


def _write(tmp, name, text):
    """Write one fixture file under tmp and return its path."""
    path = Path(tmp) / name
    path.write_text(text, encoding='utf-8')
    return path


def _run(tmp, *args):
    """Run the reporter inside the fixture directory."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=tmp, env=_util.coverage_free_environment(os.environ),
        capture_output=True, text=True, timeout=60)


def _run_main(tmp, *args):
    """Run the real entry point in-process and capture its stdout.

    A subprocess proves the wiring but the suite's coverage measurement
    never sees inside it: the per-change report named main()'s
    --js-coverage arm and the merge helper as never executed while every
    subprocess case around them was green. main() parses real argv and
    reads real files either way, so this is the same entry point the
    workflow invokes, made visible to the measurement.
    """
    out = io.StringIO()
    argv, cwd = sys.argv, os.getcwd()
    sys.argv = ['diff_coverage.py', *args]
    os.chdir(tmp)
    try:
        with contextlib.redirect_stdout(out):
            status = diff_coverage.main()
    finally:
        sys.argv = argv
        os.chdir(cwd)
    return status, out.getvalue()


def _both_reports(tmp):
    """Write one report per language and return their CLI arguments."""
    coverage_xml = _write(tmp, 'coverage.xml', _PYTHON_XML)
    js_xml = _write(tmp, 'javascript-coverage.xml', _JAVASCRIPT_XML)
    return '--coverage', str(coverage_xml), '--js-coverage', str(js_xml)


def test_a_covered_added_javascript_line_is_counted(tmp):
    """An added shipped-JavaScript line the run reached counts covered."""
    diff = _write(tmp, 'patch.diff',
                  '--- a/extension/content.js\n'
                  '+++ b/extension/content.js\n'
                  '@@ -0,0 +1 @@\n'
                  '+reached()\n')
    done = _run(tmp, *_both_reports(tmp), '--diff', str(diff))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '**100.0%** of added lines covered (1/1).' in done.stdout, (
        done.stdout)
    assert '| `extension/content.js` | 1 | 1 | — |' in done.stdout, (
        done.stdout)
    # A reader must be able to tell which languages the number covers.
    assert _BOTH_LANGUAGES_NOTE in done.stdout, done.stdout


def test_an_uncovered_added_javascript_line_is_counted_missed(tmp):
    """A zero-hit JavaScript record is a miss, not an absent line."""
    diff = _write(tmp, 'patch.diff',
                  '--- a/extension/content.js\n'
                  '+++ b/extension/content.js\n'
                  '@@ -1,0 +2 @@\n'
                  '+unreached()\n')
    done = _run(tmp, *_both_reports(tmp), '--diff', str(diff))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '**0.0%** of added lines covered (0/1).' in done.stdout, (
        done.stdout)
    assert '| `extension/content.js` | 0 | 1 | 2 |' in done.stdout, (
        done.stdout)


def test_one_diff_can_measure_both_languages(tmp):
    """One run totals the added statements of Python and JavaScript."""
    package = Path(tmp) / 'pkg'
    package.mkdir()
    _write(package, 'mod.py', 'one = 1\n')
    diff = _write(tmp, 'patch.diff',
                  '--- a/pkg/mod.py\n'
                  '+++ b/pkg/mod.py\n'
                  '@@ -0,0 +1 @@\n'
                  '+one = 1\n'
                  '--- a/extension/content.js\n'
                  '+++ b/extension/content.js\n'
                  '@@ -1,0 +2 @@\n'
                  '+unreached()\n')
    done = _run(tmp, *_both_reports(tmp), '--diff', str(diff))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '**50.0%** of added lines covered (1/2).' in done.stdout, (
        done.stdout)
    assert '| `pkg/mod.py` | 1 | 1 | — |' in done.stdout, done.stdout
    assert '| `extension/content.js` | 0 | 1 | 2 |' in done.stdout, (
        done.stdout)


def test_the_guard_demands_shipped_javascript_only(tmp):
    """Shipped means extension/ and dashboard/; examples/*.js stays out.

    One exact-set assertion pins both directions: a shipped path the
    report never names MUST be demanded (a path-spelling mismatch is
    otherwise invisible), and a JavaScript file outside the shipped roots
    must NOT be (examples and fixtures are not shipped source).
    """
    del tmp
    measured = {'extension/content.js': {1: 1}}
    added = {
        'extension/content.js': {1},
        'extension/options.js': {3},
        'dashboard/app.js': {7},
        'examples/kill-hls.js': {2},
    }
    missing = diff_coverage.unmeasured_sources(measured, added)
    assert missing == {'extension/options.js', 'dashboard/app.js'}, missing


def test_the_comment_names_an_unmeasured_shipped_javascript_file(tmp):
    """A partial JavaScript path mismatch is named and cannot claim 100%."""
    _write(tmp, 'mod.py', 'reached = 1\n')
    coverage_xml = _write(
        tmp, 'coverage.xml',
        '<coverage><class filename="mod.py"><lines>'
        '<line number="1" hits="1"/>'
        '</lines></class></coverage>\n')
    js_xml = _write(tmp, 'javascript-coverage.xml', _JAVASCRIPT_XML)
    diff = _write(tmp, 'patch.diff',
                  '--- a/mod.py\n'
                  '+++ b/mod.py\n'
                  '@@ -0,0 +1 @@\n'
                  '+reached = 1\n'
                  '--- a/dashboard/app.js\n'
                  '+++ b/dashboard/app.js\n'
                  '@@ -0,0 +1 @@\n'
                  '+never_measured()\n')
    done = _run(tmp, '--coverage', str(coverage_xml),
                '--js-coverage', str(js_xml), '--diff', str(diff))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '`dashboard/app.js`' in done.stdout, done.stdout
    assert 'did not measure these changed source files' in done.stdout, (
        done.stdout)
    assert 'of measured added lines covered' in done.stdout, done.stdout
    assert '**100.0%** of added lines covered' not in done.stdout, (
        done.stdout)


def test_a_python_only_run_says_javascript_is_not_measured(tmp):
    """One report must not read as though it measured the whole change."""
    _write(tmp, 'mod.py', 'reached = 1\n')
    coverage_xml = _write(
        tmp, 'coverage.xml',
        '<coverage><class filename="mod.py"><lines>'
        '<line number="1" hits="1"/>'
        '</lines></class></coverage>\n')
    diff = _write(tmp, 'patch.diff',
                  '--- a/mod.py\n'
                  '+++ b/mod.py\n'
                  '@@ -0,0 +1 @@\n'
                  '+reached = 1\n')
    done = _run(tmp, '--coverage', str(coverage_xml),
                '--diff', str(diff))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '**100.0%** of added lines covered (1/1).' in done.stdout, (
        done.stdout)
    assert _PYTHON_ONLY_NOTE in done.stdout, done.stdout
    assert _BOTH_LANGUAGES_NOTE not in done.stdout, done.stdout


def test_a_javascript_report_under_coverage_is_read_as_javascript(tmp):
    """The note names what the run measured, not which flag was passed.

    --coverage accepts the JavaScript XML just as well as --js-coverage
    does. Deriving the scope note from the flag names rendered a measured
    extension/content.js row directly above "added JavaScript lines are
    not measured" — the table and the note contradicting each other.
    """
    js_xml = _write(tmp, 'javascript-coverage.xml', _JAVASCRIPT_XML)
    diff = _write(tmp, 'patch.diff',
                  '--- a/extension/content.js\n'
                  '+++ b/extension/content.js\n'
                  '@@ -0,0 +1 @@\n'
                  '+reached()\n')
    done = _run(tmp, '--coverage', str(js_xml), '--diff', str(diff))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '| `extension/content.js` | 1 | 1 | — |' in done.stdout, (
        done.stdout)
    assert _PYTHON_ONLY_NOTE not in done.stdout, done.stdout
    assert _JAVASCRIPT_ONLY_NOTE in done.stdout, done.stdout


def test_a_python_only_run_over_a_mixed_diff_says_javascript_is_out(tmp):
    """Reading scope from the report keeps correct invocations honest."""
    _write(tmp, 'mod.py', 'reached = 1\n')
    coverage_xml = _write(
        tmp, 'coverage.xml',
        '<coverage><class filename="mod.py"><lines>'
        '<line number="1" hits="1"/>'
        '</lines></class></coverage>\n')
    diff = _write(tmp, 'patch.diff',
                  '--- a/mod.py\n'
                  '+++ b/mod.py\n'
                  '@@ -0,0 +1 @@\n'
                  '+reached = 1\n'
                  '--- a/extension/content.js\n'
                  '+++ b/extension/content.js\n'
                  '@@ -0,0 +1 @@\n'
                  '+never_measured()\n')
    done = _run(tmp, '--coverage', str(coverage_xml),
                '--diff', str(diff))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert _PYTHON_ONLY_NOTE in done.stdout, done.stdout
    assert _BOTH_LANGUAGES_NOTE not in done.stdout, done.stdout
    assert '`extension/content.js`' in done.stdout, done.stdout


def test_an_unmeasured_language_is_not_blamed_on_path_spelling(tmp):
    """A language no report measured is the cause, not path spelling.

    A Python-only run over a JavaScript-only diff lands in the
    nothing-measured branch, and the scope note directly beneath already
    says JavaScript was not measured; guessing "paths spelled
    differently" there asserts a cause the reader can see is wrong.
    """
    del tmp
    body = diff_coverage.render([], 0, 0,
                                unmeasured={'extension/content.js'},
                                languages=['Python'])
    assert 'names none of the changed paths' in body, body
    assert 'spells paths differently' not in body, body
    assert _PYTHON_ONLY_NOTE in body, body
    # With every language measured the guess is the likely cause and
    # stays: an absent record then really is a spelling mismatch.
    both = diff_coverage.render([], 0, 0,
                                unmeasured={'extension/content.js'})
    assert 'spells paths differently' in both, both


def test_main_merges_both_reports_for_one_mixed_diff(tmp):
    """The workflow's invocation: both reports, one mixed-language diff.

    The subprocess cases exercise the pieces; this runs the whole CLI
    the way the workflow does and asserts on the rendered comment, so
    the wiring it depends on — main()'s --js-coverage arm, the merge
    helper, the scope note's call site — is measured, not only green.
    """
    package = Path(tmp) / 'pkg'
    package.mkdir()
    _write(package, 'mod.py', 'one = 1\n')
    diff = _write(tmp, 'patch.diff',
                  '--- a/pkg/mod.py\n'
                  '+++ b/pkg/mod.py\n'
                  '@@ -0,0 +1 @@\n'
                  '+one = 1\n'
                  '--- a/extension/content.js\n'
                  '+++ b/extension/content.js\n'
                  '@@ -0,0 +1 @@\n'
                  '+reached()\n')
    status, out = _run_main(tmp, *_both_reports(tmp), '--diff', str(diff))
    assert status == 0, (status, out)
    assert '**100.0%** of added lines covered (2/2).' in out, out
    assert '| `pkg/mod.py` | 1 | 1 | — |' in out, out
    assert '| `extension/content.js` | 1 | 1 | — |' in out, out
    assert 'Every added line was reached.' in out, out
    assert _BOTH_LANGUAGES_NOTE in out, out


def test_a_report_measuring_no_language_gets_a_distinct_sentence(tmp):
    """An empty language list must not render inside "Only the  report".

    No producer here emits such a report — coverage.py always names
    Python files and js_coverage.py only names shipped JavaScript — but
    the tool accepts arbitrary Cobertura XML, and joining an empty list
    into the one-language sentence renders "Only the  report was
    given", a broken sentence for a true claim.
    """
    del tmp
    measured = {'examples/kill-hls.js': {1: 1}}
    languages = diff_coverage.report_languages(measured)
    assert languages == [], languages

    body = diff_coverage.render([], 0, 0, languages=languages)

    assert _NO_LANGUAGE_NOTE in body, body
    assert 'Only the  report' not in body, body


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
