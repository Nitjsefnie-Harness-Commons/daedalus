#!/usr/bin/env python3
"""Merge Node's V8 dumps into tracked JavaScript line coverage."""
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
from js_coverage import (collect_coverage, merge_records,  # noqa: E402
                         resolve_script, tracked_sources)


def _git(root, *args):
    return subprocess.run(
        ['git', '-C', str(root), *args], capture_output=True,
        check=True, text=True, timeout=30)


def _repository(tmp, sources):
    root = Path(tmp) / 'repo'
    root.mkdir()
    _git(root, 'init', '-q')
    for rel, source in sources.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    _git(root, 'add', *sources)
    return root


def _record(url, ranges):
    return {
        'scriptId': '1',
        'url': url,
        'functions': [{
            'functionName': '',
            'isBlockCoverage': True,
            'ranges': ranges,
        }],
    }


def _write_dump(directory, records, name='coverage-fixture.json'):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        json.dumps({'result': records, 'timestamp': 1}),
        encoding='utf-8')


def test_tracked_sources_uses_git_and_the_shipped_directories(tmp):
    """A walk, broad suffix filter, or untracked-file scan must fail."""
    root = _repository(tmp, {
        'extension/kept.js': 'const extension = 1;\n',
        'dashboard/kept.js': 'const dashboard = 1;\n',
        'extension/not-js.txt': 'text\n',
        'examples/out.js': 'const example = 1;\n',
    })
    untracked = root / 'dashboard' / 'untracked.js'
    untracked.write_text('const untracked = 1;\n', encoding='utf-8')

    assert tracked_sources(root) == {
        'dashboard/kept.js': 'const dashboard = 1;\n',
        'extension/kept.js': 'const extension = 1;\n',
    }


def test_absolute_relative_and_file_urls_resolve_to_tracked_files(tmp):
    """Dropping any path-based V8 naming route must fail."""
    root = _repository(tmp, {
        'extension/absolute.js': 'const absolute = 1;\n',
        'dashboard/relative.js': 'const relative = 1;\n',
        'dashboard/file-url.js': 'const fileUrl = 1;\n',
    })
    sources = tracked_sources(root)

    assert resolve_script(
        str(root / 'extension' / 'absolute.js'), root, sources
    ) == 'extension/absolute.js'
    assert resolve_script(
        'dashboard/relative.js', root, sources
    ) == 'dashboard/relative.js'
    assert resolve_script(
        (root / 'dashboard' / 'file-url.js').as_uri(), root, sources
    ) == 'dashboard/file-url.js'


def test_percent_and_base64_data_urls_match_exact_source_text(tmp):
    """Changing either data decoder or matching decoded bytes must fail."""
    percent_source = 'export const percent = "one & two";\n'
    base64_source = 'export const encoded = "three";\n'
    root = _repository(tmp, {
        'dashboard/percent.js': percent_source,
        'dashboard/base64.js': base64_source,
    })
    sources = tracked_sources(root)
    percent_url = 'data:text/javascript,' + quote(percent_source, safe='')
    payload = base64.b64encode(base64_source.encode()).decode('ascii')
    base64_url = 'data:text/javascript;base64,' + payload

    assert resolve_script(
        percent_url, root, sources
    ) == 'dashboard/percent.js'
    assert resolve_script(
        base64_url, root, sources
    ) == 'dashboard/base64.js'


def test_ambiguous_data_url_is_an_error(tmp):
    """Selecting either of two exact source matches must fail."""
    duplicate = 'export const duplicate = true;\n'
    root = _repository(tmp, {
        'extension/duplicate.js': duplicate,
        'dashboard/duplicate.js': duplicate,
    })
    sources = tracked_sources(root)
    url = 'data:text/javascript,' + quote(duplicate, safe='')

    try:
        resolve_script(url, root, sources)
    except ValueError as failure:
        assert 'ambiguous data URL' in str(failure), failure
        assert 'dashboard/duplicate.js' in str(failure), failure
        assert 'extension/duplicate.js' in str(failure), failure
    else:
        raise AssertionError('ambiguous data URL selected a tracked file')


