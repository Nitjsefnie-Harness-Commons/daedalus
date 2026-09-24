"""Deferred-selection cases for the tab-routing focus harness.

Each case is ``(label, store, invoke, expected)``. ``store`` runs before the
harness rebinds ``send = ext_cmd``; ``invoke`` is the expression the guard and
the runtime both run. ``expected`` is the verdict pair ``(runtime hit, guard
report)``, or one value applied to both.

A shape the plain selection model does not claim appears as a labelled
*exclusion* row whose expected verdict is the known false green ``(True,
False)`` (runtime reaches the focus sender, the guard reads clean). Each names
the daedalus issue that tracks the excluded shape, so the row fails if the
shape starts being modelled — a disclosure that doubles as a tripwire. A
value-axis clean twin of such a shape is kept separately and labelled as a
value-axis control, never as a shape control.
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
    # A constant name the owner does not resolve to a tracked value falls back
    # to the default, and that default is the value seeded for the call.
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
    # The present-name 3-argument cell. The model records no occupancy, so a
    # present-but-untracked attribute reads as absent and the default is
    # selected: the guard reports a value the runtime never produces, with no
    # runtime send. daedalus issue 978 tracks the model change that would make
    # this cell's runtime verdict reachable.
    ('getattr-present-name-sender-default', 'class H: pass\nh = H(); '
     'h.fn = ordinary\nx = getattr(h, "fn", relay())', 'x()', (False, True)),
    ('getattr-present-name-clean-default', 'class H: pass\nh = H(); '
     'h.fn = ordinary\nx = getattr(h, "fn", ordinary)', 'x()', False),
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
    # A class owner under a dynamic name: the owner is a class object, so
    # _attribute_values reads its methods.
    ('getattr-class-dynamic', 'class H:\n    ext_cmd = relay()\n'
     'name = "ext_cmd"\nx = getattr(H, name)', 'x()', True),
    ('getattr-class-dynamic-clean', 'class H:\n    clean = ordinary\n'
     'name = "clean"\nx = getattr(H, name)', 'x()', False),
    # A dynamic name in the 3-argument form merges the selected value with the
    # default.
    ('getattr-dynamic-3arg-sender-default', 'class H: pass\nh = H(); '
     'h.ext_cmd = ordinary\nname = "ext_cmd"\nx = getattr(h, name, relay())',
     'x()', (False, True)),
    ('getattr-dynamic-3arg-clean-default', 'class H: pass\nh = H(); '
     'h.ext_cmd = ordinary\nname = "ext_cmd"\nx = getattr(h, name, ordinary)',
     'x()', False),
    # Names and arities the plain selection model does not claim.
    ('getattr-nonstring-name', 'class H: pass\nh = H(); h.fn = relay()\n'
     'try:\n    x = getattr(h, 5)\nexcept TypeError:\n    x = ordinary', 'x()',
     False),
    ('getattr-arity-four', 'class H: pass\nh = H(); h.fn = relay()\n'
     'try:\n    x = getattr(h, "fn", None, None)\nexcept TypeError:\n'
     '    x = ordinary', 'x()', False),
    # Value-axis control for the starred-argument-list shape: a clean attribute
    # stays clean. This is a value control, not the shape exclusion below.
    ('getattr-star-args-clean-value', 'class H: pass\nh = H(); '
     'h.clean = ordinary\npair = (h, "clean")\nx = getattr(*pair)', 'x()',
     False),
    # ---- Excluded shapes: known false greens, each a tripwire. -------------
    # Starred argument positions (starred argument list, starred owner,
    # starred default). Tracked by daedalus issue 977.
    ('getattr-star-arg-list (977)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\npair = (h, "ext_cmd")', 'getattr(*pair)()',
     (True, False)),
    ('getattr-star-owner (977)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\nowners = (h,)', 'getattr(*owners, "ext_cmd")()',
     (True, False)),
    ('getattr-star-default (977)', 'class H: pass\nh = H()\n'
     'vals = (relay(),)', 'getattr(h, "missing", *vals)()', (True, False)),
    # Direct-invocation form getattr(h, name)(), with no alias binding.
    # Tracked by daedalus issue 979.
    ('getattr-direct-invoke-const (979)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()', 'getattr(h, "ext_cmd")()', (True, False)),
    ('getattr-direct-invoke-dynamic (979)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\nname = "ext_cmd"', 'getattr(h, name)()',
     (True, False)),
    # Nested-selection forms, the same use-site mechanism as 979.
    ('getattr-nested-const (979)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()', '[getattr(h, "ext_cmd")][0]()', (True, False)),
    ('getattr-nested-dynamic (979)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\nname = "ext_cmd"', '[getattr(h, name)][0]()',
     (True, False)),
    # Issue 959's absent-name deferred default with the binding moved inside
    # the expression, so the seeding (which runs only on the whole RHS) never
    # reaches the getattr. Same use-site mechanism as 979.
    ('getattr-nested-absent-default (979)', 'class H: pass\nh = H()\n'
     'x = [getattr(h, "missing", relay())][0]', 'x()', (True, False)),
]
