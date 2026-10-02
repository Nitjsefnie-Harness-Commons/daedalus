#!/usr/bin/env python3
"""What each journey CARRIES, against what the product carries.

Three of the seven journeys posted a payload far smaller than the one a user
actually produces, so their own work was not separable from the fixed
background every child shares and the budget could not mean anything for
them. The fix is a payload sized from a cited product basis.

WHAT IS PINNED HERE, and what is not, is worth stating exactly, because the
difference is what a reviewer is being asked to trust:

  - Pinned: every size is the number this file holds, AND the thing that
    builds it produces exactly that many of them — the capture's base64
    encodes to the capture's byte count, each segment is the segment size,
    and the tab list the fan-out posts is the tab count.
  - Pinned: the product still makes the CALL the basis names, and still
    says the NUMBER the basis names, in a file that has to exist.
  - NOT pinned: that the size is REPRESENTATIVE. No control can derive
    that and none tries. A size moved together with its pin here is not
    caught by anything in the tree — that is what makes it a reviewable
    commit and not a quiet loosening of a budget, and it is the judgement
    the citation exists to inform. What these controls buy is that the
    tripwire fires on the common case (a number edited, the pin not), and
    that each number still rests on a claim the product has to keep making.

No journey is RUN here. A journey costs what the machine it ran on costs, so
a control that drove one would be asserting a number this repository must
not write down; what is asserted is the shape the journey builds, the basis
the size comes from, and the call the size made necessary.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    _util,
)


# Every resized journey, and two separate claims per row.
#
# The first three columns are the journeys module's constant, the size this
# file holds for it, and the word the module's own comment beside that
# constant has to contain — the basis, written where a reader meets the
# number.
#
# The last two are the product claim: the file that has to exist, and the
# CALL that file has to still make, matched as a call. A call rather than a
# name, because the name survives a rename in a comment: with
# `captureVisibleTab` renamed at its one call site and the citation left
# saying the word, a word search stayed green — the same false green as a
# basis nobody can re-derive. `None` is where there is no product claim to
# make and the basis is a property of the content rather than of this tree.
#
# The third column is checked in the product file too, not only in the
# journeys module. A call and a SIZE are two different claims, and the tab
# count is only in the second: `chrome.tabs.query({}, …)` carries no `10`,
# the extension's own comment at `scheduleRegisterAllTabs` does. A pin that
# checked only the call let the product change its statement of a 10-url
# session to a 30-url one without a word — which is exactly the loose end
# the call recogniser introduced when it replaced the word search.
BASES = (
    ('SHOT_CAPTURE_BYTES', 196608, 'captureVisibleTab',
     'extension/worker/capture.js',
     r'chrome\.tabs\.captureVisibleTab\s*\(', '_journey_typed'),
    ('SEG_BODY_BYTES', 1048576, 'HLS', None, None, '_journey_typed'),
    ('FANOUT_TABS', 10, '10-url open_tabs',
     'extension/worker/registry.js',
     r'chrome\.tabs\.query\s*\(', '_journeys'),
    ('FANOUT_HEARTBEATS', 20, 'periodInMinutes: 0.5',
     'extension/background.js',
     r"chrome\.alarms\.create\(\s*['\"]daedalus-heartbeat['\"],\s*"
     r'\{\s*periodInMinutes:\s*0\.5\s*\}\s*\)', '_journeys'),
)


def _journeys_module(which):
    """One of the two modules a journey lives in, by its own name."""
    sys.path.insert(0, str(_journey_contract.ROOT / 'tests'))
    try:
        imported = __import__(which)  # the module under test, by its own name
    finally:
        sys.path.pop(0)
    return imported


def _cited_comment(module, name):
    """The comment block around `name`, which is where its basis is stated.

    Both the block above the constant and the one under it, since either is
    where a basis written beside the number it justifies lands, and neither
    is where a reader would look anywhere else.
    """
    lines = Path(module.__file__).read_text(encoding='utf-8').splitlines()
    start = next((index for index, line in enumerate(lines)
                  if line.startswith(f'{name} = ')), None)
    assert start is not None, f'{name} is not declared in {module.__file__}'
    above = []
    for line in reversed(lines[:start]):
        cited = line.strip()
        if not cited or cited.startswith('#'):
            above.append(cited.lstrip('#').strip())
            continue
        break
    below = []
    for line in lines[start + 1:]:
        cited = line.strip()
        if not cited or cited.startswith('#'):
            below.append(cited.lstrip('#').strip())
            continue
        break
    return '\n'.join(list(reversed(above)) + below)


def test_every_resized_payload_carries_the_size_it_was_given(tmp):
    """The pinned size and the size the journey builds are the same number.

    Asserting the constant alone would pass against a generator that ignored
    it, and asserting the generator alone would pass against a constant
    nobody raised a tolerance for. So both are read, and each row is read
    from the module the table NAMES rather than from one module by
    assumption — the fan-out's two counts live in `_journeys` beside the
    journey that uses them, and a control that only ever looked in
    `_journey_typed` passed against both of them inflated to eight times
    their basis.
    """
    del tmp
    for name, size, _token, _product, _call, which in BASES:
        module = _journeys_module(which)
        assert getattr(module, name) == size, (
            f'{name} is {getattr(module, name, None)} and the basis this '
            f'file pins says {size}: a payload past the size the product '
            'produces is a number nobody measured')
    typed = _journeys_module('_journey_typed')
    # `4 * -(-n // 3)` and not `4n // 3`: base64 emits one character per
    # three bytes plus a `=` pad per remainder, so the length of an ENCODING
    # is the ceiling. `(4n) // 3` holds only for a multiple of three and reds
    # on a 4 MiB capture — a valid encoding of exactly that size — so the
    # arithmetic was refusing a real body rather than a bad one.
    assert len(typed.SHOT_CAPTURE_B64) == 4 * -(
        -typed.SHOT_CAPTURE_BYTES // 3), (
        'the capture body the journey posts is not the base64 of a capture '
        f'of the pinned size: {len(typed.SHOT_CAPTURE_B64)} characters for '
        f'{typed.SHOT_CAPTURE_BYTES} bytes')
    for index in range(typed.SEG_COUNT):
        assert len(typed.segment_payload(index)) == typed.SEG_BODY_BYTES, (
            f'segment {index} is not the pinned size: '
            f'{len(typed.segment_payload(index))}')
    # The fan-out's BOTH halves, on the same argument as the capture's: a
    # constant nothing builds is a number nothing reads. `_dashboard_tabs`
    # is what every sync above posts, so the tab count the basis justifies
    # is the tab count the journey sends — and `range(1, 3)` sent two while
    # this file and the constant both still said ten.
    fanout = _journeys_module('_journeys')
    assert len(fanout._dashboard_tabs()) == fanout.FANOUT_TABS, (
        'the tab list the fan-out posts is not the pinned tab count: '
        f'{len(fanout._dashboard_tabs())} against {fanout.FANOUT_TABS}')


def test_every_basis_is_still_the_call_it_cites(tmp):
    """The product still CALLS what the basis says it calls.

    Matched as a call, in a file that has to exist. A word search does not
    survive the move a basis pin exists for: with `captureVisibleTab` renamed
    at its one call site and the citation left saying the word, the suite
    stayed 3/3 — so the number stayed quoted from something the product no
    longer does. The recogniser is anchored on the call and on its arguments
    where an argument carries the size (`periodInMinutes: 0.5`), because a
    heartbeat that still fires but fires twice a minute is a different basis,
    not the same one.

    And the FIGURE the basis names, checked in the same file, because a call
    and a number are separate claims and the tab count is only in the second:
    `chrome.tabs.query({}, …)` says nothing about how many tabs a session
    has, so a pin that checked the call alone let the product's own "10-url
    open_tabs" become a 30-url one without a word. Under `a4f09a27`'s word
    search that edit was caught; the call recogniser introduced the gap and
    this is the fix.

    The segment size is the row with no product claim to resolve: an HLS
    segment's length is a property of the encode a page happens to be
    watching rather than of anything in this tree. What is asked of that row
    is the prose basis under the constant, which is the only place a reader
    can re-derive it from.
    """
    del tmp
    for name, _size, token, product, call, _which in BASES:
        if product is None:
            assert call is None, (
                f'{name} carries a call to resolve and no file to resolve it '
                'in, so one of the two was forgotten rather than both')
            continue
        assert call is not None, (
            f'{name} names a product file but no call in it, so the basis '
            'would be checked for a word and survive a rename of the thing '
            'the word names')
        target = _journey_contract.ROOT / product
        assert target.is_file(), (
            f'the basis for {name} names a file that is not in the tree: '
            f'{product}')
        text = target.read_text(encoding='utf-8')
        # TWO claims, two sentences: the file must still make the call the
        # basis rests on, and must still say the NUMBER the basis names. The
        # call alone is not enough — the tab count is nowhere in
        # `chrome.tabs.query({}, …)`, only in the extension's own comment
        # saying a session is a 10-url open_tabs, so a pin that checked the
        # call alone let the product triple its statement of one silently.
        assert re.search(call, text), (
            f'{name} is sized from {product}, which no longer makes the call '
            f'{call!r}: the number is quoted from something the product no '
            'longer does')
        assert token in text, (
            f'{name} is sized from {product}, which no longer says '
            f'{token!r}: the number is quoted from a figure the product no '
            'longer states')


def test_every_basis_is_still_stated_beside_its_number(tmp):
    """The basis is written where a reader meets the number.

    The call above is about the product; this one is about the journeys
    module. A size whose constant carries no comment saying where it came
    from is a number a reader has to take on trust, and the call in the
    product says what the product does — not what this journey decided a
    session is.

    Folded before matching, because a citation quoted from a wrapped line of
    prose arrives here wrapped too, and a control that could not read its own
    citation would be fixed by editing the citation.
    """
    del tmp
    for name, _size, token, _product, _call, which in BASES:
        module = _journeys_module(which)
        cited = ' '.join(_cited_comment(module, name).split())
        assert token in cited, (
            f'{name} carries no basis saying {token!r} beside it, so the '
            f'number is one nobody can re-derive: {cited!r}')


def test_the_capture_body_travels_under_a_header_and_not_in_the_body(tmp):
    """A capture at the pinned size cannot carry its credential in its body.

    A body-carried token is only accepted below
    `DAEDALUS_MAX_UNAUTHENTICATED_BODY` (64 KiB by default), and a larger
    body with no `Authorization` header is answered 401 without being read.
    At a 1x1 PNG the upload journey's token rode in its body; at a real
    capture's size that same call is a refusal, so the journey would be
    measuring the refusal — and a refusal this shape reports as an ordinary
    journey failure rather than as the shape it is. The call is read out of
    the module's own tree, so this control is about the call rather than
    about a string that happens to be near it.
    """
    del tmp
    typed = _journeys_module('_journey_typed')
    assert typed.SHOT_CAPTURE_B64 != '', 'the capture body is empty'
    assert len(typed.SHOT_CAPTURE_B64) > 64 * 1024, (
        'the capture is small enough to carry its credential in its body, so '
        'this control is checking a property the journey no longer depends '
        f'on: {len(typed.SHOT_CAPTURE_B64)} characters')
    tree = ast.parse(Path(typed.__file__).read_text(encoding='utf-8'))
    journey = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == 'screenshot')
    uploads = [node for node in ast.walk(journey)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == 'post_json'
               and any('/upload' in ast.dump(arg) for arg in node.args)]
    assert len(uploads) == 1, (
        f'the capture journey makes {len(uploads)} upload calls, so this '
        'control is walking a shape the journey no longer has')
    upload = uploads[0]
    body = upload.args[1]
    keys = [key.value for key in getattr(body, 'keys', ())]
    assert 'token' not in keys, (
        'the upload still carries the credential in its body, which is the '
        f'form a body of this size is refused for: {sorted(keys)}')
    headers = next((ast.dump(word.value) for word in upload.keywords
                    if word.arg == 'headers'), '')
    assert 'Authorization' in headers and 'SHOT_TOKEN' in headers, (
        f'the upload sends no credential header, so at a real capture size '
        f'the bridge answers 401 unread: {headers}')


def test_no_journey_buys_its_own_headroom_from_a_product_setting(tmp):
    """A journey may not raise a product constant to make its own budget.

    The fan-out session has to publish every event before the subscription
    opens, and `command_queue.gc_loop` unlinks anything older than
    `DAEDALUS_CMD_TTL` (90 s by default) whether or not a window is attached.
    The window is therefore the whole publish loop, and a slow runner could
    spend it. The two available moves are both wrong: raising the TTL
    measures a bridge configured unlike every other one, and cutting the
    session to fit sizes the journey to an infrastructure ceiling instead of
    to the product.

    This control says which way it went, so a reviewer is not left inferring
    it. `bridge_env` is the one place a journey's bridge is configured, so
    it is where a journey would quietly do it.
    """
    del tmp
    journeys_module = _journeys_module('_journeys')
    environment = journeys_module.bridge_env(journeys_module.COMMAND_TOKEN)
    for setting in ('DAEDALUS_CMD_TTL', 'DAEDALUS_MAX_BODY_SIZE',
                    'DAEDALUS_MAX_UNAUTHENTICATED_BODY',
                    'DAEDALUS_STREAM_MAX_AGE'):
        assert setting not in environment, (
            f'{setting} is set on a journey\'s own bridge, so that journey is '
            'measured against a configuration no other one runs under: '
            f'{sorted(environment)}')
    assert sorted(environment) == ['DAEDALUS_TOKEN', 'TOKEN'], \
        sorted(environment)


def _product_default():
    """The TTL default the bridge ships with, read by path.

    By path and not by import: the suite process puts `tests/` on the path
    without the repository root, so a top-level `daedalus_bridge` import is
    a resolution this file cannot rely on, and the same loader every other
    helper here uses is what makes the read work from anywhere.
    """
    source = _journey_contract.ROOT / 'daedalus_bridge' / 'env_config.py'
    return _util.load(source, 'journey_env_config_contract').CMD_TTL_DEFAULT


def test_the_ceiling_the_session_is_measured_against_is_the_products(tmp):
    """The TTL the fan-out message compares against comes from the product.

    It is three times over the message now — the printed figure, the branch
    that chooses between two sentences, and the prose beside the constant —
    and the branch is load-bearing rather than decorative, because the
    literal decides which of the two a reader is sent to. So the number is
    read from the module that ships it rather than written out, and this
    says both things: that the journeys module carries no literal of its
    own, and that what it reads is the value the product ships with.
    """
    del tmp
    fanout = _journeys_module('_journeys')
    source = Path(fanout.__file__).read_text(encoding='utf-8')
    assert 'CMD_TTL_DEFAULT' in source, (
        'the fan-out journey no longer names the command TTL at all, so its '
        'refusal has nothing to compare the publish loop against')
    literals = [line.strip() for line in source.splitlines()
                if 'CMD_TTL_DEFAULT = ' in line]
    assert not literals, (
        f'the journeys module states the ceiling itself rather than reading '
        f'it from the product: {literals}')
    assert fanout.CMD_TTL_DEFAULT == _product_default(), (
        f'the ceiling the message compares against is '
        f'{fanout.CMD_TTL_DEFAULT} and the product ships with '
        f'{_product_default()}')
    # And the product is where the number lives: the bridge reads its own
    # setting through this same constant rather than through a literal.
    assert 'CMD_TTL_DEFAULT)' in Path(
        _journey_contract.ROOT / 'daedalus_bridge' / 'config.py'
    ).read_text(encoding='utf-8'), (
        'the bridge has stopped reading the TTL default through the shared '
        'constant, so the two sides have separate copies again')


def test_the_command_ttl_default_is_the_one_the_documentation_states(tmp):
    """The number is the PRODUCT's decision, and AGENTS.md is where it is
    written down.

    The control above pins that one place states it and everything else
    reads it — a hoist, not a value. So the value itself was pinned by
    nothing, and `CMD_TTL_DEFAULT = 90` became `= 3` with every suite still
    green: the two sides would move together and agree, which is what a
    control comparing a module to itself always does.

    The oracle is the endpoint and directory reference, which says
    `DAEDALUS_CMD_TTL` defaults to 90 — twice, and both must say it. A
    control holding `90` as a literal would be a second copy that a rename
    moves with the first; this one reads the published number back, so
    changing the default without changing what the documentation says fails
    here rather than at a real run — where a raised TTL delivers a stale
    command and a lowered one drops a fresh one, and neither is visible in
    the journey's own count.
    """
    del tmp
    documented = re.findall(
        r'DAEDALUS_CMD_TTL[^.]{0,40}?default (\d+)',
        (_journey_contract.ROOT / 'AGENTS.md').read_text(
            encoding='utf-8'))
    assert documented, 'AGENTS.md no longer states the DAEDALUS_CMD_TTL '
    'default, so the number the product ships has no published value to be '
    'checked against'
    assert set(documented) == {str(_product_default())}, (
        f'AGENTS.md states the default as {sorted(set(documented))} and the '
        f'product ships with {_product_default()}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeypayloads_')


if __name__ == '__main__':
    raise SystemExit(main())
