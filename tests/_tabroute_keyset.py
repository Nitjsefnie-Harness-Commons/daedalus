"""The shared shapes and verdicts of the constant-key dict-read suites.

A `_`-prefixed module rather than a second suite's namespace: a suite
importing a sibling suite re-executes that suite's whole module body,
and these are the prefixes and fixtures both the census suite and the
retirement suite drive. Nothing here is a test.

`_verdict` and `_body` are reserved names owned by other suites, so the
shared ones are `_row_verdict` and `_row_body` and the census suite
aliases them back, rather than this module claiming a name it does not
own.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tabroute_focus import _tracked_focus_verdict  # noqa: E402

_LAMBDA = ("lambda *a, **k: send('_focus', 'focus-tab', "
           "tab=args.chrome_tab)")
_RELAY = ('def maker():\n    return ' + _LAMBDA + '\n'
          'def relay(): return maker()\n')
_PRE = f'send = ordinary\n{_RELAY}'
_CLEAN = ('send = ordinary\ndef maker():\n    return lambda *a, **k: '
          'ordinary()\ndef relay(): return maker()\n')

# The read under test binds its result, so what moves is the read's own
# verdict: an absent answer binds nothing, a joined answer binds an
# unprovable sender, and a `tab` through either is reported only for the
# second.
_READS = {'subscript': 'd["k"]', 'get': 'd.get("k")',
          'setdefault': 'd.setdefault("k")'}
_CALL = '(1, tab=args.flag)'

# A source the model cannot read as pairs.
_UNREADABLE = 'def mk():\n    return dict(zip(["k"], [relay()]))\n'

# A source the model cannot read as pairs, reached through a NAME.
_UNACCOUNTABLE = 'o = {}\no.update(zip(["k"], [relay()]))'

# A key the model recorded BEFORE a store that retires it, written back
# after it. What the model wrote there is what the runtime holds, so the
# read answers from it; the three forms differ only in how the key is made
# current again, and the third in a removal the model follows, which takes
# an entry out without writing anything anywhere.
#
# The retiring store has to STAY OPAQUE, which is the whole point of it: a
# source whose pairs its own syntax spells is read, folds its keys
# exactly, and retires nothing. `mk()` is a user call, so its source is
# never readable and the retirement is what this limb is measured against.
_OPAQUE_RETIRE = 'd = {"k": ordinary}\n' + _UNREADABLE + 'd.update(mk())\n'
_FRESH = _OPAQUE_RETIRE + '\nd["k"] = relay()'
_REFRESHED = _OPAQUE_RETIRE + '\nd.update({"k": relay()})'
_POPPED = _FRESH + '\nd.pop("j", None)'

# A store whose key the model cannot resolve may have named the key the
# container already held, but it also wrote the value it read to the
# unknown-key slot every read arm joins, so it retires nothing: the store's
# own fold already accounts for the computed key, and more precisely.
_COMPUTED = 'd = {"k": ordinary}\nd[args.values] = relay()'


def _row_body(store, read, prefix):
    return f'{prefix}{store}\nx = {read}\nsend = ext_cmd\nreturn x{_CALL}'


def _row_verdict(tmp, store, read, prefix=_PRE):
    return _tracked_focus_verdict(
        tmp, _row_body(store, read, prefix), counts=True)


# `zip` over a different key leaves the recorded value standing, and the
# control then measures a false positive where the defect lives.
_RETIRED_SOURCE = _OPAQUE_RETIRE
_DESTINATION_FOLDS = ('o = {}\no.update(d)', 'o = {}\no |= d',
                      'o = dict(d)', 'o = {**d}')
_DESTINATION_READS = {'subscript': 'o["k"]', 'get': 'o.get("k")',
                      'setdefault': 'o.setdefault("k")'}
_FOLDS = ('update', 'update-star', 'ior', 'display', 'or-value', 'dict-call')
