#!/usr/bin/env python3
"""Browser-free controls pinning the fixture's delivery-matching contract.

A polled result that is not this command's own delivery must keep the wait
alive and end in the delivery timeout assertion. Nothing here starts a
browser.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _deliveries  # noqa: E402
import _realbrowser_controls  # noqa: E402
import _util  # noqa: E402


def _call_failure(call):
    try:
        call()
    except (AssertionError, _util.Skipped) as failure:
        return failure
    raise AssertionError('controlled call did not raise')


def _unmatched_delivery_failure(call, raw, polled):
    with mock.patch.object(
            _deliveries._util, 'request', return_value=(200, raw)), \
            mock.patch.object(
                _deliveries._util, 'get_json', return_value=polled), \
            mock.patch.object(
                _deliveries.time, 'time', side_effect=(0, 0, 21)), \
            mock.patch.object(_deliveries.time, 'sleep'):
        return _call_failure(call)


def test_extension_command_without_did_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_ext_command(
            'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}),
        '{"ok": true}', (200, {'pending': True}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-command' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_eval_without_did_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'),
        '{"ok": true}', (200, {'pending': True}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-eval' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_extension_command_pending_body_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_ext_command(
            'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}),
        '{"did":"controlled-delivery"}', (200, {'pending': True}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-command' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_eval_pending_body_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'),
        '{"did":"controlled-delivery"}', (200, {'pending': True}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-eval' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_extension_command_empty_did_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_ext_command(
            'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}),
        '{"did":""}', (200, {'deliveryId': ''}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-command' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_eval_empty_did_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'),
        '{"did":""}', (200, {'deliveryId': ''}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-eval' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_extension_command_unrelated_delivery_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_ext_command(
            'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}),
        '{"did":"controlled-delivery"}',
        (200, {'deliveryId': 'unrelated-delivery'}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-command' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_eval_unrelated_delivery_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'),
        '{"did":"controlled-delivery"}',
        (200, {'deliveryId': 'unrelated-delivery'}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-eval' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_extension_command_non_200_result_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_ext_command(
            'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}),
        '{"did":"controlled-delivery"}',
        (503, {'deliveryId': 'controlled-delivery'}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-command' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_eval_non_200_result_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'),
        '{"did":"controlled-delivery"}',
        (503, {'deliveryId': 'controlled-delivery'}))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-eval' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_extension_command_non_dict_result_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_ext_command(
            'http://127.0.0.1:1', 'controltoken', 'controlled-command', {}),
        '{"did":"controlled-delivery"}',
        (200, ['unexpected non-dict body']))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-command' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def test_eval_non_dict_result_times_out(tmp):
    del tmp
    failure = _unmatched_delivery_failure(
        lambda: _deliveries.real_eval(
            'http://127.0.0.1:1', 'controltoken', 'controlled-tab',
            'controlled-eval', '2 + 2'),
        '{"did":"controlled-delivery"}',
        (200, ['unexpected non-dict body']))
    assert failure.__class__ is AssertionError, failure
    assert 'controlled-eval' in str(failure), failure
    assert 'did not return its delivery result' in str(failure), failure


def main():
    return _realbrowser_controls.run_controls(
        globals(), tmp_prefix='deliverymatching_')


if __name__ == '__main__':
    raise SystemExit(main())
