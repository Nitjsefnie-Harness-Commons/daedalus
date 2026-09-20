#!/usr/bin/env python3
"""Direct boundary coverage for the patch coverage reporter."""
import contextlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
import diff_coverage  # noqa: E402


def test_cli_diff_file_preserves_internal_bare_cr(tmp):
    coverage_xml = Path(tmp) / 'coverage.xml'
    coverage_xml.write_text(
        '<coverage><class filename="extension/content.js"><lines>'
        '<line number="1" hits="1"/>'
        '<line number="2" hits="0"/>'
        '</lines></class></coverage>\n', encoding='utf-8')
    diff = Path(tmp) / 'patch.diff'
    diff.write_bytes(
        b'--- /dev/null\n'
        b'+++ b/extension/content.js\n'
        b'@@ -0,0 +1,2 @@\n'
        b'+first();\r second();\n'
        b'+missed();\n')
    done = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/ci/diff_coverage.py'),
         '--coverage', str(coverage_xml), '--diff', str(diff)],
        cwd=tmp, env=_util.child_coverage('scrub'),
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '**50.0%** of added lines covered (1/2).' in done.stdout, (
        done.stdout)
    assert '| `extension/content.js` | 1 | 2 | 2 |' in done.stdout, (
        done.stdout)


def test_added_lines_split_only_on_git_newlines(tmp):
    del tmp
    separators = '\v\f\r\x1c\x1d\x1e\x85\u2028\u2029'
    actual = {}
    for char in separators:
        diff = (
            '--- /dev/null\n'
            '+++ b/sample.py\n'
            '@@ -0,0 +1,2 @@\n'
            f'+first = 1{char}\n'
            '+second = 2\n')
        actual[f'U+{ord(char):04X}'] = diff_coverage.added_lines(diff)
    expected = {
        f'U+{ord(char):04X}': {'sample.py': {1, 2}}
        for char in separators}
    assert actual == expected, actual


def test_configured_omissions_do_not_demand_coverage(tmp):
    del tmp
    added = {
        '.claude/skills/changing-daedalus/watch_all.py': {1},
        '.venv/lib/helper.py': {1},
        'build/lib/helper.py': {1},
        'dist/helper.py': {1},
        'vendor/site-packages/helper.py': {1},
        'node_modules/pkg/helper.py': {1},
        'tests/test_helper.py': {1},
    }
    with contextlib.chdir(ROOT):
        missing = diff_coverage.unmeasured_sources({}, added)
    assert missing == set(), missing
    body = diff_coverage.render([], 0, 0, missing)
    assert 'no patch coverage to report' in body, body


def test_omissions_follow_current_config_and_keep_other_source(tmp):
    config = Path(tmp) / 'pyproject.toml'
    absolute = (Path(tmp) / 'absolute.py').as_posix()
    config.write_text(
        '[tool.coverage.run]\n'
        f'omit = ["generated/*", "*/vendored/*", "{absolute}"]\n'
        '[tool.coverage.report]\n'
        'omit = ["report_only.py", "extension/*"]\n',
        encoding='utf-8')
    added = {
        'absolute.py': {1},
        'generated/nested/helper.py': {1},
        'pkg/vendored/helper.py': {1},
        'report_only.py': {1},
        'generated_elsewhere/helper.py': {1},
        'xgenerated/nested/helper.py': {1},
        'pkg/helper.py': {1},
        'extension/content.js': {1},
    }
    with contextlib.chdir(tmp):
        missing = diff_coverage.unmeasured_sources({}, added)
    body = diff_coverage.render([], 0, 0, missing)
    assert 'Unmeasured changed source files:' in body, body
    for path in (
            'generated_elsewhere/helper.py', 'xgenerated/nested/helper.py',
            'pkg/helper.py', 'extension/content.js'):
        assert f'- `{path}`' in body, body
    for path in (
            'absolute.py', 'generated/nested/helper.py',
            'pkg/vendored/helper.py', 'report_only.py'):
        assert f'`{path}`' not in body, body
    assert missing == {
        'generated_elsewhere/helper.py', 'xgenerated/nested/helper.py',
        'pkg/helper.py',
        'extension/content.js'}, missing


def test_omission_near_misses_stay_in_the_complaint(tmp):
    del tmp
    added = {
        '.claude/yes.py': {1},
        '.claude2/foo.py': {1},
        'x.claude/foo.py': {1},
        'pkg/helper.py': {1},
    }
    with contextlib.chdir(ROOT):
        missing = diff_coverage.unmeasured_sources({}, added)
    body = diff_coverage.render([], 0, 0, missing)
    assert 'Unmeasured changed source files:' in body, body
    for path in ('.claude2/foo.py', 'x.claude/foo.py', 'pkg/helper.py'):
        assert f'- `{path}`' in body, body
    assert '`.claude/yes.py`' not in body, body


def test_decode_git_path_keeps_a_final_unmatched_backslash(tmp):
    del tmp
    assert diff_coverage._decode_git_path('"b/end\\') == 'end\\'


def test_decode_git_path_decodes_the_tab_escape(tmp):
    del tmp
    assert diff_coverage._decode_git_path('"b/a\\tb"') == 'a\tb'


def test_measure_skips_added_lines_without_statement_records(tmp):
    del tmp
    assert diff_coverage.measure({'x.py': {1: 1}}, {'x.py': {9}}) == (
        [], 0, 0)


def test_executable_lines_skips_a_nameless_class_before_a_usable_one(tmp):
    coverage_xml = Path(tmp) / 'classes.xml'
    coverage_xml.write_text(
        '<coverage><classes>'
        '<class><lines><line number="1" hits="1"/></lines></class>'
        '<class filename="usable.py"><lines>'
        '<line number="1" hits="1"/>'
        '</lines></class>'
        '</classes></coverage>\n',
        encoding='utf-8')
    assert diff_coverage.executable_lines(coverage_xml) == {
        'usable.py': {1: 1}}


def test_executable_lines_refuses_coordinate_zero(tmp):
    coverage_xml = Path(tmp) / 'zero.xml'
    coverage_xml.write_text(
        '<coverage><class filename="zero.py"><lines>'
        '<line number="0" hits="0"/>'
        '</lines></class></coverage>\n',
        encoding='utf-8')
    try:
        diff_coverage.executable_lines(coverage_xml)
    except ValueError as error:
        assert str(error) == (
            "invalid line number for zero.py: '0' (must be positive)")
    else:
        raise AssertionError('coordinate zero was accepted')


def test_validate_statement_records_refuses_an_omitted_statement(tmp):
    source = Path(tmp) / 'statements.py'
    source.write_text('first = 1\nsecond = 2\n', encoding='utf-8')
    measured = {str(source): {1: 1}}
    added = {str(source): {1, 2}}
    try:
        diff_coverage.validate_statement_records(measured, added)
    except ValueError as error:
        assert str(error) == (
            f'missing executable statement records for {source}: 2')
    else:
        raise AssertionError('an omitted statement record was accepted')


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
