#!/usr/bin/env python3
"""Patch coverage: what the diff added, and which of it the suites reached.

The number this reports is only useful if it is honest in both directions, so
these pin the two ways it could lie. Counting a blank line or a comment as
missed would make the percentage depend on formatting; counting a file the
report never measured would make it depend on which extras a runner resolved.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
import diff_coverage  # noqa: E402

_SCRIPT = ROOT / 'scripts' / 'ci' / 'diff_coverage.py'

_COVERAGE_XML = """<?xml version="1.0" ?>
<coverage>
  <packages>
    <package name="pkg">
      <classes>
        <class filename="pkg/mod.py">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="0"/>
            <line number="3" hits="0"/>
            <line number="7" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def _write(tmp, name, text):
    """Write one fixture file under tmp and return its path."""
    path = Path(tmp) / name
    path.write_text(text, encoding='utf-8')
    return path


def test_added_lines_follow_the_new_file_numbering(tmp):
    """Context advances the counter, a removed line does not."""
    del tmp
    diff = (
        '--- a/pkg/mod.py\n'
        '+++ b/pkg/mod.py\n'
        '@@ -1,3 +1,4 @@\n'
        ' keep\n'
        '-gone\n'
        '+added_two\n'
        '+added_three\n'
        ' tail\n')
    assert diff_coverage.added_lines(diff) == {'pkg/mod.py': {2, 3}}


def test_added_lines_reads_every_hunk_of_every_file(tmp):
    """A second hunk restarts the counter from its own header."""
    del tmp
    diff = (
        '--- a/one.py\n'
        '+++ b/one.py\n'
        '@@ -1 +1,2 @@\n'
        ' a\n'
        '+b\n'
        '@@ -10,2 +11,3 @@\n'
        ' c\n'
        '+d\n'
        ' e\n'
        '--- a/two.py\n'
        '+++ b/two.py\n'
        '@@ -0,0 +1 @@\n'
        '+only\n')
    assert diff_coverage.added_lines(diff) == {
        'one.py': {2, 12}, 'two.py': {1}}


def test_added_triple_plus_content_stays_in_the_current_file(tmp):
    """A source line beginning `+++ ` is not a later file header."""
    del tmp
    diff = (
        'diff --git a/x.py b/x.py\n'
        '--- a/x.py\n'
        '+++ b/x.py\n'
        '@@ -1,0 +2,2 @@\n'
        '+++ value\n'
        '+print(value)\n')
    assert diff_coverage.added_lines(diff) == {'x.py': {2, 3}}


def test_added_lines_decode_git_quoted_paths(tmp):
    """Git C-quoted UTF-8 paths join the coverage report spelling."""
    del tmp
    diff = (
        '--- "a/caf\\303\\251.py"\n'
        '+++ "b/caf\\303\\251.py"\n'
        '@@ -1 +1,2 @@\n'
        ' old\n'
        '+new\n')
    assert diff_coverage.added_lines(diff) == {'café.py': {2}}


def test_a_deleted_file_adds_nothing(tmp):
    """`+++ /dev/null` must not become a path named after the deletion."""
    del tmp
    diff = (
        '--- a/gone.py\n'
        '+++ /dev/null\n'
        '@@ -1,2 +0,0 @@\n'
        '-a\n'
        '-b\n')
    assert diff_coverage.added_lines(diff) == {}


def test_executable_lines_keeps_the_best_hit_count(tmp):
    """One file split across classes keeps the hit count in either order."""
    for index, lines in enumerate(((0, 4), (4, 0))):
        xml = _write(tmp, f'split-{index}.xml', f"""<?xml version="1.0" ?>
<coverage><packages><package name="p"><classes>
  <class filename="pkg/mod.py"><lines>
    <line number="1" hits="{lines[0]}"/>
  </lines></class>
  <class filename="pkg/mod.py"><lines>
    <line number="1" hits="{lines[1]}"/>
  </lines></class>
</classes></package></packages></coverage>
""")
        assert diff_coverage.executable_lines(xml) == {
            'pkg/mod.py': {1: 4}}


