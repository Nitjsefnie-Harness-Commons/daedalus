#!/usr/bin/env python3
"""Every plant-based control of the launch-routing walk.

`tests/_node_launch_routing.py` holds the walk; this holds the shapes it has
to read and the shapes it must refuse. Each plant below is a case the walk
used to get wrong in the direction that loses a site, and each is checked
against the decision the walk makes rather than against a restatement of
it. The rationale for what each one is for lives beside the code it
constrains, not here.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _command_type_readers import _parents  # noqa: E402
from _node_launch_routing import (  # noqa: E402
    NOT_FIXED_WORK, VERDICT_NODE, VERDICT_OTHER, VERDICT_UNRESOLVED, _bounds,
    _bounds_its_own_child, _exempt, _launches)


def test_a_deadline_that_cannot_expire_is_not_a_deadline(tmp):
    """`timeout=` being present is not a bound, in all five spellings.

    The value is read as a node, not off its printed form, so the stdlib's
    own "no deadline" spellings are refused, and so is a negative one. A
    `Constant` that is not a positive number bounds nothing; a composed
    name is a deadline, because what it composes to is not visible from
    here and refusing it would refuse every real bound in the tree.
    """
    del tmp
    for value in ('None', '0', 'False', '0.0', '-1'):
        source = ('import subprocess\n'
                  'def launch():\n'
                  "    return subprocess.run(['node', 'c.js'],\n"
                  f'                          timeout={value})\n')
        launch = _launches(ast.parse(source))[0]
        assert not _bounds(launch['deadline']), (value, launch['deadline'])
    composed = ('CHILD_DEADLINE_S', 'round(30 * 2)', 'None if x else 5',
                "'30'")
    for value in composed:
        source = ('import subprocess\n'
                  'def launch():\n'
                  "    return subprocess.run(['node', 'c.js'],\n"
                  f'                          timeout={value})\n')
        launch = _launches(ast.parse(source))[0]
        assert _bounds(launch['deadline']), (value, launch['deadline'])
    # And the same rule on the wait that bounds a `Popen`.
    tree = ast.parse(
        'import subprocess\n'
        'def launch():\n'
        "    process = subprocess.Popen(['node', 'c.js'])\n"
        '    return process.communicate(timeout=None)\n')
    launch = _launches(tree)[0]
    assert not _bounds_its_own_child(tree, launch, _parents(tree)), (
        'timeout=None was read as a bound on the child')


def test_a_launch_in_a_class_body_is_a_site(tmp):
    """Liveness of the class-body scope, in both of its halves.

    A class body is walked by no method scope, so a launch declared as a
    class attribute is in neither the class nor any method. The second half
    is the other side of the same construction: a METHOD is counted once,
    under its own name, and not again under the class.
    """
    del tmp
    in_a_body = ('import subprocess\n'
                 'class Harness:\n'
                 "    LAUNCH = subprocess.run(['node', 'c.js'], timeout=30)\n")
    launches = _launches(ast.parse(in_a_body))
    assert [row['verdict'][0] for row in launches] == [VERDICT_NODE], launches
    assert [row['function'] for row in launches] == ['Harness'], launches
    in_a_method = ('import subprocess\n'
                   'class Harness:\n'
                   '    def probe(self):\n'
                   "        return subprocess.run(['node', 'c.js'],\n"
                   '                          timeout=30)\n')
    launches = _launches(ast.parse(in_a_method))
    assert len(launches) == 1, launches
    assert launches[0]['function'] == 'probe', launches
    # A nested function is its own scope too, and is counted once.
    nested = ('import subprocess\n'
              'def outer():\n'
              '    def inner():\n'
              "        return subprocess.run(['node', 'c.js'], timeout=30)\n"
              '    return inner\n')
    launches = _launches(ast.parse(nested))
    assert len(launches) == 1, launches
    assert launches[0]['function'] == 'inner', launches


def test_a_call_the_walk_does_not_recognise_is_not_resolved_through_it(tmp):
    """A call's first argument is not the call's result.

    The three shapes are the ones a walk that descends into any call's
    first argument gets wrong; `_executable_verdict` says why.
    """
    del tmp
    for line in ("name = os.environ.get('RUNTIME', 'node')",
                 'name = os.path.basename(sys.argv[0])',
                 'name = Path(sys.argv[0]).name'):
        source = ('import os\n'
                  'import sys\n'
                  'from pathlib import Path\n'
                  'import subprocess\n'
                  'def launch():\n'
                  f'    {line}\n'
                  "    return subprocess.run([name, 'c.js'], timeout=30)\n")
        launch = _launches(ast.parse(source))[0]
        assert launch['verdict'][0] == VERDICT_UNRESOLVED, (
            line, launch['verdict'])
    # And the one call that DOES return its argument still resolves.
    which = ('import shutil\n'
             'import subprocess\n'
             'def launch():\n'
             "    name = shutil.which('node')\n"
             "    return subprocess.run([name, 'c.js'], timeout=30)\n")
    assert _launches(ast.parse(which))[0]['verdict'][0] == VERDICT_NODE


def test_a_node_row_silences_a_stub_and_not_a_real_child(tmp):
    """A stub row silences the stub and not a real child beside it.

    Two plants, same module, same function, same callee — so the shape a
    row is keyed on cannot tell them apart, and the discriminator has to be
    something else. `_exempt` says what it is; this pins both halves.
    """
    del tmp
    stub = ('import subprocess\n'
            'def launch(node, tmp):\n'
            "    return subprocess.run([node, 'probe.js'],\n"
            '                          capture_output=True, timeout=30)\n')
    real = ('import shutil\n'
            'import subprocess\n'
            'def launch(node=None, tmp=None):\n'
            "    node = shutil.which('node')\n"
            "    return subprocess.run([node, 'probe.js'], timeout=30)\n")
    stub_launch = _launches(ast.parse(stub))[0]
    real_launch = _launches(ast.parse(real))[0]
    assert stub_launch['verdict'] == (VERDICT_NODE, 'spelled'), (
        stub_launch['verdict'])
    assert real_launch['verdict'] == (VERDICT_NODE, 'bound'), (
        real_launch['verdict'])
    # Same module, same function, same callee — the shape a row is keyed on
    # — so the row cannot tell them apart by its key alone and must not try.
    shape = ('planted.py', 'launch', 'subprocess.run')
    original = dict(NOT_FIXED_WORK)
    NOT_FIXED_WORK[shape] = "a control's own stub node"
    try:
        assert _exempt(shape, stub_launch), 'a row could not silence it'
        assert not _exempt(shape, real_launch), (
            'a row silenced a launch that resolves to a real which(node)')
    finally:
        NOT_FIXED_WORK.clear()
        NOT_FIXED_WORK.update(original)
    # And the table is back to what it was, so the control leaves nothing
    # behind for the sweep that runs after it.
    assert NOT_FIXED_WORK == original, 'the table did not restore'


def test_a_launch_the_walk_cannot_read_is_not_a_non_launch(tmp):
    """Liveness of the fail-closed direction, on the shapes that defeat it.

    Each shape below is one a resolver that answered "not node" for
    anything it did not recognise would pass. Planting only the shapes it
    already handles would be the same defect one level down.
    """
    del tmp
    constant = ('import subprocess\n'
                "subprocess.run(['node', 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(constant))[0]['verdict'][0] == VERDICT_NODE
    # A function PARAMETER named `node` is the shape that was live in this
    # tree and invisible: no walk of one module can bind it. It is admitted
    # anyway, so the control demands an answer for it rather than passing.
    parameter = ('import shutil\n'
                 'import subprocess\n'
                 'def launch(node):\n'
                 "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(parameter))[0]['verdict'][0] == VERDICT_NODE
    # A parameter under any other name is unprovable, and that is the case
    # UNRESOLVED_LAUNCHES exists to discharge.
    other_parameter = ('import subprocess\n'
                       'def launch(exe):\n'
                       "    return subprocess.run([exe, 'child.js'],\n"
                       '                          timeout=30)\n')
    assert _launches(
        ast.parse(other_parameter))[0]['verdict'][0] == VERDICT_UNRESOLVED
    # A local bound to a parameter, and a second name for the executable.
    rebound = ('import subprocess\n'
               'def launch(exe):\n'
               "    other = exe\n"
               "    return subprocess.run([other, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(rebound))[0]['verdict'][0] == VERDICT_UNRESOLVED
    # A different spelling of the same executable, which may be node.
    alias = ('import shutil\n'
             'import subprocess\n'
             'def launch():\n'
             "    n = shutil.which('nodejs')\n"
             "    return subprocess.run([n, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(alias))[0]['verdict'][0] == VERDICT_UNRESOLVED
    # A computed executable, and one read out of the environment.
    computed = ('import subprocess\n'
                'def launch():\n'
                "    name = 'no' + 'de'\n"
                "    return subprocess.run([name, 'child.js'], timeout=30)\n")
    assert _launches(
        ast.parse(computed))[0]['verdict'][0] == VERDICT_UNRESOLVED
    # And the two it IS allowed to discharge, so the cases above are not
    # passing because nothing is ever classified.
    resolved = ('import shutil\n'
                'import subprocess\n'
                'def launch():\n'
                "    node = shutil.which('node')\n"
                "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(resolved))[0]['verdict'][0] == VERDICT_NODE
    python = ('import sys\n'
              'import subprocess\n'
              'def launch():\n'
              '    return subprocess.run([sys.executable, "s.py"],\n'
              '                          timeout=30)\n')
    assert _launches(ast.parse(python))[0]['verdict'][0] == VERDICT_OTHER


def test_a_wait_inside_an_expiry_handler_is_not_the_bounds_of_the_child(tmp):
    """Liveness of the second direction, in the shape that made it short.

    `_dashnode.py` bounds one child three times over: the wait that ends
    it, the drain of it after it was killed, and the reap that follows the
    drain. A check that asks only whether the enclosing function mentions
    a `timeout=` is satisfied by the second two, and deleting the first
    leaves it green — which is the mutation this control exists to catch.
    """
    del tmp
    source = (
        'import subprocess\n'
        'def launch():\n'
        "    process = subprocess.Popen(['node', 'child.js'])\n"
        '    try:\n'
        '        out = process.communicate(timeout=30)\n'
        '    except subprocess.TimeoutExpired:\n'
        '        process.kill()\n'
        '        out = process.communicate(timeout=5)\n'
        '    return out\n')
    tree = ast.parse(source)
    parents = _parents(tree)
    launch = _launches(tree)[0]
    assert _bounds_its_own_child(tree, launch, parents), 'the real wait missed'
    dropped = source.replace('out = process.communicate(timeout=30)',
                             'out = process.communicate()')
    assert dropped != source, 'the plant did not reach the real code'
    tree = ast.parse(dropped)
    assert not _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree)), (
        'a drain of a killed child satisfied the child\'s own bound')
    # A bound at the launch itself needs no later wait.
    inline = ('import subprocess\n'
              'def launch():\n'
              "    return subprocess.run(['node', 'child.js'], timeout=30)\n")
    tree = ast.parse(inline)
    assert _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree))


def test_the_boundary_direction_can_still_tell_bounded_from_unbounded(tmp):
    """Liveness of the second direction, in both of its spellings.

    A classifying module is exempt from the routing rule, so the only thing
    stopping the exemption from becoming "no bound anywhere" is this check.
    """
    del tmp
    on_the_launch = ('import subprocess\n'
                     'def launch():\n'
                     "    return subprocess.run(['node', 'c.js'],\n"
                     '                          timeout=30)\n')
    tree = ast.parse(on_the_launch)
    assert _bounds_its_own_child(tree, _launches(tree)[0], _parents(tree))
    with_nothing = ('import subprocess\n'
                    'def launch():\n'
                    "    return subprocess.run(['node', 'c.js'])\n")
    tree = ast.parse(with_nothing)
    assert not _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree))
    # A bound on a DIFFERENT process does not bound this one.
    other_process = ('import subprocess\n'
                     'def launch():\n'
                     "    process = subprocess.Popen(['node', 'c.js'])\n"
                     '    other = subprocess.Popen(["node", "d.js"])\n'
                     '    other.communicate(timeout=5)\n'
                     '    return process.wait()\n')
    tree = ast.parse(other_process)
    assert not _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree))


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='nodelaunchshapes_')


if __name__ == '__main__':
    raise SystemExit(main())
