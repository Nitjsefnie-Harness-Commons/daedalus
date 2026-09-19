#!/usr/bin/env python3
"""The repository's module layout, pinned so it cannot silently drift back.

Generic module names at the repository root occupy the top-level import
namespace of every process started there. The bridge's modules live in the
`daedalus_bridge/` package instead; this suite is what keeps them there.
"""
import ast
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

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


def test_the_inventory_refuses_a_missing_tracked_python_file(tmp):
    """A tracked package module must also exist in the worktree."""
    tree = Path(tmp) / 'tree'
    subprocess.run(
        ['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(tree)],
        check=True)
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
    subprocess.run(
        ['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(tree)],
        check=True)
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
    subprocess.run(
        ['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(tree)],
        check=True)
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


def test_the_bridge_modules_live_in_the_bridge_package(tmp):
    """The package holds exactly its thirteen modules; the root holds none."""
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
    """A git subprocess here runs unbounded and fails loudly on failure.

    Dropping the bound also drops the only hang-guard on a wedged local
    clone; the issue's remedy accepts a hang surfacing as run_tests.py's
    900-second suite bound ("SUITE TIMED OUT") instead of any wall-clock
    margin here. The audit sees this file alone, accepts only a plain
    `import subprocess`, and resolves every binding derived from the
    module to a fixpoint: an aliased or from-imported subprocess, a call
    through a receiver it cannot resolve, or a keyword it cannot read is
    a refusal, never an accept.
    """
    del tmp
    here = 'tests/test_repo_layout.py'
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))

    def derives(value, bound):
        """Does this expression yield the module or one of its members?"""
        if isinstance(value, ast.Name):
            return value.id == 'subprocess' or value.id in bound
        if isinstance(value, (ast.Attribute, ast.NamedExpr)):
            return derives(value.value, bound)
        if isinstance(value, ast.Subscript):
            base = value.value
            if isinstance(base, ast.Attribute) \
                    and isinstance(base.value, ast.Name) \
                    and base.value.id == 'sys' and base.attr == 'modules':
                return True
            return derives(base, bound)
        if isinstance(value, ast.Call):
            called = None
            if isinstance(value.func, ast.Attribute) \
                    and isinstance(value.func.value, ast.Name):
                called = f'{value.func.value.id}.{value.func.attr}'
            elif isinstance(value.func, ast.Name):
                called = value.func.id
            if called in ('functools.partial', 'partial') and any(
                    derives(arg, bound) for arg in value.args):
                return True
            if called in ('importlib.import_module', 'import_module') \
                    and any(isinstance(arg, ast.Constant)
                            and arg.value == 'subprocess'
                            for arg in value.args):
                return True
            if called == 'getattr' and any(
                    derives(arg, bound) for arg in value.args):
                return True
        return False

    bindings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings.extend(
                (target, node.value) for target in node.targets
                if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value \
                and isinstance(node.target, ast.Name):
            bindings.append((node.target, node.value))
        elif isinstance(node, ast.NamedExpr):
            bindings.append((node.target, node.value))
    bound = set()
    changed = True
    while changed:
        changed = False
        for target, value in bindings:
            if target.id not in bound and derives(value, bound):
                bound.add(target.id)
                changed = True
    refusals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'subprocess' and alias.asname:
                    refusals.append(
                        f'{here}:{node.lineno} aliases the subprocess '
                        f'import as {alias.asname}')
        elif isinstance(node, ast.ImportFrom) and node.module == \
                'subprocess':
            refusals.append(
                f'{here}:{node.lineno} from-imports subprocess')
    if not refusals and not any(
            isinstance(node, ast.Import)
            and any(alias.name == 'subprocess' and not alias.asname
                    for alias in node.names)
            for node in ast.walk(tree)):
        refusals.append(
            f'{here} declares no plain "import subprocess"; the launch '
            'audit cannot vouch for any launch')
    launches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) \
                and (func.value.id == 'subprocess'
                     or func.value.id in bound):
            launches.append(node)
        elif isinstance(func, ast.Name) and func.id in bound:
            launches.append(node)
        elif isinstance(func, ast.NamedExpr) and func.target.id in bound:
            launches.append(node)
        elif isinstance(func, (ast.Call, ast.Subscript)):
            refusals.append(
                f'{here}:{node.lineno} calls through a receiver the '
                'audit cannot resolve')
        elif isinstance(func, ast.Attribute):
            receiver = func.value
            unresolved = isinstance(receiver, ast.Subscript) \
                and isinstance(receiver.value, ast.Attribute) \
                and isinstance(receiver.value.value, ast.Name) \
                and receiver.value.value.id == 'sys' \
                and receiver.value.attr == 'modules'
            if isinstance(receiver, ast.Call):
                inner = receiver.func
                unresolved = unresolved or (
                    isinstance(inner, ast.Name)
                    and (inner.id == 'getattr' or inner.id in bound)
                    or isinstance(inner, ast.Attribute)
                    and isinstance(inner.value, ast.Name)
                    and f'{inner.value.id}.{inner.attr}'
                    in ('importlib.import_module', 'import_module'))
            if unresolved:
                refusals.append(
                    f'{here}:{node.lineno} calls through a receiver the '
                    'audit cannot resolve')
    if not launches:
        refusals.append(
            f'{here} declares no launch the audit can see through '
            '"subprocess" or a binding derived from it')
    for node in launches:
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        if None in keywords:
            refusals.append(
                f'{here}:{node.lineno} unpacks a keyword mapping the '
                'audit cannot read')
            continue
        if 'timeout' in keywords:
            refusals.append(
                f'{here}:{node.lineno} carries a '
                f'timeout={ast.dump(keywords["timeout"])} argument')
        check = keywords.get('check')
        if not (isinstance(check, ast.Constant) and check.value is True):
            refusals.append(
                f'{here}:{node.lineno} does not fail loudly on a failed '
                'git command')
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr != 'run':
            refusals.append(
                f'{here}:{node.lineno} launches through '
                f'{node.func.value.id}.{node.func.attr}, '
                'which the audit does not see')
    assert not refusals, '\n'.join(refusals)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
