#!/usr/bin/env python3
"""The chrome-calling handlers in extension/worker/tabs.js.

Each test asserts the posted postResult payload (the handler's answer) and
the chrome calls it made. A handler that returned a right answer through the
wrong calls, or made the right calls but posted the wrong answer, fails.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _tabs_harness import apis, command, run_tabs  # noqa: E402

CSS = 'body{color:red}'

CREATE = 'tabs.create'
QUERY = 'tabs.query'
UPDATE = 'tabs.update'
RELOAD = 'tabs.reload'
WINDOW = 'windows.update'
INSERT = 'scripting.insertCSS'
REMOVE = 'scripting.removeCSS'


def _posted(outcome):
    assert outcome['outcomes'] == [{'settled': 'resolved'}], outcome
    assert len(outcome['posted']) == 1, outcome
    return outcome['posted'][0]


def _result(outcome):
    posted = _posted(outcome)
    assert posted['error'] is None, posted
    assert posted['tabId'] == 'extension', posted
    return posted['result']


def _error(outcome):
    posted = _posted(outcome)
    assert posted['result'] is None, posted
    assert posted['tabId'] == 'extension', posted
    return posted['error']


def _timed_result(outcome):
    """The answer with create_ms checked as a clock, then dropped."""
    result = _result(outcome)
    value = result.pop('create_ms')
    assert isinstance(value, int) and value >= 0, result
    return result


# ─── handleOpenTab ───

def test_open_tab_creates_the_url_and_posts_the_new_tab(tmp):
    del tmp
    outcome = run_tabs([
        command(type='open-tab', url='https://a.example.com')])
    assert apis(outcome, CREATE) == [
        [CREATE, [{'url': 'https://a.example.com'}]]], outcome
    assert _timed_result(outcome) == {
        'tabId': 100,
        'url': 'https://a.example.com',
        'windowId': 1,
    }, outcome


def test_open_tab_carries_active_pinned_and_window_options(tmp):
    del tmp
    outcome = run_tabs([command(
        type='open-tab', url='https://a.example.com', active=False,
        pinned=True, windowId='7')])
    assert apis(outcome, CREATE) == [[CREATE, [{
        'url': 'https://a.example.com',
        'active': False,
        'pinned': True,
        'windowId': 7,
    }]]], outcome


def test_open_tab_create_rejection_posts_the_message(tmp):
    del tmp
    outcome = run_tabs(
        [command(type='open-tab', url='https://a.example.com')],
        createReject={'https://a.example.com': 'planned create rejection'})
    assert _error(outcome) == 'planned create rejection', outcome


def test_open_tab_missing_url_is_refused_without_creating(tmp):
    del tmp
    outcome = run_tabs([command(type='open-tab')])
    assert _error(outcome) == 'Missing url', outcome
    assert apis(outcome, CREATE) == [], outcome


# ─── handleOpenTabs ───

def test_open_tabs_creates_every_url_in_order(tmp):
    del tmp
    outcome = run_tabs([command(
        type='open-tabs',
        urls=['https://a.example.com', 'https://b.example.com'])])
    assert apis(outcome, CREATE) == [
        [CREATE, [{'url': 'https://a.example.com'}]],
        [CREATE, [{'url': 'https://b.example.com'}]],
    ], outcome
    assert _timed_result(outcome) == {
        'opened': [
            {'tabId': 100, 'url': 'https://a.example.com', 'windowId': 1},
            {'tabId': 101, 'url': 'https://b.example.com', 'windowId': 1},
        ],
        'errors': [],
    }, outcome


def test_open_tabs_partial_rejection_carries_the_reason(tmp):
    del tmp
    outcome = run_tabs(
        [command(type='open-tabs', urls=['https://a.example.com',
                                         'https://b.example.com'])],
        createReject={'https://b.example.com': 'cannot open b'})
    assert _timed_result(outcome) == {
        'opened': [
            {'tabId': 100, 'url': 'https://a.example.com', 'windowId': 1},
        ],
        'errors': [{'url': 'https://b.example.com',
                    'error': 'cannot open b'}],
    }, outcome


def test_open_tabs_missing_urls_is_refused_without_creating(tmp):
    del tmp
    outcome = run_tabs([command(type='open-tabs')])
    assert _error(outcome) == 'Missing urls', outcome
    assert apis(outcome, CREATE) == [], outcome


def test_open_tabs_empty_urls_is_refused_without_creating(tmp):
    del tmp
    outcome = run_tabs([command(type='open-tabs', urls=[])])
    assert _error(outcome) == 'Missing urls', outcome
    assert apis(outcome, CREATE) == [], outcome


# ─── handleFocusTab ───

def test_focus_tab_activates_the_tab_then_focuses_the_window(tmp):
    del tmp
    outcome = run_tabs([command(type='focus-tab', tabId=5)])
    assert apis(outcome, UPDATE, WINDOW) == [
        [UPDATE, [5, {'active': True}]],
        [WINDOW, [3, {'focused': True}]],
    ], outcome
    assert _result(outcome) == {'tabId': 5, 'windowId': 3}, outcome


def test_focus_tab_missing_tab_id_is_refused_without_updating(tmp):
    del tmp
    outcome = run_tabs([command(type='focus-tab')])
    assert _error(outcome) == 'Missing tabId', outcome
    assert apis(outcome, UPDATE, WINDOW) == [], outcome


# ─── handleNavigate ───

def test_navigate_updates_the_named_tab(tmp):
    del tmp
    outcome = run_tabs([command(
        type='navigate', tabId='5', url='https://new.example.com')])
    assert apis(outcome, UPDATE) == [
        [UPDATE, [5, {'url': 'https://new.example.com'}]]], outcome
    assert _result(outcome) == {
        'tabId': 5, 'url': 'https://new.example.com'}, outcome


def test_navigate_resolves_the_active_tab_when_none_is_named(tmp):
    del tmp
    outcome = run_tabs([command(
        type='navigate', url='https://new.example.com')])
    assert apis(outcome, QUERY) == [
        [QUERY, [{'active': True, 'currentWindow': True}]]], outcome
    assert apis(outcome, UPDATE) == [
        [UPDATE, [7, {'url': 'https://new.example.com'}]]], outcome
    assert _result(outcome) == {
        'tabId': 7, 'url': 'https://new.example.com'}, outcome


def test_navigate_no_active_tab_is_refused_without_updating(tmp):
    del tmp
    outcome = run_tabs(
        [command(type='navigate', url='https://new.example.com')],
        activeTabs=[])
    assert _error(outcome) == 'No active tab', outcome
    assert apis(outcome, UPDATE) == [], outcome


def test_navigate_missing_url_is_refused_without_querying(tmp):
    del tmp
    outcome = run_tabs([command(type='navigate')])
    assert _error(outcome) == 'Missing url', outcome
    assert apis(outcome, QUERY, UPDATE) == [], outcome


# ─── handleReload ───

def test_reload_passes_bypass_cache_true(tmp):
    del tmp
    outcome = run_tabs([command(
        type='reload', tabId=5, bypassCache=True)])
    assert apis(outcome, RELOAD) == [
        [RELOAD, [5, {'bypassCache': True}]]], outcome
    assert _result(outcome) == {
        'tabId': 5, 'bypassCache': True}, outcome


def test_reload_defaults_bypass_cache_to_false(tmp):
    del tmp
    outcome = run_tabs([command(type='reload', tabId=5)])
    assert apis(outcome, RELOAD) == [
        [RELOAD, [5, {'bypassCache': False}]]], outcome
    assert _result(outcome) == {
        'tabId': 5, 'bypassCache': False}, outcome


def test_reload_uses_the_active_tab_when_none_is_named(tmp):
    del tmp
    outcome = run_tabs([command(type='reload', bypassCache=True)])
    assert apis(outcome, RELOAD) == [
        [RELOAD, [7, {'bypassCache': True}]]], outcome
    assert _result(outcome) == {
        'tabId': 7, 'bypassCache': True}, outcome


def test_reload_no_active_tab_is_refused_without_reloading(tmp):
    del tmp
    outcome = run_tabs([command(type='reload')], activeTabs=[])
    assert _error(outcome) == 'No active tab', outcome
    assert apis(outcome, RELOAD) == [], outcome


# ─── handleInjectCss ───

def test_inject_css_reports_the_length_and_carries_all_frames(tmp):
    del tmp
    outcome = run_tabs([command(
        type='inject-css', tabId=5, css=CSS, allFrames=True)])
    assert apis(outcome, INSERT) == [[INSERT, [{
        'target': {'tabId': 5, 'allFrames': True},
        'css': CSS,
    }]]], outcome
    assert _result(outcome) == {
        'tabId': 5, 'injected': len(CSS)}, outcome


def test_inject_css_defaults_all_frames_to_false(tmp):
    del tmp
    outcome = run_tabs([command(
        type='inject-css', tabId=5, css=CSS)])
    assert apis(outcome, INSERT) == [[INSERT, [{
        'target': {'tabId': 5, 'allFrames': False},
        'css': CSS,
    }]]], outcome


def test_inject_css_resolves_the_active_tab_when_none_is_named(tmp):
    del tmp
    outcome = run_tabs([command(type='inject-css', css=CSS)])
    assert apis(outcome, INSERT) == [[INSERT, [{
        'target': {'tabId': 7, 'allFrames': False},
        'css': CSS,
    }]]], outcome
    assert _result(outcome) == {'tabId': 7, 'injected': len(CSS)}, outcome


def test_inject_css_missing_css_is_refused_without_inserting(tmp):
    del tmp
    outcome = run_tabs([command(type='inject-css', tabId=5)])
    assert _error(outcome) == 'Missing css', outcome
    assert apis(outcome, INSERT) == [], outcome


def test_inject_css_no_active_tab_is_refused_without_inserting(tmp):
    del tmp
    outcome = run_tabs(
        [command(type='inject-css', css=CSS)], activeTabs=[])
    assert _error(outcome) == 'No active tab', outcome
    assert apis(outcome, INSERT) == [], outcome


# ─── handleRemoveCss ───

def test_remove_css_reports_the_length_and_carries_all_frames(tmp):
    del tmp
    outcome = run_tabs([command(
        type='remove-css', tabId=5, css=CSS, allFrames=True)])
    assert apis(outcome, REMOVE) == [[REMOVE, [{
        'target': {'tabId': 5, 'allFrames': True},
        'css': CSS,
    }]]], outcome
    assert _result(outcome) == {
        'tabId': 5, 'removed': len(CSS)}, outcome


def test_remove_css_defaults_all_frames_to_false(tmp):
    del tmp
    outcome = run_tabs([command(
        type='remove-css', tabId=5, css=CSS)])
    assert apis(outcome, REMOVE) == [[REMOVE, [{
        'target': {'tabId': 5, 'allFrames': False},
        'css': CSS,
    }]]], outcome


def test_remove_css_resolves_the_active_tab_when_none_is_named(tmp):
    del tmp
    outcome = run_tabs([command(type='remove-css', css=CSS)])
    assert apis(outcome, REMOVE) == [[REMOVE, [{
        'target': {'tabId': 7, 'allFrames': False},
        'css': CSS,
    }]]], outcome
    assert _result(outcome) == {'tabId': 7, 'removed': len(CSS)}, outcome


def test_remove_css_missing_css_is_refused_without_removing(tmp):
    del tmp
    outcome = run_tabs([command(type='remove-css', tabId=5)])
    assert _error(outcome) == 'Missing css', outcome
    assert apis(outcome, REMOVE) == [], outcome


def test_remove_css_no_active_tab_is_refused_without_removing(tmp):
    del tmp
    outcome = run_tabs(
        [command(type='remove-css', css=CSS)], activeTabs=[])
    assert _error(outcome) == 'No active tab', outcome
    assert apis(outcome, REMOVE) == [], outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='tabschrome_')


if __name__ == '__main__':
    raise SystemExit(main())
