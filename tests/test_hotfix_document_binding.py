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

The token is page-readable and page-writable, and that is accepted. A page
can reach a second document's DOM — a same-origin frame or opened window it
holds — and could plant a token there; what it cannot do is make a fix run in
a document the evaluation does not read. The evaluation resolves to the tab's
top frame, and the asker holds no handle to a second top-frame document in its
own tab, so a hostile document can suppress its own fix and can never make one
run somewhere it does not hold. The binding is directional, not secret.
`D1` below rests on exactly one layer for that reason — the two documents
share a url, so the `location` guard passes for both and only the token
guard can fire — which is why removing the token guard alone turns it red.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _hotfixharness import run_hotfix_case  # noqa: E402

SITE = 'https://shop.example.com/cart'
# A page where `crypto.randomUUID` is [SecureContext] and absent.
HTTP_PAGE = 'http://shop.example.com/cart'
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
    # The asker planted token-0 and the live document planted its own
    # token-1, at the same url. The attribute is PRESENT in the live
    # document and is the WRONG one, which is the state D1 exists to reject
    # and which a guard that only asked "is it there" would wave through.
    assert outcome['minted'] == {
        'doc-1': 'doc-token-0', 'doc-2': 'doc-token-1'}, outcome
    assert outcome['minted']['doc-1'] != outcome['minted']['doc-2'], outcome
    # And the url the CDP channel compared is the same in both, so nothing
    # but the token refused this.
    assert _delivered(outcome) == {}, outcome
    assert len(_errors(outcome)) == 1, outcome
    assert _errors(outcome) == [
        '[Daedalus] hotfix replay failed on tab 7: fix1: Error: '
        + "the document that asked for this fix is no longer the tab's"
        + ' live document'], outcome


def test_a_cdp_routed_fix_runs_in_the_document_that_asked(tmp):
    """D2: the anti-vacuity half of D1, in the state a browser produces.

    D1 is refused because the live document holds the WRONG token. D2 is the
    ordinary page load: the document that asked is the one the tab is
    showing, so the token it planted is the token the guard reads back. A
    guard that refused everything would satisfy D1 perfectly and deliver no
    fix at all, so the pair is what shows the guard reads a value rather than
    always answering.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 1,
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert outcome['current'] == 'doc-2', outcome
    assert outcome['delivered'].get('doc-1') == [], outcome
    assert _delivered(outcome) == {'doc-2': ['fix1']}, outcome
    assert not _errors(outcome), outcome


def test_the_guard_compares_the_value_and_not_only_its_absence(tmp):
    """F1: an attribute that is PRESENT and WRONG is still refused.

    A prerender asks and plants token-0. It is abandoned; the visible
    document loads, its own content script plants token-1, and the first
    request's evaluation resolves to that document. Both are at one url, and
    the live document's attribute is not missing — it holds a different
    value. A guard that refuses only on ABSENCE reads that as a match and
    runs the first document's fix in the second, which is the misdelivery
    #1111 exists to close. So the degenerate input is built on purpose: the
    attribute present, the value wrong.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 0,
        'copiedToken': False,
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The anti-vacuity half of the whole finding: the live document's
    # attribute is not null. A control that only knew about absence would be
    # satisfied by the mutant this pins.
    live_token = outcome['minted']['doc-2']
    assert live_token is not None, outcome
    assert live_token != outcome['minted']['doc-1'], outcome
    assert _delivered(outcome) == {}, outcome
    assert len(_errors(outcome)) == 1, outcome
    assert 'no longer the tab\'s live document' in _errors(outcome)[0], outcome


def test_the_live_document_holding_the_askers_own_token_still_runs(tmp):
    """F1 again, on the other side of the comparison.

    D1 and the control above are both refusals; neither shows the comparison
    PASSES for a second document carrying the right value. This is the one
    state where the comparison must succeed and the fix must run, so a guard
    that refused on inequality — the opposite error — is caught here rather
    than hiding behind D1.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 1,
        'asker': 0,
        'copiedToken': [1],
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert outcome['afterAnswer']['doc-2'] == (
        outcome['minted']['doc-1']), outcome
    assert _delivered(outcome) == {'doc-2': ['fix1']}, outcome
    assert not _errors(outcome), outcome


def test_each_document_mints_its_own_token(tmp):
    """F1: the producer half — two documents, two values, from the real code.

    Nothing else here can see this. The guard is handed whatever the shipped
    `content.js` minted; a producer that returned one value for every
    document, or a constant, would leave every downstream control green
    while making the token a constant the guard could not use.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE, SITE],
        'current': 2,
        'asker': 0,
        'copiedToken': False,
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    minted = [outcome['minted'][key]
              for key in sorted(outcome['minted'])]
    assert None not in minted, outcome
    assert len(set(minted)) == 3, minted
    # And the asker's own request carried the value IT planted, not some
    # other document's.
    assert _delivered(outcome) == {}, outcome


