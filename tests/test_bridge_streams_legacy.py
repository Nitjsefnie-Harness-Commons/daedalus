#!/usr/bin/env python3
"""Legacy command files delivered through the SSE stream."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import (  # noqa: E402
    BRIDGE_ENV, TOK, _patch_env, _wait_for_delivery_health, frame_reader,
    framer, next_stream_data, prove_scan, put_command, stub_stream,
    stream_response)

# The fault a redelivery needs: the drain removes a command file it has
# delivered, and a removal that fails is swallowed — the file stays and the
# next scan delivers it again. Injected into the bridge child rather than
# waited for, because whether a real unlink takes is the platform's business
# and a test that depends on it is a test that only passes on some hosts.
_REFUSES_LEGACY_UNLINK = f'''\
import pathlib

_real_unlink = pathlib.Path.unlink


def _refusing(self, *args, **kwargs):
    if self.name == f'{TOK}.json':
        raise PermissionError('planted: the removal failed')
    return _real_unlink(self, *args, **kwargs)


pathlib.Path.unlink = _refusing
'''


def _frame(command_id, did):
    """One SSE data frame as `write_frame` writes it."""
    return ('data: ' + json.dumps(
        {'id': command_id, 'code': '1', '_did': did}) + '\n\n').encode('utf-8')


def _refuses_legacy_unlink(tmp):
    """A `sitecustomize` fault dir whose child bridge cannot unlink the
    token's broadcast legacy file."""
    fault_dir = Path(tmp) / 'fault'
    fault_dir.mkdir(exist_ok=True)
    (fault_dir / 'sitecustomize.py').write_text(
        _REFUSES_LEGACY_UNLINK, encoding='utf-8')
    return _patch_env(str(fault_dir))


def test_stream_survives_a_surrogate_id_in_a_legacy_command_file(tmp):
    """The same lone surrogate in a legacy raw-write file must not kill the
    stream."""
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, docroot):
        conn, response = stream_response(base, TOK, tab='extension')
        frame = framer(response, served)
        try:
            assert response.status == 200, response.status
            legacy = Path(docroot) / 'commands' / f'{TOK}.json'
            legacy.write_bytes(b'{"id":"\\ud800","code":"1"}')
            first = frame('the legacy file with a surrogate id')
            assert first.get('code') == '1', first
            status, _ = put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            second = frame('the command enqueued afterwards')
            assert second.get('id') == 'after', second
        finally:
            response.close()
            conn.close()
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_legacy_publication_never_deletes_an_in_progress_write(tmp):
    """Visible partial files survive, while sibling temp names wait for
    rename."""
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, docroot):
        commands = Path(docroot) / 'commands'
        legacy = commands / f'{TOK}.json'
        writer = open(legacy, 'w', encoding='utf-8')
        conn = response = None
        try:
            writer.write('{"id":"held-open"')
            writer.flush()
            os.fsync(writer.fileno())
            conn, response = stream_response(base, TOK, tab='extension')
            assert response.status == 200, response.status
            frame = frame_reader(response, served)
            prove_scan(base, frame, 'first', TOK)
            prove_scan(base, frame, 'second', TOK)
            assert legacy.exists(), (
                'the reader unlinked a visible file while its writer was open')
            writer.write(',"code":"first"}')
            writer.flush()
            os.fsync(writer.fileno())
            writer.close()
            delivered = frame('the completed legacy file')
            assert delivered.get('id') == 'held-open', delivered

            in_progress = commands / f'.{TOK}.json.tmp'
            in_progress.write_text(
                '{"id":"atomic","code":"second"}', encoding='utf-8')
            prove_scan(base, frame, 'third', TOK)
            prove_scan(base, frame, 'fourth', TOK)
            assert in_progress.exists(), (
                'the reader deleted a sibling temp file')
            os.replace(in_progress, legacy)
            renamed = frame('the renamed legacy file')
            assert renamed.get('id') == 'atomic', renamed
        finally:
            if not writer.closed:
                writer.close()
            if response is not None:
                response.close()
            if conn is not None:
                conn.close()


def test_the_reader_skips_a_delivery_it_has_already_seen(tmp):
    """The reader's own rule, on every platform and with no bridge.

    A frame whose `_did` has already been handed back is a repeat and is
    stepped over; a frame carrying an id the reader has not seen is
    delivered, whatever its payload says. That is the whole contract, and it
    is the rule the extension's own ledger applies, so it is pinned here
    where no filesystem, no unlink semantics and no platform's clock can
    change the answer. The end-to-end control below covers the bridge.
    """
    del tmp
    frames = [
        _frame('kept', 'legacy-1-0'),
        _frame('kept', 'legacy-1-0'),      # the repeat: skipped
        _frame('after', 'legacy-1-1'),     # a new delivery: handed back
        _frame('kept', 'legacy-1-0'),      # a repeat of the first: skipped
        _frame('other', 'legacy-1-2'),     # a new delivery: handed back
        _frame('after', 'legacy-1-1'),     # a repeat of the third: skipped
    ]
    read = frame_reader(stub_stream(frames), [])

    first = read('the first delivery')
    second = read('the delivery past one repeat')
    third = read('the delivery past two repeats')

    assert [f['id'] for f in (first, second, third)] == [
        'kept', 'after', 'other'], (first, second, third)
    assert [f['_did'] for f in (first, second, third)] == [
        'legacy-1-0', 'legacy-1-1', 'legacy-1-2'], (first, second, third)


