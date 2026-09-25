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
from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS  # noqa: E402

ROOT = _util.ROOT

BRIDGE_PACKAGE = (
    '__init__.py',
    'atomic_file.py',
    'command_queue.py',
    'config.py',
    'data_root_lock.py',
    'delivery_stripes.py',
    'env_config.py',
    'http_transport.py',
    'json_body.py',
    'log_safe.py',
    'mcp_bootstrap.py',
    'parent_watch.py',
    'path_safety.py',
    'result_routes.py',
    'result_store.py',
    'route_answer.py',
    'segment_jobs.py',
    'segment_routes.py',
    'segment_store.py',
    'static_routes.py',
    'stream_route.py',
    'stream_service.py',
    'tab_registry.py',
    'upload_routes.py',
)

MCP_PACKAGE = (
    '__init__.py',
    'auth.py',
    'request_guard.py',
    'server.py',
    'tools_cookies.py',
    'tools_css.py',
    'tools_eval.py',
    'tools_hotfixes.py',
    'tools_media.py',
    'tools_network.py',
    'tools_tabs.py',
    'transport.py',
)

MCP_OLD_NAMES = (
    'mcp_auth.py',
    'mcp_request_guard.py',
    'mcp_server.py',
    'mcp_tools_cookies.py',
    'mcp_tools_css.py',
    'mcp_tools_eval.py',
    'mcp_tools_hotfixes.py',
    'mcp_tools_media.py',
    'mcp_tools_network.py',
    'mcp_tools_tabs.py',
    'mcp_transport.py',
)

# The launches exempt from the tree-wide no-wall-clock-bound rule, keyed by
# (repo-relative path, enclosing function) so an edit above a site cannot
# move a row onto the wrong launch.
BOUNDED_GIT_LAUNCHES = {
    ('.claude/skills/changing-daedalus/watch_all.py', '_repo_root'):
        'a standalone skill script an operator runs by hand; no suite or '
        'CI bound sits above it, so a wedged git hangs an operator with '
        'nothing to surface it',
    ('.claude/skills/changing-daedalus/watch_all.py', '_repo_slug'):
        'a standalone skill script an operator runs by hand; no enclosing '
        'bound sits above it, so a wedged git hangs an operator with '
        'nothing to surface it',
    ('scripts/gen_gitignore.py', '_check_ignore'):
        'a standalone generator an operator runs by hand; no enclosing '
        'bound sits above it, so a wedged git hangs an operator with '
        'nothing to surface it',
    ('scripts/gen_gitignore.py', 'main'):
        'a standalone generator an operator runs by hand; no suite or CI '
        'bound sits above it, so a wedged git hangs an operator with '
        'nothing to surface it',
}


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


def _bound_sites(source, here):
    """Every in-scope bound site in one source as (path, function, note).

    The analyser computes each launch's head, so this consumes its
    structured classification rather than re-parsing the human-readable
    refusal; a message-format change cannot move the rule. A launch whose
    head the analyser could not read is out of scope by its own stated
    boundary, named on the refusal rather than dropped.
    """
    tree = ast.parse(source)
    sites = []
    for line, head, kind in bound_sites(source, here):
        if head == 'git':
            function = _enclosing_function(tree, line)
            sites.append((here, function, f'{here}:{line} {kind} git launch'))
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

    A site is in scope only when the head reads as the constant `git`,
    through a `+` concat or a name bound to a literal, to a cap of
    _ARGV_UNWRAP_CAP. A `**`-unpacked keyword mapping on a git launch is
    in scope: a mapping the audit cannot read can hide a timeout. A
    readable non-git head is provably not a git launch; an unreadable head
    is the analyser's stated boundary, named on the refusal so nothing is
    silently dropped.

    The allowance is pinned from both sides: a live site with no row, a
    row matching zero or more than one live site, and a row whose function
    no longer holds a site all fail. Matching is on the exact (path,
    function) pair, so a launch in a different function of an allowed
    module, or a second launch in an allowed function, is a refusal — the
    exemption cannot be widened by a prefix or substring match.
    """
    del tmp
    live = {}
    for path in _tracked_python():
        source = (ROOT / path).read_text(encoding='utf-8',
                                         errors='surrogateescape')
        if 'subprocess' not in source:
            continue
        for site_path, function, refusal in _bound_sites(source, path):
            live.setdefault((site_path, function), []).append(refusal)

    unallowed = sorted(
        f'{key[0]}::{key[1]} {sites}' for key, sites in live.items()
        if key not in BOUNDED_GIT_LAUNCHES)
    assert not unallowed, (
        'bounded git launches with no BOUNDED_GIT_LAUNCHES row:\n'
        + '\n'.join(unallowed))
    for key in sorted(BOUNDED_GIT_LAUNCHES):
        assert live.get(key), (
            f'BOUNDED_GIT_LAUNCHES row {key} has no live bounded git '
            'launch; a stale allowance is a refusal')
    for key in sorted(BOUNDED_GIT_LAUNCHES):
        count = len(live.get(key, ()))
        assert count == 1, (
            f'BOUNDED_GIT_LAUNCHES row {key} matches {count} live bounded '
            'git launches; exactly one is required, so two bounded git '
            'launches in one function must be given different functions')


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
    non-git constant, and an unreadable head.

    The analyser enforces the bound only on a git head, so a head it cannot
    read must never be labelled a provable non-git: on the exemption path
    that would silently widen the exempt set. The name-bound git shape is
    proven against the real tree-wide rule by a plant; no tracked bounded
    launch has an unreadable head to plant, so the unresolved-name shape
    is pinned here against the real analyser, with the non-git shapes
    beside it so a reading that collapses the labels dies.
    """
    del tmp
    shapes = (
        ('GIT = \'git\'\n'
         'subprocess.run([GIT, \'status\'], capture_output=True,\n'
         '               check=True, timeout=30)', 'git'),
        ('subprocess.run([\'node\', \'x\'], capture_output=True,\n'
         '               check=True, timeout=30)', 'non-git'),
        ('subprocess.run([cmd, \'status\'], capture_output=True,\n'
         '               check=True, timeout=30)', 'unreadable'),
    )
    for body, label in shapes:
        bound = [r for r in _launch_refusals(
            'import subprocess\n' + body, 'probe')
            if 'carries a timeout=' in r]
        assert len(bound) == 1, (label, bound)
        assert f'on a {label} launch' in bound[0], (label, bound[0])


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
