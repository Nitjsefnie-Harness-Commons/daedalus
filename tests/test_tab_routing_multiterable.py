#!/usr/bin/env python3
"""A multi-iterable builtin consumer pairs what it walks, or reads clean.

`zip`, `enumerate` and `map` reach a routed value through a pairing rather
than through one operand. Before the arm, the operand funnel reduced each
of them to the merge of what the expression yields, a merge is not a tuple,
a destructuring target read nothing, and the value behind it stayed
invisible: a comprehension over any of the three read CLEAN while the call
really reached the focus sender with `tab`.

Every verdict here is `_tracked_focus_verdict`'s `(runtime_calls,
guard_violations)` on the real `do_focus_tab`, so the guard and the runtime
are both measured and no row is a verdict about an internal helper. Two
operands are used for every consumer: a mapping read-back (`d.values()`,
whose container the guard already models) and a plain list (`l`, whose
positions are statically known) -- the second is the row that rules out the
materializer reading, because a list leaves nothing about order or occupancy
unresolvable and the only thing missing there is the PAIRING.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_multiter import (  # noqa: E402
    INDEXED_PAIRING, MULTI_ITERABLE_DECLARATIONS, POSITIONAL_PAIRING,
    PROJECTION)
from _tabroute_focus import _tracked_focus_verdict  # noqa: E402

_PREAMBLE = (
    'send = ordinary\n'
    'def maker():\n'
    '    return lambda: send("_focus", "focus-tab", tab=args.chrome_tab)\n'
    'def relay(): return maker()\n')
_SEND = 'send = ext_cmd\n'
_DICT = 'd = {"k": relay()}'
_LIST = 'l = [relay()]'
_PLAIN = 'p = [relay()]\nq = [ordinary]'
_SENDER = 'd = {"k": ext_cmd}'
_CALL = 'lambda: send("_focus", "focus-tab", tab=args.chrome_tab)'
_CALLS = f'l = [{_CALL}]'
_PLAIN_CALLS = f'p = [ordinary]\nq = [{_CALL}]'
# The uncertainty token reaches a finding through a call that carries `tab`
# itself: `call_violations` reads an unprovable callee as "this may be
# ext_cmd", not as a send. So the rows that pin the token call the element
# with a `tab` -- the routed lambda takes none, and `ext_cmd` takes one,
# which is the element the runtime hands back in those rows.
_ROUTED_CALL = "v('focus', tab=1)"
_PROJECTED_CALL = "f('focus', tab=1)"
_TAB_ARG = "('focus', tab=1)"


def _body(store, invoke):
    return _PREAMBLE + store + '\n' + _SEND + invoke


def _sources(tmp, cases):
    observed = [(label, *_tracked_focus_verdict(tmp, source, counts=True))
                for label, source, _ in cases]
    expected = [(label, *value) for label, _, value in cases]
    assert observed == expected, observed


def _verdicts(tmp, cases):
    _sources(tmp, [(label, _body(store, invoke), value)
                   for label, store, invoke, value in cases])


# What the running interpreter's own builtin accepts, measured rather than
# written down: `map` took no `strict` before 3.14, and a spelling the
# runtime refuses is a call that raises before the guard reaches a verdict,
# so there is no runtime case to measure and no row is generated for it. The
# grammar decides the enumeration on the interpreter the rows run on, rather
# than a table of the version this suite was written on.
_PROBE = {'zip': '(), ()', 'enumerate': '()', 'map': 'lambda g: g, ()'}


def _accepts(member, keyword):
    """Whether this interpreter's own builtin takes that keyword, asked by
    constructing the call rather than by a version table. `_PROBE` is the
    operand list each declaration needs before the keyword, because `map`'s
    callable is the first operand and a probe that omitted it would be asking
    about a different call."""
    spelling = f'{member}({_PROBE[member]}, {keyword}=True)'
    try:
        # pylint: disable-next=eval-used
        eval(spelling)
    except TypeError:
        return False
    return True


def _streams(declaration, container):
    """The operands a declaration pairs over one container shape. A pairing
    takes two streams so that a routed value lands in a position the target
    reads; an indexed pairing iterates one and reads a base beside it, and a
    projection pairs that one with the callable it projects through."""
    if container == 'dict':
        return _DICT, ('d, d.values()' if declaration is POSITIONAL_PAIRING
                       else 'd.values()')
    return _LIST, ('l, l' if declaration is POSITIONAL_PAIRING else 'l')


def _call_shape(declaration, member, container, keyword):
    """The call as the source spells it over one operand shape, and the
    store that builds the operand. A projection's callable is the identity,
    so the row pairs the arm and not the callable -- the row that pins the
    difference between the projection and the operand element is its own."""
    store, streams = _streams(declaration, container)
    spell = (f'{member}(lambda g: g, {streams}'
             if declaration is PROJECTION else f'{member}({streams}')
    if keyword:
        spell += f', {keyword}=True'
    spell += ')'
    if declaration is PROJECTION:
        return store, f'return [f() for f in {spell}]'
    return store, f'return [v() for _, v in {spell}]'


def _grammar_battery():
    """Every (declaration, member, operand, keyword) the enumeration admits
    on the interpreter this runs on, enumerated from the declarations
    themselves rather than from a list of names written out here. What a
    declaration reads is its own, so a member added to a kind is read by the
    same two functions and covered by the same rows; the count this returns
    is what the run measured, and the coverage assertion in the test is what
    says a member cannot be added without being run."""
    return [(declaration, member, container, keyword)
            for declaration in MULTI_ITERABLE_DECLARATIONS
            for member in sorted(declaration.members)
            for container in ('dict', 'list')
            for keyword in ('', *sorted(
                word for word in declaration.keywords
                if _accepts(member, word)))]


def test_every_enumerated_consumer_is_covered(tmp):
    """The battery, run as one set. Every row puts a routed value in a
    position its consumer pairs and calls it, so every one of them makes its
    call at runtime. The count is derived from the declarations and the
    running builtin, and the coverage assertion is what makes it a claim
    about every member rather than about the ones the repro used."""
    battery = _grammar_battery()
    cases = []
    for declaration, member, container, keyword in battery:
        store, invoke = _call_shape(declaration, member, container, keyword)
        cases.append((f'{member}-{container}-{keyword or "plain"}',
                      _body(store, invoke), (1, 1)))
    assert {(declaration, member) for declaration, member, _, _
            in battery} == {(declaration, member)
                            for declaration in MULTI_ITERABLE_DECLARATIONS
                            for member in declaration.members}
    covered = {(declaration, member) for declaration, member, _, _ in battery}
    print(f'\n  multi-iterable battery: {len(cases)} rows over '
          f'{len(covered)} enumerated consumers on '
          f'{sys.version_info.major}.{sys.version_info.minor}')
    _sources(tmp, cases)


def test_the_two_operands_pin_the_pairing_and_not_the_container(tmp):
    """The rows that rule out the materializer reading. A plain list's
    positions are statically known and nothing about its order or occupancy
    is unresolvable, so a consumer over one is clean only when the PAIRING
    is missing. The mapping read-back is the same pairing over a container
    the guard already models, and the read-back on its own is already
    correct -- these are the rows a widening would break."""
    cases = [
        ('zip-dict', _DICT, 'return [v() for _, v in zip(d, d.values())]',
         (1, 1)),
        ('zip-list', _LIST, 'return [v() for _, v in zip(l, l)]', (1, 1)),
        ('enumerate-dict', _DICT,
         'return [v() for _, v in enumerate(d.values())]', (1, 1)),
        ('enumerate-list', _LIST, 'return [v() for _, v in enumerate(l)]',
         (1, 1)),
        ('map-dict', _DICT, 'return [f() for f in map(lambda g: g, '
         'd.values())]', (1, 1)),
        ('map-list', _LIST, 'return [f() for f in map(lambda g: g, l)]',
         (1, 1)),
        ('map-two-streams', 'l = [relay()]\nm = [ordinary]',
         'return list(map(lambda a, b: a(), l, m))', (1, 1)),
        ('preserved-values', _DICT, 'return [v() for v in d.values()]',
         (1, 1)),
        ('preserved-items', _DICT, 'return [v() for _, v in d.items()]',
         (1, 1)),
        ('preserved-list-materializer', _LIST,
         'return [v() for v in list(l)]', (1, 1)),
        ('preserved-tuple-literal', 'p = (relay(), ordinary)',
         'return [v() for v in p]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_the_projection_is_the_result_not_the_operand_element(tmp):
    """`map` yields what its callable returns, not the operand element. The
    identity projection hands the element on, so the routed call happens; a
    projection that returns something else leaves the element clean even
    though the operand carried the routed value, and that row is the one
    that fails if the arm read the operand element through the projection.
    A projection the model cannot resolve is a value it cannot place, which
    fails closed rather than reading as the element it cannot see."""
    cases = [
        ('identity', _DICT, 'return [f() for f in map(lambda g: g, '
         'd.values())]', (1, 1)),
        ('dropping', _DICT, 'return [f() for f in map(lambda g: ordinary, '
         'd.values())]', (0, 0)),
        ('sent-inside-dict', _DICT,
         'return list(map(lambda f: f(), d.values()))', (1, 1)),
        ('sent-inside-list', _LIST, 'return list(map(lambda f: f(), l))',
         (1, 1)),
        ('send-inside-a-walked-result', _LIST,
         'return [f for f in map(lambda f: f(), l)]', (1, 1)),
        ('unresolved-projection', 'class P:\n'
         '    def __call__(self, g):\n        return g\n' + _SENDER,
         f'return [{_PROJECTED_CALL} for f in map(P(), d.values())]',
         (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_the_keywords_the_enumeration_admits(tmp):
    """`zip`'s `strict` and `enumerate`'s `start` are spellings of what the
    declaration decides, and a reader that took every positional operand as
    an iterable would read `enumerate`'s base as a second stream -- so the
    `start` rows carry the routed value in exactly one operand and would
    read two streams if the base were one. A keyword the running builtin
    refuses is skipped rather than spelled: the call raises before the guard
    reaches a verdict, so there is nothing to measure on that interpreter."""
    cases = [
        ('zip-strict', _LIST,
         'return [v() for _, v in zip(l, l, strict=True)]', (1, 1)),
        ('zip-strict-false', _LIST,
         'return [v() for _, v in zip(l, l, strict=False)]', (1, 1)),
        ('zip-strict-matches', _LIST,
         'return [v() for _, v in zip(l, [relay()], strict=True)]', (1, 1)),
        ('enumerate-start', _LIST,
         'return [v() for _, v in enumerate(l, 1)]', (1, 1)),
        ('enumerate-start-keyword', _LIST,
         'return [v() for _, v in enumerate(l, start=1)]', (1, 1)),
        ('enumerate-start-empty', _LIST,
         'return [v() for _, v in enumerate([], 1)]', (0, 0)),
        ('enumerate-base-is-not-a-stream', _LIST,
         'return [v() for _, v in enumerate(l, args.chrome_tab)]', (1, 1)),
    ]
    if _accepts('map', 'strict'):
        cases.append(('map-strict', _LIST,
                      'return [f() for f in map(lambda g: g, l, strict=True)]',
                      (1, 1)))
    _verdicts(tmp, cases)


def test_an_undecided_arity_fails_closed(tmp):
    """A `**` keyword unpacking and a starred positional operand each decide
    how many streams a step pairs over, and the guard can read neither. The
    element is then undecided at EVERY position rather than at the one it
    would have guessed, so a target that carries `tab` through it is
    reported -- the token reaches the caller as an unprovable callee, which
    is what `call_violations` reads it as. The last row is the same pair
    with a decided arity, so the two cannot both pass by accident."""
    cases = [
        ('zip-starred-operand', _SENDER,
         f'return [{_ROUTED_CALL} for _, v in zip(*[d, d.values()])]', (1, 1)),
        ('zip-keyword-unpacking', _SENDER + '\nkw = {"strict": True}',
         f'return [{_ROUTED_CALL} for _, v in zip(d, d.values(), **kw)]',
         (1, 1)),
        ('map-starred-operand', _SENDER,
         f'return [{_PROJECTED_CALL} for f in '
         'map(lambda g: g, *[d.values()])]',
         (1, 1)),
        ('decided-arity-control', _SENDER,
         f'return [{_ROUTED_CALL} for _, v in zip(d, d.values())]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_an_operand_the_model_cannot_read_still_pairs(tmp):
    """A value the model HOLDS but cannot PAIR must never read clean. An
    operand it holds no container for is not an empty stream: the consumer
    still pairs it, so the tuple still reaches the target, and the position
    the model cannot read carries the uncertainty token. The routed operand
    beside it is what the target reads, and the row where the TARGET is the
    undecided position is the one that says the token reached it."""
    cases = [
        ('zip-unread-next-to-routed', _DICT,
         'return [v() for _, v in zip(args.values, d.values())]', (1, 1)),
        ('zip-unread-alone', _LIST,
         'return [x for _, x in zip(args.values, [ordinary])]', (0, 0)),
        ('zip-unread-alone-routed-through', _SENDER,
         f'return [{_ROUTED_CALL} for _, v in zip(args.values, [ext_cmd])]',
         (1, 1)),
        # The target IS the undecided position here, which is the only row
        # that says the token reached the caller rather than merely sitting
        # beside a routed value. `globals().values()` is a call the guard
        # reads no route into -- no module path, no builtin receiver -- so
        # the operand is one it holds no stream for at all.
        ('zip-unread-is-the-called-position', '',
         f'return [{_ROUTED_CALL} for v, _ in zip(globals().values(), '
         '[ordinary])]', (1, 1)),
        ('zip-unread-is-the-called-position-quiet', '',
         'return [x for x in globals().values()]', (0, 0)),
        ('enumerate-unread-alone', _LIST,
         'return [x for _, x in enumerate(args.values)]', (0, 0)),
        ('map-unread-alone', _LIST,
         'return [x for x in map(lambda g: g, args.values)]', (0, 0)),
        ('map-unread-next-to-routed', _DICT,
         'return [f() for f in map(lambda g: g, d.values())]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_a_starred_or_unpacked_operand_is_read_not_substituted(tmp):
    """A star and a `**` decide how many streams a step pairs over, and the
    arm READS them rather than substituting a token for the whole element.
    Substitution is the fail-open the arm exists to remove: the runtime pairs
    the container's ELEMENTS, and a token is not a tuple, so a destructuring
    target paired nothing and the call through it read clean. Each undecided
    row has a decided twin beside it, so the two cannot both pass by
    accident."""
    cases = [
        ('zip-starred', _LIST,
         'return [v() for _, v in zip(*[l, l])]', (1, 1)),
        ('zip-decided-twin', _LIST,
         'return [v() for _, v in zip(l, l)]', (1, 1)),
        ('map-starred', _LIST,
         'return [f() for f in map(lambda g: g, *[l])]', (1, 1)),
        ('map-decided-twin', _LIST,
         'return [f() for f in map(lambda g: g, l)]', (1, 1)),
        ('enumerate-starred', _LIST,
         'return [v() for _, v in enumerate(*[l])]', (1, 1)),
        ('enumerate-decided-twin', _LIST,
         'return [v() for _, v in enumerate(l)]', (1, 1)),
        ('zip-kwargs-empty', _LIST + '\nkw = {}',
         'return [v() for _, v in zip(l, l, **kw)]', (1, 1)),
        # A `**` whose value is not a container of ITERABLES is the ordinary
        # case -- `**{"strict": True}` passes a KEYWORD -- so the count is
        # undecided rather than guessed, and the operand the call spelled
        # plainly is still read.
        ('zip-kwargs-keyword', _LIST + '\nkw = {"strict": True}',
         'return [v() for _, v in zip(l, l, **kw)]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_a_generator_operand_the_model_cannot_read_says_so(tmp):
    """A generator the model tracks but whose yielded value it does not hold
    is a stream it cannot READ, and it carries the token the way any other
    unreadable operand does. Reading it as an empty stream instead left no
    token and no positions, which is the same fail-open at a second site.
    The quiet row is the twin: the token is there, and nothing calls it."""
    cases = [
        ('genexp-unread-tab', '',
         f'return [v{_TAB_ARG} for v, _ in zip((x for x in '
         'globals().values()), [ordinary])]', (1, 1)),
        ('genexp-unread-quiet', '',
         'return [x for x, _ in zip((x for x in globals().values()),'
         ' [ordinary])]', (0, 0)),
        ('genexp-decided-tab', 'd = {"k": ext_cmd}',
         f'return [v{_TAB_ARG} for v, _ in zip((x for x in [d["k"]]),'
         ' [ordinary])]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_a_decidable_generator_expression_is_not_a_false_positive(tmp):
    """The negative direction, and it is a control rather than a plant. A
    generator expression the model CAN decide states how many steps it
    yields, and a stated zero is a pairing that reaches no step at all: a
    routed value sitting in the operand BESIDE it is paired with nothing,
    so the `tab` the target carries never reaches a sender. The arm is
    fail-closed on an operand it cannot decide, and that must not turn a
    generator the model reads into an over-report the moment a new consumer
    is in front of it.

    Every target here CALLS the element it reads, with a `tab`, because
    that call is the only way a token in the position becomes a finding. A
    row whose body never calls its element reads clean whatever the model
    holds there, which is why two maximal fail-closed mutants -- every
    generator undecided, and every decided pairing also carrying the token
    -- left the four earlier spellings of this test at `(0, 0)` and
    passing. The decision these rows pin is the COUNT, so each of them dies
    the moment the count is thrown away, and the mutant table quotes it.

    The value a decidable generator YIELDS is a separate question this test
    does not claim: the model does not hold it, so a `tab` passed through
    one already reads as a finding whatever the count is. Pinning that as
    the contract would be asserting a defect, so the rows here are the ones
    whose correct verdict is clean."""
    _CALL = 'v("focus", tab="1")'
    cases = [
        ('zip-genexp-decided-empty', '',
         f'return [{_CALL} for _, v in zip((x for x in []), [ext_cmd])]',
         (0, 0)),
        ('enumerate-genexp-decided-empty', '',
         f'return [{_CALL} for _, v in enumerate(x for x in [])]', (0, 0)),
        ('map-genexp-decided-empty', '',
         f'return [{_CALL} for v in map(lambda f: f, (x for x in []))]',
         (0, 0)),
        # The same decided count beside a mapping the model holds a routed
        # value in. The routing is real and the pairing still reaches no
        # step, so a reader that kept the count would report nothing; one
        # that could not would report the routed value beside it.
        ('zip-genexp-empty-beside-routed', 'd = {"k": ext_cmd}',
         f'return [{_CALL} for _, v in zip((x for x in []), [d["k"]])]',
         (0, 0)),
    ]
    _verdicts(tmp, cases)


def test_a_star_the_model_cannot_read_leaves_no_position_decided(tmp):
    """A starred operand is a count of streams, and a container the model
    cannot read is a count it cannot name. Nothing at or after such a star
    is placeable -- the operands spelled after it sit at positions that
    count decides -- so the step is undecided at EVERY position and the
    target reads the uncertainty token wherever it looks.

    Reading it as one more stream at one more position is the fail-open in
    its purest form: the tuple the model builds claims a length the runtime
    never pairs over, a destructuring target reads a different number of
    names from it, the pair is REFUSED, and the names are left bound to
    nothing at all. The routed call then reads clean while the runtime
    really reached the sender.

    The first two rows are the two entry points of that one mechanism and
    they are both here because removing the early return that stood between
    them is only a real repair if a plant shows BOTH classes appear. The
    third source is a generator expression, which the runtime also builds
    extra streams from and which the model cannot read either."""
    _CALL_ALL = 'a("focus", tab=1)'
    _STORE = ('def pair(*items): return list(items)\n'
              'O = [ext_cmd]\nP = [ordinary]')
    cases = [
        # The star stands BEFORE an operand the model did read, so that
        # operand's own position is one the unreadable count decides.
        ('zip-star-before-plain', _STORE,
         f'return [{_CALL_ALL} for a, b, c in zip(*pair(O, O), P)]', (1, 1)),
        # The star is the only operand, so there is no placed stream beside
        # it: the early return that fired on an empty operand list is the
        # only thing that stood between this row and a verdict.
        ('zip-star-alone', _STORE,
         f'return [{_CALL_ALL} for a, b in zip(*pair(O, O))]', (1, 1)),
        ('map-star-before-plain', _STORE,
         f'return [{_CALL_ALL} for a in '
         'map(lambda a, b, c: a, *pair(O, O), P)]', (1, 1)),
        ('map-star-alone', _STORE,
         f'return [{_CALL_ALL} for a in map(lambda a, b: a, '
         '*pair(O, O))]', (1, 1)),
        # A generator expression is the third way the runtime builds the
        # extra streams, and the model reads it no better than a call.
        ('zip-genexp-star-alone', _STORE,
         f'return [{_CALL_ALL} for a, b in zip(*(q for q in [O, O]))]',
         (1, 1)),
        ('map-genexp-star-before-plain', _STORE,
         f'return [{_CALL_ALL} for a in '
         'map(lambda a, b, c: a, *(q for q in [O, O]), P)]', (1, 1)),
        # The decided twin beside the undecided rows, so the two cannot both
        # pass by accident: a star over a container the model DOES read is
        # placed, and this is where it is placed.
        ('decided-twin', 'l = [relay()]',
         'return [v() for _, v in zip(*[l, l])]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_a_star_container_is_read_by_what_it_holds_not_by_its_kind(tmp):
    """One character of container KIND separates a correct verdict from a
    false green, and nothing in the arm's own vocabulary says it should.

    A star unpacks a CONTAINER OF ITERABLES, so what the model needs is to
    enumerate that container's elements and say which element is at which
    position. A generator expression states both -- how many steps it
    yields, and what each step yields -- so it IS a container of that many
    iterables, and the arm reads a list display and a call without
    difficulty while a generator expression fell through to the declined
    path. The runtime reached the routed sender and the guard read clean.

    A generator expression consumed DIRECTLY is read correctly and always
    was, so this is not a generator the model cannot follow. It is a
    generator in the position this arm adds, which is what makes it the
    arm's own defect rather than a gap in the guard.

    The three list-display twins are the control that keeps the fix honest:
    the same operand list over a container the arm already read must not
    change verdict, and the clean twin must stay clean, or a widening that
    reads more containers has simply started reporting more."""
    _CALL_ALL = 'a("focus", tab=1)'
    _BARE = 'a()'
    # Two spellings of the same routed operand, and both are here because
    # neither alone tells reading from declining. A `tab`-carrying call
    # through the uncertainty token is a finding, so a ROUTED element reads
    # `(1, 1)` whether the container was read or declined -- those rows pin
    # the sound direction and cannot see this defect. A routed lambda takes
    # no arguments, so a BARE call is the only shape in which declining
    # reads clean: those rows are the false greens, and the clean twin is
    # the row that says reading did not cost precision.
    _ROUTED = 'l = [relay()]'
    _SENDER = 'l = [ext_cmd]'
    cases = [
        # The false-green class: a bare call through a position the model
        # declined to place reads clean while the runtime called the
        # sender. Four container spellings, one per consumer placement.
        ('zip-star-container-genexp', _ROUTED,
         f'return [{_BARE} for a, b in zip(*(x for x in [l, l]))]', (1, 1)),
        ('zip-star-after-plain-genexp', _ROUTED,
         f'return [{_BARE} for a, b in zip(l, *(x for x in [l]))]', (1, 1)),
        ('map-star-container-genexp', _ROUTED,
         f'return [{_BARE} for a in map(lambda g: g, *(x for x in [l]))]',
         (1, 1)),
        ('enumerate-star-container-genexp', _ROUTED,
         f'return [{_BARE} for _, a in enumerate(*(x for x in [l]))]',
         (1, 1)),
        # The same operand lists over a container the arm ALREADY read. If
        # these were to move, the fix changed a list display.
        ('zip-list-container-bare', _ROUTED,
         f'return [{_BARE} for a, b in zip(*[l, l])]', (1, 1)),
        ('zip-list-after-plain-bare', _ROUTED,
         f'return [{_BARE} for a, b in zip(l, *[l])]', (1, 1)),
        ('map-list-container-bare', _ROUTED,
         f'return [{_BARE} for a in map(lambda g: g, *[l])]', (1, 1)),
        # The clean twin, and the only row here that separates reading from
        # declining: a declined container puts the token at every position,
        # so a `tab` through a clean element is reported and this goes red.
        ('genexp-container-clean', 'def h(*a, **k): return 0\nq = [h]',
         'return [a("focus", tab="1") for a, b in zip(*(x for x in [q, q]))]',
         (0, 0)),
        # The sound direction, over a `tab`-carrying target: a routed
        # element read through a generator-expression container is a
        # finding whether the container was read or declined, and these say
        # so for all three consumers.
        ('zip-star-container-genexp-tab', _SENDER,
         f'return [{_CALL_ALL} for a, b in zip(*(x for x in [l, l]))]',
         (1, 1)),
        ('map-star-container-genexp-tab', _SENDER,
         f'return [{_CALL_ALL} for a in map(lambda g: g, *(x for x in [l]))]',
         (1, 1)),
        ('enumerate-star-container-genexp-tab', _SENDER,
         f'return [{_CALL_ALL} for _, a in enumerate(*(x for x in [l]))]',
         (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_a_readable_star_expands_where_the_source_spells_it(tmp):
    """`f(a, *b, c)` reaches `f` as `(a, *b, c)`: a star expands IN PLACE, so
    the streams its container holds sit between the operands spelled before
    it and the operands spelled after it. A reconstruction that appends them
    at the end models a different call, and it models it wrongly in the
    direction that reads clean -- the routed value is placed at a position
    the runtime never gives it, and the position the runtime does give it
    holds something else.

    The container here is a list display, so every row is one the model can
    read: the count is stated and only the ORDER is the decision. That is
    what separates it from the unreadable star beside it, which cannot state
    a count at all. The quiet row is the twin that says the ordering does not
    cost precision on a star whose streams are clean."""
    _CALL_ALL = 'a("focus", tab=1)'
    cases = [
        # The leading star's streams come FIRST, so the routed value is at
        # tuple position 0 -- which is where the runtime puts it and where
        # the target reads.
        ('zip-leading-star', 'O = [ext_cmd]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a, b, c in zip(*[O, O], P)]', (1, 1)),
        # A projection reads the end it names, so a leading star is visible
        # through the callable's FIRST parameter as well.
        ('map-leading-star', 'O = [ext_cmd]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a in '
         'map(lambda a, b, c: a, *[O, O], P)]', (1, 1)),
        # A TRAILING star is where appending happens to agree with the
        # runtime, so it is the row that keeps the two apart: the plain
        # operand is at position 0 under both readings, and the runtime
        # really does hand the target a clean value there.
        ('zip-trailing-star', 'O = [ext_cmd]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a, b, c in zip(P, *[O, O])]', (0, 0)),
        # The quiet twin: every stream clean, so the correct verdict is
        # clean and an ordering that invented a routed position would turn
        # this row red.
        ('zip-leading-star-quiet', 'Q = [ordinary]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a, b, c in zip(*[Q, Q], P)]', (0, 0)),
    ]
    _verdicts(tmp, cases)


def test_a_position_the_runtime_cannot_reach_stays_clean(tmp):
    """The twin of every routing row. The deferred value is held and paired
    in each of the first three -- the target binds it and the guard knows
    what it is -- and the runtime calls none of them, so the guard reports
    none. The arm decides what a position HOLDS; whether it is reached is
    the caller's, and a guard that read the holding as the reaching would
    fail every one of these."""
    cases = [
        ('zip-not-called', _LIST, 'for _, v in zip(l, l):\n    ordinary()',
         (0, 0)),
        ('enumerate-not-called', _LIST,
         'for _, v in enumerate(l):\n    ordinary()', (0, 0)),
        ('map-not-called', _LIST,
         'for f in map(lambda g: g, l):\n    ordinary()', (0, 0)),
        ('zip-empty-operand', _LIST, 'return [v() for _, v in zip(l, [])]',
         (0, 0)),
        ('enumerate-empty-operand', _LIST,
         'return [v() for _, v in enumerate([])]', (0, 0)),
        ('map-empty-operand', _LIST,
         'return [f() for f in map(lambda g: g, [])]', (0, 0)),
        ('zip-no-operand', _LIST, 'return [v() for _, v in zip()]', (0, 0)),
    ]
    _verdicts(tmp, cases)


def test_a_shadowed_builtin_is_not_the_builtin(tmp):
    """The shadow decides the pairing at runtime, not the spelling that
    matched. Each of the first three is a false-positive pin: the shadow's
    own element is clean, and a reader that took the name for the builtin
    would pair the operand through it and report a send the runtime never
    made. Each of the last three pins the correct contract for a shadow
    whose element IS routed, which the guard reaches by walking the shadow
    rather than by recognising a name."""
    cases = [
        ('zip-shadow-swapped', 'zip = lambda a, b: [(b[0], a[0])]\n'
         + _PLAIN_CALLS, 'return [v() for _, v in zip(p, q)]', (0, 0)),
        ('enumerate-shadow-clean',
         'enumerate = lambda s, *r: [(0, ordinary)]\n' + _LIST,
         'return [v() for _, v in enumerate(l, 1)]', (0, 0)),
        ('map-shadow-ignores', 'map = lambda f, *it: (ordinary,)\n' + _LIST,
         'return [v() for v in map(relay, l)]', (0, 0)),
        ('zip-shadow-routed', 'zip = lambda a, b: [(b[0], a[0])]\n' + _PLAIN,
         'return [v() for _, v in zip(p, q)]', (1, 1)),
        ('enumerate-shadow-routed',
         'enumerate = lambda s, *r: [(0, s[0])]\n' + _CALLS,
         'return [v() for _, v in enumerate(l, 1)]', (1, 1)),
        ('map-shadow-routed', f'map = lambda f, *it: (f,)\n{_CALLS}',
         f'return [v() for v in map({_CALL}, l)]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_the_module_path_boundary_is_stated_and_measured(tmp):
    """The boundary in `_pyroute_multiter`'s docstring, pinned in BOTH
    directions. The guard reads `ast.Name.id` and `ast.Attribute.attr`,
    never a `func.value`-rooted module path, so no spelling of one of the
    three declarations THROUGH a module is paired -- `builtins.zip` is not
    `zip`, and `itertools.chain` is not a pairing either.

    Every row here is a false green the docstring discloses, and every one
    of them is a row a mutant that CLOSES the boundary turns red: the lead's
    mutant, which teaches the arm the `builtins.` path for all three, takes
    `builtins.map` from 0 to 1 and nothing here noticed. `builtins.map`,
    `builtins.zip` and `builtins.enumerate` are here for that reason and for
    no other one -- an `itertools`-only control never reaches the arm at all,
    because the pre-existing import rule already pins those. The last row is
    the other direction: the bare name IS closed, so a narrowing that made a
    bare name unreadable fails here too."""
    cases = [
        ('builtins-map', 'import builtins\n' + _LIST,
         'return [f() for f in builtins.map(lambda g: g, l)]', (1, 0)),
        ('builtins-zip', 'import builtins\n' + _LIST,
         'return [v() for _, v in builtins.zip(l, l)]', (1, 0)),
        ('builtins-enumerate', 'import builtins\n' + _LIST,
         'return [v() for _, v in builtins.enumerate(l)]', (1, 0)),
        ('itertools-qualified-chain', 'import itertools\n' + _LIST,
         'return [f() for f in itertools.chain(l)]', (1, 0)),
        ('itertools-from-import-chain',
         'from itertools import chain\n' + _LIST,
         'return [f() for f in chain(l)]', (1, 0)),
        ('itertools-from-import-starmap',
         'from itertools import starmap\n' + _DICT,
         'return [f() for f in starmap(lambda a, b: b, d.items())]', (1, 0)),
        ('itertools-zip-longest', 'import itertools\n' + _LIST,
         'return [x for _, x in itertools.zip_longest(l, l)]', (0, 0)),
        ('itertools-pairwise', 'import itertools\n' + _LIST,
         'return [x for x in itertools.pairwise(l)]', (0, 0)),
        ('bare-closed', _LIST, 'return [f() for f in map(lambda g: g, l)]',
         (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_the_declarations_name_what_they_decide(_tmp):
    """The enumeration is a claim about the builtin namespace, and the cheap
    test of it is that every member it names is reachable from the record
    that states its declaration -- a table the battery could not reach is a
    claim nothing runs. The keywords each declaration admits are part of the
    claim too, and they are what changes a step's arity or its indexing."""
    named = {member: declaration.decides
             for declaration in MULTI_ITERABLE_DECLARATIONS
             for member in declaration.members}
    assert named == {
        'zip': POSITIONAL_PAIRING.decides,
        'enumerate': INDEXED_PAIRING.decides,
        'map': PROJECTION.decides}, named
    for declaration in MULTI_ITERABLE_DECLARATIONS:
        assert declaration.members, declaration.decides
        assert declaration.keywords, declaration.decides
        assert declaration.operands and declaration.value, declaration.decides
    assert POSITIONAL_PAIRING.keywords == {'strict'}
    assert INDEXED_PAIRING.keywords == {'start'}
    assert PROJECTION.keywords == {'strict'}
    assert set(MULTI_ITERABLE_DECLARATIONS) == {
        POSITIONAL_PAIRING, INDEXED_PAIRING, PROJECTION}


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='multiter_')


if __name__ == '__main__':
    raise SystemExit(main())
