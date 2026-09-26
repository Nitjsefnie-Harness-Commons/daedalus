"""Plant one mutation at a time and require a fresh child to start failing.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of the body of
test_each_new_binding_and_match_arm_is_mutation_sensitive, which
tests/test_static_guard_regressions.py imported as a runner: a test
function cannot move into a helper, so the mechanism came here and each
suite kept a test that calls it. The two imports are function-local
because the regressions that pin this sweep patch `_owned_writes` and
`subprocess` and must see the patched objects through them.
"""
import os
import subprocess
import sys
from pathlib import Path

FRESH_SOURCE_MARKER = 'mutation child loaded fresh source'

# A row's target key names the file in the copied tree the replacement is
# written into, so a needle that no longer matches fails here by name.
_TARGETS = {
    'bindings': '_coverage_bindings.py',
    'scopes': '_coverage_scopes.py',
    'bash': '_bash_resolver_scan.py',
    'guard': '_coverage_guard.py',
    'runner': '_mutation_sweep.py',
    'owned': '_owned_writes.py',
    'calls': '_control_calls.py',
}


def mutation_sweep(tmp, specs):
    """Require every row's invocation to fail once its mutation is planted.

    `specs` is read from the caller rather than imported, so a suite that
    narrows the sweep to two rows gets exactly those two and every other
    assertion below still applies to them.
    """
    from _util import child_coverage
    from _owned_writes import clear_bytecode, copy_test_tree

    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    targets = {key: root / 'tests' / name
               for key, name in _TARGETS.items()}
    for name, target_name, replacements, invocation in specs:
        target = targets[target_name]
        original = target.read_bytes()
        crlf = b'\r\n' in original
        text = original.decode('utf-8').replace('\r\n', '\n')
        for mutation in replacements:
            needle, replacement = mutation
            assert text.count(needle) == 1, (name, needle)
            text = text.replace(needle, replacement, 1)
        try:
            mutated = text.replace('\n', '\r\n') if crlf else text
            target.write_bytes(mutated.encode('utf-8'))
            program = (
                "import sys\nsys.path.insert(0, 'tests')\n"
                "import test_coverage_bindings as suite\n"
                "assert sys.dont_write_bytecode, "
                "'cached bytecode writes enabled without -B'\n"
                "assert sys.flags.no_site, 'site initialization enabled'\n"
                f"print({FRESH_SOURCE_MARKER!r})\n"
                f"{invocation}\n")
            clear_bytecode(root)
            result = subprocess.run(
                [sys.executable, '-B', '-S', '-c', program], cwd=root,
                env=child_coverage('scrub', {
                    **os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}),
                capture_output=True,
                text=True, timeout=30)
        finally:
            target.write_bytes(original)
        assert result.stdout == FRESH_SOURCE_MARKER + '\n', (
            'cached bytecode freshness', result.stdout, result.stderr)
        assert result.returncode != 0, (name, result.stdout, result.stderr)
        assert 'AssertionError' in result.stderr, (name, result.stderr)
