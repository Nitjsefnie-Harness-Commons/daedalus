#!/usr/bin/env python3
"""PUT /command admission — what a retried command id enqueues.

A sibling of tests/test_command_queue.py, which is at its size ceiling. The
same fixture style: a real bridge() no extension is draining, and the queue
directory is read as evidence of what admission published.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import BRIDGE_ENV, TOK, put_command  # noqa: E402


def test_a_retried_put_answers_the_original_delivery_as_duplicate(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, first_raw = put_command(
            base, {'token': TOK, 'id': 'same-id', 'code': '1'})
        assert status == 200, (status, first_raw)
        first = json.loads(first_raw)
        status, second_raw = put_command(
            base, {'token': TOK, 'id': 'same-id', 'code': '1'})
        assert status == 200, (status, second_raw)
        second = json.loads(second_raw)
        assert 'duplicate' not in first, first
        assert second.get('duplicate') is True, second
        assert second['did'] == first['did'], (first, second)
        published = sorted(
            (Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(published) == 1, published
        assert json.loads(published[0].read_text(encoding='utf-8')) == {
            'id': 'same-id', 'code': '1', '_did': first['did']}


def test_a_put_with_the_same_id_but_a_new_payload_enqueues_fresh(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, first_raw = put_command(
            base, {'token': TOK, 'id': 'same-id', 'code': '1'})
        assert status == 200, (status, first_raw)
        status, second_raw = put_command(
            base, {'token': TOK, 'id': 'same-id', 'code': '2'})
        assert status == 200, (status, second_raw)
        first, second = json.loads(first_raw), json.loads(second_raw)
        assert 'duplicate' not in second, second
        assert second['did'] != first['did'], (first, second)
        published = sorted(
            (Path(docroot) / 'commands' / TOK).glob('*.json'))
        assert len(published) == 2, published
        payloads = [json.loads(path.read_text(encoding='utf-8'))
                    for path in published]
        assert [p['code'] for p in payloads] == ['1', '2'], payloads


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
