#!/usr/bin/env python3
"""Legacy command files delivered through the SSE stream."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import (  # noqa: E402
    BRIDGE_ENV, TOK, framer, next_stream_data, put_command, stream_response)
from test_bridge_streams import _wait_for_delivery_health  # noqa: E402


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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
            time.sleep(1.25)
            assert legacy.exists(), (
                'the reader unlinked a visible file while its writer was open')
            writer.write(',"code":"first"}')
            writer.flush()
            os.fsync(writer.fileno())
            writer.close()
            frame = next_stream_data(response, timeout=5)
            assert frame.get('id') == 'held-open', frame

            in_progress = commands / f'.{TOK}.json.tmp'
            in_progress.write_text(
                '{"id":"atomic","code":"second"}', encoding='utf-8')
            time.sleep(1.25)
            assert in_progress.exists(), (
                'the reader deleted a sibling temp file')
            os.replace(in_progress, legacy)
            frame = next_stream_data(response, timeout=5)
            assert frame.get('id') == 'atomic', frame
        finally:
            if not writer.closed:
                writer.close()
            if response is not None:
                response.close()
            if conn is not None:
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
