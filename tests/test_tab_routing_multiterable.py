#!/usr/bin/env python3
"""A multi-iterable builtin consumer pairs what it walks, or reads clean.

`zip`, `enumerate` and `map` reach a routed value through a pairing rather
than through one operand. Before the arm the operand funnel reduced each to
the merge of what the expression yields, a merge is not a tuple, a
destructuring target read nothing, and the value behind it stayed invisible.

Every verdict here is `_tracked_focus_verdict`'s `(runtime_calls,
guard_violations)` on the real `do_focus_tab`, so the guard and the runtime
are both measured. Two operands are used for every consumer: a mapping
read-back (`d.values()`) and a plain list (`l`, whose positions are
statically known) -- the second rules out the materializer reading, because
a list leaves nothing about order or occupancy unresolvable and the only
thing missing there is the PAIRING.
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
    constructing the call rather than by a version table. `_PROBE` is keyed
    by MEMBER, not by kind, because it is that member's own signature that
    decides how many operands stand before the keyword -- `map`'s callable
    is the first operand, and a probe omitting it would ask about a
    different call. A member with no probe is named rather than a bare
    `KeyError`."""
    operands = _PROBE.get(member)
    if operands is None:
        raise AssertionError(
            f'no probe for builtin {member!r}; _PROBE is keyed by member '
            'because it is the signature of that member itself, and the '
            f'known probes are {sorted(_PROBE)}')
    spelling = f'{member}({operands}, {keyword}=True)'
    try:
        # pylint: disable-next=eval-used
        eval(spelling)
    except TypeError:
        return False
    return True


# One row shape per KIND, keyed on the kind the declaration carries as data.
# A fourth kind with no entry here is refused by name, rather than falling
# into the `else` of the two this knows and building a source shaped like the
# wrong one -- which is what an identity dispatch on the declaration does.
# Each entry is (stores for mapping and list, the operand spelling for each,
# the target template); a projection yields its callable's RESULT and the
# other two yield an operand element, and an indexed pairing's step is an
# (index, value) pair, so only the projection reads one name.
# The kinds a row shape exists for, and the kinds the two decisions below
# are ABOUT -- a pairing takes two streams, an indexed pairing one plus a
# base, a projection pairs that one with a callable. Read off the kind the
# declaration carries as data, not off an identity comparison: a fourth kind
# then refuses by name here rather than falling into the `else` of the two
# this knows and building a source shaped like the wrong one.
_KINDS = ('pairing', 'indexed', 'projection')
_CONTAINERS = ('dict', 'list')


def _call_shape(declaration, member, container, keyword):
    """The call as the source spells it over one operand shape, and the
    store that builds the operand. A projection's callable is the identity,
    so the row pairs the arm and not the callable."""
    if declaration.shape not in _KINDS:
        raise AssertionError(
            f'kind {declaration.shape!r} has no row shape; known kinds are '
            f'{sorted(_KINDS)}, so a member of a new kind is not covered')
    shape = declaration.shape
    pair, project = shape == 'pairing', shape == 'projection'
    mapping = container == 'dict'
    store, read = (_DICT, 'd.values()') if mapping else (_LIST, 'l')
    if pair:
        read = 'd, d.values()' if mapping else 'l, l'
    spell = f'{member}({read}'
    if project:
        spell = f'{member}(lambda g: g, {read}'
    if keyword:
        spell += f', {keyword}=True'
    target = 'return [f() for f in ' if project else 'return [v() for _, v in '
    return store, f'{target}{spell})]'


def _grammar_battery():
    """Every (declaration, member, container, keyword) the enumeration admits
    on this interpreter, from the declarations rather than a list of names.
    What a declaration reads is its own, so a member added to a kind is read
    and covered by the same two functions."""
    return [(declaration, member, container, keyword)
            for declaration in MULTI_ITERABLE_DECLARATIONS
            for member in sorted(declaration.members)
            for container in _CONTAINERS
            for keyword in ('', *sorted(
                word for word in declaration.keywords
                if _accepts(member, word)))]


