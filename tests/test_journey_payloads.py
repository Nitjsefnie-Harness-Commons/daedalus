#!/usr/bin/env python3
"""What each journey CARRIES, against what the product carries.

Three of the seven journeys posted a payload far smaller than the one a user
actually produces, so their own work was not separable from the fixed
background every child shares and the budget could not mean anything for
them. The fix is a payload sized from a cited product basis, and this file
is what stops a later change from quietly inflating one past that basis to
chase a number: every size below is pinned here, and every basis is pinned
to a symbol that still has to be in the product.

No journey is RUN here. A journey costs what the machine it ran on costs, so
a control that drove one would be asserting a number this repository must
not write down; what is asserted is the shape the journey builds, the basis
the size comes from, and the call the size made necessary.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    _util,
)


# Every resized journey: the size it was given, the token that says where the
# size comes from, the product file that token is quoted from (or None where
# the basis is a property of the content rather than of this tree), and which
# journeys module declares the constant.
#
# The sizes are not chosen here and not chosen in the journeys module
# either: each is read off a product symbol — the capture the extension
# takes, the tab count the extension's own comment calls a session, the
# heartbeat period the extension arms. A symbol rather than a sentence,
# because a basis stated only in this file drifts the moment the product
# moves, while one that has to still be there fails the day it is renamed.
BASES = (
    ('SHOT_CAPTURE_BYTES', 196608, 'captureVisibleTab',
     'extension/worker/capture.js', '_journey_typed'),
    ('SEG_BODY_BYTES', 1048576, 'HLS', None, '_journey_typed'),
    ('FANOUT_TABS', 10, '10-url open_tabs',
     'extension/worker/registry.js', '_journeys'),
    ('FANOUT_HEARTBEATS', 20, 'periodInMinutes: 0.5',
     'extension/background.js', '_journeys'),
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
    for name, size, _token, _product, which in BASES:
        module = _journeys_module(which)
        assert getattr(module, name) == size, (
            f'{name} is {getattr(module, name, None)} and the basis this '
            f'file pins says {size}: a payload past the size the product '
            'produces is a number nobody measured')
    typed = _journeys_module('_journey_typed')
    assert len(typed.SHOT_CAPTURE_B64) == (
        typed.SHOT_CAPTURE_BYTES * 4) // 3, (
        'the capture body the journey posts is not the base64 of a capture '
        f'of the pinned size: {len(typed.SHOT_CAPTURE_B64)} characters for '
        f'{typed.SHOT_CAPTURE_BYTES} bytes')
    for index in range(typed.SEG_COUNT):
        assert len(typed.segment_payload(index)) == typed.SEG_BODY_BYTES, (
            f'segment {index} is not the pinned size: '
            f'{len(typed.segment_payload(index))}')


def test_every_basis_is_still_the_symbol_it_cites(tmp):
    """A citation that has moved is a size nobody can re-derive.

    Each basis is stated in the comment under the constant it justifies, and
    every one of them but the segment's is also a symbol in a product file
    — so the product file is resolved too, and a renamed symbol or a deleted
    comment fails rather than leaving a number quoted from something that no
    longer says it. The segment size is the one with no symbol to resolve:
    an HLS segment's length is a property of the encode a page happens to be
    watching rather than of anything in this tree, which is exactly why the
    prose basis under the constant is what the control asks for.
    """
    del tmp
    for name, _size, token, product, which in BASES:
        module = _journeys_module(which)
        # Folded, because a citation quoted from a wrapped line of prose
        # arrives here wrapped too, and a control that could not read its own
        # citation would be fixed by editing the citation.
        cited = ' '.join(_cited_comment(module, name).split())
        assert token in cited, (
            f'{name} carries no basis saying {token!r} beside it, so the '
            f'number is one nobody can re-derive: {cited!r}')
        if product is None:
            continue
        target = _journey_contract.ROOT / product
        assert target.is_file(), (
            f'the basis for {name} names a file that is not in the tree: '
            f'{product}')
        assert token in target.read_text(encoding='utf-8'), (
            f'{name} is sized from {product}, which no longer says '
            f'{token!r}: the number is quoted from something the product '
            'no longer says')


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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeypayloads_')


if __name__ == '__main__':
    raise SystemExit(main())
