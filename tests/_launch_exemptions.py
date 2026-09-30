"""The four hand-kept tables the Node launch rule consults.

`tests/_node_launch_routing.py` states the RULE and the readers that decide
whether one launch is bounded; this holds only the DATA the rule consults.
They move together because `tests/_node_launch_sweep.py` — the walk — reads
all four, so moving one alone would invert the dependency it runs on.

A shared helper rather than a suite, for the reason the rule's own module
states: two suites need these decisions and a sibling SUITE import is a seam
this repository refuses.

The tables are pure DATA, and the controls that exercise them mutate one in
place and restore it, so every module that imports one imports the SAME
object: a copy would leave the plant where the walk does not read it.
"""

# The modules the sweep does not walk, each carrying its own reason rather
# than one rule applied to all of them. A member is a reason string, not a
# name in a tuple, because a member is skipped WHOLE: every launch in it is
# skipped with it, so a table that can be appended to without saying why is a
# table whose next member silences a real defect. `_carve_outs` is what
# closes that — it asks every member for the verdicts the walk WOULD report
# and requires it to report none, so an entry added to make a red go away
# reds here instead.
#
# `_noderun.py` holds the launcher's own `Popen`, so it is the subject
# rather than a site. This module and the routing suite are the walk and
# the control that runs it: a control cannot be a site of the rule it
# states, and this module is the rule.
#
# The shapes suite is a different case and is named for it: it contributes
# zero launches today, because every plant in it is a string literal and
# `ast.walk` never sees a `Call` inside one. That is a fact about how it
# is WRITTEN, not a property of it, so the entry fails OPEN — a real launch
# added to a shapes test would be suppressed rather than reported. It is
# named anyway, because the alternative is a suite whose correctness
# depends on a property of a file nobody is thinking about when they edit
# it, and the honest direction to fail is stated here rather than left for
# a reader to infer.
NOT_SITES = {
    '_noderun.py':
        "the shared launcher's own Popen IS the detector, not a site of it",
    '_node_launch_routing.py':
        'the walk itself; a rule is not a site of itself',
    'test_node_launch_routing.py':
        'the control that runs the walk over the real tree',
    'test_node_launch_routing_shapes.py':
        'every plant in it is a string literal, so it contributes no launch '
        'to any walk — a fact about how it is WRITTEN, not a property of it, '
        'so this entry fails OPEN',
}

# The boundary: a module whose child is NOT a fixed unit of work, or whose
# expiry is already classified, keeps a bound of its own. The population is
# derived and never listed, so this is closed only because every member is
# separately required to still bound its own child.
#
# Each reason NAMES the function it excuses, in backticks and nothing else
# in backticks, because the key is a module while the requirement it
# carries is per-launch: a reason that reads as though it covered the whole
# file is a reader's licence to add a fourth launch under it.
# `tests/test_node_launch_routing.py` requires every backticked name to be a
# function the module still defines, so a rename reds rather than quietly
# widening the exemption the sentence claims to cover.
CLASSIFYING_MODULES = {
    '_dashnode.py': '`_run_dashboard_node_once` scales its own bound per '
                    'retry attempt',
    '_gm_harness.py': '`_run_node` is a real-browser storage boundary '
                      '(task 3)',
    '_overlap.py': '`run_background_overlap` has its expiry already '
                   'classified by the harness',
    '_realbrowser.py': '`browser_requirements` is a real-browser probe with '
                       'a composed bound (task 3)',
    # Its executable is a function PARAMETER named `node`, so the walk
    # admits it without binding it — a name spelled `node` is in scope
    # whether or not it resolves, because admitting a site only makes the
    # control ask for more, while leaving one out loses it. The requirement
    # that keeps it honest is the one every member carries.
    '_realbrowser_workers.py': '`cdp_call` is a CDP call whose bound IS the '
                               'response deadline it asserts, classified '
                               'into CDPTimeout by the module (task 3). Its '
                               'other two launches are a real browser rather '
                               'than a Node child, and UNRESOLVED_LAUNCHES '
                               'names them by shape',
    'test_real_browser_control_extension.py':
        '`test_the_control_extension_satisfies_its_own_probe` is a '
        'real-browser probe (task 3)',
    'test_real_browser_environment.py':
        'the two real-browser probes, in '
        '`test_repository_node_probe_starts_and_terminates` and in '
        '`evaluate`, whose executable is its own node parameter (task 3)',
    'test_real_browser_harness.py':
        '`_bounded` is a real-browser probe whose executable is its own '
        'node parameter (task 3)',
}

