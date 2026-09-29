#!/usr/bin/env python3
"""The exemption that lets a module declare the key it searches for.

A control that refuses a second statement of an authority has to hold
that authority's keys somewhere, and holding them is a statement of
them. This suite is about the one exemption that resolves that, and
about the ways it must not reach: not another file, not a nested scope,
not a spelling the count never found, and not a value that merely
contains the key.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _coverage_authority_scan import (  # noqa: E402
    _KEY_HOLDER, phrase_holders)


def test_only_the_keys_own_definition_is_exempt(tmp):
    """One plain assignment in the key-holder is exempt; nothing else is.

    The exemption is what lets the module holding the keys be read like
    any other, and it is the one place this control could go blind a
    second time. Exempt by shape alone it is worse than the file-name
    skip it replaced: a second statement of the whole authority,
    written as a plain assignment under any name in any other file, was
    read as a key's own definition and subtracted. So the first row
    below is a complete statement in a file that does not hold a key,
    and it is counted.

    What the key-holder gets is the same row, and the module level: a
    function-local or `if`-guarded assignment in it is counted too, so
    the one exempt site is the module's own top-level declaration and
    not anywhere an assignment can reach. A second declaration beside
    the first stays counted, and so does every copy that is prose.
    """
    phrase = 'a synthetic probe phrase'
    holder = '_coverage_authority_scan.py'
    assert holder == _KEY_HOLDER, (holder, _KEY_HOLDER)
    (Path(tmp) / 'probe.py').write_text(
        f"KEY = {phrase!r}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (['probe.py'], 1)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"KEY = {phrase!r}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (['probe.py'], 1)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"KEY = {phrase!r}\n# {phrase}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == ([holder, 'probe.py'], 2)

    (Path(tmp) / 'doc.py').write_text(f'"""{phrase}."""\n', encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 3)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"ONE = {phrase!r}\nTWO = {phrase!r}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 3)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"def go():\n    KEY = {phrase!r}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 3)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"if True:\n    KEY = {phrase!r}\n# {phrase}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 4)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"ONE = 'prefix {phrase} suffix'\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 3)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"_OBJ.KEY = {phrase!r}\n# {phrase}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 4)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"ONE = TWO = {phrase!r}\n# {phrase}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 4)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"ONE: str = {phrase!r}\n# {phrase}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == (
        [holder, 'doc.py', 'probe.py'], 3)


def test_only_a_bare_literal_in_the_key_holder_defines_a_key(tmp):
    """Which spellings of a key are a definition, and which are not.

    An explicit `+` is a `BinOp` and an f-string a `JoinedStr`, so
    neither is the `Constant` a definition is; each is counted beside
    the prose copy that proves no exemption was spent on it. Adjacent
    literals are folded by the parser into one `Constant` before this
    sees them, so their VALUE is the key while their written text is
    not — the closing quote between the two pieces keeps the key from
    appearing in the count at all. An exemption taken against an
    occurrence that does not exist eats a real statement instead, so
    that spelling earns none, and the prose copy below survives it.
    """
    left, right = 'a synthetic', 'probe phrase'
    phrase = f'{left} {right}'
    holder = '_coverage_authority_scan.py'
    assert holder == _KEY_HOLDER, (holder, _KEY_HOLDER)
    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f'ONE = f"{phrase}"\n# {phrase}\n', encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == ([holder], 2)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"ONE = {left!r} + {right!r}\n# {phrase}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == ([holder], 1)

    (Path(tmp) / '_coverage_authority_scan.py').write_text(
        f"ONE = {left!r} {right!r}\n# {phrase}\n", encoding='utf-8')
    assert phrase_holders(phrase, Path(tmp)) == ([holder], 1)


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
