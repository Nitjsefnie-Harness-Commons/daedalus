#!/usr/bin/env python3
"""Merge Node's V8 dumps into tracked JavaScript line coverage."""
import base64
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
from js_coverage import (_data_source, _file_url_path, _resolve_data,
                         collect_coverage, merge_records,  # noqa: E402
                         resolve_script, tracked_sources)


_SCRIPT = ROOT / 'scripts' / 'ci' / 'js_coverage.py'


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
        path.write_bytes(source.encode('utf-8'))
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


def _run_cli(root, dumps, *args):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(dumps), '--root', str(root),
         *args], cwd=str(ROOT), capture_output=True, text=True, timeout=60)


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


def test_file_url_outside_repository_is_not_resolved(tmp):
    """Crediting an unmatched external file URL must fail."""
    source = 'const identical = true;\n'
    root = _repository(tmp, {'extension/tracked.js': source})
    outside = Path(tmp) / 'outside.js'
    outside.write_bytes(source.encode('utf-8'))

    assert resolve_script(
        outside.as_uri(), root, tracked_sources(root)
    ) is None


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


def test_data_url_without_a_separator_is_rejected(tmp):
    """Removing the missing-separator guard must fail."""
    del tmp
    assert _data_source('data:text/javascript') is None


def test_malformed_base64_data_url_is_rejected(tmp):
    """Letting malformed base64 escape the decode guard must fail."""
    del tmp
    assert _data_source('data:text/javascript;base64,%%%') is None


def test_failed_data_decode_stops_before_source_lookup(tmp):
    """Removing the decoded-None guard must consult this forbidden map."""
    del tmp

    class UnreadableSources:
        @staticmethod
        def items():
            raise AssertionError('sources consulted after failed decode')

    assert _resolve_data(
        'data:text/javascript;base64,%%%', UnreadableSources()
    ) is None


def test_base64_data_url_with_invalid_utf8_is_rejected(tmp):
    """Returning undecodable bytes as source text must fail."""
    del tmp
    payload = base64.b64encode(b'\xff').decode('ascii')
    assert _data_source('data:text/javascript;base64,' + payload) is None


def test_empty_data_url_decodes_to_empty_source(tmp):
    """Treating an empty successful decode as an error must fail."""
    del tmp
    assert _data_source('data:text/javascript,') == ''


def test_non_file_scheme_has_no_file_path(tmp):
    """Accepting another URL scheme as a local path must fail."""
    del tmp
    assert _file_url_path('https://example.com/extension/a.js') is None


def test_foreign_file_host_has_no_file_path(tmp):
    """Accepting a foreign file host as a local path must fail."""
    del tmp
    assert _file_url_path('file://example.com/extension/a.js') is None


def test_resolution_stops_when_a_file_url_has_no_candidate(tmp):
    """Removing the candidate guard must turn this into an exception."""
    root = _repository(tmp, {
        'extension/tracked.js': 'const tracked = true;\n',
    })
    sources = tracked_sources(root)

    assert resolve_script(
        'file://example.com/extension/tracked.js', root, sources
    ) is None


def test_data_url_near_match_is_not_resolved(tmp):
    """Ignoring a tracked source's trailing newline must fail."""
    source = 'export const exact = true;\n'
    root = _repository(tmp, {'dashboard/exact.js': source})
    near_url = 'data:text/javascript,' + quote(source.rstrip('\n'), safe='')

    assert resolve_script(
        near_url, root, tracked_sources(root)
    ) is None


def test_data_url_with_unmatched_text_is_not_resolved(tmp):
    """Assigning unrelated decoded text to a tracked file must fail."""
    root = _repository(tmp, {
        'dashboard/tracked.js': 'export const tracked = true;\n',
    })
    url = 'data:text/javascript,' + quote(
        'export const unrelated = true;\n', safe='')

    assert resolve_script(url, root, tracked_sources(root)) is None


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
        result = resolve_script(url, root, sources)
    except ValueError as failure:
        assert 'ambiguous data URL' in str(failure), failure
        assert 'dashboard/duplicate.js' in str(failure), failure
        assert 'extension/duplicate.js' in str(failure), failure
    else:
        raise AssertionError(f'expected ValueError, got {result!r}')


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


def test_inner_nonzero_range_overrides_outer_zero_range(tmp):
    """Suppressing an inner hit under an outer zero must fail."""
    del tmp
    record = _record('extension/sample.js', [
        {'startOffset': 0, 'endOffset': 6, 'count': 0},
        {'startOffset': 2, 'endOffset': 4, 'count': 1},
    ])

    assert merge_records([record], 6) == [0, 0, 1, 1, 0, 0]


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


