#!/usr/bin/env python3
"""The tab registry, and the routing field that is not a browser tab id.

`POST /sync-tabs` replaces a token's registry and `POST /register` only
refreshes what is already in it, which is the distinction these tests pin —
along with the one between `tab`, the server-side queue name, and `tabId`,
the browser's own identifier.
"""
import http.client
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import TOK, put_command  # noqa: E402


def test_sync_tabs_dashboard_event_lands_in_command_tree(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(
            base + '/sync-tabs', {'token': TOK, 'tabs': []})
        assert status == 200 and body == {'ok': True, 'count': 0}, (
            status, body)

        queue = Path(docroot) / 'commands' / f'{TOK}_dashboard'
        events = list(queue.glob('*.json'))
        assert len(events) == 1, events
        event = json.loads(events[0].read_text(encoding='utf-8'))
        assert event['type'] == 'tabs-synced' and event['count'] == 0, event
        wrong_root = Path(docroot) / 'results' / f'{TOK}_dashboard'
        assert not wrong_root.exists(), wrong_root


def test_tabs_registry(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        tabs = [{'tabId': '11', 'url': 'https://example.com/a', 'title': 'A'},
                {'tabId': '22', 'url': 'https://example.com/b', 'title': 'B'}]
        status, body = _util.post_json(base + '/sync-tabs',
                                       {'token': TOK, 'tabs': tabs})
        assert status == 200 and body == {'ok': True, 'count': 2}, (status, body)

        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert status == 200 and len(body) == 2, body
        by_id = {t['tabId']: t for t in body}
        assert by_id['11']['url'] == 'https://example.com/a'
        assert by_id['22']['title'] == 'B'
        assert all(isinstance(t['age'], (int, float)) for t in body)

        # /register updates an existing tab...
        status, body = _util.post_json(
            base + '/register',
            {'token': TOK, 'tabId': '11', 'url': 'https://example.com/c',
             'title': 'C'})
        assert status == 200 and body == {'ok': True, 'updated': True}, (
            status, body)
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        by_id = {t['tabId']: t for t in body}
        assert by_id['11']['title'] == 'C' and len(body) == 2

        # ...but never creates one (sync-tabs is authoritative), and says so
        # rather than reporting the no-op as a refresh.
        status, body = _util.post_json(
            base + '/register',
            {'token': TOK, 'tabId': '33', 'url': 'https://example.com/d'})
        assert status == 200 and body == {'ok': True, 'updated': False}, (
            status, body)
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert len(body) == 2, body

        status, body = _util.post_json(base + '/register', {'token': TOK})
        assert status == 400 and body['error'] == 'missing tabId', (status, body)

        status, body = _util.post_json(base + '/unregister',
                                       {'token': TOK, 'tabId': '11'})
        assert status == 200 and body == {'ok': True, 'removed': True}, body
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert [t['tabId'] for t in body] == ['22'], body

        # sync-tabs replaces, it does not merge.
        status, _ = _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': []})
        assert status == 200, status
        status, body = _util.get_json(base + f'/tabs?token={TOK}')
        assert body == [], body


def test_register_says_whether_it_actually_updated_a_tab(tmp):
    """Update-only means a tab the registry never had is a no-op, and says so.

    The route answered {'ok': True} either way, so a client whose tab had
    fallen out of the registry was told its entry had been refreshed. Nothing
    in the answer let it notice it should re-sync.
    """
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/register', {
            'token': TOK, 'tabId': '404', 'url': 'http://example.com/a',
            'title': 'a'})
        assert status == 200, (status, body)
        assert body == {'ok': True, 'updated': False}, body

        status, _ = _util.post_json(base + '/sync-tabs', {
            'token': TOK, 'tabs': [{'tabId': '404',
                                    'url': 'http://example.com/a',
                                    'title': 'a'}]})
        assert status == 200, status
        status, body = _util.post_json(base + '/register', {
            'token': TOK, 'tabId': '404', 'url': 'http://example.com/b',
            'title': 'b'})
        assert status == 200, (status, body)
        assert body == {'ok': True, 'updated': True}, body
        status, tabs = _util.get_json(base + f'/tabs?token={TOK}')
        assert [t['url'] for t in tabs] == ['http://example.com/b'], tabs