def test_non_shipped_script_shapes_are_not_resolved(tmp):
    """Builtins, evals, remote, untracked, and outside paths must stay out."""
    root = _repository(tmp, {
        'extension/tracked.js': 'const tracked = 1;\n',
    })
    untracked = root / 'extension' / 'untracked.js'
    untracked.write_text('const untracked = 1;\n', encoding='utf-8')
    sources = tracked_sources(root)
    ignored = (
        'node:fs',
        '[eval]',
        '[eval]-wrapper',
        'evalmachine.<anonymous>',
        '',
        str(Path(tmp).parent / 'fixture-tree' / 'extension' / 'temp.js'),
        'https://example.com/remote.js',
        str(untracked),
    )

    assert {
        url: resolve_script(url, root, sources) for url in ignored
    } == {url: None for url in ignored}


def test_inner_zero_range_overrides_outer_nonzero_range(tmp):
    """Treating any loaded outer range as coverage must fail."""
    del tmp
    record = _record('extension/sample.js', [
        {'startOffset': 0, 'endOffset': 6, 'count': 1},
        {'startOffset': 2, 'endOffset': 4, 'count': 0},
    ])

    assert merge_records([record], 6) == [1, 1, 0, 0, 1, 1]


def test_separate_script_records_add_their_counts(tmp):
    """Replacing rather than adding child-process counts must fail."""
    del tmp
    first = _record('extension/sample.js', [
        {'startOffset': 0, 'endOffset': 3, 'count': 2},
    ])
    second = _record('extension/sample.js', [
        {'startOffset': 0, 'endOffset': 3, 'count': 3},
    ])

    assert merge_records([first, second], 3) == [5, 5, 5]


def test_line_coverage_uses_nested_counts_and_keeps_unseen_files(tmp):
    """Loaded outer ranges and omitted zero-coverage files must fail."""
    executed = 'a();\nb();\nc();\n'
    unseen = 'first();\n// comment\nsecond();\n'
    root = _repository(tmp, {
        'extension/executed.js': executed,
        'dashboard/unseen.js': unseen,
    })
    dumps = Path(tmp) / 'coverage'
    _write_dump(dumps, [_record('extension/executed.js', [
        {'startOffset': 0, 'endOffset': len(executed), 'count': 1},
        {'startOffset': 5, 'endOffset': 9, 'count': 0},
    ])])

    report = collect_coverage(dumps, root)

    assert report.files['extension/executed.js'].executable_lines == {
        1, 2, 3,
    }
    assert report.files['extension/executed.js'].covered_lines == {1, 3}
    assert report.files['dashboard/unseen.js'].executable_lines == {1, 3}
    assert report.files['dashboard/unseen.js'].covered_lines == set()


def test_report_counts_builtin_and_other_unattributed_records(tmp):
    """Silently dropping either kind of measurement gap must fail."""
    source = 'run();\n'
    root = _repository(tmp, {'extension/run.js': source})
    dumps = Path(tmp) / 'coverage'
    _write_dump(dumps, [
        _record('extension/run.js', [
            {'startOffset': 0, 'endOffset': len(source), 'count': 1},
        ]),
        _record('node:fs', []),
        _record('[eval]', []),
        _record(str(Path(tmp).parent / 'outside.js'), []),
    ])

    report = collect_coverage(dumps, root)

    assert report.records_seen == 4
    assert report.ignored_builtins == 1
    assert report.ignored_other == 2
    assert report.unattributed_records == 3


def test_real_node_dump_reports_executed_and_skipped_lines(tmp):
    """A drift from Node's emitted schema or offsets must fail."""
    node = shutil.which('node')
    if node is None:
        _util.skip('node is not installed')
    source = (
        'const hit = 1;\n'
        'function missed() {\n'
        '  return 2;\n'
        '}\n'
        'console.log(hit);\n'
    )
    root = _repository(tmp, {'extension/real.js': source})
    dumps = Path(tmp) / 'coverage'
    dumps.mkdir()
    env = dict(os.environ)
    env['NODE_V8_COVERAGE'] = str(dumps)

    completed = subprocess.run(
        [node, str(root / 'extension' / 'real.js')], cwd=str(root),
        env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert list(dumps.glob('coverage-*.json')), 'node wrote no coverage dump'

    report = collect_coverage(dumps, root)
    coverage = report.files['extension/real.js']
    assert coverage.executable_lines == {1, 2, 3, 4, 5}
    assert coverage.covered_lines == {1, 5}


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='jscoverage_')


if __name__ == '__main__':
    raise SystemExit(main())
