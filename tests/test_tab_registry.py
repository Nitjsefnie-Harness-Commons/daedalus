#!/usr/bin/env python3
"""The tab registry and the four routes that operate it.

`daedalus_bridge/tab_registry.py` owns the registry dictionary and its lock,
and exposes `/tabs`, `/register`, `/sync-tabs` and `/unregister` as functions
returning `(status, payload)`. Each test loads the module under its own name
so it gets a registry nothing else has written to, and drives the functions
directly rather than through a bridge child.

The command directory is a parameter rather than a module global, so every
route that publishes a dashboard event is pinned against the directory it was
handed: a route that published somewhere else would still answer 200.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402


def _load(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'tab_registry.py', name)


def _events(cmd_dir, token):
    """Every dashboard event the routes published under `cmd_dir`."""
    queue = Path(cmd_dir) / f'{token}_dashboard'
    if not queue.is_dir():
        return []
    return [json.loads(path.read_text(encoding='utf-8'))
            for path in sorted(queue.iterdir())]


def test_normalized_tab_id_accepts_strings_and_integers(_tmp):
    reg = _load('fixture_tab_registry_normalized')
    assert reg.normalized_tab_id('7') == '7'
    assert reg.normalized_tab_id(7) == '7'
    assert reg.normalized_tab_id('') == ''
    assert reg.normalized_tab_id(True) is None
    assert reg.normalized_tab_id(7.5) is None
    assert reg.normalized_tab_id(None) is None
    assert reg.normalized_tab_id({'tabId': 7}) is None


def test_refresh_updates_only_a_known_tab(tmp):
    reg = _load('fixture_tab_registry_refresh')
    assert reg.refresh(
        tmp, 'tok', {'tabId': '7', 'url': 'u', 'title': 't'}) == (
            200, {'ok': True, 'updated': False})
    reg.replace(tmp, 'tok', {'tabs': [{'tabId': 7, 'url': 'a',
                                       'title': 'b'}]})
    assert reg.refresh(
        tmp, 'tok', {'tabId': '7', 'url': 'u', 'title': 't'}) == (
            200, {'ok': True, 'updated': True})
    status, tabs = reg.list_tabs('tok')
    assert status == 200 and tabs[0]['url'] == 'u', tabs


def test_refresh_refuses_a_missing_or_unusable_tab_id(tmp):
    reg = _load('fixture_tab_registry_refresh_bad_id')
    assert reg.refresh(tmp, 'tok', {}) == (
        400, {'error': 'missing tabId'})
    assert reg.refresh(tmp, 'tok', {'tabId': ''}) == (
        400, {'error': 'missing tabId'})
    assert reg.refresh(tmp, 'tok', {'tabId': []}) == (
        400, {'error': 'invalid tabId'})
    assert _events(tmp, 'tok') == []


def test_refresh_publishes_its_update_event_into_cmd_dir(tmp):
    reg = _load('fixture_tab_registry_refresh_event')
    reg.replace(tmp, 'tok', {'tabs': [{'tabId': '7', 'url': 'a',
                                       'title': 'b'}]})
    reg.refresh(tmp, 'tok', {'tabId': '7', 'url': 'u', 'title': 't'})
    updates = [e for e in _events(tmp, 'tok') if e['type'] == 'tab-updated']
    assert len(updates) == 1, _events(tmp, 'tok')
    assert updates[0]['tabId'] == '7'
    assert updates[0]['url'] == 'u' and updates[0]['title'] == 't'


def test_refresh_of_an_unknown_tab_publishes_nothing(tmp):
    reg = _load('fixture_tab_registry_refresh_silent')
    reg.refresh(tmp, 'tok', {'tabId': '7', 'url': 'u', 'title': 't'})
    assert _events(tmp, 'tok') == []


def test_replace_publishes_one_synced_event_into_cmd_dir(tmp):
    reg = _load('fixture_tab_registry_replace_event')
    assert reg.replace(tmp, 'tok', {'tabs': [{'tabId': '7'},
                                             {'tabId': '8'}]}) == (
        200, {'ok': True, 'count': 2})
    events = _events(tmp, 'tok')
    assert len(events) == 1, events
    assert events[0]['type'] == 'tabs-synced', events[0]
    assert events[0]['count'] == 2, events[0]


def test_replace_refuses_a_tab_list_that_is_not_a_list_of_objects(tmp):
    reg = _load('fixture_tab_registry_replace_bad')
    assert reg.replace(tmp, 'tok', {'tabs': 'x'}) == (
        400, {'error': 'invalid tabs'})
    assert reg.replace(tmp, 'tok', {'tabs': [1]}) == (
        400, {'error': 'invalid tabs'})
    assert reg.replace(tmp, 'tok', {'tabs': [{'tabId': 1.5}]}) == (
        400, {'error': 'invalid tabs'})
    assert _events(tmp, 'tok') == []
    assert reg.counts() == (0, 0)


def test_replace_drops_a_blank_tab_id_and_replaces_the_registry(tmp):
    reg = _load('fixture_tab_registry_replace_blank')
    reg.replace(tmp, 'tok', {'tabs': [{'tabId': '7', 'url': 'a',
                                       'title': 'b'}]})
    assert reg.replace(tmp, 'tok', {'tabs': [{'tabId': 9},
                                             {'tabId': ''}]}) == (
        200, {'ok': True, 'count': 1})
    status, tabs = reg.list_tabs('tok')
    assert status == 200 and [t['tabId'] for t in tabs] == ['9'], tabs


def test_remove_reports_whether_anything_was_removed(tmp):
    reg = _load('fixture_tab_registry_remove')
    assert reg.remove(tmp, 'tok', {}) == (400, {'error': 'missing tabId'})
    assert reg.remove(tmp, 'tok', {'tabId': '7'}) == (
        200, {'ok': True, 'removed': False})
    reg.replace(tmp, 'tok', {'tabs': [{'tabId': 7}]})
    assert reg.remove(tmp, 'tok', {'tabId': 7}) == (
        200, {'ok': True, 'removed': True})
    assert reg.list_tabs('tok') == (200, [])


def test_remove_publishes_its_unregistered_event_into_cmd_dir(tmp):
    reg = _load('fixture_tab_registry_remove_event')
    reg.remove(tmp, 'tok', {'tabId': 7})
    events = [e for e in _events(tmp, 'tok')
              if e['type'] == 'tab-unregistered']
    assert len(events) == 1, _events(tmp, 'tok')
    assert events[0]['tabId'] == '7', events[0]


def test_list_tabs_reports_each_tab_with_its_age(tmp):
    reg = _load('fixture_tab_registry_list')
    assert reg.list_tabs('nobody') == (200, [])
    reg.replace(tmp, 'tok', {'tabs': [{'tabId': '7', 'url': 'u',
                                       'title': 't'}]})
    reg._registry['tok']['7']['ts'] = 1000.0
    assert reg.list_tabs('tok', now=1042.0) == (
        200, [{'tabId': '7', 'url': 'u', 'title': 't', 'age': 42}])


def test_counts_reflects_every_token(tmp):
    reg = _load('fixture_tab_registry_counts')
    assert reg.counts() == (0, 0)
    reg.replace(tmp, 'a', {'tabs': [{'tabId': '1'}, {'tabId': '2'}]})
    reg.replace(tmp, 'b', {'tabs': [{'tabId': '3'}]})
    assert reg.counts() == (2, 3)
    reg.remove(tmp, 'a', {'tabId': '1'})
    assert reg.counts() == (2, 2)


def test_the_module_imports_without_daedalus_configuration(_tmp):
    """A route module never imports config, so it needs no environment."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    done = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.tab_registry'],
        env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='tabregistry_')


if __name__ == '__main__':
    raise SystemExit(main())
