"""Deferred-selection cases for the tab-routing focus harness.

Each case is ``(label, store, invoke, expected)``. ``store`` runs before the
harness rebinds ``send = ext_cmd``; ``invoke`` is the expression the guard and
the runtime both run. ``expected`` is the verdict pair ``(runtime hit, guard
report)``, or one value applied to both.

A shape the model does not claim is a labelled *exclusion* row expecting the
known false green ``(True, False)`` (runtime reaches the focus sender, guard
reads clean). Each names the daedalus issue tracking the shape, so it fails if
the shape starts being modelled — a disclosure that doubles as a tripwire. Its
value-axis clean twin is kept separately, labelled a value control, never a
shape control.

``(False, True)`` is the opposite: the guard reports a selection the runtime
never produces, on a cell tracked by the occupancy model change (daedalus
issue 978). It pins a known false positive — runtime ``0``, guard ``1`` — and
its comment names the issue.
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
    # to the default, which is seeded as the call's value.
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
    # runtime send. The runtime verdict is 0 and always is; daedalus issue 978
    # makes the guard's verdict clean here.
    ('getattr-present-name-sender-default', 'class H: pass\nh = H(); '
     'h.fn = ordinary\nx = getattr(h, "fn", relay())', 'x()', (False, True)),
    ('getattr-present-name-clean-default', 'class H: pass\nh = H(); '
     'h.fn = ordinary\nx = getattr(h, "fn", ordinary)', 'x()', False),
    # The present-AND-tracked half: the owner carries the named attribute as a
    # clean deferred callable, so the runtime selects it and sends nothing.
    # Pins the "the default is dead" early return in _selection_value.
    ('getattr-present-tracked-default', 'def cleanrelay():\n'
     '    return lambda: ordinary()\nclass H: pass\nh = H(); '
     'h.fn = cleanrelay()\nx = getattr(h, "fn", relay())', 'x()', False),
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
    # A class owner under a dynamic name: _attribute_values reads its methods.
    ('getattr-class-dynamic', 'class H:\n    ext_cmd = relay()\n'
     'name = "ext_cmd"\nx = getattr(H, name)', 'x()', True),
    ('getattr-class-dynamic-clean', 'class H:\n    clean = ordinary\n'
     'name = "clean"\nx = getattr(H, name)', 'x()', False),
    # An alternatives owner under a dynamic name, via a pick() helper that
    # returns one of two instances. Pins _attribute_values' alternatives arm:
    # delete the arm and this row flips to (1,0).
    ('getattr-alternatives-dynamic', 'class H: pass\na = H(); '
     'a.ext_cmd = relay()\nb = H(); b.ext_cmd = relay()\n'
     'def pick(flag):\n    return a if flag else b\n'
     'owner = pick(int(args.flag))\nname = "ext_cmd"\n'
     'x = getattr(owner, name)', 'x()', True),
    ('getattr-alternatives-dynamic-clean', 'class H: pass\na = H(); '
     'a.clean = ordinary\nb = H(); b.clean = ordinary\n'
     'def pick(flag):\n    return a if flag else b\n'
     'owner = pick(int(args.flag))\nname = "clean"\n'
     'x = getattr(owner, name)', 'x()', False),
    # A dynamic name in the 3-argument form merges the selected value with the
    # default. When the owner carries the named attribute as an untracked
    # value, runtime returns it and the guard reports the default: the same
    # observable false positive as the present-name cell, a different route,
    # tracked by the same daedalus issue 978.
    ('getattr-dynamic-3arg-sender-default', 'class H: pass\nh = H(); '
     'h.ext_cmd = ordinary\nname = "ext_cmd"\nx = getattr(h, name, relay())',
     'x()', (False, True)),
    ('getattr-dynamic-3arg-clean-default', 'class H: pass\nh = H(); '
     'h.ext_cmd = ordinary\nname = "ext_cmd"\nx = getattr(h, name, ordinary)',
     'x()', False),
    # The class-owner form of that same false-positive cell: an untracked
    # method, a dynamic name, a sender-bearing default. Also daedalus 978.
    ('getattr-class-3arg-sender-default', 'class H:\n    fn = ordinary\n'
     'name = "fn"\nx = getattr(H, name, relay())', 'x()', (False, True)),
    ('getattr-class-3arg-clean-default', 'class H:\n    fn = ordinary\n'
     'name = "fn"\nx = getattr(H, name, ordinary)', 'x()', False),
    # Names and arities the plain selection model does not claim.
    ('getattr-nonstring-name', 'class H: pass\nh = H(); h.fn = relay()\n'
     'try:\n    x = getattr(h, 5)\nexcept TypeError:\n    x = ordinary', 'x()',
     False),
    ('getattr-arity-four', 'class H: pass\nh = H(); h.fn = relay()\n'
     'try:\n    x = getattr(h, "fn", None, None)\nexcept TypeError:\n'
     '    x = ordinary', 'x()', False),
    # Value-axis control for the starred-argument-list shape: a clean attribute
    # stays clean. A value control, never a shape control.
    ('getattr-star-args-clean-value', 'class H: pass\nh = H(); '
     'h.clean = ordinary\npair = (h, "clean")\nx = getattr(*pair)', 'x()',
     False),
    # Starred argument positions (list, owner, default): each operand is
    # spliced into the plain selection. Tracked by daedalus issue 977.
    ('getattr-star-arg-list (977)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\npair = (h, "ext_cmd")\nx = getattr(*pair)',
     'x()', True),
    ('getattr-star-owner (977)', 'class H: pass\nh = H(); '
     'h.ext_cmd = relay()\nowners = (h,)\nx = getattr(*owners, "ext_cmd")',
     'x()', True),
    ('getattr-star-default (977)', 'class H: pass\nh = H()\n'
     'vals = (relay(),)\nx = getattr(h, "missing", *vals)', 'x()', True),
    # ---- Excluded shapes: each a labelled tripwire. -----------------------
    # Direct-invocation form, no alias binding. Tracked by daedalus issue 979.
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
    # Issue 959's shape with the binding moved inside the expression, so the
    # seeding (whole-RHS only) never reaches the getattr. Same mechanism as
    # 979.
    ('getattr-nested-absent-default (979)', 'class H: pass\nh = H()\n'
     'x = [getattr(h, "missing", relay())][0]', 'x()', (True, False)),
]
