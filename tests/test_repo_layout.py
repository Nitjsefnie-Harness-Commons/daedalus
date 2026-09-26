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
from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS  # noqa: E402

ROOT = _util.ROOT

BRIDGE_PACKAGE = (
    '__init__.py',
    'atomic_file.py',
    'command_queue.py',
    'config.py',
    'dashboard_drain.py',
    'data_root_lock.py',
    'delivery_stripes.py',
    'env_config.py',
    'http_transport.py',
    'json_body.py',
    'log_safe.py',
    'mcp_bootstrap.py',
    'parent_watch.py',
    'path_safety.py',
    'queue_order.py',
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

BOUNDED_GIT_LAUNCHES = {
    # Every in-scope bound site in the tracked tree, keyed by
    # (path, line, function) so an allowance is for THAT site: a second
    # bounded call in an already-allowed function is a different site and
    # needs a row of its own, and an edit above a site cannot move a row
    # onto another launch. Each reason says what the call is and why it
    # cannot hang a git launch; a receiver the analyser cannot prove is
    # reported rather than passed, and this table is that report's
    # disposition.
    ('.claude/skills/changing-daedalus/watch_all.py', 362, '_aggregate'):
        'a queue read with a deadline: the queue is drained,'
        'nothing is launched',
    ('.claude/skills/changing-daedalus/watch_all.py', 66, '_repo_root'):
        'a standalone skill script an operator runs by hand; no'
        'suite or CI bound sits above it, so a wedged git hangs an'
        'operator',
    ('.claude/skills/changing-daedalus/watch_all.py', 170, '_repo_slug'):
        'a standalone skill script an operator runs by hand; no'
        'enclosing bound sits above it, so a wedged git hangs an'
        'operator',
    ('run_tests.py', 52, '_terminate_and_reap'):
        'a child wait while tearing the suite down; the runner is'
        'the bound',
    ('run_tests.py', 56, '_terminate_and_reap'):
        'a second child wait in the same teardown, after a kill',
    ('scripts/gen_gitignore.py', 101, '_check_ignore'):
        'a standalone generator an operator runs by hand; no'
        'enclosing bound sits above it, so a wedged git hangs an'
        'operator',
    ('scripts/gen_gitignore.py', 115, 'main'):
        'a standalone generator an operator runs by hand; no suite'
        'or CI bound sits above it, so a wedged git hangs an'
        'operator',
    ('tests/_drain.py', 25, 'kill_and_drain'):
        'a drain of an already-killed process: it can only return',
    ('tests/_drain.py', 33, 'kill_and_drain'):
        'the reap that follows that drain, on the same dead process',
    ('tests/_realbrowser_workers.py', 115, '_devtools_targets'):
        'an HTTP read: a socket, not a git process, and the'
        ' timeout is the bound itself',
    ('tests/_realbrowser_workers.py', 89, '_retire_browser'):
        'a browser-process wait while retiring it',
    ('tests/_realbrowser_workers.py', 92, '_retire_browser'):
        'the reap after that wait, on the same process',
    ('tests/_repo.py', 45, 'git_index'):
        'a generic git runner: the bound covers the index-writing'
        'commands its callers pass, not only the reads',
    ('tests/_speedharness.py', 171, '_reap_process'):
        "a measurement process wait inside the harness's own"
        'teardown',
    ('tests/_speedharness.py', 187, '_reap_process'):
        'the reap that follows it, on the same process',
    ('tests/_util.py', 369, '_startup_observations'):
        'a thread join on a thread this helper started',
    ('tests/_util.py', 496, 'bridge'):
        'a helper waiting for a port line; the caller bounds it',
    ('tests/_util.py', 556, 'get'):
        'an HTTP helper opening a socket; no git '
        'process is behind it',
    ('tests/_util.py', 560, 'get_json'):
        'an HTTP helper opening a socket; no git '
        'process is behind it',
    ('tests/_util.py', 574, 'header_stream'):
        'a connection constructor: it opens a socket and returns'
        ' a client, and no git process sits behind a socket',
    ('tests/_util.py', 565, 'post_json'):
        'an HTTP helper opening a socket; no git '
        'process is behind it',
    ('tests/_util.py', 549, 'request'):
        'an HTTP read: a socket, not a git process, and the'
        ' timeout is the bound itself',
    ('tests/test_aggregate_gate.py', 324,
     'test_every_single_dependency_result_is_tabled'):
        'a table builder called with a keyword mapping; it builds a'
        'table, it launches nothing',
    ('tests/test_aggregate_gate.py', 392,
     'test_two_dependencies_are_decided_jointly'):
        'the same table builder, keyed from a zipped mapping',
    ('tests/test_bridge_startup.py', 616,
     'test_dashboard_responses_refuse_cross_origin_framing'):
        'an HTTP read: a socket, not a git process, and the'
        ' timeout is the bound itself',
    ('tests/test_dashboard_behaviour.py', 538, 'popen'):
        'a test double constructed with a keyword mapping; it'
        'records, it does not launch',
    ('tests/test_dashboard_gate.py', 102,
     'test_gate_is_released_by_the_os_when_the_holder_is_killed'):
        'a wait on a holder this test started, in a teardown that'
        ' kills it only while it is still running; the timeout is'
        ' the bound itself',
    ('tests/test_dashboard_gate.py', 104,
     'test_gate_is_released_by_the_os_when_the_holder_is_killed'):
        'the gate child this test started; the timeout is the'
        ' bound itself',
    ('tests/test_dashboard_node_retry.py', 681,
     'test_two_dashboard_children_cannot_be_inside_the_gate_together'):
        'a wait on the two gate children this test started;'
        ' the timeout is the bound itself',
    ('tests/test_mcp_entry_point.py', 37, '_cleanup_mcp'):
        'an MCP process wait while the test tears it down',
    ('tests/test_mcp_entry_point.py', 40, '_cleanup_mcp'):
        'the reap after that wait, on the same process',
    ('tests/test_mcp_server.py', 122, '_mcp_request'):
        'a connection constructor: it opens a socket and returns'
        ' a client, and no git process sits behind a socket',
    ('tests/test_mcp_server.py', 111, '_surface_responder_errors'):
        'a thread join on a thread the fixture started',
    ('tests/test_mcp_server.py', 940, 'callers'):
        "an MCP tool call whose timeout is the tool's, not a bound"
        'on a process',
    ('tests/test_mcp_server.py', 554,
     'test_a_nonpositive_mcp_timeout_admits_no_command'):
        "the test's subject: an MCP call the server must reject for"
        'its timeout',
    ('tests/test_mcp_server.py', 1149,
     'test_bearer_middleware_rejects_duplicate_authorization_headers'):
        'a connection constructor: it opens a socket and returns'
        ' a client, and no git process sits behind a socket',
    ('tests/test_mcp_server.py', 1429,
     'test_mcp_port_zero_announces_the_actual_bound_port'):
        'an assertion on a bound event the module sets; the wait IS'
        'the assertion',
    ('tests/test_parent_watch.py', 369,
     'test_bounded_wait_reports_live_child_port_and_watch_state'):
        'a helper waiting for a child to exit; the test bounds it',
    ('tests/test_real_browser_classification.py', 266,
     'test_answering_control_worker_twice_marks_worker_absence_our_failure'):
        'a mock assertion on a wait call: it asserts, it does not'
        'wait',
    ('tests/test_real_browser_classification.py', 311,
     'test_control_browser_exit_ends_the_diagnosis_without_a_verdict'):
        'the same mock assertion, on the exit control',
    ('tests/test_real_browser_classification.py', 275,
     'test_control_diagnosis_launches_both_extensions_twice_before_guilt'):
        'the same mock assertion, in the twice-launched control',
    ('tests/test_real_browser_classification.py', 61,
     'test_indeterminate_e2big_diagnostics_are_harness_failures'):
        'a mock patch whose keyword mapping substitutes the launch'
        'the test is asserting on',
    ('tests/test_real_browser_classification.py', 298,
     'test_unanswered_control_worker_leaves_the_skip_with_the_machine'):
        'the same mock assertion, on the unanswered control',
    ('tests/test_real_browser_classification.py', 323,
     'test_unreadable_control_answer_polls_again_instead_of_settling'):
        'the same mock assertion, on the unreadable control',
    ('tests/test_real_browser_harness.py', 617, 'exercise'):
        "the same navigation, on the harness's own page",
    ('tests/test_real_browser_harness.py', 610, 'first_navigation'):
        'a browser navigation whose timeout is the bound itself',
    ('tests/test_segment_routes.py', 101, 'refusing'):
        'a test double delegating with its arguments; it launches'
        'nothing of its own',
    ('tests/test_stream_lifecycle.py', 38, '_open_stream'):
        'a connection constructor: it opens a socket and returns'
        ' a client, and no git process sits behind a socket',
    ('tests/test_suite_runner.py', 399,
     'test_output_close_failure_reaps_the_spawned_suite'):
        'a suite-process wait inside the reaping the test asserts',
    ('tests/test_suite_runner.py', 405,
     'test_output_close_failure_reaps_the_spawned_suite'):
        'the reap that follows, on the same process',
    ('tests/test_suite_runner.py', 408,
     'test_output_close_failure_reaps_the_spawned_suite'):
        'the final reap, on the same process',
    ('tests/test_watcher_budget.py', 173, 'stop'):
        'a fixture stopping a child it started',
    ('tests/test_watcher_budget.py', 565,
     'test_a_graceful_exit_leaves_no_children_behind'):
        'the same, in the graceful-exit control',
    ('tests/test_watcher_budget.py', 381,
     'test_the_children_die_with_their_parent'):
        'a parent handle stopping a child the test started',
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
    """Every in-scope bound site as (path, function, line, note).

    The analyser computes each launch's head, so this consumes its
    structured classification rather than re-parsing the human-readable
    refusal; a message-format change cannot move the rule. The LINE is in
    the key, so an allowance is for that site: a second bounded call in an
    already-allowed function is a different site and needs a row of its
    own. A launch whose head the analyser could not read is out of scope
    by its own stated boundary, named on the refusal rather than dropped.
    """
    tree = ast.parse(source)
    sites = []
    for line, head, kind in bound_sites(source, here):
        if head in ('git', 'ambiguous') or kind == 'unplaced':
            function = _enclosing_function(tree, line)
            sites.append((here, function, line,
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

    A site is in scope two ways, and both are decidable rather than a
    list of spellings. A launch the analyser places whose head reads as
    the constant `git` (through a `+` concat, a single-bound name, or a
    container of argv literals, to a cap of `ARGV_UNWRAP_CAP` in
    `tests/_argv_read.py`) is refused, as is a `**`-unpacked mapping on
    it; a name bound more than once in a module reads `unreadable`,
    never a guessed non-git. Second, and this is what keeps the policy
    decidable: ANY other call carrying a `timeout=` or a `**`-unpacked
    mapping is reported at `unreadable` unless its receiver is PROVED a
    fixed, non-launch value. Proof is a bare name this module binds and
    the analyser read — not a method parameter, not an attribute, not a
    subscript, and not a name bound to a call the import machinery makes
    (its argument decides what it returns, so an argument the analyser
    cannot read leaves the module itself unknown).

    So a receiver reached through an import name held in a variable, an
    `import_module` argument it cannot read, a class attribute however
    that attribute was bound, an instance, or a run-time namespace is
    not passed. It is reported, and reported is in scope: the rule then
    demands a refusal or an allowance row. That is the whole of what the
    analyser resolves rather than enumerates — the `import_module`
    argument, whose spellings the reader can list, because it makes a
    placed launch head git instead of a reported one at `unreadable`, and
    nothing else.

    A placed launch whose head is a dynamic expression (a parameter, a
    call, a slice, a comprehension, a starred argument, or a return value)
    reads `unreadable` and, as a single placed launch, is not re-examined
    for git. The shipped tree holds a bounded git launch in the allowance
    table only, so the summary sentence is true of it; a future one with
    no row is the filed boundary issue, not enforced here.

    The allowance is pinned from both sides: a live site with no row, a
    row matching zero or more than one live site, and a row whose function
    no longer holds a site all fail. Matching is on the exact (path, line,
    function) key, so a launch in a different function of an allowed
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
        for site_path, function, line, refusal in _bound_sites(source, path):
            live.setdefault((site_path, line, function), []).append(refusal)

    unallowed = sorted(
        f'{key[0]}::{key[1]}:{key[2]} {sites}'
        for key, sites in live.items()
        if key not in BOUNDED_GIT_LAUNCHES)
    assert not unallowed, (
        'bounded git launches with no BOUNDED_GIT_LAUNCHES row:\n'
        + '\n'.join(unallowed))
    for key in sorted(BOUNDED_GIT_LAUNCHES):
        assert live.get(key), (
            f'BOUNDED_GIT_LAUNCHES row {key} has no live bounded git '
            'launch; a stale allowance is a refusal')
    # The keying is what carries the anti-prefix promise, so it is
    # checked rather than asserted in prose: a (path,) key alone, the
    # loosest prefix the sentence forbids, would let one row stand for
    # every site in a module.
    assert all(len(key) == 3 for key in BOUNDED_GIT_LAUNCHES), (
        'every BOUNDED_GIT_LAUNCHES key names a site, not a module')
    for key in sorted(BOUNDED_GIT_LAUNCHES):
        count = len(live.get(key, ()))
        assert count == 1, (
            f'BOUNDED_GIT_LAUNCHES row {key} matches {count} live bounded '
            'sites; exactly one is required, so a site in a function that '
            'already has a row must be given its own')


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