def test_only_measured_statements_count(tmp):
    """Added lines absent from the report are excluded, not counted missed."""
    del tmp
    measured = {'pkg/mod.py': {1: 1, 2: 0, 3: 0, 7: 1}}
    # 4, 5 and 6 are blank/comment lines the report never mentions.
    added = {'pkg/mod.py': {1, 2, 4, 5, 6, 7}}
    rows, covered, total = diff_coverage.measure(measured, added)
    assert (covered, total) == (2, 3), rows
    assert rows == [('pkg/mod.py', 2, 3, [2])], rows


def test_a_file_the_report_never_measured_is_skipped(tmp):
    """A workflow or Markdown file must not drag the percentage down."""
    del tmp
    measured = {'pkg/mod.py': {1: 1}}
    added = {'pkg/mod.py': {1}, '.github/workflows/tests.yml': {4, 5, 6}}
    rows, covered, total = diff_coverage.measure(measured, added)
    assert (covered, total) == (1, 1), rows
    assert [row[0] for row in rows] == ['pkg/mod.py'], rows


def test_nothing_measurable_added_is_said_plainly(tmp):
    """Zero added statements must not render as 0%, which reads as failure."""
    del tmp
    body = diff_coverage.render([], 0, 0)
    assert 'no patch coverage to report' in body, body
    # It must name the reason: tests/* is omitted, so a test-only change
    # lands here and a bare "nothing added" would read as a broken tool.
    assert 'tests/*' in body, body
    assert '0.0%' not in body, body


def test_missed_lines_are_collapsed_into_spans(tmp):
    """A reviewer reads `12-15`, not four separate numbers."""
    del tmp
    body = diff_coverage.render([('pkg/mod.py', 1, 6, [12, 13, 14, 15, 20])],
                                1, 6)
    assert '12-15, 20' in body, body
    assert '16.7%' in body, body


def test_the_cli_reports_the_percentage_and_the_misses(tmp):
    """End to end: the script reads both inputs and prints the table."""
    coverage_xml = _write(tmp, 'coverage.xml', _COVERAGE_XML)
    diff = _write(tmp, 'patch.diff',
                  '--- a/pkg/mod.py\n'
                  '+++ b/pkg/mod.py\n'
                  '@@ -1,0 +1,3 @@\n'
                  '+one\n'
                  '+two\n'
                  '+three\n')
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), '--coverage', str(coverage_xml),
         '--diff', str(diff)],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '**33.3%** of added lines covered (1/3).' in done.stdout, (
        done.stdout)
    assert '2-3' in done.stdout, done.stdout


def test_the_cli_does_not_round_a_miss_up_to_perfect(tmp):
    """A non-perfect result must never headline as 100.0 percent."""
    del tmp
    body = diff_coverage.render(
        [('pkg/mod.py', 1999, 2000, [2000])], 1999, 2000)
    assert '**100.0%**' not in body, body


def test_non_cobertura_xml_is_an_error_not_clean_coverage(tmp):
    """A well-formed XML document with the wrong root must fail."""
    coverage_xml = _write(
        tmp, 'wrong-root.xml',
        '<html><class filename="evil.py"><line number="9" hits="12"/>'
        '</class></html>\n')
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), '--coverage', str(coverage_xml),
         '--diff', '-'],
        input=('+++ b/evil.py\n@@ -0,0 +9 @@\n+evil\n'),
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert 'root is not <coverage>' in done.stderr, done.stderr


def test_coverage_without_usable_lines_is_an_error(tmp):
    """A named file without numeric line data must fail the measurement."""
    coverage_xml = _write(
        tmp, 'unusable.xml',
        '<coverage><class filename="x.py"><line hits="1"/></class>'
        '</coverage>\n')
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), '--coverage', str(coverage_xml),
         '--diff', '-'],
        input='', capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert 'no usable line entries' in done.stderr, done.stderr


def test_an_empty_report_is_an_error_not_a_clean_result(tmp):
    """A measurement that did not happen must not read as nothing to cover."""
    coverage_xml = _write(tmp, 'empty.xml',
                          '<?xml version="1.0" ?><coverage/>\n')
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), '--coverage', str(coverage_xml),
         '--diff', '-'],
        input='', capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert 'no usable line entries' in done.stderr, done.stderr


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
