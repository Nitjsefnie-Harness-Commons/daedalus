#!/usr/bin/env python3
"""Where a stored hotfix runs, and when it runs nowhere at all.

Replay answered a content script's request with the tab id and nothing
else, and both `executeScript` calls in the MAIN channel named that tab id
alone. Chrome resolves a tab id to whatever the tab holds at injection
time, so a document that navigated between the request and the injection
received the previous document's fix, a prerendered document's request
injected into the visible one, and a fix stored for one site ran on every
site. The CDP fallback was tab-bound the same way and had no document
binding of its own.

These controls read which DOCUMENT received which fix. The property is
carried by more than one layer — `documentIds` on the MAIN channel, the
result's own `documentId`, the `location.href` gate inside the CDP
evaluation, and the stored pattern beside the URL it is matched against —
so a mutation that removes one layer leaves this suite green, and that
green is the signature of defence in depth rather than a weak control.
`review-references/controls/2026-09-24-a-defence-in-depth-defeats-a-
single-layer-mutation.md` records the procedure this suite follows.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _hotfixharness import run_hotfix_case  # noqa: E402

SITE = 'https://shop.example.com/cart'
ELSEWHERE = 'https://other.example.com/page'
SCOPE = '*://shop.example.com/*'
LOCAL_PAGE = 'file:///Users/op/page.html'
FILE_SCOPE = 'file:///*'
# The fix every control replays: it announces itself to the document that
# received it, so `delivered` answers "which document got it" rather than
# "which call was made".
FIX = "daedalusHits.push('fix1')"


def _errors(outcome):
    return [entry['text'] for entry in outcome['replay']
            if entry['level'] == 'error']


def _logs(outcome):
    return [entry['text'] for entry in outcome['replay']
            if entry['level'] == 'log']


def _delivered(outcome):
    return {doc: hits for doc, hits in outcome['delivered'].items() if hits}


def test_a_fix_reaches_the_document_that_asked_and_only_that_one(tmp):
    """C1: the asking document is a live document the tab is not showing.
    del tmp

    A prerendered document is live in the tab before it is activated, so it
    asks for replay while a different document is the one on screen. A tab
    id cannot tell them apart; the document the request carried can.
    """
    outcome = run_hotfix_case({
        'documents': [SITE, 'https://shop.example.com/home'],
        'current': 1,
        'asker': 0,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The anti-vacuity half: the document on screen has a live recorder and
    # recorded nothing, so an empty array here is a fact and not a broken
    # oracle, and the one that did record proves which array it ran in.
    assert outcome['documentUrls'] == {
        'doc-1': SITE, 'doc-2': 'https://shop.example.com/home'}, outcome
    assert outcome['delivered'].get('doc-2') == [], outcome
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome
    # Both browser calls name the same document as the request did, and
    # that document is not the one the tab was showing.
    assert outcome['current'] == 'doc-2', outcome
    assert outcome['injections'] == [
        {'documentId': 'doc-1', 'world': 'MAIN', 'probe': True},
        {'documentId': 'doc-1', 'world': 'MAIN', 'probe': False},
    ], outcome
    assert not _errors(outcome), outcome


def test_a_fix_does_not_reach_the_document_that_replaced_the_asker(tmp):
    """C2: the asking document navigated before the injection landed.
    del tmp

    The request was true when it was made. A tab-bound target delivers to
    whatever holds the tab at injection time, which is the next document.
    """
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'navigateAt': 'first-script-call',
        'navigateTo': 'https://shop.example.com/next',
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The anti-vacuity half: the document the tab moved to exists and has a
    # live recorder, so a tab-bound target would have been seen here.
    assert outcome['documentUrls'].get('doc-2') == (
        'https://shop.example.com/next'), outcome
    assert outcome['delivered'].get('doc-2') == [], outcome
    assert _delivered(outcome) == {}, outcome
    assert len(_errors(outcome)) == 1, outcome
    assert 'fix1' in _errors(outcome)[0], outcome


def test_a_navigation_between_the_probe_and_the_injection_also_refuses(tmp):
    """C2 again, at the other point the tab can move underneath the replay.
    del tmp

    The probe is the first browser call and the injection the second, so a
    tab-bound channel has a window between them. Binding only the probe
    would still deliver here.
    """
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'navigateAt': 'after-probe',
        'navigateTo': 'https://shop.example.com/next',
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert _delivered(outcome) == {}, outcome
    assert len(_errors(outcome)) == 1, outcome
    assert 'fix1' in _errors(outcome)[0], outcome


def test_a_refused_probe_does_not_fall_through_to_the_cdp_channel(tmp):
    """A document-bound probe that throws is a refusal, not a routing step.
    del tmp

    The CDP channel is tab-bound, so falling through to it after the
    document the request named is refused is exactly the delivery this
    boundary exists to stop. Nothing may be dispatched through it.
    """
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'probe': 'throw',
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert _delivered(outcome) == {}, outcome
    assert outcome['submitted'] == [], outcome
    assert len(_errors(outcome)) == 1, outcome
    assert 'fix1' in _errors(outcome)[0], outcome


def test_a_fix_scoped_to_a_site_does_not_run_on_another_site(tmp):
    """C3: the fix is stored for one site and the page is a different one."""
    del tmp
    outcome = run_hotfix_case({
        'documents': [ELSEWHERE],
        'current': 0,
        'asker': 0,
        'fixes': [{'id': 'fix1', 'code': FIX, 'match': SCOPE}],
    })
    assert outcome['delivered'].get('doc-1') == [], outcome
    assert _delivered(outcome) == {}, outcome
    # The replayed count is what ran — zero here — and the skip is its own
    # line, so neither number reads as a success the fix never was.
    assert len(_logs(outcome)) == 2, outcome
    assert _logs(outcome)[0] == (
        '[Daedalus] replayed 0 hotfix(es) on tab 7'), outcome
    assert 'skipped 1 hotfix(es) by site scope' in _logs(outcome)[1], outcome
    assert SCOPE in _logs(outcome)[1], outcome
    assert 'fix1' in _logs(outcome)[1], outcome


def test_a_fix_scoped_to_a_site_does_run_on_that_site(tmp):
    """C4: the anti-vacuity half of C3.

    Without this, C3 is satisfied by a scope filter that matches nothing at
    all — the same defect the store-time refusal prevents, arriving by
    another route.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'fixes': [{'id': 'fix1', 'code': FIX, 'match': SCOPE}],
    })
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome
    assert not _errors(outcome), outcome


