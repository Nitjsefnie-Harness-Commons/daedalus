#!/usr/bin/env python3
"""Transparency controls for test-side command queue fault injectors."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cmdqueue_faults import (  # noqa: E402
    _disappear_on_first_open,
    _path_open_failure,
    _queued_file,
    _refuse_first_queue_read,
    _refuse_path_operation,
    _vanish_during_read,
    _virtual_cmdqueue_clock,
)


def test_injectors_preserve_target_failures_and_untargeted_create(tmp):
    queue, queued = _queued_file(tmp)

    class PathProtocolExplosion(BaseException):
        pass

    class RefusingReceiver:
        def __fspath__(self):
            raise RuntimeError('receiver evaluated before argument validation')

    class ExplodingReceiver:
        def __fspath__(self):
            raise PathProtocolExplosion('path protocol exploded')

    def path_failure(operation, receiver, *args, **kwargs):
        try:
            result = getattr(Path, operation)(receiver, *args, **kwargs)
        except (OSError, RuntimeError, TypeError, ValueError,
                PathProtocolExplosion) as caught:
            filename = getattr(caught, 'filename', None)
            return type(caught), str(caught), type(filename), filename
        if operation == 'open':
            result.close()
        raise AssertionError(
            f'Path.{operation} accepted the control arguments')

    def open_outcome(opener, receiver, *args, write=None, **kwargs):
        try:
            with opener(receiver, *args, **kwargs) as opened:
                outcome = (opened.mode, opened.tell(), opened.readable(),
                           opened.writable())
                if write is not None:
                    outcome += (opened.write(write),)
        except (OSError, RuntimeError, TypeError, ValueError,
                PathProtocolExplosion) as caught:
            filename = getattr(caught, 'filename', None)
            return ('raised', (type(caught), str(caught),
                               type(filename), filename))
        return 'returned', outcome

    refusing_receiver = RefusingReceiver()
    exploding_receiver = ExplodingReceiver()
    invalid_mode = {'mode': None}
    expected_mode = path_failure(
        'open', refusing_receiver, **invalid_mode)
    expected_exploding_mode = path_failure(
        'open', exploding_receiver, **invalid_mode)
    excess_unlink = (False, 'extra')
    expected_unlink = path_failure(
        'unlink', refusing_receiver, *excess_unlink)
    expected_exploding_unlink = path_failure(
        'unlink', exploding_receiver, *excess_unlink)
    bytes_receiver = os.fsencode(str(queued))
    expected_bytes = path_failure('open', bytes_receiver, mode='x')
    excess_args = ('r', 1, -1, None, None, None, False, 'extra')
    expected_excess = _path_open_failure(queued, *excess_args)
    expected_binary = _path_open_failure(queued, mode='rb', encoding='utf-8')
    expected_buf = _path_open_failure(queued, buffering=-1.0, encoding='utf-8')
    rejected_calls = (('excess arguments', excess_args, {}, expected_excess),
                      ('binary encoding', (),
                       {'mode': 'rb', 'encoding': 'utf-8'}, expected_binary),
                      ('value-equal wrong-type buffering', (),
                       {'buffering': -1.0, 'encoding': 'utf-8'}, expected_buf))
    original, underlying_opens = Path.open, [0]

    def counted(candidate, *args, **kwargs):
        underlying_opens[0] += candidate == queued
        return original(candidate, *args, **kwargs)
    Path.open = counted
    try:
        with _refuse_path_operation(queued, 'open', 1):
            _path_open_failure(queued, encoding='utf-8')
            underlying_opens[0] = 0
            queued.open(mode='rt', encoding='utf-8').close()
    finally:
        Path.open = original
    # This open count is the transparency contract, not an internal detail.
    assert underlying_opens == [1], (
        'exhausted fault reran native-validation probe', underlying_opens)
    with _refuse_path_operation(queued, 'unlink', 1) as unlink_calls:
        actual_exploding_unlink = path_failure(
            'unlink', exploding_receiver, *excess_unlink)
        assert actual_exploding_unlink == expected_exploding_unlink, (
            'unlink exposed the injector path-protocol probe',
            actual_exploding_unlink, expected_exploding_unlink)
        actual_unlink = path_failure(
            'unlink', refusing_receiver, *excess_unlink)
        assert actual_unlink == expected_unlink, (
            'unlink named receiver before signature validation',
            actual_unlink, expected_unlink)
        unlink_fault = None
        try:
            queued.unlink()
        except PermissionError:
            unlink_fault = PermissionError
    assert unlink_fault is PermissionError, (
        'unlink fault was consumed by signature refusal', unlink_fault)
    assert unlink_calls == [1], (
        'unlink signature refusal changed target call count', unlink_calls)
    assert queued.exists(), 'unlink control removed the target path'
    with _virtual_cmdqueue_clock() as (clock, _, _):
        injectors = (
            ('generic refusal', _refuse_path_operation,
             (queued, 'open', 1), PermissionError),
            ('first-read', _refuse_first_queue_read,
             (queue,), PermissionError),
            ('disappearance', _disappear_on_first_open,
             (queued,), FileNotFoundError),
            ('vanish', _vanish_during_read,
             (queued, clock), FileNotFoundError))
        for label, injector, injector_args, injected_type in injectors:
            with injector(*injector_args):
                expected_injected = open_outcome(
                    Path.open, queued, encoding='utf-8')
            for mode in ('w', 'a'):
                queued.write_text('seed', encoding='utf-8')
                expected_write = open_outcome(
                    original, queued, mode=mode, encoding='utf-8',
                    write=mode)
                queued.write_text('seed', encoding='utf-8')
                underlying_opens[0] = 0
                Path.open = counted
                try:
                    with injector(*injector_args):
                        actual_write = open_outcome(
                            Path.open, queued, mode=mode,
                            encoding='utf-8', write=mode)
                        write_opens = underlying_opens[0]
                        target_present = queued.exists()
                        after_write = open_outcome(
                            Path.open, queued, encoding='utf-8')
                finally:
                    Path.open = original
                assert actual_write == expected_write, (
                    'target non-read open changed', label, mode,
                    actual_write, expected_write)
                assert write_opens == 1, (
                    'target non-read open count changed', label, mode,
                    write_opens)
                assert target_present, (
                    'target non-read open applied injector effects',
                    label, mode)
                assert after_write == expected_injected, (
                    'target non-read open consumed the read fault',
                    label, mode, after_write, expected_injected)
            queued.write_text(json.dumps({'id': 'queued', 'type': 'reload'}),
                              encoding='utf-8')
            before = queued.stat()
            candidate = Path(tmp) / f'exclusive-create-{label}'
            assert not candidate.exists()
            open_failure = None
            with injector(*injector_args):
                actual_exploding_mode = path_failure(
                    'open', exploding_receiver, **invalid_mode)
                assert actual_exploding_mode == expected_exploding_mode, (
                    'open exposed the injector path-protocol probe',
                    label, actual_exploding_mode, expected_exploding_mode)
                actual_mode = path_failure(
                    'open', refusing_receiver, **invalid_mode)
                assert actual_mode == expected_mode, (
                    'open named receiver before mode validation',
                    label, actual_mode, expected_mode)
                actual_bytes = path_failure(
                    'open', bytes_receiver, mode='x')
                assert actual_bytes == expected_bytes, (
                    'bytes receiver refusal changed',
                    label, actual_bytes, expected_bytes)
                try:
                    with candidate.open(mode='x', encoding='utf-8') as opened:
                        opened.write('caller-owned content')
                except OSError as caught:
                    open_failure = type(caught)
                for case, args, kwargs, expected in rejected_calls:
                    actual = _path_open_failure(queued, *args, **kwargs)
                    assert queued.exists(), f'{label} removed path for {case}'
                    after = queued.stat()
                    assert actual == expected, ('target failure changed',
                                                label, case, actual, expected)
                    assert after == before, ('target path changed',
                                             label, case, after, before)
                injected = open_outcome(
                    Path.open, queued, encoding='utf-8')
            assert injected == expected_injected, (
                'injected outcome differed from fresh run', label,
                injected, expected_injected)
            assert injected[1][0] is injected_type, (
                'injected exception type changed', label,
                injected[1][0], injected_type)
            assert open_failure is None, (label, open_failure)
            assert candidate.read_text('utf-8') == 'caller-owned content'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_injectors_'))