# A launch whose executable this walk cannot resolve is a FINDING, not a
# non-launch. Proving it is not node is what closes the population; a
# resolver that quietly answers "not node" for a shape it does not know
# makes the set close on its own vocabulary instead, and a rename is all it
# takes for a real site to vanish. Every such site is named here, keyed on
# the call's SHAPE — module, function, callee — so moving a line does not
# disown a row and renaming a variable does not re-own it.
#
# A row that matches nothing is a failure, so the table cannot outlive the
# site it excused. That is what keeps it a construction rather than a list.
UNRESOLVED_LAUNCHES = {
    ('_actionlint.py', '_installed_actionlint_version', 'subprocess.run'):
        'an `actionlint` child behind the `binary` parameter, not node',
    ('_actionlint.py', '_run_actionlint', 'subprocess.run'):
        'an `actionlint` or `shellcheck` child behind `binary`, not node',
    ('_branch_boundary.py', '_git_text', 'subprocess.run'):
        'a git child behind a local helper',
    ('_coverage_comment_workflow.py', 'run_shell_block', 'subprocess.run'):
        'a bash child',
    ('_fake_gh.py', '_self_test', 'subprocess.run'):
        'a bash child',
    ('_overlap_clients.py', 'run_same_id_client_overlap', 'subprocess.Popen'):
        "a sys.executable child behind `client_argv`",
    ('_realbrowser.py', '_launch_and_reach', 'subprocess.Popen'):
        'a sys.executable child',
    ('_realbrowser_workers.py', '_browser_version', 'subprocess.run'):
        "a NODE child: the executable is this function's `browser` "
        "parameter, which no walk of one module can bind. The census "
        "reads the file, and the child is a real browser rather than a "
        "fixed unit of work.",
    ('_realbrowser_workers.py', '_diagnosis_launch', 'subprocess.Popen'):
        'a NODE child on the unresolved-parameter shape _browser_version '
        'has, and the same real-browser reason applies',
    ('_speedharness.py', 'run_workflow_script', 'subprocess.Popen'):
        "a workflow shell child behind the `command` parameter",
    ('_version_contract.py', '_versioned_git_tree', 'subprocess.run'):
        'a git child',
    ('_watcher_waits.py', '__init__', 'subprocess.Popen'):
        "a child behind the `argv` this constructor is handed",
    ('_workflowrun.py', 'run_step', 'subprocess.run'):
        "a workflow shell child: `command[0] = executable` reassigns the "
        "first element from PATH resolution the walk does not follow",
    ('test_ci_ratchets.py',
     'test_publisher_python_carries_posix_and_windows_paths_without_embedding',
     'subprocess.run'):
        "a bash child behind `_util.workflow_bash()`",
    ('test_cli_waits.py', '_run', 'subprocess.run'):
        'a sys.executable child behind the `argv` parameter',
    ('test_coverage_config.py', '_coverage_report', 'subprocess.run'):
        'a sys.executable child: the commands are sliced out of a tuple '
        "whose elements the walk reads, but `commands[-1]` is an index",
    ('test_dashboard_node_retry.py',
     'test_two_dashboard_children_cannot_be_inside_the_gate_together',
     'subprocess.Popen'):
        'a sys.executable child behind the `_gate_worker` helper',
    ('test_diff_coverage.py',
     'test_real_workflow_diffs_binary_attributed_python_as_text',
     'subprocess.run'):
        'a sys.executable child: `command[:-2]` is a slice of a list the '
        'walk does not resolve',
    ('test_helper_reimplementation.py',
     'test_the_boundary_says_which_declaration_the_branch_wrote',
     'subprocess.run'):
        'a git child',
    ('test_fake_gh.py', '_run_fake', 'subprocess.run'):
        "the `gh` double behind the `fake` parameter's own `launcher`",
    ('test_fake_gh.py', 'test_the_fake_holds_a_call_open_until_its_gate_'
     'opens', 'subprocess.Popen'):
        "the same `gh` double, held open by the gate: a `Popen` rather than"
        " a `run`, and the executable is still the `fake` parameter's own",

    ('test_file_sizes.py', '_size_fixture', 'subprocess.run'):
        'a git child',
    ('test_helper_reimplementation_js.py',
     'test_the_javascript_boundary_decides_a_row_it_is_asked_about',
     'subprocess.run'):
        'a git child',
    ('test_js_coverage_workflow.py', '_capture_updates', 'subprocess.run'):
        "a bash child behind `_util.workflow_bash()`",
    ('test_plant_restore.py', '_as_nobody', 'subprocess.run'):
        'a sys.executable child behind the `command` parameter, running the '
        "plant helper rather than a Node child: the same shape "
        '`tests/test_worker_runtime.py` carries and for the same reason',
    ('test_reserved_test_names.py', '_fixture_checkout', 'subprocess.run'):
        'a git child',
    ('test_static_guard_regressions.py', 'run', 'real_run'):
        'a mutation-planting DOUBLE for subprocess.run, holding the real '
        "one under a local and calling it through `*args`, installed by "
        "each of the two controls that patch subprocess.run",
    ('test_workflow_bash.py',
     'test_workflow_bash_resolves_relative_candidate_for_other_cwd',
     'subprocess.run'):
        "a bash child behind `_util.workflow_bash()`, the thing under test",
}

# A `node` verdict can be a FALSE one — a control that plants its own stub
# executable to prove routing writes a function whose parameter is named
# `node` — and a finding its author cannot answer is a finding they stop
# reading. Such a site would be named here, and the table is empty because
# the two `spelled` sites the tree actually holds are answered by
# `CLASSIFYING_MODULES` before this table is ever consulted, not by a row.
# What a row may and may not excuse is the `bound`/`spelled` asymmetry,
# stated in `_executable_verdict` and pinned in the shapes suite.
NOT_FIXED_WORK = {}