def test_a_scope_that_does_not_parse_is_refused_at_store_time(tmp):
    """C5: an unparseable scope is refused, and a parseable one is stored.
    del tmp

    The refusal half is the property; the acceptance half is what stops it
    from being satisfied by refusing every scope.
    """
    outcome = run_hotfix_case({
        'documents': [SITE],
        'ask': False,
        'store': [
            {'id': 'store-bad', 'fixId': 'bad', 'code': FIX,
             'match': 'ht!tp:/nonsense'},
            {'id': 'store-good', 'fixId': 'good', 'code': FIX,
             'match': SCOPE},
        ],
    })
    by_id = {row['id']: row for row in outcome['posted']}
    assert by_id['store-bad']['error'], outcome
    assert by_id['store-bad']['result'] is None, outcome
    assert by_id['store-good']['error'] is None, outcome
    assert by_id['store-good']['result'] == {
        'stored': 'good', 'total': 1, 'permanent': False, 'match': SCOPE,
    }, outcome
    # The record gained the fix the operator meant and neither gained the
    # one the operator could not have meant.
    assert outcome['stored'] == [{'id': 'good', 'match': SCOPE}], outcome


def test_the_cdp_channel_runs_nothing_when_the_live_document_differs(tmp):
    """C6: the CDP channel is tab-bound, so it carries its own check.
    del tmp

    `window.location` is unforgeable in Chrome, so a comparison inside the
    evaluation that runs the fix cannot be spoofed by the page, and one
    evaluation leaves no window between the check and the run.
    """
    outcome = run_hotfix_case({
        'documents': [SITE, ELSEWHERE],
        'current': 1,
        'asker': 0,
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The fix reached the tab, and the tab was showing the other document.
    # A tab-bound CDP channel would have run it there.
    assert outcome['current'] == 'doc-2', outcome
    assert _delivered(outcome) == {}, outcome
    # The evaluation was submitted and answered with a refusal, so the
    # check is inside the evaluation rather than a second round trip.
    assert len(outcome['submitted']) == 1, outcome
    assert outcome['submitted'][0]['replMode'] is True, outcome
    # The MAIN channel was probed and answered false, and its injection was
    # never reached, so the CDP channel is the one that carried the fix.
    assert [row['probe'] for row in outcome['injections']] == [True], outcome
    assert len(_errors(outcome)) == 1, outcome
    assert _errors(outcome) == [
        '[Daedalus] hotfix replay failed on tab 7: fix1: Error: '
        + "the document that asked for this fix is no longer the tab's"
        + ' live document'], outcome


def test_a_fix_with_a_top_level_var_and_await_still_reaches_global_scope(tmp):
    """C7: the CDP guard is a statement, not a wrapper.
    del tmp

    An IIFE would move the fix's own `var` and function declarations out of
    global scope and turn its top-level `await` into a syntax error, so
    every stored fix written that way would stop working — silently, on the
    channel that reaches the pages a CSP forbids.
    """
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'probe': False,
        'globals': ['daedalusTopLevel'],
        'fixes': [{'id': 'fix1', 'code':
                   "var daedalusTopLevel = 'kept';\n"
                   + "daedalusHits.push('fix1');\n"
                   + 'await Promise.resolve();'}],
    })
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome
    assert outcome['globals'] == {'doc-1.daedalusTopLevel': 'kept'}, outcome
    assert not _errors(outcome), outcome


