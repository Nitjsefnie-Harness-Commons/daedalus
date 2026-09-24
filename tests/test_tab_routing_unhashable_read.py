#!/usr/bin/env python3
"""A key the runtime cannot hash makes every mapping read unprovable.

The setdefault arm carries the discriminator that names one, and a store's
setdefault is where it was declared. The reads were silent, so a send bound
to `d.get(key, ordinary)`, `d.get(key)`, `d.pop(key, None)` or `d[key]`
read clean on a key the runtime raises on before the call returns.

Every row here runs the whole guard over a written module and reads the
verdict AT THE CALL, because a stored value that never reaches the call
cannot tell a report from a silent merge. The negative space is pinned in
the same module: a key the guard cannot fold but the runtime CAN hash — an
unbound name, a concatenation, an f-string — must keep reading exactly as
it reads today, or the discriminator has been widened rather than reused.

The starred arm of a display value is not a read at all: a starred operand
in a sequence literal carries a value, not a key, so the discriminator's
term must stay off that arm too.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402

# A key the runtime cannot hash, spelled the way a program writes it: bound
# to a name first. A tuple OF a list is the row a bare tuple spelling cannot
# supply, because `key = (1, 2)` hashes and is not the same key.
_UNHASHABLE = [
    ('unhashable-list', 'key = [1]'),
    ('unhashable-tuple-of-list', 'key = ([1],)'),
    ('unhashable-dict', 'key = {"a": 1}'),
    ('unhashable-set', 'key = {1, 2}'),
]

# The four read forms plus the setdefault that declared the verdict. The
# two-argument get and the pop are one arm of the resolver; the subscript
# and the one-argument get are the others.
_READ_FORMS = [
    ('get', 'd.get(key, ordinary)'),
    ('get-one-arg', 'd.get(key)'),
    ('pop', 'd.pop(key, None)'),
    ('subscript', 'd[key]'),
    ('setdefault', 'd.setdefault(key, ordinary)'),
]

# Hashable but unresolvable: the guard cannot fold any of them and the
# program routes all of them, so the discriminator must stay silent. The
# setdefault rows are the control the change is measured against.
_NEGATIVE = [
    ('unbound-name', ''),
    ('concat', 'key = "k" + ""'),
    ('fstring', 'key = f"k"'),
]

# The starred-positional merge of a sequence literal is a value, not a key:
# an unhashable value reaches it and reads exactly as it reads today, or
# the discriminator's term has been over-applied to the starred arm. A set
# is the row that reaches that arm at all — a list or a dict literal folds
# to a deferred container and is merged by position instead.
_STARRED = [
    ('starred-unhashable-set', 'key = {1, 2}', '[*key]'),
]

# The same unhashable keys written inline at the call, where no name
# carries the literal, plus the inline hashable controls.
_INLINE = [
    ('inline-list', 'd.get([1], ordinary)', True),
    ('inline-tuple-of-list', 'd.get(([1],), ordinary)', True),
    ('inline-dict', 'd.get({"a": 1}, ordinary)', True),
    ('inline-set', 'd.get({1, 2}, ordinary)', True),
    ('inline-subscript-list', 'd[[1]]', True),
    ('inline-concat', 'd.get("k" + "", ordinary)', False),
    ('inline-fstring', 'd.get(f"k", ordinary)', False),
]

_MODULE = ('def ordinary(*a, **k):\n'
           '    return 0\n'
           'def probe():\n'
           '    d = {"k": 1}\n')


def _reported(tmp, label, binding, expression):
    """Whether the guard reports the call the read feeds."""
    source = Path(tmp) / f'{label}.py'
    setup = f'    {binding}\n' if binding else ''
    source.write_text(
        f'{_MODULE}{setup}'
        f'    send = {expression}\n'
        '    return send("_focus", "focus-tab", tab=5)\n',
        encoding='utf-8')
    return bool(py_tab_routing_violations(source, source.name))


def _rows(keys, forms):
    return [(f'{key}-{form}', binding, expression)
            for key, binding in keys for form, expression in forms]


def test_every_read_form_of_an_unhashable_key_is_unprovable(tmp):
    quiet = [label for label, binding, expression in _rows(
        _UNHASHABLE, _READ_FORMS) if not _reported(
            tmp, label, binding, expression)]
    assert not quiet, quiet


def test_unhashable_key_written_inline_at_the_call_is_unprovable(tmp):
    wrong = [(label, _reported(tmp, label, '', expression), expected)
             for label, expression, expected in _INLINE]
    assert [row for row in wrong if row[1] is not row[2]] == [], wrong


def test_a_hashable_unresolvable_key_still_reads_clean(tmp):
    dirty = [label for label, binding, expression in _rows(
        _NEGATIVE, _READ_FORMS) if _reported(
            tmp, label, binding, expression)]
    assert not dirty, dirty


def test_a_starred_unhashable_operand_still_reads_clean(tmp):
    dirty = [label for label, binding, expression in _STARRED if _reported(
        tmp, label, binding, expression)]
    assert not dirty, dirty


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='unhashread_')


if __name__ == '__main__':
    raise SystemExit(main())
