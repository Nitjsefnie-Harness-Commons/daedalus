"""Shared JSON and projected check-run fixtures."""
import json


def _encode(value):
    return value if isinstance(value, str) else json.dumps(value)


def _check(check_id, status='completed', external_id=None,
           conclusion='success', summary=None):
    return {'id': check_id, 'status': status, 'external_id': external_id,
            'conclusion': conclusion if status == 'completed' else None,
            'summary': summary}