def test_every_enumerated_consumer_is_covered(tmp):
    """The battery, run as one set. Every row puts a routed value in a
    position its consumer pairs and calls it, so every one makes its call at
    runtime. The count is derived from the declarations and the running
    builtin, so the assertion is about every member rather than the ones a
    repro used."""
    battery = _grammar_battery()
    cases = []
    for declaration, member, container, keyword in battery:
        store, invoke = _call_shape(declaration, member, container, keyword)
        cases.append((f'{member}-{container}-{keyword or "plain"}',
                      _body(store, invoke), (1, 1)))
    covered = {(declaration, member) for declaration, member, _, _ in battery}
    assert covered == {(declaration, member)
                       for declaration in MULTI_ITERABLE_DECLARATIONS
                       for member in declaration.members}
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
    projection returning something else leaves the element clean even though
    the operand carried the routed value, and that row fails if the arm read
    the operand element through the projection. A projection the model cannot
    resolve is a value it cannot place, which fails closed."""
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
    `start` rows carry the routed value in exactly one operand.

    What these rows pin is the BEHAVIOUR, not the truncation producing it.
    Making `_indexed_operands` return every positional operand leaves this
    suite green, because `_indexed_value` reads only `streams[0]` and the
    extra stream is discarded: the truncation is dead code and the rows would
    not notice it going. A keyword the running builtin refuses is skipped
    rather than spelled: the call raises before the guard reaches a verdict.
"""
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
    """A `**` the guard cannot read leaves the count undecided, and the token
    reaches the caller as an unprovable callee, which is what
    `call_violations` reads it as -- so a target carrying a `tab` through it
    is reported. A starred positional operand whose container the guard CAN
    read is decided and placed, and the arm's own tests cover that; these
    rows are the `**` half, and the last is the same pair with a decided
    arity, so the two cannot both pass by accident."""
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
    still pairs it, so the tuple reaches the target and the unread position
    carries the token. The row where the TARGET is that undecided position
    is the one that says the token reached the caller."""
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
    the container's ELEMENTS, a token is not a tuple, so a destructuring
    target paired nothing and the call through it read clean. Each undecided
    row has a decided twin beside it."""
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
    is a stream it cannot READ, and carries the token the way any other
    unreadable operand does. Reading it as an empty stream instead left no
    token and no positions: the same fail-open at a second site. The quiet
    row is the twin, where the token is there and nothing calls it."""
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
    """The negative direction. A generator expression the model CAN decide
    states how many steps it yields, and a stated zero is a pairing that
    reaches no step: a routed value in the operand BESIDE it is paired with
    nothing, so the `tab` the target carries reaches no sender.

    Every target CALLS the element it reads, because that call is the only
    way a token in the position becomes a finding. The four earlier
    spellings never called it, which is why two fail-closed mutants left
    them at `(0, 0)` and passing.

    The decision pinned is `generator.remaining`, the count
    `_operand_stream` reads off a tracked generator -- NOT
    `_comprehension_count`, which a mutation can throw away entirely and
    leave all four rows green; forcing a tracked generator's declared length
    to 1 is what kills all four. What a decidable generator YIELDS is a
    separate question this test does not claim: the model does not hold it,
    so a `tab` through one reads as a finding whatever the count is, and
    pinning that would assert a defect."""
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
        # value in: the routing is real and the pairing still reaches no
        # step, so a reader that lost the count would report it.
        ('zip-genexp-empty-beside-routed', 'd = {"k": ext_cmd}',
         f'return [{_CALL} for _, v in zip((x for x in []), [d["k"]])]',
         (0, 0)),
    ]
    _verdicts(tmp, cases)


def test_a_star_the_model_cannot_read_leaves_no_position_decided(tmp):
    """A starred operand is a count of streams, and a container the model
    cannot read is a count it cannot name. Nothing at or after such a star is
    placeable, so the step is undecided at EVERY position.

    Reading it as one more stream is the fail-open in its purest form: the
    tuple claims a length the runtime never pairs over, a destructuring
    target reads a different number of names from it, the pair is REFUSED,
    and the names are left bound to nothing. The routed call then reads
    clean while the runtime really reached the sender.

    The first two rows are that mechanism's two entry points, both here
    because removing the early return between them is only a real repair if
    a plant shows BOTH classes appear; mutant M6 does."""
    _CALL_ALL = 'a("focus", tab=1)'
    _STORE = ('def pair(*items): return list(items)\n'
              'O = [ext_cmd]\nP = [ordinary]')
    cases = [
        # The star stands BEFORE an operand the model did read, so that
        # operand's own position is one the unreadable count decides.
        ('zip-star-before-plain', _STORE,
         f'return [{_CALL_ALL} for a, b, c in zip(*pair(O, O), P)]', (1, 1)),
        # The star is the only operand, so no placed stream stands beside
        # it and the early return that fired on an empty operand list is the
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
        ('decided-twin', 'l = [relay()]',
         'return [v() for _, v in zip(*[l, l])]', (1, 1)),
    ]
    _verdicts(tmp, cases)


def test_a_star_container_is_read_by_what_it_holds_not_by_its_kind(tmp):
    """One character of container KIND separates a correct verdict from a
    false green, and nothing in the arm's own vocabulary said it should.

    A star unpacks a CONTAINER OF ITERABLES, so what the model needs is to
    enumerate that container's elements and say which is at which position.
    A generator expression states both -- how many steps it yields, and what
    each yields -- so it IS a container of that many iterables, and the arm
    read a list display while it fell through to the declined path.

    A generator expression consumed DIRECTLY is read correctly and always
    was, so this is one in the position THIS ARM ADDS: on the base, where
    the arm does not exist, the same call reads `(1, 1)`.

    The list-display twins keep the fix honest: the same operand list over a
    container the arm already read must not change verdict, and the clean
    twin must stay clean."""
    _CALL_ALL = 'a("focus", tab=1)'
    _BARE = 'a()'
    # Two spellings of the same routed operand, and both are here because
    # neither alone tells reading from declining. A `tab` through the token
    # is a finding, so a ROUTED element reads `(1, 1)` whether the container
    # was read or declined: those rows pin the sound direction and cannot
    # see this defect. A routed lambda takes no arguments, so a BARE call is
    # the only shape in which declining reads clean -- those are the false
    # greens.
    _ROUTED = 'l = [relay()]'
    _SENDER = 'l = [ext_cmd]'
    cases = [
        # The false-green class, one row per consumer placement.
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
        ('zip-list-container-bare', _ROUTED,
         f'return [{_BARE} for a, b in zip(*[l, l])]', (1, 1)),
        ('zip-list-after-plain-bare', _ROUTED,
         f'return [{_BARE} for a, b in zip(l, *[l])]', (1, 1)),
        ('map-list-container-bare', _ROUTED,
         f'return [{_BARE} for a in map(lambda g: g, *[l])]', (1, 1)),
        # The clean twin, and the only row separating reading from
        # declining: a declined container puts the token at every position,
        # so this goes red under one.
        ('genexp-container-clean', 'def h(*a, **k): return 0\nq = [h]',
         'return [a("focus", tab="1") for a, b in zip(*(x for x in [q, q]))]',
         (0, 0)),
        # The sound direction: a routed element through a generator-
        # expression container is a finding either way, for all three.
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
    it and the operands spelled after it. Appending them at the end models a
    different call, and wrongly in the direction that reads clean -- the
    routed value is placed at a position the runtime never gives it.

    The container here is a list display, so every row states its count and
    only the ORDER is the decision -- which separates it from the unreadable
    star beside it, that cannot state a count at all. The quiet row is the
    twin saying the ordering costs no precision."""
    _CALL_ALL = 'a("focus", tab=1)'
    cases = [
        # A leading star's streams come FIRST, so the routed value is at
        # tuple position 0, which is where the runtime puts it.
        ('zip-leading-star', 'O = [ext_cmd]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a, b, c in zip(*[O, O], P)]', (1, 1)),
        # A projection reads the end it names, so a leading star is visible
        # through the callable's FIRST parameter too.
        ('map-leading-star', 'O = [ext_cmd]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a in '
         'map(lambda a, b, c: a, *[O, O], P)]', (1, 1)),
        # A TRAILING star is where appending agrees with the runtime, so
        # this row keeps the two apart: the plain operand is at position 0
        # under both readings, and the runtime hands the target a clean one.
        ('zip-trailing-star', 'O = [ext_cmd]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a, b, c in zip(P, *[O, O])]', (0, 0)),
        # The quiet twin: an ordering that invented a routed position would
        # turn this row red.
        ('zip-leading-star-quiet', 'Q = [ordinary]\nP = [ordinary]',
         f'return [{_CALL_ALL} for a, b, c in zip(*[Q, Q], P)]', (0, 0)),
    ]
    _verdicts(tmp, cases)


