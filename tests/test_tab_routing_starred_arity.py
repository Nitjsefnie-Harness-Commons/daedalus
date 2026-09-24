#!/usr/bin/env python3
"""A starred operand's arity is one fact every spelling reads."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _tabroute_selections import SELECTION_PRE  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402


def test_starred_operand_arity_is_a_single_fact(tmp):
    """A starred operand the model cannot resolve to a provable element list
    leaves the arity unprovable, so the alias is reported not clean. Every
    length-mutating spelling and the generator / yield-from operand, plus three
    boundary controls (count() reads length; an unrelated generator adds no
    carriers; an index store is length-neutral) that fail (0,1) if their
    boundary is over-broadened."""
    rows = (
        ('append', 'pair=[h]\npair.append("{a}")\nx=getattr(*pair)'),
        ('alias', 'pair=[h]\nq=pair\npair.append("{a}")\nx=getattr(*q)'),
        ('slice', 'pair=[h]\npair[1:3]=["{a}"]\nx=getattr(*pair)'),
        ('aug', 'pair=[h]\npair += ["{a}"]\nx=getattr(*pair)'),
        ('star-def', 'vals=(ordinary,)\nx=getattr(h,"{a}",*vals)'),
        ('gen', 'def g():\n    yield h\n    yield "{a}"\nx=getattr(*g())'),
        ('yield-from', 'def g():\n    yield from [h,"{a}"]\n'
         'x=getattr(*g())'),
        ('bound3', 'a=(h,)\nb=("z",)\nc=({v},)\nx=getattr(*a,*b,*c)'),
    )
    guards = (
        ('count', 'h.clean=ordinary\nk.ext_cmd=relay()\npair=[h,"clean",k]\n'
         'pair.count(h)\nx=getattr(*pair)'),
        ('unrel-gen', 'h.clean=ordinary\nk.ext_cmd=relay()\ndef o():\n'
         '    yield k\n    yield "ext_cmd"\nvals=(ordinary,)\n'
         'x=getattr(h,"clean",*vals)'),
        ('index-store', 'h.clean=ordinary\nk.ext_cmd=relay()\n'
         'pair=[h,"clean",k]\npair[0]=h\nx=getattr(*pair)'),
    )
    pre = 'class H: pass\nclass K: pass\nh = H()\nk = K()\n'

    def run(body):
        return _tracked_focus_verdict(
            tmp, SELECTION_PRE + body + '\nsend = ext_cmd\nreturn x()\n',
            counts=True)

    axes = (('ext_cmd', 'relay()', (1, 1)), ('clean', 'ordinary', (0, 0)))
    cases = [(f'{label}-{attr}', pre + f'h.{attr} = {val}\n'
              + tmpl.format(a=attr, v=val), want)
             for attr, val, want in axes for label, tmpl in rows]
    assert [(label, *run(body)) for label, body, _ in cases] == [
        (label, *want) for label, _, want in cases]
    guards = [(label, pre + body) for label, body in guards]
    assert [(label, *run(body)) for label, body in guards] == [
        (label, 0, 0) for label, _ in guards]


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='starredarity_')


if __name__ == '__main__':
    raise SystemExit(main())