def test_coverage_range_past_source_length_is_an_error(tmp):
    """Dropping the stale-dump overflow guard must fail."""
    del tmp
    record = _record('extension/stale.js', [
        {'startOffset': 0, 'endOffset': 4, 'count': 1},
    ])

    try:
        merge_records([record], 3)
    except ValueError as failure:
        assert 'coverage range 0:4 outside source length 3' in str(failure)
    else:
        raise AssertionError('an offset past the source was accepted')


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


def _cli_fixture(tmp):
    source = 'run();\nskip();\n'
    root = _repository(tmp, {
        'extension/run.js': source,
        'dashboard/unseen.js': 'unseen();\n',
    })
    dumps = Path(tmp) / 'coverage'
    _write_dump(dumps, [
        _record('extension/run.js', [
            {'startOffset': 0, 'endOffset': len(source), 'count': 1},
            {'startOffset': 7, 'endOffset': 14, 'count': 0},
        ]),
        _record('node:fs', []),
        _record('[eval]', []),
    ])
    return root, dumps


def test_cli_renders_per_file_total_and_attribution_markdown(tmp):
    """Dropping a row, total, or attribution count must fail."""
    root, dumps = _cli_fixture(tmp)

    completed = _run_cli(root, dumps)

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == '', completed.stderr
    assert completed.stdout == (
        '| Name | Covered | Total | Cover |\n'
        '| :--- | ---: | ---: | ---: |\n'
        '| dashboard/unseen.js | 0 | 1 | 0.0% |\n'
        '| extension/run.js | 1 | 2 | 50.0% |\n'
        '| **TOTAL** | **1** | **3** | **33.3%** |\n'
        '\n'
        'Unattributed V8 records: 1 built-in, 1 other '
        '(2 total of 3).\n')


def test_cli_fail_under_returns_nonzero_below_measured_total(tmp):
    """Ignoring a floor above the real total must fail."""
    root, dumps = _cli_fixture(tmp)

    completed = _run_cli(root, dumps, '--fail-under', '33.4')

    assert completed.returncode != 0, completed.stdout
    assert 'Coverage failure: total of 33.3 is less than fail-under=33.4' \
        in completed.stderr, completed.stderr


def test_cli_total_format_is_machine_readable(tmp):
    """Making the ratchet scrape the Markdown table must fail."""
    root, dumps = _cli_fixture(tmp)

    completed = _run_cli(root, dumps, '--format=total')

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stdout == '33.3\n', completed.stdout
    assert completed.stderr == '', completed.stderr


def test_cli_writes_cobertura_with_repo_paths_and_line_hits(tmp):
    """Absolute paths, omitted executable lines, or wrong hits must fail."""
    root, dumps = _cli_fixture(tmp)
    xml_path = Path(tmp) / 'javascript-coverage.xml'

    completed = _run_cli(root, dumps, '--xml', str(xml_path))

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    tree = ET.parse(xml_path)
    classes = {
        node.get('filename'): {
            int(line.get('number')): int(line.get('hits'))
            for line in node.findall('./lines/line')
        }
        for node in tree.findall('.//class')
    }
    assert classes == {
        'dashboard/unseen.js': {1: 0},
        'extension/run.js': {1: 1, 2: 0},
    }


def test_real_node_dump_reports_executed_and_skipped_lines(tmp):
    """A drift from Node's emitted schema or offsets must fail."""
    node = shutil.which('node')
    if node is None:
        _util.skip('node is not installed')
    source = (
        'function called() {\n'
        '  return 1;\n'
        '}\n'
        'function missed() {\n'
        '  return 2;\n'
        '}\n'
        'called();\n'
        'console.log("done");\n'
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
    assert coverage.executable_lines == {1, 2, 3, 4, 5, 6, 7, 8}
    assert coverage.covered_lines == {1, 2, 3, 7, 8}


def test_real_node_dump_preserves_crlf_offsets(tmp):
    """Translating CRLF before applying V8 offsets must fail everywhere."""
    node = shutil.which('node')
    if node is None:
        _util.skip('node is not installed')
    source = (
        b'const hit = 1;\r\n'
        b'function missed() {\r\n'
        b'  return 2;\r\n'
        b'}\r\n'
        b'console.log(hit);\r\n'
    )
    root = _repository(tmp, {'extension/crlf.js': ''})
    script = root / 'extension' / 'crlf.js'
    script.write_bytes(source)
    dumps = Path(tmp) / 'coverage'
    dumps.mkdir()
    env = dict(os.environ)
    env['NODE_V8_COVERAGE'] = str(dumps)

    completed = subprocess.run(
        [node, str(script)], cwd=str(root), env=env,
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    report = collect_coverage(dumps, root)
    coverage = report.files['extension/crlf.js']
    assert coverage.executable_lines == {1, 2, 3, 4, 5}
    assert coverage.covered_lines == {1, 5}


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='jscoverage_')


if __name__ == '__main__':
    raise SystemExit(main())
