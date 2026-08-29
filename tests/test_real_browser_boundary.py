#!/usr/bin/env python3
"""Browser-free controls for the fixture's fault boundary mechanisms."""
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _realbrowser  # noqa: E402
import _realbrowser_controls  # noqa: E402
import _util  # noqa: E402


def _nested_run(run, namespace):
    output = io.StringIO()
    with mock.patch.dict(os.environ), contextlib.redirect_stdout(output):
        os.environ.pop('DAEDALUS_TEST_SUMMARY', None)
        status = run(namespace)
    lines = [line.strip() for line in output.getvalue().splitlines()]
    return status, lines


def _raising(name, failure):
    def test(tmp):
        del tmp
        raise failure
    test.__name__ = name
    return test


def _verdicts(lines):
    return {line.split()[1].rstrip(':'): line.split()[0]
            for line in lines
            if line.split()[:1]
            and line.split()[0] in ('PASS', 'SKIP', 'FAIL', 'ERROR')}


def test_browser_free_controls_fail_on_any_escaped_fixture_skip(tmp):
    del tmp

    def test_passes(tmp):
        del tmp

    namespace = {
        'test_environment': _raising(
            'test_environment', _realbrowser.BrowserEnvironmentSkipped(
                'controlled fixture environment verdict')),
        'test_plain': _raising(
            'test_plain', _util.Skipped('controlled bare skip')),
        'test_requirement': _raising(
            'test_requirement',
            _realbrowser_controls.ControlRequirementSkipped(
                'controlled requirement')),
        'test_passes': test_passes,
    }
    status, lines = _nested_run(
        lambda ns: _realbrowser_controls.run_controls(ns, 'boundary_'),
        namespace)
    assert status == 1, (status, lines)
    verdicts = _verdicts(lines)
    assert verdicts == {
        'test_environment': 'FAIL',
        'test_plain': 'FAIL',
        'test_requirement': 'SKIP',
        'test_passes': 'PASS',
    }, (verdicts, lines)


def test_real_browser_suite_skips_only_on_the_environment_verdict(tmp):
    del tmp
    namespace = {
        'test_environment': _raising(
            'test_environment', _realbrowser.BrowserEnvironmentSkipped(
                'controlled fixture environment verdict')),
        'test_plain': _raising(
            'test_plain', _util.Skipped('controlled bare skip')),
        'test_requirement': _raising(
            'test_requirement',
            _realbrowser_controls.ControlRequirementSkipped(
                'controlled requirement')),
    }
    status, lines = _nested_run(
        lambda ns: _realbrowser_controls.run_real_browser_tests(
            ns, 'boundary_', 'nothing'), namespace)
    assert status == 1, (status, lines)
    verdicts = _verdicts(lines)
    assert verdicts == {
        'test_environment': 'SKIP',
        'test_plain': 'FAIL',
        'test_requirement': 'FAIL',
    }, (verdicts, lines)


def _manifest_shapes(tmp):
    outside = Path(tmp) / 'outside.js'
    outside.write_text('', encoding='utf-8')
    named = str(outside.resolve())
    manifest = 'manifest.json'
    return {
        'escaping': (json.dumps(
            {'background': {'service_worker': '../outside.js'}}), named),
        'absolute': (json.dumps(
            {'background': {'service_worker': str(outside)}}), named),
        'missing': (json.dumps(
            {'background': {'service_worker': 'missing.js'}}), 'missing.js'),
        'empty': (json.dumps(
            {'background': {'service_worker': ''}}), manifest),
        'non-string': (json.dumps(
            {'background': {'service_worker': 3}}), manifest),
        'undeclared': (json.dumps({'background': {}}), manifest),
        'no-background': (json.dumps({'name': 'controlled'}), manifest),
        'not-an-object': (json.dumps(['background']), manifest),
        'invalid-json': ('{"background": ', manifest),
        'absent': (None, manifest),
    }


def test_every_unloadable_declared_worker_is_repository_failure(tmp):
    launch = mock.Mock(side_effect=AssertionError(
        'browser launched before the declared worker was checked'))
    for label, (manifest, named) in _manifest_shapes(tmp).items():
        extension = Path(tmp) / f'{label}-extension'
        extension.mkdir()
        if manifest is not None:
            (extension / 'manifest.json').write_text(
                manifest, encoding='utf-8')
        failure = None
        with mock.patch.object(
                _realbrowser, 'browser_requirements',
                return_value=('node-for-control', '/controlled/chromium')), \
                mock.patch.object(_realbrowser.subprocess, 'Popen', launch):
            try:
                with _realbrowser.real_extension_page(
                        tmp, 'http://127.0.0.1:1', 'controltoken',
                        'http://127.0.0.1:2/plain.html',
                        extension_root=extension):
                    raise AssertionError(f'{label} fixture yielded')
            except Exception as why:  # noqa: BLE001
                failure = why
        assert failure.__class__ is AssertionError, (label, failure)
        assert named in str(failure), (label, failure)
        launch.assert_not_called()


def test_declared_worker_is_returned_relative_and_drives_the_filter(tmp):
    extension = Path(tmp) / 'nested-extension'
    (extension / 'sw').mkdir(parents=True)
    (extension / 'sw' / 'worker.js').write_text('', encoding='utf-8')
    (extension / 'manifest.json').write_text(json.dumps(
        {'background': {'service_worker': 'sw/worker.js'}}),
        encoding='utf-8')
    assert _realbrowser.declared_worker(extension.resolve()) == 'sw/worker.js'
    targets = [
        {'type': 'service_worker',
         'url': 'chrome-extension://other/background.js'},
        {'type': 'service_worker',
         'url': 'chrome-extension://ours/sw/worker.js'},
        {'type': 'page', 'url': 'chrome-extension://ours/sw/worker.js'},
    ]
    assert _realbrowser._worker_targets(targets, 'sw/worker.js') == [
        targets[1]], targets
    assert _realbrowser._worker_targets(targets, 'background.js') == [
        targets[0]], targets


def test_closed_region_converts_only_the_environment_verdict(tmp):
    del tmp
    environment = _realbrowser.BrowserEnvironmentSkipped(
        'controlled late skip')
    outcomes = {}
    for label, raised in (
            ('environment', environment),
            ('failure', AssertionError('controlled failure')),
            ('bare', _util.Skipped('controlled bare skip'))):
        try:
            with _realbrowser._environment_verdicts_closed():
                raise raised
        except Exception as why:  # noqa: BLE001
            outcomes[label] = why
    assert outcomes['environment'].__class__ is AssertionError, outcomes
    assert outcomes['environment'].__cause__ is environment, outcomes
    assert 'controlled late skip' in str(outcomes['environment']), outcomes
    assert outcomes['failure'].__class__ is AssertionError, outcomes
    assert str(outcomes['failure']) == 'controlled failure', outcomes
    assert outcomes['bare'].__class__ is _util.Skipped, outcomes


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='realbrowserboundary_')


if __name__ == '__main__':
    raise SystemExit(main())