def test_the_replayed_count_names_the_fixes_that_ran(tmp):
    """The success count is what ran, not the whole eligible set.

    A count that includes the fixes a scope kept away is a success number
    for work that did not happen, and it is the number an operator reads to
    decide whether the fix they stored is live.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [ELSEWHERE],
        'current': 0,
        'asker': 0,
        'fixes': [
            {'id': 'fix1', 'code': FIX, 'match': SCOPE},
            {'id': 'fix2', 'code': "daedalusHits.push('fix2')"},
        ],
    })
    # The anti-vacuity half: one fix really did reach the document, so a
    # count of zero would be a broken counter rather than a wrong one.
    assert _delivered(outcome) == {'doc-1': ['fix2']}, outcome
    assert _logs(outcome)[0] == (
        '[Daedalus] replayed 1 hotfix(es) on tab 7'), outcome
    assert 'skipped 1 hotfix(es)' in _logs(outcome)[1], outcome


def test_re_storing_a_scoped_fix_keeps_the_scope_it_had(tmp):
    """A store that omits `match` must not widen the fix it replaces.

    `permanent` is carried over from the record the store already holds when
    a command leaves it out, because a command that means "update the code"
    does not mean "drop the flag". A scope is a boundary, so the same
    omission must not turn a fix for one site into a fix for every site —
    and the dashboard's own edit re-stores with no `match` at all.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE],
        'ask': False,
        'store': [
            {'id': 'store-scoped', 'fixId': 'scoped', 'code': '1',
             'match': SCOPE},
            # The ordinary "the code changed" call: same id, no match.
            {'id': 'store-again', 'fixId': 'scoped', 'code': '2'},
            # And the anti-vacuity half: a store that DOES name a scope
            # replaces it, so preserving is not a field nothing can update.
            {'id': 'store-rescoped', 'fixId': 'scoped', 'code': '3',
             'match': '*://*.example.com/*'},
        ],
    })
    posted = {row['id']: row for row in outcome['posted']}
    assert posted['store-again']['error'] is None, outcome
    assert posted['store-again']['result']['match'] == SCOPE, outcome
    assert posted['store-rescoped']['error'] is None, outcome
    assert posted['store-rescoped']['result']['match'] == (
        '*://*.example.com/*'), outcome
    assert outcome['stored'] == [
        {'id': 'scoped', 'match': '*://*.example.com/*'}], outcome


def test_a_wildcard_host_scope_covers_the_domain_and_its_subdomains(tmp):
    """`*.example.com` is how an operator writes "this site and everything
    under it", and Chrome's host wildcard means exactly that.

    A scope that compiles to a required leading label instead accepts the
    pattern at store time and then never fires on the domain it names, which
    is a boundary the operator believes exists and largely does not.
    """
    del tmp
    for page in ('https://example.com/cart',
                 'https://shop.example.com/cart',
                 'https://a.b.example.com/cart'):
        outcome = run_hotfix_case({
            'documents': [page],
            'current': 0,
            'asker': 0,
            'fixes': [{'id': 'fix1', 'code': FIX,
                       'match': '*://*.example.com/*'}],
        })
        assert _delivered(outcome) == {'doc-1': ['fix1']}, (page, outcome)


