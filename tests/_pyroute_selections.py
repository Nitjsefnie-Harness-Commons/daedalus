"""Deferred-selection cases for the tab-routing focus harness.

Each case is ``(label, store, invoke, expected)``. ``store`` runs before the
harness rebinds ``send = ext_cmd``; ``invoke`` is the expression the guard and
the runtime both run. ``expected`` is the verdict pair, or one value applied to
both.
"""
SELECTION_PRE = ('send = ordinary\ndef maker():\n    return lambda: send('
                 '"_focus", "focus-tab", tab=args.chrome_tab)\n'
                 'def relay(): return maker()\n')
SELECTIONS = [
    ('tuple-select', 'x = (relay(), ext_cmd)[int(args.flag) - 1]',
     'x()', True),
    ('getattr-default', 'class H: pass\nh = H(); h.ext_cmd = ordinary; '
     'h.fn = relay()\nx = getattr(h, "fn", h.ext_cmd)', 'x()', True),
    ('getattr-direct', 'class H: pass\nh = H(); h.ext_cmd = relay()\n'
     'x = getattr(h, "ext_cmd")', 'x()', True),
    ('control-ordinary', 'x = relay()', 'ordinary()', False),
    ('sighting-getattr-dict', 'class C: pass\nc = C(); c.box = '
     '{"k": relay()}\nx = getattr(c, "box")', 'x["k"]()', True),
    ('sighting-control-attr', 'class C: pass\nc = C(); c.box = '
     '{"k": relay()}\nx = c.box', 'x["k"]()', True),
    ('select-constant-index', 'x = (relay(), ext_cmd)[1]', 'x()',
     (True, False)),
    ('rebound-getattr', 'getattr = lambda *a: ordinary\nclass H: pass\n'
     'h = H(); h.fn = relay()\nx = getattr(h, "fn")', 'x()', False),
    ('getattr-keywords', 'class H: pass\nh = H(); h.fn = relay()\n'
     'try:\n    x = getattr(h, "fn", None, bad=1)\nexcept TypeError:\n'
     '    x = ordinary', 'x()', False),
    # A constant name the owner does not carry falls back to the default, and
    # that default is the value the call selects.
    ('getattr-absent-default', 'class H: pass\nh = H()\n'
     'x = getattr(h, "missing", relay())', 'x()', True),
    ('getattr-absent-attr-default', 'class H: pass\nh = H(); h.fn = relay()\n'
     'x = getattr(h, "missing", h.fn)', 'x()', True),
    ('getattr-absent-sender-default', 'class H: pass\nh = H()\n'
     'x = getattr(h, "missing", ext_cmd)',
     'x("_focus", "focus-tab", tab=5)', True),
    # A default that is not a sender leaves the selection clean.
    ('getattr-absent-default-clean', 'class H: pass\nh = H(); '
     'h.clean = ordinary\nx = getattr(h, "missing", h.clean)', 'x()',
     False),
    # A name that is not a provable string constant makes the attribute
    # unknowable, so the selection is every value the owner could carry.
    ('getattr-dynamic-name', 'class H: pass\nh = H(); h.ext_cmd = relay()\n'
     'name = "ext_cmd"\nx = getattr(h, name)', 'x()', True),
    ('getattr-dynamic-attribute', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\nclass N: pass\nn = N(); n.key = "ext_cmd"\n'
     'x = getattr(h, n.key)', 'x()', True),
    ('getattr-dynamic-fstring', 'class H: pass\nh = H(); h.ext_cmd = relay()\n'
     'x = getattr(h, f"ext_cmd")', 'x()', True),
    ('getattr-dynamic-concat', 'class H: pass\nh = H(); h.ext_cmd = relay()\n'
     'x = getattr(h, "ext" + "_cmd")', 'x()', True),
    ('getattr-dynamic-walrus', 'class H: pass\nh = H(); h.ext_cmd = relay()\n'
     'x = getattr(h, (name := "ext_cmd"))', 'x()', True),
    ('getattr-dynamic-star-name', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\nclass N: pass\nn = N(); n.key = "ext_cmd"\n'
     'x = getattr(h, *[n.key])', 'x()', True),
    # The same unresolvable name over an owner carrying no sender stays clean.
    ('getattr-dynamic-name-clean', 'class H: pass\nh = H(); '
     'h.clean = ordinary\nname = "clean"\nx = getattr(h, name)', 'x()',
     False),
    # Names and shapes the plain selection model does not claim.
    ('getattr-nonstring-name', 'class H: pass\nh = H(); h.fn = relay()\n'
     'try:\n    x = getattr(h, 5)\nexcept TypeError:\n    x = ordinary', 'x()',
     False),
    ('getattr-arity-four', 'class H: pass\nh = H(); h.fn = relay()\n'
     'try:\n    x = getattr(h, "fn", None, None)\nexcept TypeError:\n'
     '    x = ordinary', 'x()', False),
    ('getattr-star-args', 'class H: pass\nh = H(); h.clean = ordinary\n'
     'args = (h, "clean")\nx = getattr(*args)', 'x()', False),
]
