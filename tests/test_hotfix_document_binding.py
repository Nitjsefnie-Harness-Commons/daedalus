#!/usr/bin/env python3
"""Which document a CDP-routed hotfix runs in, when the url cannot say.

A tab id names a tab. The MAIN channel names the document in its
`executeScript` target and checks the answer's own `documentId`, so it binds.
The CDP channel cannot: the DevTools protocol carries no document identifier
at all, so it was left comparing `location` — unforgeable, and blind to the
one case that matters, a prerender and the document that will replace it
sitting at the SAME url in one tab. A fix asked for by one of them ran in the
other.

So the binding comes from the document side. The asking document plants a
token in its OWN DOM and sends it with the request, and the CDP channel reads
that token back inside the same evaluation that runs the fix — the property
the existing `location` guard already had, one step finer.

The token is page-readable and page-writable, and that is accepted: a
document can only plant a token in its own DOM, so a hostile one can
suppress its own fix and can never make a fix run in a document it does not
hold. The direction is the property, not the secrecy. `D1` below rests on
exactly one layer for that reason — the two documents share a url, so the
`location` guard passes for both and only the token guard can fire — which
is why removing the token guard alone turns it red.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _hotfixharness import run_hotfix_case  # noqa: E402

SITE = 'https://shop.example.com/cart'
# The fix every control replays: it announces itself to the document that
# received it, so `delivered` answers "which document got it" rather than
# "which call was made".
FIX = "daedalusHits.push('fix1')"


def _errors(outcome):
    return [entry['text'] for entry in outcome['replay']
            if entry['level'] == 'error']


def _delivered(outcome):
    return {doc: hits for doc, hits in outcome['delivered'].items() if hits}


def test_a_cdp_routed_fix_does_not_reach_a_twin_document_in_the_same_tab(tmp):
    """D1: two documents at the same url, and only one of them asked.

    A prerender is a second document at the url the tab is already on, and
    both carry the same `location` — so the unforgeable url comparison, the
    one check the CDP channel already had, passes for both. Nothing in the
    protocol names a document, so the binding has to come from the document
    side: the asker plants a token in its OWN DOM and the check reads that
    token inside the same evaluation that runs the fix.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 0,
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The two documents really are indistinguishable by url, which is what
    # makes the token the only thing that can tell them apart.
    assert outcome['documentUrls'] == {
        'doc-1': SITE, 'doc-2': SITE}, outcome
    assert outcome['current'] == 'doc-2', outcome
    assert outcome['planted'] == {
        'doc-1': 'doc-token-0', 'doc-2': None}, outcome
    # And the url the CDP channel compared is the same in both, so nothing
    # but the token refused this.
    assert _delivered(outcome) == {}, outcome
    assert len(_errors(outcome)) == 1, outcome
    assert _errors(outcome) == [
        '[Daedalus] hotfix replay failed on tab 7: fix1: Error: '
        + "the document that asked for this fix is no longer the tab's"
        + ' live document'], outcome


def test_a_cdp_routed_fix_runs_when_the_live_document_holds_the_token(tmp):
    """D2: the anti-vacuity half of D1.

    The same two documents, with the token in the live one as well. A guard
    that refused everything would satisfy D1 perfectly and deliver no fix at
    all, so the pair is what shows the guard reads a token rather than
    answering.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 0,
        'planted': [0, 1],
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert outcome['planted'] == {
        'doc-1': 'doc-token-0', 'doc-2': 'doc-token-0'}, outcome
    assert outcome['current'] == 'doc-2', outcome
    assert outcome['delivered'].get('doc-1') == [], outcome
    assert _delivered(outcome) == {'doc-2': ['fix1']}, outcome
    assert not _errors(outcome), outcome


def test_a_replay_request_with_no_token_takes_no_attachment(tmp):
    """D3: fail closed before the debugger, not inside the evaluation.

    A request carrying no token cannot be bound to any document, so there is
    nothing an evaluation could usefully check. Attaching first would show
    Chrome's debugger banner for a request that was never going to run — and
    folding the absent token into the comparison would pass it for every
    document whose attribute happens to be absent, which is the widening.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 0,
        'planted': [0, 1],
        'docTokenOmitted': True,
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # Both documents hold the token the asker minted, so nothing about the
    # documents refused this — the request carried none.
    assert outcome['planted'] == {
        'doc-1': 'doc-token-0', 'doc-2': 'doc-token-0'}, outcome
    assert outcome['submitted'] == [], outcome
    assert _delivered(outcome) == {}, outcome
    assert len(_errors(outcome)) == 1, outcome
    assert 'fix1' in _errors(outcome)[0], outcome
    assert 'no document token' in _errors(outcome)[0], outcome


def test_the_main_channel_still_binds_by_document_not_by_token(tmp):
    """D4: the MAIN channel is untouched by any of this.

    It names the document in its `executeScript` target and checks the
    answer's own `documentId`, so it binds whether or not a token was ever
    planted — and a probe that throws is still a refusal rather than a step
    on the way to the tab-bound channel.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 0,
        'planted': [0],
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert outcome['injections'] == [
        {'documentId': 'doc-1', 'world': 'MAIN', 'probe': True},
        {'documentId': 'doc-1', 'world': 'MAIN', 'probe': False},
    ], outcome
    assert outcome['submitted'] == [], outcome
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome
    assert not _errors(outcome), outcome

    refused = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 0,
        'planted': [0, 1],
        'probe': 'throw',
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # A refused probe does not fall through to the channel that could not
    # have told the two documents apart either.
    assert refused['submitted'] == [], refused
    assert _delivered(refused) == {}, refused
    assert len(_errors(refused)) == 1, refused


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='hotfixdocbind_')


if __name__ == '__main__':
    raise SystemExit(main())
