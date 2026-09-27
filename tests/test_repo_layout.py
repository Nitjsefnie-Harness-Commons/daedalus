#!/usr/bin/env python3
"""The repository's module layout, pinned so it cannot silently drift back.

Generic module names at the repository root occupy the top-level import
namespace of every process started there. The bridge's modules live in the
`daedalus_bridge/` package instead; this suite is what keeps them there.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _launch_audit import bound_sites  # noqa: E402
from _launch_audit import launch_refusals as _launch_refusals  # noqa: E402
from _bound_site_rows import BOUND_SITE_ROWS  # noqa: E402
from _bounded_git_launches import BOUNDED_GIT_LAUNCHES  # noqa: E402
from _launch_keep import control_keeps, in_launch_population  # noqa: E402
from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS  # noqa: E402
from _step_ceiling import within_step_ceiling  # noqa: E402

from _package_layout import (  # noqa: E402
    BRIDGE_PACKAGE, MCP_OLD_NAMES, MCP_PACKAGE)

ROOT = _util.ROOT


def _clone(root, target):
    """The one clone invocation every fixture tree comes from.

    A detached source leaves the initial branch name to init.defaultBranch
    and advises about it on stderr; naming it keeps the clone silent
    whatever the source's HEAD points at. Git 2.48 also advises about the
    detached checkout its internal clone performs, so that advice is
    silenced by its own key.
    """
    return subprocess.run(
        ['git', '-c', 'init.defaultBranch=main',
         '-c', 'advice.detachedHead=false', 'clone', '--quiet',
         '--no-hardlinks', str(root), str(target)],
        check=True, capture_output=True)


def _tracked_python(root=ROOT):
    root = Path(root)
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-sz', '*.py'],
        capture_output=True, check=True)
    entries = [entry for entry in listed.stdout.decode(
        'utf-8', 'surrogateescape').split('\0') if entry]
    assert entries, 'Git returned no tracked Python files'
    paths = [entry.split('\t', 1)[1] for entry in entries]
    # A symlink checks out as an ordinary file wherever core.symlinks is
    # off, so the recorded mode is the only reliable witness.
    recorded = sorted(
        entry.split('\t', 1)[1] for entry in entries
        if not entry.split(' ', 1)[0].startswith('100'))
    assert not recorded, (
        f'tracked Python paths not recorded as regular files: {recorded}')
    missing = sorted(
        path for path in paths
        if (root / path).is_symlink() or not (root / path).is_file())
    assert not missing, (
        f'tracked Python paths missing or not regular files: {missing}')
    return paths


def _enclosing_function(tree, line):
    """The innermost function whose body spans `line`, else '<module>'."""
    best_lineno = -1
    best_name = '<module>'
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, 'end_lineno', node.lineno)
            if node.lineno <= line <= end and node.lineno > best_lineno:
                best_lineno = node.lineno
                best_name = node.name
    return best_name


def _call_signature(node):
    """The callee as it is spelled, then its keyword names in order.

    A `**` unpack carries no keyword name, so it contributes nothing
    here: a row never has to be rewritten when one is introduced.
    """
    callee = ' '.join(ast.unparse(node.func).split())
    keywords = sorted(kw.arg for kw in node.keywords if kw.arg)
    return f'{callee}({", ".join(keywords)})'


def _site_signature(tree, line):
    """The call's own shape: its callee and the keywords it carries.

    The keyword names ride along because `process.wait(timeout=10)` and
    `process.wait()` are different sites, and a positional timeout is not
    the same call either. The tightest call whose own span holds the
    line wins, and among those the RIGHTMOST, so a two-line launch
    sharing its first line with a nested call is ordered the same way on
    every run rather than by whichever node the traversal reached first.

    The line is the analyser's, and it reports the `ast.Call` it
    examined, so some call always spans it: `_launch_audit.py` appends
    `node.lineno` and no other. If that ever stops holding, this raises
    rather than inventing a key for a site it did not find.
    """
    tightest = None
    tightest_rank = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        end = getattr(node, 'end_lineno', node.lineno)
        if not node.lineno <= line <= end:
            continue
        rank = (end - node.lineno, -node.lineno, -node.col_offset,
                -getattr(node, 'end_col_offset', 0))
        if tightest_rank is None or rank < tightest_rank:
            tightest, tightest_rank = node, rank
    return _call_signature(tightest)


def _spelled_signatures(source):
    """Each function the file binds, mapped to the calls it spells.

    Read from the parse rather than from the allowance table, so a row's
    function and signature are checked against what the file it names can
    actually say, and not against the table's own agreement with itself.
    The enclosing function is resolved against one precomputed span list:
    walking the tree per call is quadratic and this control runs on twelve
    CI legs.
    """
    tree = ast.parse(source)
    spans = sorted(
        ((node.lineno, getattr(node, 'end_lineno', node.lineno), node.name)
         for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        reverse=True)
    spelled = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = '<module>'
        for start, end, name in spans:
            if start > node.lineno:
                continue
            if end >= node.lineno:
                function = name
                break
        spelled.setdefault(function, set()).add(_call_signature(node))
    return spelled


def _read_spelled(root, path, cache):
    """`_spelled_signatures` for one path, read once per control run.

    `ast.unparse` on every call in every tracked module costs more than
    the whole rest of this suite, and only the paths a row names are ever
    asked about.
    """
    if path not in cache:
        text = (root / path).read_text(encoding='utf-8',
                                       errors='surrogateescape')
        cache[path] = _spelled_signatures(text)
    return cache[path]


def _row_text(key):
    """The key as the source that spells it, so a repair is a paste."""
    return '(' + ', '.join(repr(part) for part in key) + ')'


def _row_defect(key, spelled):
    """What a row the analyser did not compute gets wrong, or ''.

    Every shape the table can be mistyped into is a sentence rather
    than a raise, so a fixer who shortens a key, mistypes a path or
    approximates a signature gets the answer used everywhere else.
    """
    if len(key) != 4:
        return f'names {len(key)} components, not one site'
    if not isinstance(key[0], str) or not (ROOT / key[0]).is_file():
        return f'names {key[0]!r}, which is not a file in this tree'
    named = _read_spelled(ROOT, key[0], spelled)
    if key[2] not in named.get(key[1], set()):
        return (f'{key[0]} spells no {key[2]!r} inside a {key[1]!r}, or '
                'binds no such function')
    return ''


def _shifted_note(key, keyed):
    """The rows a new call of this shape pushed onto a different site.

    A bounded call inserted above baselined ones of the same shape
    keeps every later ordinal where it was, so the rows below the new
    ordinal now name the shifted sites — said here rather than left for
    a fixer to infer.
    """
    moved = [row for row in keyed if row[:3] == key[:3] and row[3] < key[3]]
    if not moved:
        return ''
    return ('; these rows now name a different site: '
            + ', '.join(_row_text(row) for row in moved))


def _bound_sites(source, here):
    """Every in-scope bound site as its key, then the refusal text.

    The analyser computes each launch's head, so this consumes its
    structured classification rather than re-parsing the human-readable
    refusal; a message-format change cannot move the rule. The key names
    the site by shape and position within its function, so an edit above
    a baselined launch leaves the row alone while a second call of the
    same shape in the same function becomes a site of its own. A launch
    whose head the analyser could not read is out of scope by its own
    stated boundary, named on the refusal rather than dropped. Which
    sites that boundary keeps is `tests/_launch_keep.py`'s to say, read
    by the control over the kept-out ones as well.
    """
    tree = ast.parse(source)
    found = []
    for line, head, kind in bound_sites(source, here):
        if control_keeps(head, kind):
            found.append((line, _enclosing_function(tree, line),
                          _site_signature(tree, line), kind))
    found.sort()
    seen = {}
    sites = []
    for line, function, signature, kind in found:
        seen[(function, signature)] = seen.get((function, signature), 0) + 1
        sites.append((here, function, signature, seen[(function, signature)],
                      f'{here}:{line} {kind} bound launch'))
    return sites


def test_the_inventory_refuses_a_missing_tracked_python_file(tmp):
    """A tracked package module must also exist in the worktree."""
    tree = Path(tmp) / 'tree'
    _clone(ROOT, tree)
    missing = tree / 'daedalus_bridge' / 'config.py'
    missing.unlink()
    try:
        _tracked_python(tree)
    except AssertionError as exc:
        assert 'daedalus_bridge/config.py' in str(exc), str(exc)
    else:
        raise AssertionError(
            'the layout inventory accepted a missing tracked Python file')


def test_the_inventory_refuses_a_tracked_symlink_blob(tmp):
    """A module recorded as a symlink is refused however it checks out."""
    tree = Path(tmp) / 'tree'
    _clone(ROOT, tree)
    subprocess.run(
        ['git', '-C', str(tree), 'config', 'core.symlinks', 'false'],
        check=True)
    target = tree / 'symlink-target'
    target.write_text('auth.py')
    blob = subprocess.run(
        ['git', '-C', str(tree), 'hash-object', '-w', str(target)],
        capture_output=True, check=True, text=True).stdout.strip()
    target.unlink()
    module = 'daedalus_mcp/server.py'
    subprocess.run(
        ['git', '-C', str(tree), 'update-index', '--add', '--cacheinfo',
         f'120000,{blob},{module}'], check=True)
    (tree / module).unlink()
    subprocess.run(
        ['git', '-C', str(tree), 'checkout-index', '-f', '--', module],
        check=True)
    try:
        _tracked_python(tree)
    except AssertionError as exc:
        assert module in str(exc), str(exc)
    else:
        raise AssertionError(
            'the layout inventory accepted a tracked symlink blob')


def test_the_inventory_refuses_a_symlinked_tracked_python_file(tmp):
    """A tracked package module must be a regular worktree file."""
    tree = Path(tmp) / 'tree'
    _clone(ROOT, tree)
    symlink = tree / 'daedalus_bridge' / 'config.py'
    symlink.unlink()
    symlink.symlink_to('__init__.py')
    try:
        _tracked_python(tree)
    except AssertionError as exc:
        assert 'daedalus_bridge/config.py' in str(exc), str(exc)
    else:
        raise AssertionError(
            'the layout inventory accepted a symlinked Python file')


def test_the_suite_clone_is_silent_whatever_the_source_head_state(tmp):
    """Cloning a detached source creates an initial branch, and git advises
    about the name on stderr unless the clone names it — ten hint lines per
    clone behind which a real stderr message would hide.
    """
    branch_source = Path(tmp) / 'branch-source'
    _clone(ROOT, branch_source)
    subprocess.run(
        ['git', '-C', str(branch_source), 'checkout', '-b', 'pin-branch'],
        check=True, capture_output=True)
    detached_source = Path(tmp) / 'detached-source'
    _clone(ROOT, detached_source)
    subprocess.run(
        ['git', '-C', str(detached_source), 'checkout', '--detach'],
        check=True, capture_output=True)
    from_branch = _clone(branch_source, Path(tmp) / 'from-branch')
    from_detached = _clone(detached_source, Path(tmp) / 'from-detached')
    for label, completed in (('branch', from_branch),
                             ('detached', from_detached)):
        assert completed.stderr == b'', (
            f'cloning a {label} source wrote to stderr: '
            + completed.stderr.decode('utf-8', 'replace'))


def test_the_bridge_modules_live_in_the_bridge_package(tmp):
    """The package holds exactly the modules named in BRIDGE_PACKAGE;
    the root holds none.
    """
    del tmp
    tracked = _tracked_python()
    packaged = sorted(
        path.split('/', 1)[1] for path in tracked
        if path.startswith('daedalus_bridge/') and '/' not in path.split('/', 1)[1])
    assert packaged == sorted(BRIDGE_PACKAGE), (
        f'daedalus_bridge/ holds {packaged}, expected {sorted(BRIDGE_PACKAGE)}')
    stray = sorted(
        path for path in tracked if '/' not in path
        and path in set(BRIDGE_PACKAGE) | {'bridge_config.py'})
    assert not stray, (
        f'these bridge modules are still tracked at the repository root: {stray}')


def test_the_transport_re_exports_no_json_body_helper(tmp):
    """Neither JSON body helper nor the json_body module handle is an
    attribute of the transport module.

    The helpers live in `daedalus_bridge/json_body.py`; the split that
    moved them there was required to re-export nothing, and a by-name
    import quietly keeps both names on the importing module.
    """
    program = (
        'import daedalus_bridge.http_transport as transport\n'
        'for name in ("JSONObject", "json_nests_deeper_than", "json_body"):\n'
        '    assert not hasattr(transport, name), name\n')
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.update({
        'DAEDALUS_DIR': tmp, 'DAEDALUS_PORT': '0',
        'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1',
    })
    try:
        subprocess.run(
            [sys.executable, '-c', program], env=env, check=True,
            capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(exc.stderr) from exc


def test_the_mcp_modules_live_in_the_mcp_package(tmp):
    """The package holds exactly its twelve modules, and none of the eleven
    old `mcp_*.py` names is tracked anywhere."""
    del tmp
    tracked = _tracked_python()
    packaged = sorted(
        path.split('/', 1)[1] for path in tracked
        if path.startswith('daedalus_mcp/') and '/' not in path.split('/', 1)[1])
    assert packaged == sorted(MCP_PACKAGE), (
        f'daedalus_mcp/ holds {packaged}, expected {sorted(MCP_PACKAGE)}')
    stray = sorted(
        path for path in tracked
        if path.rsplit('/', 1)[-1] in set(MCP_OLD_NAMES))
    assert not stray, (
        f'these moved MCP modules are still tracked under their old names: {stray}')


def test_the_root_holds_no_python_module_but_the_entry_points(tmp):
    """Only the two process entry points stay at the repository root."""
    del tmp
    tracked = _tracked_python()
    root_modules = sorted(path for path in tracked if '/' not in path)
    assert root_modules == ['run_tests.py', 'server.py'], (
        f'the repository root holds {root_modules}, '
        "expected ['run_tests.py', 'server.py']")


def test_no_git_subprocess_invocation_carries_a_wall_clock_bound(tmp):
    """No git subprocess launch in the tracked tree carries a wall-clock
    bound, except the ones BOUNDED_GIT_LAUNCHES allows by name.

    Scope is the tracked tree, not a hand-written module list, so a module
    added later is inside its reach with no hand edit. Each file is
    prefilted on 'subprocess': the analyser only understands launches
    spelled through `subprocess`, so a source without it cannot hold a
    launch it would see.

    A launch that only reads the local repository, or sits inside an
    enclosing suite or CI bound, is bounded by that; a hang surfaces as
    the enclosing bound, a better failure than a margin on a loaded
    runner. A launch that can block on a repository lock or the network,
    or that runs as a standalone tool with nothing above it to catch a
    hang, keeps a bound and a table row naming it.

    Which sites are in scope, and what makes a launch head readable, is
    `tests/_launch_audit.py`'s to say and its own docstrings to state.
    This consumes its structured classification rather than re-parsing
    the human-readable refusal, so a message-format change cannot move
    the rule. Reported is in scope, so each one demands a refusal or an
    allowance row. A placed launch whose head the analyser cannot read
    is reported at `unreadable` and, on its own, is not re-examined for
    git: the boundary issue, filed, and not enforced here.

    The allowance is pinned from both sides, and a key is the site's own
    shape rather than a position, so an edit above a baselined launch is
    not a change to its row. A live site with no row fails; a row whose
    function does not bind a call spelling that signature fails; a row
    matching zero live sites fails, because a stale allowance is a
    refusal; and a key two live sites share fails, because the ordinal
    that separates two calls of one shape in a function is what makes a
    key name a site. One assert reports all four, so a run answers the
    whole question and not only the class its first failure happened to
    name. Matching is on the (path, function, signature, ordinal) key, so
    another function of an allowed module, a second launch of the same
    shape in an allowed function, and a launch that has changed shape are
    each a refusal — the exemption cannot be widened by a prefix or
    substring match, and every failure names the key to paste.
    """
    del tmp
    live = {}
    for path in _tracked_python():
        source = (ROOT / path).read_text(encoding='utf-8',
                                         errors='surrogateescape')
        if not in_launch_population(path, source):
            continue
        for site in _bound_sites(source, path):
            live.setdefault(site[:4], []).append(site[4])

    # Ordered by the rendered key rather than by the tuple, so a row of
    # the wrong shape — a fixer's first draft is the old (path, line,
    # function) — sorts beside the rest instead of raising where an int
    # meets a str. A malformed row is reported below, not here.
    rows = sorted(BOUNDED_GIT_LAUNCHES, key=_row_text)
    keyed = [row for row in rows
             if len(row) == 4 and isinstance(row[3], int)]
    findings = sorted(
        f'{_row_text(key)} {sites}{_shifted_note(key, keyed)}'
        for key, sites in live.items()
        if key not in BOUNDED_GIT_LAUNCHES)
    # The keying is what carries the anti-prefix promise, so it is
    # checked rather than asserted in prose: a (path,) key alone, the
    # loosest prefix the sentence forbids, would let one row stand for
    # every site in a module. The test for that is that a row's function
    # and signature are the ones the file it names spells, that it names
    # four of them, and that the file it names is one this tree has — a
    # literal a fixer chose, a short key and a mistyped path all match
    # nothing, and each is said in the same words. The file is read here
    # rather than in the walk above, so a row in a file the prefilter
    # skips is still judged on what it says.
    for key, sites in live.items():
        if len(sites) > 1:
            findings.append(
                f'two live bounded sites share the key {_row_text(key)}; the '
                'ordinal separating repeats of one shape in a function is '
                'what makes a key name a site')
    spelled = {}
    for key in rows:
        if not live.get(key):
            findings.append(
                f'BOUNDED_GIT_LAUNCHES row {_row_text(key)} has no live '
                'bounded git launch; a stale allowance is a refusal')
        defect = _row_defect(key, spelled)
        if defect:
            findings.append(
                f'BOUNDED_GIT_LAUNCHES row {_row_text(key)} {defect}; the '
                'analyser computes every component, so the key printed '
                'here is the one to paste')
    assert not findings, '\n'.join(findings)


def test_the_clone_helper_modules_carry_the_git_launch_policy(tmp):
    """The three modules that carry the clone helper hold the deep launch
    policy: fail loudly, clone silencing configs, readable argv, and the
    whole _launch_refusals surface red-exercised by LAUNCH_REFUSAL_ROWS.

    This set is exactly the modules that carry the clone helper, chosen
    because the helper lives there; it is not a list of every module that
    launches git. The wall-clock bound is enforced tree-wide by
    test_no_git_subprocess_invocation_carries_a_wall_clock_bound, which
    reads its scope from the tracked tree.
    """
    del tmp
    for name in ('test_repo_layout.py', '_scratch_index.py',
                 'test_drain_bounds.py'):
        refusals = _launch_refusals(
            Path(__file__).with_name(name).read_text(encoding='utf-8'),
            f'tests/{name}')
        assert not refusals, '\n'.join(refusals)
    for label, snippet, limb in LAUNCH_REFUSAL_ROWS:
        row_refusals = _launch_refusals(snippet, label)
        assert len(row_refusals) == 1 and limb in row_refusals[0], (
            f'{label}: expected one refusal naming {limb!r}, '
            f'got {row_refusals}')


def test_the_head_label_separates_git_non_git_and_unreadable(tmp):
    """A bound launch's head label distinguishes a git head, a readable
    non-git constant, an unreadable head, a container of argvs, and the
    interpreter.

    The analyser enforces the bound only on a git head, so a head it cannot
    read must never be labelled a provable non-git: on the exemption path
    that would silently widen the exempt set. The name-bound git shape is
    proven against the real tree-wide rule by a plant; no tracked bounded
    launch has an unreadable head to plant, so the unresolved-name shape
    is pinned here against the real analyser, with the other shapes beside
    it. The container shape pins the multi-argv branch (deleting it leaves
    this shape unlabelled) and the interpreter shape pins the sys.executable
    recognition in first_word, so neither branch is a line with no entry.
    """
    del tmp
    shapes = (
        ('import subprocess\n'
         'GIT = \'git\'\n'
         'subprocess.run([GIT, \'status\'], check=True, timeout=30)', 'git'),
        ('import subprocess\n'
         'for argv in ([\'git\', \'init\'], [\'git\', \'add\']):\n'
         '    subprocess.run(argv, check=True, timeout=30)', 'git'),
        ('import subprocess\n'
         'subprocess.run([\'node\', \'x\'], check=True, timeout=30)',
         'non-git'),
        ('import subprocess\n'
         'import sys\n'
         'subprocess.run([sys.executable, \'pass\'], check=True, timeout=30)',
         'non-git'),
        ('import subprocess\n'
         'subprocess.run([cmd, \'status\'], check=True, timeout=30)',
         'unreadable'),
    )
    for body, label in shapes:
        bound = [r for r in _launch_refusals(body, 'probe')
                 if 'carries a timeout=' in r]
        assert len(bound) == 1, (label, bound)
        assert f'on a {label} launch' in bound[0], (label, bound[0])


def test_the_sink_pins_the_unplaced_and_ambiguous_branches(tmp):
    """The analyser's structured sink emits an unplaced and an ambiguous
    bound site for the two fail-closed branches.

    Neither branch emits a refusal string, so LAUNCH_REFUSAL_ROWS cannot
    watch them; deleting the unplaced path or the ambiguity mechanism would
    otherwise leave the suite green.
    """
    del tmp
    for label, source, expected in BOUND_SITE_ROWS:
        assert bound_sites(source, label) == expected, label


def test_a_cyclic_machinery_base_terminates_within_a_step_ceiling(tmp):
    """machinery_route's `seen` guard is load-bearing, and its mutant does
    not answer wrong — it does not stop.

    A cycle is the only input the guard answers: every other one is
    decided by an exit that needs no guard, a base the bindings table
    does not hold, a name bound twice, or a target that is not a bare
    name. So a cycle is the only shape that can tell whether the guard
    is there, and its absence is a loop rather than a value. The step
    count is what can tell that; `_step_ceiling` has why it is taken in
    a child process, and why the count covers the whole analysis rather
    than one frame.

    The two assertions pin the two halves of that. `child != os.getpid()`
    pins the BOUNDARY, and it is the control that holds: an in-process
    `within_step_ceiling` returns this process's pid and goes red.
    `sys.gettrace() is tracer` pins the SYMPTOM — the caller's tracer slot
    is unchanged — and it is not the boundary on its own, because an
    in-process version that restores cleanly passes it too.
    """
    del tmp
    source = ("import importlib\n"
              "import subprocess\n"
              "a = a\n"
              "mod = a.import_module('subprocess')\n"
              "mod.run(['git', 'status'], check=True, timeout=30)\n")
    tracer = sys.gettrace()
    sites, child = within_step_ceiling(source, 'cyclic-base')
    assert sites == []
    assert child != os.getpid()
    assert sys.gettrace() is tracer


def test_a_bounded_launch_key_survives_an_edit_above_the_site(tmp):
    """The key an allowance is written on does not move when a launch does.

    A line number is a position, so a table keyed on one reports a
    launch it already accounts for the moment an unrelated edit lands
    above the baselined call — the same class of red this branch
    exists to clear, planted here at forty lines rather than one so
    the shape is the one a real relocation produces.

    The key is the row minus its refusal, because the refusal names
    the line by design: it is the failure text, not the identity. The
    fixture is pinned to exactly one site so an analyser that stopped
    reporting cannot make the comparison pass on two empty lists.
    """
    del tmp
    head = ('import subprocess\n'
            'def reap(process):\n')
    launch = '    process.wait(timeout=10)\n'
    before = _bound_sites(head + launch, 'probe/reap.py')
    after = _bound_sites(
        head + '    # an unrelated edit above the launch\n' * 40 + launch,
        'probe/reap.py')
    assert len(before) == 1, before
    assert [row[:-1] for row in before] == [row[:-1] for row in after], (
        f'the key moved with the line: {before} then {after}')


def test_a_launch_key_separates_keywords_unpacks_and_repeats(tmp):
    """The three components of a key that a line number used to carry.

    Two calls that differ only in their keywords are two sites, so the
    keyword names are in the key; a `**` unpack names no keyword, so it
    adds nothing to the key and a row survives its introduction; and two
    identical calls in one function are two sites, which is what the
    ordinal is for. Each is driven through the real analyser, so a
    signature that stopped reading its keywords, or an ordinal that
    stopped counting, cannot leave the table's rows passing.
    """
    del tmp
    keyed = _bound_sites(
        'import subprocess\n'
        'def reap(process):\n'
        '    process.wait(timeout=10)\n'
        "    process.wait(**{'timeout': 10, 'shell': True})\n",
        'probe/keywords.py')
    assert [row[2] for row in keyed] == [
        'process.wait(timeout)', 'process.wait()'], keyed
    unpacked = _bound_sites(
        'import subprocess\n'
        'def reap(process):\n'
        "    process.wait(**{'timeout': 10})\n", 'probe/unpack.py')
    assert [row[2] for row in unpacked] == ['process.wait()'], unpacked
    repeated = _bound_sites(
        'import subprocess\n'
        'def reap(process):\n'
        '    process.wait(timeout=10)\n'
        '    process.wait(timeout=10)\n', 'probe/repeat.py')
    assert [row[3] for row in repeated] == [1, 2], repeated


def test_a_mistyped_or_moved_allowance_row_is_named_not_raised(tmp):
    """A row the analyser never computed is a sentence, never a raise.

    A fixer's first draft after this rekey is the old (path, line,
    function) row, so a short key, a path that does not resolve and a
    guessed signature are shapes this control must answer in its own
    words rather than with a traceback — and ordering the table by the
    tuple raises on that first draft, an int meeting a str. A new call
    of a shape that already has rows also leaves those rows naming other
    sites, which the unplaced message now says.
    """
    del tmp
    real = ('tests/_drain.py', 'kill_and_drain', 'process.wait(timeout)', 1)
    spelled = {}
    assert _row_defect(real, spelled) == '', real
    for key, expected in (
            (('tests/_drain.py',), 'names 1 components'),
            (('tests/_drain.py', 'kill_and_drain'), 'names 2 components'),
            (('tests/_drin.py', 'kill_and_drain', 'process.wait', 1),
             "'tests/_drin.py', which is not a file"),
            (('tests/_drain.py', 'reap', 'process.wait(timeout)', 1),
             'spells no'),
            (('tests/_drain.py', 'kill_and_drain', 'process.wait(t=5)', 1),
             'spells no')):
        defect = _row_defect(key, spelled)
        assert expected in defect, (key, defect)
    old = ('run_tests.py', 52, '_terminate_and_reap')
    assert [len(k) for k in sorted([old, real], key=_row_text)] == [3, 4]
    moved = '; these rows now name a different site: ' + _row_text(real)
    assert _shifted_note(real[:3] + (3,), [real]) == moved, real
    assert _shifted_note(real, [real]) == '', real


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
