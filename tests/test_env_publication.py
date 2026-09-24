#!/usr/bin/env python3
"""No tracked test module publishes a credential into its own environment.

Seven modules wrote into the suite process's `os.environ` at import and never
restored it: the credential and the child's MCP port, for consumers that did
not exist. Nothing that runs after the import in the same process needs
them, and a raw child that inherits `os.environ` inherits a credential the
suite never meant to hand it. The suites take the credential as a Python
name, or as an explicit per-spawn `env=`, which is what the modules' own
constants are for.

Two halves, because either alone is a snapshot of today. The runtime half
drives `PUBLISHERS` as a table, one row per module, and watches each import
in a FRESH interpreter: this process has already imported the modules that
matter, so an in-process before/after would snapshot an installed helper and
read green whatever it did. The structural half scans every tracked
`tests/*.py` for a module-level write into `os.environ`, so an EIGHTH site
fails here rather than waiting for a successor to sweep for it. Every site
that scan admits is classified below, and an unclassified one is a failure:
a list of these seven paths with no classification behind it would pass by
construction on a site nobody has met yet.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent

# The three names the publication is about, and the whole of the family the
# structural half reads: a module-level write of any of them is a site.
NAMES = ('DAEDALUS_TOKEN', 'DAEDALUS_MCP_PORT', 'TOKEN')
CREDENTIAL_NAMES = ('TOKEN',)
CREDENTIAL_PREFIXES = ('DAEDALUS_',)

# The modules this branch took the publication out of, each with the names it
# published. The runtime half imports every row; the structural half requires
# that none of them has a site left.
PUBLISHERS = (
    ('_bridge', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('_segments', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_cli', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_cli_duplicate_admission',
     ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_cli_error_reporting', ('DAEDALUS_MCP_PORT',)),
    ('test_cli_waits', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_path_safety', ('DAEDALUS_TOKEN', 'TOKEN')),
)

# The sites the scan admits that STAY, with the reason each write is the
# purpose rather than a publication: the names are the ones the row admits,
# and `None` admits whatever the file publishes. A site here publishing a
# name outside its row is unclassified, and fails like any other.
KEPT = {
    'test_http_transport': (
        ('DAEDALUS_DIR', 'DAEDALUS_PORT'),
        'the in-process load reads this root back, which is the write'),
    'test_mcp_refusal_drain': (
        ('DAEDALUS_TOKEN', 'TOKEN'),
        'the request guard reads the token at request time, which is the '
        'read direction and not this mechanism'),
    'test_mcp_server': (
        None,
        'the request guard reads this token at request time; the mapping is '
        'imported, so what it publishes is not readable from this file'),
    'test_result_routes': (
        ('DAEDALUS_DIR', 'DAEDALUS_PORT'),
        'the in-process load reads this root back, which is the write'),
    'test_segment_routes': (
        ('DAEDALUS_DIR', 'DAEDALUS_PORT', 'DAEDALUS_MAX_SEGMENT_INDEX',
         'DAEDALUS_MAX_SEGMENTS_PER_JOB', 'DAEDALUS_MAX_SEGMENT_JOB_SIZE'),
        'the in-process load reads this root and these quotas back, which '
        'is the write'),
}

SNAPSHOT = """
import importlib
import json
import os
import sys
sys.path.insert(0, sys.argv[1])
before = dict(os.environ)
importlib.import_module(sys.argv[2])
print(json.dumps({'before': before, 'after': dict(os.environ)},
                 sort_keys=True))
"""


def _import_in_a_fresh_process(module, **ambient):
    """What a fresh interpreter's environment holds after the import.

    Every inherited `DAEDALUS_*` and `TOKEN` is dropped first, so the child
    starts from a known environment and `ambient` alone decides which of the
    watched names are present. Both snapshots are taken by the child itself,
    in one interpreter: a name the platform normalises on the way into a
    child is then already normalised in both halves of the comparison.
    """
    env = {name: value for name, value in os.environ.items()
           if name not in NAMES and not name.startswith('DAEDALUS_')}
    env.update({name: value for name, value in ambient.items()
                if value is not None})
    child = subprocess.run(
        [sys.executable, '-c', SNAPSHOT, str(TESTS_DIR), module], env=env,
        capture_output=True, text=True, check=False)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def _values(snapshot, names, other=None) -> dict:
    """The named entries, as a reader of the credential needs to see them.

    A name the snapshot does not carry is absent, not a value: absence is
    one of the outcomes these controls score, so it is skipped here and
    found by the caller's comparison. `other` is the second snapshot, and
    then each entry reads as the pair the two snapshots disagree about.
    """
    reads: dict = {
        name: (snapshot[name] if other is None
               else (snapshot[name], other[name]))
        for name in names
        if name in snapshot and _is_credential(name)}
    unread = [name for name in names
              if name in snapshot and name not in reads]
    if unread:
        reads['(names only)'] = unread
    return reads


def _is_credential(name):
    return name in CREDENTIAL_NAMES or name.startswith(CREDENTIAL_PREFIXES)


def _assert_nothing_published(snap):
    """The whole environment is one value, its key set included.

    Comparing only the names this issue happens to name would pass a helper
    that published some fourth one, so the diff is over both snapshots in
    full. Values are reported for the credential family only: a failure
    message that printed the whole inherited environment would put this
    machine's secrets into every CI log that ever saw it go red.
    """
    before, after = snap['before'], snap['after']
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(name for name in before.keys() & after.keys()
                     if before[name] != after[name])
    assert not (added or removed or changed), (
        f'the import published into the suite environment: '
        f'added={_values(after, added)} '
        f'changed={_values(before, changed, after)} removed={removed}')


def _tracked_tests():
    """Every tracked module under tests/, as worktree-relative paths."""
    listed = subprocess.run(
        ['git', '-C', str(_util.ROOT), 'ls-files', '-z', 'tests/*.py'],
        capture_output=True, check=True, timeout=30)
    return [raw.decode('utf-8') for raw in listed.stdout.split(b'\0') if raw]


def _environ(node):
    """The `os.environ` a node reaches, or None.

    A subscript target and a method call both carry the mapping one level
    down, so the receiver is what is read: a scan that matched only the
    bare attribute would see `os.environ['X'] = ...` as no site at all,
    which is the shape most of these writes have.
    """
    if isinstance(node, ast.Subscript):
        node = node.value
    return (isinstance(node, ast.Attribute) and node.attr == 'environ'
            and isinstance(node.value, ast.Name) and node.value.id == 'os')


def _subscript_key(node):
    """The string a subscript spells, or None when it spells nothing."""
    if (isinstance(node, ast.Constant)
            and isinstance(node.value, str)):
        return node.value
    return None


def _published_names(node, bindings, depth=2):
    """The names a written value publishes, or None when unreadable here.

    An unreadable value is None rather than an empty list on purpose: a site
    whose names cannot be read has to be classified, because a control that
    read "no names" for what it could not parse would pass it.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Dict):
        return [key.value for key in node.keys
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)]
    if depth and isinstance(node, ast.Name) and node.id in bindings:
        return _published_names(bindings[node.id], bindings, depth - 1)
    return None


