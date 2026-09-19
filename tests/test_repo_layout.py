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

CLONE_SILENCING_CONFIG = ('init.defaultBranch=main',
                          'advice.detachedHead=false')


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
    """A git subprocess here runs unbounded and fails loudly on failure.

    Dropping the bound also drops the only hang-guard on a wedged local
    clone; the issue's remedy accepts a hang surfacing as run_tests.py's
    900-second suite bound ("SUITE TIMED OUT") instead of any wall-clock
    margin here. The audit sees this file alone, accepts only a plain
    `import subprocess`, resolves every binding derived from the module
    to a fixpoint (parameter defaults, for-targets, class bodies,
    with-targets and def returns included), and follows
    functools/importlib under any alias: an aliased or from-imported
    subprocess, an eval-built launcher, an unresolvable callee or
    receiver, or a keyword it cannot read is a refusal, never an
    accept. Called bare names are censused against in-file
    definitions, tracked bindings, imports and the runtime builtins
    table; what remains outside that census is a builtin shadowed at
    runtime, which a static read of this file cannot see, and a
    decorated definition, which is trusted as its own callee — a
    decorator returning a launcher sits outside the census by design.
    Every `git clone` launch must carry each config in
    CLONE_SILENCING_CONFIG through `-c`, so the helper's silencing
    cannot be drifted back by a hand-spelled fixture clone; the missing
    configs are named, and an argv that is not a list literal is a
    refusal. A launch whose argv does not start with the constant 'git'
    is refused; the one accepted non-git head is the literally spelled
    sys.executable, whose spelling proves the launch runs the
    interpreter rather than git. Matching the bare token `clone`
    anywhere in a git argv is deliberate over-approximation: a
    non-clone git command carrying that word must carry the silencing
    configs too.
    """
    del tmp
    refusals = _launch_refusals(
        Path(__file__).read_text(encoding='utf-8'),
        'tests/test_repo_layout.py')
    assert not refusals, '\n'.join(refusals)
    for label, snippet, limb in LAUNCH_REFUSAL_ROWS:
        row_refusals = _launch_refusals(snippet, label)
        assert len(row_refusals) == 1 and limb in row_refusals[0], (
            f'{label}: expected one refusal naming {limb!r}, '
            f'got {row_refusals}')


