#!/usr/bin/env python3
"""Legacy command files delivered through the SSE stream."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import (  # noqa: E402
    BRIDGE_ENV, TOK, _patch_env, _wait_for_delivery_health, framer,
    frame_reader, next_stream_data, prove_scan, put_command,
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
            delivered = frame('the completed legacy file', timeout=5)
            assert delivered.get('id') == 'held-open', delivered

            in_progress = commands / f'.{TOK}.json.tmp'
            in_progress.write_text(
                '{"id":"atomic","code":"second"}', encoding='utf-8')
            prove_scan(base, frame, 'third', TOK)
            prove_scan(base, frame, 'fourth', TOK)
            assert in_progress.exists(), (
                'the reader deleted a sibling temp file')
            os.replace(in_progress, legacy)
            renamed = frame('the renamed legacy file', timeout=5)
            assert renamed.get('id') == 'atomic', renamed
        finally:
            if not writer.closed:
                writer.close()
            if response is not None:
                response.close()
            if conn is not None:
                conn.close()


def test_a_legacy_file_the_bridge_cannot_remove_is_delivered_once(tmp):
    """A redelivery is a repeat, and the reader steps over it.

    The drain is at-least-once and says so: a file it cannot remove stays,
    and the next scan delivers it again. Both copies carry the same `_did`,
    which is what the extension's ledger skips on and what this reader skips
    on. The fault makes the removal fail, so a redelivery is certain here
    rather than a thing that happens to occur on whichever hosts take the
    unlink — without it this test would pass with a reader that skipped
    nothing, because there would be nothing to skip.
    """
    env = _refuses_legacy_unlink(tmp)
    served = []
    with _util.bridge(tmp, output=served, env=env) as (base, docroot):
        legacy = Path(docroot) / 'commands' / f'{TOK}.json'
        conn, response = stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, response.status
            frame = frame_reader(response, served)
            prove_scan(base, frame, 'first', TOK)
            legacy.write_text('{"id":"kept","code":"1"}', encoding='utf-8')
            delivered = frame('the legacy command', timeout=5)
            assert delivered.get('id') == 'kept', delivered
            # The drain writes the frame before the removal it could not do,
            # so the file stays and every scan delivers it again under the
            # same `_did` — the property the in-process control pins
            # component-wise. What is new here is the end of it: the reader
            # steps over the repeats and still hands back what comes after.
            assert legacy.exists(), 'the file was removed after all'
            prove_scan(base, frame, 'second', TOK)
            assert legacy.exists(), 'the file was removed after all'
            status, _ = put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            after = frame('the command past the repeats', timeout=5)
            assert after.get('id') == 'after', after
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
