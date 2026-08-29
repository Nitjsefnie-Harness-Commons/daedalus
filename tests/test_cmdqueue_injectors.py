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


def test_vanish_fault_stays_armed_until_open_observes_disappearance(tmp):
    _queue, queued = _queued_file(tmp)
    immediate_faults = {'r', 'rt', 'rb', 'r+'}

    def open_error(mode):
        try:
            with queued.open(mode=mode):
                pass
        except OSError as caught:
            return type(caught)
        return None

    modes = ('r', 'rt', 'rb', 'r+', 'w', 'a', 'x', 'x+', 'w+', 'a+')
    for mode in modes:
        queued.write_text('seed', encoding='utf-8')
        with _virtual_cmdqueue_clock() as (clock, events, _origin):
            with _vanish_during_read(queued, clock):
                first_error = open_error(mode)
                if mode in immediate_faults:
                    assert first_error is FileNotFoundError, (
                        mode, first_error)
                else:
                    native_error = (FileExistsError
                                    if mode.startswith('x') else None)
                    assert first_error is native_error, (mode, first_error)
                    next_error = open_error('r')
                    assert next_error is FileNotFoundError, (
                        mode, next_error)
        assert [kind for kind, _cost in events] == ['read'], (mode, events)


def test_exhausted_refusal_accounts_only_for_completed_operations(tmp):
    _queue, queued = _queued_file(tmp)
    invalid = {'mode': 'rb', 'encoding': 'utf-8'}
    expected = _path_open_failure(queued, **invalid)
    with _virtual_cmdqueue_clock() as (clock, events, _origin):
        with _refuse_path_operation(
                queued, 'open', 1, clock=clock) as calls:
            injected = _path_open_failure(queued, encoding='utf-8')
            assert injected[0] is PermissionError, injected
            assert calls == [1], calls
            assert len(events) == 1, events
            actual = _path_open_failure(queued, **invalid)
            assert actual == expected, (actual, expected)
            assert calls == [1], calls
            assert len(events) == 1, events
            with queued.open(mode='rt', encoding='utf-8') as opened:
                assert opened.readable(), opened
    assert calls == [2], calls
    assert len(events) == 2, events


def test_untargeted_readable_and_nonreadable_creates_match_native(tmp):
    queue, queued = _queued_file(tmp)
    original = Path.open
    underlying_opens = [0]

    def counted(candidate, *args, **kwargs):
        underlying_opens[0] += 1
        return original(candidate, *args, **kwargs)

    def create_outcome(opener, path, mode, content):
        try:
            with opener(path, mode=mode, encoding='utf-8') as opened:
                outcome = (opened.mode, opened.tell(), opened.readable(),
                           opened.writable(), opened.write(content))
        except OSError as caught:
            filename = getattr(caught, 'filename', None)
            return ('raised', type(caught), type(filename), filename)
        return 'returned', outcome

    def file_effect(path):
        if not path.exists():
            return False, None
        with original(path, mode='r', encoding='utf-8') as opened:
            return True, opened.read()

    with _virtual_cmdqueue_clock() as (clock, _events, _origin):
        injectors = (
            ('generic refusal', _refuse_path_operation,
             (queued, 'open', 1)),
            ('first-read', _refuse_first_queue_read, (queue,)),
            ('disappearance', _disappear_on_first_open, (queued,)),
            ('vanish', _vanish_during_read, (queued, clock)))
        for label, injector, injector_args in injectors:
            for mode in ('x', 'x+'):
                content = f'{label}-{mode}'
                native = Path(tmp) / f'native-create-{label}-{mode}'
                injected = Path(tmp) / f'injected-create-{label}-{mode}'
                underlying_opens[0] = 0
                expected = create_outcome(counted, native, mode, content)
                expected_opens = underlying_opens[0]
                expected_effect = file_effect(native)
                underlying_opens[0] = 0
                Path.open = counted
                try:
                    with injector(*injector_args):
                        actual = create_outcome(
                            Path.open, injected, mode, content)
                finally:
                    Path.open = original
                actual_opens = underlying_opens[0]
                actual_effect = file_effect(injected)
                assert actual == expected, (label, mode, actual, expected)
                assert actual_effect == expected_effect, (
                    label, mode, actual_effect, expected_effect)
                assert actual_opens == expected_opens, (
                    label, mode, actual_opens, expected_opens)


def test_bound_and_unbound_receivers_receive_each_one_shot_fault(tmp):
    queue, queued = _queued_file(tmp)
    files = [queued]

    def read_outcome(spelling):
        try:
            if spelling == 'bound':
                opened = files[0].open(mode='rt', encoding='utf-8')
            elif spelling == 'str':
                opened = Path.open(  # pylint: disable=bad-open-mode
                    str(files[0]), mode='r', encoding='utf-8')
            else:
                opened = Path.open(
                    os.fsencode(str(files[0])), mode='r', encoding='utf-8')
            with opened:
                return 'returned', opened.read()
        except OSError as caught:
            return 'raised', type(caught)

    with _virtual_cmdqueue_clock() as (clock, _events, _origin):
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
            for spelling in ('bound', 'str', 'bytes'):
                files[0].write_text('readable content', encoding='utf-8')
                native = read_outcome(spelling)
                assert native == ('returned', 'readable content'), (
                    label, spelling, native)
                with injector(*injector_args) as state:
                    first = read_outcome(spelling)
                    assert first == ('raised', injected_type), (
                        label, spelling, first)
                    if state is not None:
                        assert state == [1], (label, spelling, state)
                    if label == 'vanish':
                        assert not files[0].exists(), (
                            label, spelling, files[0])
                        files[0].write_text(
                            'readable content', encoding='utf-8')
                    second = read_outcome(spelling)
                    assert second == ('returned', 'readable content'), (
                        label, spelling, second)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_injectors_'))