def test_the_reader_delivers_a_frame_the_extension_would_also_run(tmp):
    """A redelivery carrying a new id is delivered, not skipped.

    The bridge derives a legacy delivery id from the object's identity,
    which moves for one file over time on macOS (#1411), so the same
    redelivery carries a different `_did` there than it does on Linux or
    Windows. The reader's answer to that is the extension's answer: an id
    the ledger has not recorded is a command that has not run, so it is
    delivered. Skipping it would be the reader guessing, and guessing here
    is the failure that loses a command.
    """
    del tmp
    frames = [
        _frame('kept', 'legacy-1-0'),
        _frame('kept', 'legacy-1-1'),   # the same file, a moved identity
        _frame('after', 'legacy-1-2'),
    ]
    read = frame_reader(stub_stream(frames), [])

    first = read('the first copy')
    second = read('the copy after the identity moved')
    third = read('the command after both')

    assert [f['id'] for f in (first, second, third)] == [
        'kept', 'kept', 'after'], (first, second, third)
    assert [f['_did'] for f in (first, second)] == [
        'legacy-1-0', 'legacy-1-1'], (first, second)


def test_a_legacy_file_the_bridge_cannot_remove_keeps_being_delivered(tmp):
    """The stuck file is delivered again and again, and the reader keeps up.

    The fault makes the removal fail, so the drain redelivers on every scan
    — that is the at-least-once outcome, and it is certain here rather than
    a thing that depends on which hosts take an unlink.

    The redelivery is asserted as its place on the wire rather than as a
    count read at a moment. One scan of the extension stream drains the
    broadcast queue and then the broadcast legacy file, so the frame
    carrying the last command enqueued is followed by that same scan's copy
    of the stuck file, however many scans have run by the time it is read.
    A count was what this test used to assert, and it read correct code as
    a defect on a loaded cell: one repeat had landed when the count was
    taken.

    What this test does NOT claim is that the redelivery carries the id the
    first copy carried: Linux and all four Windows interpreters hand back
    one id for one file, and macOS does not, because the identity the id is
    built from moves over time there (#1411). So the repeat is read raw,
    past `frame_reader`'s ledger, which would skip it on two of the three
    platforms. The reader's rule is pinned on every platform by the two
    controls above, and the stability of the id over a short interval is
    pinned by `test_stream_service_legacy_ids`, which drains one object
    twice and is green on every leg.
    """
    env = _refuses_legacy_unlink(tmp)
    served = []
    with _util.bridge(tmp, output=served, env=env) as (base, docroot):
        legacy = Path(docroot) / 'commands' / f'{TOK}.json'
        conn, response = stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, response.status
            read = frame_reader(response, served)
            prove_scan(base, read, 'first', TOK)
            legacy.write_text('{"id":"kept","code":"1"}', encoding='utf-8')
            delivered = read('the legacy command')
            assert delivered.get('id') == 'kept', delivered
            status, _ = put_command(
                base, {'token': TOK, 'id': 'last', 'code': '2'})
            assert status == 200, status
            raw = framer(response, served)
            while True:
                wanted = raw('the last command enqueued')
                if wanted.get('id') == 'last':
                    break
            again = raw('the redelivery after the last command')
            assert again.get('id') == 'kept', (
                'the scan that delivered the last command did not redeliver '
                f'the file it could not remove: {again}')
            assert legacy.exists(), 'the file was removed after all'
        finally:
            response.close()
            conn.close()


def test_legacy_delivery_updates_the_health_clock(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        conn, response = stream_response(base, TOK, tab='extension')
        try:
            status, health = _util.get_json(base + '/health')
            assert status == 200, (status, health)
            assert health['last_delivery_s_ago'] is None, health

            legacy = Path(docroot) / 'commands' / f'{TOK}.json'
            legacy.write_text(
                '{"id":"legacy-clock","code":"1"}', encoding='utf-8')
            delivered = next_stream_data(response)
            assert delivered['id'] == 'legacy-clock', delivered

            _wait_for_delivery_health(base)
        finally:
            response.close()
            conn.close()


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='bridgelegacystreams_')


if __name__ == '__main__':
    raise SystemExit(main())