def test_a_scope_matches_a_host_whatever_case_it_is_written_in(tmp):
    """A host is case-insensitive, and a URL parser has already folded the
    one the browser reports.

    A pattern written `*://EXAMPLE.com/*` is accepted at store time, so a
    case-sensitive comparison makes it a second spelling of a scope that
    never matches.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'fixes': [{'id': 'fix1', 'code': FIX,
                   'match': '*://SHOP.Example.COM/*'}],
    })
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome
    assert not _errors(outcome), outcome


def test_a_request_the_worker_cannot_bind_runs_nothing(tmp):
    """No document id, no url, or a url that names no page: all refused.

    Chrome supplies all three on a content-script message, so none of these
    is a real page's path. They are here because the branch that refuses
    them is production code, and a branch nothing exercises is a branch
    nobody has read.
    """
    del tmp
    for omitted, sender in (
            ({'senderOmits': ['documentId']}, 'no documentId'),
            ({'senderOmits': ['url']}, 'no url'),
            ({'senderUrl': '/relative/path'}, 'no absolute url')):
        outcome = run_hotfix_case({
            'documents': [SITE],
            'current': 0,
            'asker': 0,
            'fixes': [{'id': 'fix1', 'code': FIX}],
            **omitted,
        })
        assert _delivered(outcome) == {}, (sender, outcome)
        assert outcome['submitted'] == [], (sender, outcome)
        assert outcome['injections'] == [], (sender, outcome)
        assert _errors(outcome) == [
            '[Daedalus] hotfix replay on tab 7 ran nothing: the request '
            'named no document, or a url that names no page'], (sender,
                                                                outcome)


def test_a_fragment_change_does_not_refuse_a_cdp_routed_fix(tmp):
    """The CDP check must be no finer than the MAIN channel's document
    binding, or the two channels disagree about the same page.

    A fragment change is not a navigation: the document is the same one, the
    tab still holds it, and the MAIN channel — which binds by document —
    would have delivered to it. Comparing the whole `location.href` refuses
    a fix whose document never changed.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'probe': False,
        'relocateAt': 'before-cdp-evaluate',
        'relocateTo': SITE + '#checkout',
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The tab still holds the one document it held, and it is the one the
    # request named: a fragment change opened nothing and retired nothing.
    assert set(outcome['delivered']) == {'doc-1'}, outcome
    assert outcome['current'] == 'doc-1', outcome
    assert len(outcome['submitted']) == 1, outcome
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome
    assert not _errors(outcome), outcome


def test_a_file_scope_reaches_a_local_page_on_both_channels(tmp):
    """`file:` is in the scope grammar, and the extension holds file access.

    The manifest matches `<all_urls>`, so a local HTML file is a page the
    operator visits and a scope they can write. A `file:` url has no
    origin — `URL.origin` is the string `"null"` for it — so an identity
    built from `origin` cannot be matched by a pattern compiled from the
    pattern's own text, and a stored `file:///*` scope silently stops
    firing. Both channels are covered because the identity is compared on
    each: the worker's projection against the scope, and the page's own
    against the worker's.
    """
    del tmp
    for probe in (True, False):
        outcome = run_hotfix_case({
            'documents': [LOCAL_PAGE],
            'current': 0,
            'asker': 0,
            'probe': probe,
            'fixes': [{'id': 'fix1', 'code': FIX, 'match': FILE_SCOPE}],
        })
        channel = 'MAIN' if probe else 'CDP'
        assert _delivered(outcome) == {'doc-1': ['fix1']}, (channel, outcome)
        assert not _errors(outcome), (channel, outcome)
        # The anti-vacuity half: the page really was the one the request
        # named, and the channel the case selected is the one that ran.
        assert outcome['current'] == 'doc-1', (channel, outcome)
        assert outcome['submitted'] == ([] if probe
                                        else [{'replMode': True,
                                               'awaitPromise': False}]
                                        ), (channel, outcome)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='hotfixscope_')


if __name__ == '__main__':
    raise SystemExit(main())
