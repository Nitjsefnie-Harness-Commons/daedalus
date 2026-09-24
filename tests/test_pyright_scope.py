#!/usr/bin/env python3
"""The type checker's scope, pinned as a property rather than a pattern.

`include` once carried `scripts/*.py`, which matches the files directly
under `scripts/` and descends into none of its subdirectories, so the
seventeen modules under `scripts/ci/` were checked by nothing while the job
reported success. Widening that glob to the next directory up would have
left the same defect with a larger radius: the next package added outside
the listed set is unchecked again, silently, and no gate says so.

So the property this suite pins is the whole tree minus a stated exclusion
list, and the mechanism is a refusal: an `include` entry carrying a glob
character cannot be resolved by plain containment, and is rejected rather
than modelled. That keeps this suite honest about the one thing it cannot
do, which is reimplement pyright's own pattern matching.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
CONFIG = ROOT / 'pyrightconfig.json'
CONFIG_TESTS = ROOT / 'pyrightconfig.tests.json'

GLOB_CHARACTERS = '*?['

# Each exclusion is deliberate and says why, which is the half of the
# property that a whole-tree `include` cannot express on its own.
EXCLUSIONS = {
    '**/__pycache__': 'compiled bytecode, not source',
    'tests': 'the suites are stdlib-only and drive deliberate bad shapes',
    '.venv': 'a local virtualenv is not repository source',
    'node_modules': 'installed JavaScript packages are not ours',
}


def _config():
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def _tests_config():
    return json.loads(CONFIG_TESTS.read_text(encoding='utf-8'))


def _tracked_python():
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', '*.py'],
        capture_output=True, check=True, timeout=30)
    paths = [path for path in listed.stdout.decode(
        'utf-8', 'surrogateescape').split('\0') if path]
    assert paths, 'Git returned no tracked Python files'
    return paths


def _under(path, entry):
    entry = entry.rstrip('/')
    if entry in ('.', ''):
        return True
    return path == entry or path.startswith(entry + '/')


def test_include_carries_no_glob(tmp):
    """A glob in `include` is refused, not resolved."""
    del tmp
    globbed = sorted(
        entry for entry in _config()['include']
        if any(char in entry for char in GLOB_CHARACTERS))
    assert not globbed, (
        f'pyrightconfig.json include carries glob entries {globbed}; '
        'name the directory whose contents are in scope instead, so a '
        'file cannot fall outside every pattern without a gate saying so')


def test_every_tracked_module_is_in_scope_or_excluded(tmp):
    """No tracked module falls outside both lists."""
    del tmp
    config = _config()
    escaped = sorted(
        path for path in _tracked_python()
        if not any(_under(path, entry) for entry in config['include'])
        and not any(_under(path, entry) for entry in config['exclude'])
    )
    assert not escaped, (
        f'these tracked modules are neither in the type checker\'s scope '
        f'nor deliberately excluded from it: {escaped}')


def test_every_exclusion_is_one_this_suite_states_a_reason_for(tmp):
    """The exclusion list is reviewed here, not grown in passing."""
    del tmp
    excluded = sorted(_config()['exclude'])
    assert excluded == sorted(EXCLUSIONS), (
        f'pyrightconfig.json excludes {excluded}, while this suite states '
        f'a reason for {sorted(EXCLUSIONS)}; a directory leaves the type '
        'checker\'s scope by being named in both places')


def test_the_test_tree_config_exists_and_really_scopes_the_tests(tmp):
    del tmp
    assert CONFIG_TESTS.is_file(), (
        f'{CONFIG_TESTS.name} is missing; without it nothing type-checks '
        'the test tree, and a clean gate reads the same as a zero-file '
        'analysis')
    config = _tests_config()
    assert 'tests' in config['include'], (
        f'{CONFIG_TESTS.name} include is {config["include"]}, which does '
        'not name the tests directory, so the test tree is not in scope')
    assert 'tests' not in config['exclude'], (
        f'{CONFIG_TESTS.name} excludes tests as well as including it, so '
        'the checker analyses nothing there')


def test_the_two_configs_agree_outside_their_deliberate_differences(tmp):
    del tmp
    main = _config()
    tests = _tests_config()
    deliberate = {'include', 'exclude', 'extraPaths'}
    # A sentinel, not `dict.get`'s default of None, so a key that is absent
    # from one config and present-and-null in the other reads as a
    # disagreement: `None` on both sides would otherwise satisfy "identical"
    # whether the key is missing from both or null in one.
    absent = object()
    shared = (set(main) | set(tests)) - deliberate
    mismatched = sorted(key for key in shared
                        if main.get(key, absent) != tests.get(key, absent))
    assert not mismatched, (
        f'the two checker configs disagree on {mismatched}; every key '
        f'other than {sorted(deliberate)} must stay byte-identical, so the '
        'two scopes are one policy stated twice rather than two drifting '
        'ones')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