def test_a_position_the_runtime_cannot_reach_stays_clean(tmp):
    """The twin of every routing row. The deferred value is held and paired
    in each of the first three and the runtime calls none of them, so the
    guard reports none. The arm decides what a position HOLDS; whether it is
    reached is the caller's, and a guard reading the holding as the
    reaching would fail every one of these."""
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
    matched. The first three are false-positive pins: the shadow's own
    element is clean, and a reader that took the name for the builtin would
    pair through it and report a send the runtime never made. The last three
    pin the correct contract for a shadow whose element IS routed, which the
    guard reaches by walking the shadow rather than by recognising a name."""
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

    Every row here is a false green the docstring discloses, and each is a
    row a mutant that CLOSES the boundary turns red: one teaching the arm
    the `builtins.` path takes `builtins.map` from 0 to 1 and nothing else
    here noticed, so those three are here for that reason alone -- an
    `itertools`-only control never reaches the arm, the pre-existing import
    rule already pins those. The last row is the other direction: the bare
    name IS closed, so a narrowing that made a bare name unreadable fails
    here too."""
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
    # A bare set equality here says only that some set differs, and a set is
    # the one thing that cannot name what is missing from it. Name the kinds
    # instead, so a fourth kind reads as a kind with no row shape rather
    # than as a battery that quietly stopped covering something.
    uncovered = [declaration.decides for declaration
                 in MULTI_ITERABLE_DECLARATIONS
                 if declaration.shape not in _KINDS]
    assert not uncovered, (
        f'declaration kinds with no row shape: {uncovered}; add the kind to '
        f'_KINDS, whose kinds are {sorted(_KINDS)}')
    assert len(MULTI_ITERABLE_DECLARATIONS) == len(_KINDS), (
        f'{len(MULTI_ITERABLE_DECLARATIONS)} declarations over '
        f'{len(_KINDS)} kinds: a kind with no declaration, or a declaration '
        'with no kind')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='multiter_')


if __name__ == '__main__':
    raise SystemExit(main())
