#!/usr/bin/env python3
"""The speed verdict's record and table, with the ratio that decided them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _speedharness import (  # noqa: E402
    run_workflow_script, workflow_script)
from _wfgraph import _tests_yml  # noqa: E402


def _record_cell(tmp):
    """One cell directory holding the record the cell's own step wrote."""
    cells = Path(tmp) / 'cells' / 'speed-durations-bridge'
    cells.mkdir(parents=True)
    return cells / 'verdict.json'


def _record_step(tmp, verdict, ratio=None):
    """The record step, run over a reports directory seeded with a ratio."""
    workflow = _tests_yml()
    reports = Path(tmp) / 'reports'
    reports.mkdir()
    if ratio is not None:
        (reports / 'ratio.txt').write_text(ratio, encoding='utf-8')
    environment = {'GROUP': 'bridge', 'VERDICT': verdict}
    return run_workflow_script(
        tmp, workflow_script(workflow, 'timed', 'Record the cell verdict'),
        environment)


def _aggregate(tmp):
    """The aggregate step's run over the cells' records, with its summary."""
    workflow = _tests_yml()
    workdir = Path(tmp)
    summary = workdir / 'summary.md'
    result = run_workflow_script(
        workdir,
        workflow_script(workflow, 'speed', 'Aggregate the cell verdicts'),
        {
            'AGGREGATE': 'success',
            'TIMED': 'success',
            'DOCS_ONLY': 'false',
            'GITHUB_STEP_SUMMARY': str(summary),
        })
    assert result.returncode == 0, (result.stdout, result.stderr)
    return result, summary.read_text(encoding='utf-8')


def test_a_record_carries_the_ratio_its_file_exported(tmp):
    result = _record_step(tmp, 'success', ratio='1.042\n')
    assert result.returncode == 0, (result.stdout, result.stderr)
    reports = Path(tmp) / 'reports'
    written = (reports / 'verdict.json').read_text(encoding='utf-8')
    expected = '{"group": "bridge", "verdict": "pass", "ratio": 1.042}\n'
    assert written == expected, written


def test_a_cell_with_no_ratio_file_records_null(tmp):
    result = _record_step(tmp, 'failure')
    assert result.returncode == 0, (result.stdout, result.stderr)
    reports = Path(tmp) / 'reports'
    written = (reports / 'verdict.json').read_text(encoding='utf-8')
    expected = '{"group": "bridge", "verdict": "fail", "ratio": null}\n'
    assert written == expected, written


def test_the_table_carries_the_ratio_beside_the_verdict(tmp):
    record = _record_cell(tmp)
    record.write_text(
        '{"group": "bridge", "verdict": "pass", "ratio": 1.042}\n',
        encoding='utf-8')
    _result, text = _aggregate(tmp)
    assert '| bridge | pass | 1.042 | `speed-durations-bridge` |' in text, text
    assert '| cell | verdict | ratio | durations artifact |' in text, text
    assert '|---|---|---|---|' in text, text


def test_a_null_ratio_renders_as_a_dash(tmp):
    record = _record_cell(tmp)
    record.write_text(
        '{"group": "bridge", "verdict": "pass", "ratio": null}\n',
        encoding='utf-8')
    _result, text = _aggregate(tmp)
    assert '| bridge | pass | — | `speed-durations-bridge` |' in text, text


def test_an_unreadable_record_is_reported_and_left_out(tmp):
    record = _record_cell(tmp)
    record.write_text('not a record\n', encoding='utf-8')
    result, text = _aggregate(tmp)
    assert 'Unreadable verdict record' in result.stderr, result.stderr
    assert '| bridge |' not in text, text
    assert 'No cell uploaded' in text, text


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedverdicts_')


if __name__ == '__main__':
    raise SystemExit(main())