def _sites(source):
    """Every module-level write into `os.environ`, as (line, names-or-None).

    Only the statements the module body holds directly are read: a write
    inside a function or a `main()` guard runs per call, not at import, and
    is another mechanism. `os.environ = <wrapper>` rebinds the name rather
    than writing into the mapping, so it is not a site either.
    """
    tree = ast.parse(source)
    bindings = {node.targets[0].id: node.value for node in tree.body
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)}
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _environ(target) and isinstance(target, ast.Subscript):
                    key = _subscript_key(target.slice)
                    found.append((node.lineno, [key] if key else None))
        elif isinstance(node, ast.AugAssign) and _environ(node.target):
            found.append((node.lineno, _published_names(node.value, bindings)))
        else:
            written = _written_value(node)
            if written is not None:
                found.append(
                    (node.lineno, _published_names(written, bindings)))
    return found


def _written_value(node):
    """The value a module-level statement writes into `os.environ`.

    `os.environ = <wrapper>` rebinds the name rather than writing into the
    mapping, and is deliberately not a value here.
    """
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return None
    call = node.value
    if not (isinstance(call.func, ast.Attribute)
            and _environ(call.func.value)
            and call.func.attr in ('setdefault', 'set', 'update')
            and call.args):
        return None
    return call.args[0]


def test_every_publisher_leaves_a_poisoned_environment_alone(_tmp):
    """Each row's import keeps every occupied name at the value it had.

    An import that overwrote `DAEDALUS_TOKEN` with its own credential would
    still leave a suite presenting the right token through its `env=`, so
    the ambient value itself is the only thing that shows the write.
    """
    for module, published in PUBLISHERS:
        snap = _import_in_a_fresh_process(
            module, DAEDALUS_TOKEN='ambient-token', DAEDALUS_MCP_PORT='8086')
        occupied = {name: snap['before'].get(name) for name in NAMES}
        assert occupied == {
            'DAEDALUS_TOKEN': 'ambient-token', 'DAEDALUS_MCP_PORT': '8086',
            'TOKEN': None}, (module, published,
                             _values(snap['before'], NAMES))
        _assert_nothing_published(snap)


def test_every_publisher_installs_nothing_into_an_empty_environment(_tmp):
    """With every watched name absent, all three stay absent.

    Absence is a value here: a control that read only present names would
    score a module that installed `TOKEN` where there was none as clean.
    """
    for module, published in PUBLISHERS:
        snap = _import_in_a_fresh_process(module)
        for phase in ('before', 'after'):
            occupied = _values(snap[phase], NAMES)
            assert occupied == {}, (module, published, phase, occupied)


def test_no_unclassified_module_publishes_at_import(_tmp):
    """Every module-level credential write is a classified site.

    This is the half that outlives today's seven rows. A module the table
    has never met fails here with its file and line, so the sweep is this
    control's job and not a successor's.
    """
    unclassified, republished = [], []
    for rel in _tracked_tests():
        source = (_util.ROOT / rel).read_text(encoding='utf-8')
        stem = Path(rel).stem
        for line, names in _sites(source):
            if names is not None and not any(map(_is_credential, names)):
                continue
            if stem in dict(PUBLISHERS):
                republished.append(f'{rel}:{line}')
            elif stem in KEPT:
                admitted, reason = KEPT[stem]
                if admitted is None:
                    continue
                if names is None or set(names) - set(admitted):
                    unclassified.append(
                        f'{rel}:{line} publishes {names}; {stem} is '
                        f'classified for {admitted} ({reason})')
            else:
                published = names if names is not None else (
                    'names this file does not spell out')
                unclassified.append(
                    f'{rel}:{line} publishes {published}, and no row '
                    f'classifies it')
    assert not republished, (
        'a module this branch took the publication out of has one again: '
        f'{republished}')
    assert not unclassified, (
        'a module-level credential write with no classification: '
        f'{unclassified}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
