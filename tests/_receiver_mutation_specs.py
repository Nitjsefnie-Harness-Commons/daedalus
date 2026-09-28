"""The mutation rows for the receiver descent and the loop-iterable arm.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/_coverage_mutation_specs.py, which carries the rows for
the guard as a whole and had no room for these: it sits at the 700-line
ceiling. The two invokes below that rows outside this file also use stay in
that module, so both are read there and imported here.
"""
from _coverage_mutation_specs import _CHAIN_INVOKE, _INLINE_INVOKE

_ATOM_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_transforming_form_stays_an_atom(None)')
_RECEIVER_CARRIER_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_receiver_that_carries_a_launcher_is_refused(None)')
_RECEIVER_DESCENT_REFUSED_INVOKE = (
    'import test_receiver_descent as descent_suite; '
    'descent_suite.test_a_receiver_reached_through_an_intermediate_'
    'call_is_refused(None)')
_RECEIVER_DESCENT_CLEAN_INVOKE = (
    'import test_receiver_descent as descent_suite; '
    'descent_suite.test_a_receiver_reading_no_launch_method_stays_'
    'clean(None)')
_FOR_ITERABLE_INVOKE = (
    'import test_receiver_descent as descent_suite; '
    'descent_suite.test_a_loop_reading_a_launcher_out_of_its_'
    'iterable_is_refused(None)')

_ATOM_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_transforming_form_stays_an_atom(None)')
_RECEIVER_CARRIER_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_receiver_that_carries_a_launcher_is_refused(None)')
_RECEIVER_DESCENT_REFUSED_INVOKE = (
    'import test_receiver_descent as descent_suite; '
    'descent_suite.test_a_receiver_reached_through_an_intermediate_'
    'call_is_refused(None)')
_RECEIVER_DESCENT_CLEAN_INVOKE = (
    'import test_receiver_descent as descent_suite; '
    'descent_suite.test_a_receiver_reading_no_launch_method_stays_'
    'clean(None)')
# The base the descent hands over, and what it is handed over on. The gate
# is a narrowing in two directions, and each has a row: widening it hands
# the base of `f"{subprocess}".upper()` to the walk, which finds the bare
# module name inside the string and calls it a launcher in the receiver
# position, and dropping it loses the base the
# `{'sp': subprocess}['sp'].run(...)` rows are decided on.
_RECEIVER_BASE = (
    "    direct = callee is value.func\n"
    "    if (names_launch or direct) and not isinstance(callee, _ATOMS):\n"
    "        yield from _carried_parts(callee)\n")
_RECEIVER_MUTATIONS = (
    ('call receiver', 'bindings', ((
        _RECEIVER_BASE,
        "    direct = callee is value.func\n"),), _INLINE_INVOKE),
    ('receiver opens atoms', 'bindings', ((
        _RECEIVER_BASE,
        "    if True:\n        yield from _carried_parts(callee)\n"),),
     _RECEIVER_DESCENT_CLEAN_INVOKE),
    # The subscripts the descent consumes are sub-values in their own
    # right; dropping them hides a launcher in an index or a bound.
    ('receiver drops the subscripts', 'bindings', ((
        "        if isinstance(callee, ast.Subscript):\n"
        "            yield from _carried_parts(callee.slice)\n", ""),),
     _RECEIVER_CARRIER_INVOKE),
    # The reads the descent consumes, of the set `_carried_parts.__doc__`
    # names, and the two flags that keep or clear them. Dropping the block
    # leaves the issue's second spelling clean, so only the callee-chain
    # row notices; dropping the clearing flag alone reinstates the false
    # positive it exists to stop. The monotone flag asks the other
    # question — a launch read anywhere in the chain, never cleared — so
    # narrowing it to the last link never opens the base of
    # `{k: subprocess for k in xs}.get(k).run`, and that row dies.
    ('receiver drops the launch reads', 'bindings', ((
        "        reads_launch = (isinstance(callee, ast.Attribute)\n"
        "                        and callee.attr in _LAUNCH_READS)\n"
        "        if reads_launch and launch_only and "
        "callee is not value.func:\n"
        "            yield callee\n"
        "        launch_only = launch_only and reads_launch\n"
        "        names_launch = names_launch or reads_launch\n", ""),),
     _CHAIN_INVOKE),
    ('receiver keeps a chain open past a constant', 'bindings', ((
        "        launch_only = launch_only and reads_launch\n",
        "        launch_only = True\n"),), _ATOM_INVOKE),
    ('receiver narrows the monotone flag', 'bindings', ((
        "        names_launch = names_launch or reads_launch\n",
        "        names_launch = reads_launch\n"),), _RECEIVER_CARRIER_INVOKE),
    # A call in the chain carries `f(subprocess)` into the value `.run` is
    # read off. Dropping the handoff and keeping the descent leaves the
    # argument unseen.
    ('receiver drops the call handoff', 'bindings', ((
        "        if isinstance(callee, ast.Call):\n"
        "            if names_launch:\n"
        "                yield from _carried_parts(callee)\n"
        "            callee = callee.func\n"
        "            continue\n",
        "        if isinstance(callee, ast.Call):\n"
        "            callee = callee.func\n"
        "            continue\n"),),
     _RECEIVER_DESCENT_REFUSED_INVOKE),
    # The loop-iterable arm, from both sides. Without the second entry the
    # loop's receiver is never judged and `for launcher in {'sp':
    # subprocess}.values():` goes clean; the position is the whole of the
    # arm, so that is the narrowest needle that removes it. Making the
    # reader the default for every position instead reaches the same
    # judgement into each BIND, and `go = {"a": subprocess}.values()
    # .pop()` — the row the release exists for — is refused.
    ('loop drops the iterable base', 'bindings', ((
        "        return [(node.lineno, node.iter, _BIND),\n"
        "                (node.lineno, node.iter, _ITERABLE),\n"
        "                (node.lineno, node.target, _TARGET)]\n",
        "        return [(node.lineno, node.iter, _BIND),\n"
        "                (node.lineno, node.target, _TARGET)]\n"),),
     _FOR_ITERABLE_INVOKE),
    ('loop reads every position as an iterable', 'bindings', ((
        "            parts = _POSITION_PARTS.get(position, _carried_parts)"
        "(value)\n",
        "            parts = _POSITION_PARTS.get(position, _iterable_parts)"
        "(value)\n"),),
     _RECEIVER_DESCENT_CLEAN_INVOKE),
)