def _launch_refusals(source, here):
    """Every refusal limb one Python source's launches trip, naming its
    limb."""
    import builtins
    tree = ast.parse(source)
    safe_names = set(dir(builtins))
    partial_aliases = {'functools.partial', 'partial'}
    import_module_aliases = {'importlib.import_module', 'import_module'}
    machinery = {'functools': {'partial': partial_aliases},
                 'importlib': {'import_module': import_module_aliases}}
    module_aliases = {}

    def normalize(called):
        """Map a machinery alias's member to its canonical spelling."""
        if not called or '.' not in called:
            return called
        base, member = called.split('.', 1)
        canonical = module_aliases.get(base)
        if canonical:
            return f'{canonical}.{member}'
        return called

    def callee_of(call):
        """The call's callee as a spellable name, or None."""
        if isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name):
            return f'{call.func.value.id}.{call.func.attr}'
        if isinstance(call.func, ast.Name):
            return call.func.id
        return None

    def derives(value, bound):
        """Does this expression yield the module or one of its members?"""
        if isinstance(value, ast.Name):
            return (value.id == 'subprocess' or value.id in bound
                    or value.id in module_factories)
        if isinstance(value, (ast.Attribute, ast.NamedExpr)):
            return derives(value.value, bound)
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return any(derives(elt, bound) for elt in value.elts)
        if isinstance(value, ast.Dict):
            return any(derives(item, bound)
                       for item in value.values if item is not None)
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return (derives(value.elt, bound)
                    or any(derives(g.iter, bound)
                           for g in value.generators))
        if isinstance(value, ast.DictComp):
            return (derives(value.key, bound)
                    or derives(value.value, bound)
                    or any(derives(g.iter, bound)
                           for g in value.generators))
        if isinstance(value, ast.IfExp):
            return derives(value.body, bound) or derives(value.orelse, bound)
        if isinstance(value, ast.Lambda):
            return derives(value.body, bound)
        if isinstance(value, ast.Subscript):
            base = value.value
            if isinstance(base, ast.Attribute) \
                    and isinstance(base.value, ast.Name) \
                    and base.value.id == 'sys' and base.attr == 'modules':
                return True
            return derives(base, bound)
        if isinstance(value, ast.Call):
            called = normalize(callee_of(value))
            if called in partial_aliases and any(
                    derives(arg, bound) for arg in value.args):
                return True
            if called in import_module_aliases \
                    and any(isinstance(arg, ast.Constant)
                            and arg.value == 'subprocess'
                            for arg in value.args):
                return True
            if called == 'getattr' and any(
                    derives(arg, bound) for arg in value.args):
                return True
            if isinstance(value.func, ast.Attribute):
                if isinstance(value.func.value, ast.Name) \
                        and value.func.value.id == 'subprocess':
                    return False
                return derives(value.func.value, bound)
            if isinstance(value.func, ast.Name):
                if (value.func.id in bound
                        or value.func.id in module_factories):
                    return True
                if value.func.id in defined_names:
                    return False
                return any(derives(arg, bound) for arg in value.args)
            return any(derives(arg, bound) for arg in value.args)
        return False

    def resolves_safe(expr):
        """Is this receiver provably free of subprocess-derived values?"""
        if isinstance(expr, ast.Attribute):
            return resolves_safe(expr.value)
        if isinstance(expr, ast.Subscript):
            base = expr.value
            if isinstance(base, ast.Attribute) \
                    and isinstance(base.value, ast.Name) \
                    and base.value.id == 'sys' \
                    and base.attr == 'modules':
                return False
            return resolves_safe(base)
        if isinstance(expr, ast.Call):
            called = callee_of(expr)
            if called in ('eval', 'exec'):
                return False
            if called == 'getattr' or called in partial_aliases \
                    or called in import_module_aliases:
                return False
            if isinstance(expr.func, ast.Name):
                return expr.func.id in safe_names \
                    and expr.func.id not in bound
            if isinstance(expr.func, ast.Attribute):
                if isinstance(expr.func.value, ast.Name):
                    base = expr.func.value.id
                    if base in bound or base in module_factories:
                        return False
                    if base == 'subprocess':
                        return expr.func.attr == 'run'
                    return base in safe_names
                return resolves_safe(expr.func.value)
            return False
        if isinstance(expr, ast.Name):
            return expr.id not in bound and expr.id != 'subprocess'
        return True

    refusals = []
    defined_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'subprocess' and alias.asname:
                    refusals.append(
                        f'{here}:{node.lineno} aliases the subprocess '
                        f'import as {alias.asname}')
                elif alias.name in machinery and '.' not in alias.name:
                    module_aliases[alias.asname or alias.name] = alias.name
                elif '.' in alias.name and not alias.asname:
                    safe_names.add(alias.name.split('.')[0])
                else:
                    safe_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'subprocess':
                refusals.append(
                    f'{here}:{node.lineno} from-imports subprocess')
            else:
                for alias in node.names:
                    imported = f'{node.module}.{alias.name}'
                    if imported in partial_aliases:
                        partial_aliases.add(alias.asname or alias.name)
                        defined_names.add(alias.asname or alias.name)
                    elif imported in import_module_aliases:
                        import_module_aliases.add(alias.asname
                                                  or alias.name)
                        defined_names.add(alias.asname or alias.name)
                    else:
                        safe_names.add(alias.asname or alias.name)
    if not refusals and not any(
            isinstance(node, ast.Import)
            and any(alias.name == 'subprocess' and not alias.asname
                    for alias in node.names)
            for node in ast.walk(tree)):
        refusals.append(
            f'{here} declares no plain "import subprocess"; the launch '
            'audit cannot vouch for any launch')
    bindings = []
    returns = []
    yields = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings.extend(
                (target.id, node.value) for target in node.targets
                if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value \
                and isinstance(node.target, ast.Name):
            bindings.append((node.target.id, node.value))
        elif isinstance(node, ast.NamedExpr):
            bindings.append((node.target.id, node.value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_names.add(node.name)
            args = node.args
            defaults = args.defaults[-len(args.args):] \
                if args.defaults else []
            for arg, default in zip(args.args[-len(defaults):], defaults):
                bindings.append((arg.arg, default))
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                if default is not None:
                    bindings.append((arg.arg, default))
            returns.extend(
                (node.name, statement.value)
                for statement in ast.walk(node)
                if isinstance(statement, ast.Return) and statement.value)
            yields.extend(
                (node.name, statement.value)
                for statement in ast.walk(node)
                if isinstance(statement, (ast.Yield, ast.YieldFrom))
                and statement.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                bindings.append((node.target.id, node.iter))
        elif isinstance(node, ast.ClassDef):
            defined_names.add(node.name)
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    bindings.append((node.name, statement.value))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    bindings.append(
                        (item.optional_vars.id, item.context_expr))
    bound = set()
    module_factories = set()
    launcher_factories = set()
    changed = True
    while changed:
        changed = False
        for name, value in bindings:
            if name not in bound and derives(value, bound):
                bound.add(name)
                changed = True
        for name, value in returns + yields:
            if name in module_factories or name in launcher_factories:
                continue
            if derives(value, bound):
                if isinstance(value, ast.Name):
                    module_factories.add(name)
                else:
                    launcher_factories.add(name)
                    bound.add(name)
                    changed = True
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) \
                and isinstance(node.ctx, ast.Store) \
                and node.id not in bound:
            safe_names.add(node.id)
        if isinstance(node, ast.Assign) \
                and not all(isinstance(t, ast.Name) for t in node.targets) \
                and derives(node.value, bound):
            refusals.append(
                f'{here}:{node.lineno} unpacks subprocess-derived values '
                'the audit cannot follow')
        if isinstance(node, (ast.For, ast.AsyncFor)) \
                and not isinstance(node.target, ast.Name) \
                and derives(node.iter, bound):
            refusals.append(
                f'{here}:{node.lineno} unpacks subprocess-derived values '
                'the audit cannot follow')
    launches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = callee_of(node)
        if called in ('eval', 'exec'):
            refusals.append(
                f'{here}:{node.lineno} calls {called}, which the audit '
                'cannot resolve')
        spelled = normalize(called)
        if spelled and '.' in spelled:
            module, member = spelled.split('.', 1)
            if module in machinery \
                    and member not in machinery[module]:
                refusals.append(
                    f'{here}:{node.lineno} calls {called}, which the '
                    'audit cannot resolve')
        if isinstance(func, ast.Name) \
                and not (func.id in bound
                         or func.id in module_factories
                         or func.id in launcher_factories
                         or func.id in defined_names
                         or func.id in safe_names):
            refusals.append(
                f'{here}:{node.lineno} calls undefined name {func.id!r}, '
                'which the audit cannot resolve')
        if isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) \
                and (func.value.id == 'subprocess'
                     or func.value.id in bound):
            launches.append(node)
        elif isinstance(func, ast.Name) and func.id in bound:
            launches.append(node)
        elif isinstance(func, ast.NamedExpr) and func.target.id in bound:
            launches.append(node)
        elif isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Call) \
                and isinstance(func.value.func, ast.Name) \
                and func.value.func.id in module_factories:
            launches.append(node)
        elif isinstance(func, (ast.Call, ast.Subscript)):
            refusals.append(
                f'{here}:{node.lineno} calls through a receiver the '
                'audit cannot resolve')
        elif isinstance(func, ast.Attribute):
            unresolved = not resolves_safe(func.value)
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
        argv = node.args[0] if node.args else None
        words = []
        if isinstance(argv, ast.List):
            words = [elt.value if isinstance(elt, ast.Constant)
                     and isinstance(elt.value, str) else None
                     for elt in argv.elts]
        else:
            refusals.append(
                f'{here}:{node.lineno} builds an argv the audit '
                'cannot read')
        if words and words[0] != 'git':
            head = argv.elts[0]
            interpreter = (isinstance(head, ast.Attribute)
                           and isinstance(head.value, ast.Name)
                           and head.value.id == 'sys'
                           and head.attr == 'executable')
            if not interpreter:
                refusals.append(
                    f"{here}:{node.lineno} argv does not start with the "
                    "constant 'git'")
        if words and words[0] == 'git' and 'clone' in words:
            declared = {words[i + 1]
                        for i, slot in enumerate(words[:-1])
                        if slot == '-c' and words[i + 1] is not None}
            missing = [name for name in CLONE_SILENCING_CONFIG
                       if name not in declared]
            if missing:
                refusals.append(
                    f'{here}:{node.lineno} clones without the silencing '
                    + ', '.join(f'-c {name}' for name in missing))
    return refusals


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