def test_register_refuses_unhashable_tab_ids_and_stays_healthy(tmp):
    """JSON arrays and objects cannot reach the registry's dictionary lookup."""
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/sync-tabs', {
            'token': TOK,
            'tabs': [{'tabId': 'kept', 'url': 'about:blank'}],
        })
        assert status == 200 and body['count'] == 1, (status, body)

        replies = []
        for tab_id in ([1], {'x': 1}):
            try:
                status, body = _util.post_json(
                    base + '/register', {'token': TOK, 'tabId': tab_id})
                error = body.get('error')
            except http.client.RemoteDisconnected:
                status, error = 'dropped', None
            health_status, health = _util.get_json(base + '/health')
            tabs_status, stored = _util.get_json(base + f'/tabs?token={TOK}')
            replies.append((tab_id, status, error, stored))
            assert health_status == 200 and health['ok'] is True, (
                tab_id, health_status, health)
            assert tabs_status == 200, (tab_id, tabs_status, stored)

        assert all(status == 400 and error == 'invalid tabId'
                   and [tab['tabId'] for tab in stored] == ['kept']
                   for _tab_id, status, error, stored in replies), replies


def test_sync_tabs_validates_the_list_and_every_member_before_mutation(tmp):
    """Wrong nested shapes receive 400 without clearing the existing registry."""
    wrong_tabs = (None, {}, 1, 'tabs', [1], [None], [[]], ['tab'])
    with _util.bridge(tmp) as (base, _docroot):
        replies = []
        for tabs in wrong_tabs:
            seed = {
                'token': TOK,
                'tabs': [{'tabId': 'kept', 'url': 'about:blank'}],
            }
            status, body = _util.post_json(base + '/sync-tabs', seed)
            assert status == 200 and body['count'] == 1, (status, body)
            try:
                status, body = _util.post_json(
                    base + '/sync-tabs', {'token': TOK, 'tabs': tabs})
                error = body.get('error')
            except http.client.RemoteDisconnected:
                status, error = 'dropped', None
            health_status, health = _util.get_json(base + '/health')
            tabs_status, stored = _util.get_json(base + f'/tabs?token={TOK}')
            replies.append((tabs, status, error, stored))
            assert health_status == 200 and health['ok'] is True, (
                tabs, health_status, health)
            assert tabs_status == 200, (tabs, tabs_status, stored)

        assert all(status == 400 and error == 'invalid tabs'
                   and [tab['tabId'] for tab in stored] == ['kept']
                   for _tabs, status, error, stored in replies), replies


def test_a_browser_target_survives_routing_but_the_routing_fields_do_not(tmp):
    """`tabId` reaches the client; `token` and `tab` never do.

    Screenshot and CDP used to send the browser target as `tab`, the field the
    server strips for routing — so the target was deleted in transit and the
    extension fell back to the active tab. One sender was worse: it wrote the
    target over the routing value, so the command went to a queue nothing
    drains. This pins the separation both ways.
    """
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.request(
            base + '/command', 'PUT',
            body={'token': TOK, 'tab': 'extension', 'id': 'shot',
                  'type': 'screenshot', 'tabId': 42})
        assert status == 200, body
        queued = list((docroot / 'commands' / f'{TOK}_extension').glob('*.json'))
        assert len(queued) == 1, f'expected one queued command, got {queued}'
        cmd = json.loads(queued[0].read_text(encoding='utf-8'))
        assert cmd.get('tabId') == 42, f'the browser target was lost: {cmd}'
        assert 'tab' not in cmd, f'the routing tab leaked into the command: {cmd}'
        assert 'token' not in cmd, f'the token leaked into the command: {cmd}'


def test_poll_legacy_escape_hatch(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert status == 200 and body == {}, (status, body)

        # The documented raw-write escape hatch: a single legacy file.
        legacy = Path(docroot) / 'commands' / f'{TOK}.json'
        legacy.write_text(json.dumps({'id': 'legacy1', 'code': '1'}))
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert status == 200 and body['id'] == 'legacy1', (status, body)
        assert not legacy.exists()  # consumed

        legacy.write_text('{not json', encoding='utf-8')
        try:
            status, body = _util.post_json(base + '/poll', {'token': TOK})
        except http.client.RemoteDisconnected as exc:
            raise AssertionError('a malformed legacy command ended /poll') from exc
        assert status == 200 and body == {}, (status, body)
        assert legacy.exists(), 'the malformed legacy command was deleted'
        legacy.write_text(
            json.dumps({'id': 'legacy-after-partial', 'code': '2'}),
            encoding='utf-8')
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert status == 200 and body['id'] == 'legacy-after-partial', (
            status, body)
        assert not legacy.exists(), 'the complete legacy command was not consumed'

        # A dir-queue command (PUT /command) is NOT visible to legacy /poll.
        status, _ = put_command(base, {'token': TOK, 'id': 'q1', 'code': '2'})
        assert status == 200, status
        status, body = _util.post_json(base + '/poll', {'token': TOK})
        assert body == {}, body


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgetabs_')


if __name__ == '__main__':
    raise SystemExit(main())