def test_a_plain_http_page_mints_a_token_without_crypto_randomuuid(tmp):
    """F1: the `[SecureContext]` fallback, which no other double reaches.

    `crypto.randomUUID` is [SecureContext] and the content script is declared
    on `<all_urls>`, so on a plain-http page it is absent and the mint falls
    back. This is the only place in the tree that runs the shipped producer
    with `crypto` carrying no `randomUUID`, so it is the only place the
    fallback can fail: a producer that dropped it would raise at load on every
    http page and this case is what would say so.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [HTTP_PAGE, HTTP_PAGE],
        'current': 1,
        'asker': 0,
        'copiedToken': False,
        'cryptoFrame': 'http',
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    minted = [outcome['minted'][key]
              for key in sorted(outcome['minted'])]
    # It minted something anyway, and two documents still differ.
    assert None not in minted, outcome
    assert minted[0] != minted[1], minted
    # The ordinary positive case is untouched by the fallback: the document
    # that asked is the one the tab is showing.
    positive = run_hotfix_case({
        'documents': [HTTP_PAGE, HTTP_PAGE],
        'current': 1,
        'asker': 1,
        'copiedToken': False,
        'cryptoFrame': 'http',
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert _delivered(positive) == {'doc-2': ['fix1']}, positive
    assert not _errors(positive), positive


def test_the_content_script_takes_its_token_back_out_when_the_worker_answers(
        tmp):
    """M4: the answer is what runs the content script's cleanup.

    The message channel is held open until the replay reports, and the
    callback is what removes the planted attribute. A worker that never
    answered would leave the token in the page for as long as the page lives,
    and this reads the attribute after the answer rather than before it.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE],
        'current': 0,
        'asker': 0,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    assert outcome['minted']['doc-1'] is not None, outcome
    assert outcome['afterAnswer']['doc-1'] is None, outcome
    # Chrome delivers the callback either way, so the cleanup alone does not
    # show the worker answered. This does: an unanswered channel reaches the
    # content script as a `lastError`, which the double records separately.
    assert outcome['answered'] is True, outcome
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome


def test_a_main_channel_answer_for_another_document_is_refused(tmp):
    """F2: the MAIN channel checks the answer's own `documentId`.

    The probe and the injection both name the document the request carried,
    so the injection lands in the right document. The answer is the third
    layer: Chrome reports which document it actually injected into, and a
    document that was replaced between the two calls is reported under a
    different id. Without the check the fix's result is accepted from a
    document the request never named.
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE, SITE],
        'current': 0,
        'asker': 0,
        'copiedToken': False,
        'answerDocumentId': 'doc-2',
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The injection really did reach the document the request named, and it
    # really did run there: the refusal is about the ANSWER, not about where
    # the code went.
    assert outcome['injections'] == [
        {'documentId': 'doc-1', 'world': 'MAIN', 'probe': True},
        {'documentId': 'doc-1', 'world': 'MAIN', 'probe': False},
    ], outcome
    assert _delivered(outcome) == {'doc-1': ['fix1']}, outcome
    assert len(_errors(outcome)) == 1, outcome
    assert 'doc-2' in _errors(outcome)[0], outcome
    # The channel this took is the MAIN one: nothing was submitted over CDP,
    # so the refusal is the answer's own id and not a binding failure.
    assert outcome['submitted'] == [], outcome
    assert outcome['attachCalls'] == [], outcome


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
        'copiedToken': [1],
        'docTokenOmitted': True,
        'probe': False,
        'fixes': [{'id': 'fix1', 'code': FIX}],
    })
    # The live document holds a token, so nothing about the documents
    # refused this — the request carried none.
    assert outcome['minted']['doc-2'] is not None, outcome
    # The half this control exists for: the ATTACHMENT. The stub records
    # every `chrome.debugger.attach`, so a worker that attached before
    # refusing is caught here rather than only in the message it printed.
    assert outcome['attachCalls'] == [], outcome
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
        'copiedToken': False,
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
        'copiedToken': [1],
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
