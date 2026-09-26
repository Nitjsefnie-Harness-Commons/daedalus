"""The same-named locals that are not re-implementations.

Keyed by (repo-relative path, name), so a row cannot be widened by a
prefix or a substring match. Every entry is a pre-existing collision: a
row may name a site the branch did not create, and a row naming a file
the branch adds or edits is itself a finding, so the table can absorb
what `main` lands underneath it while excusing nothing the branch wrote.
"""
UNCONSOLIDATED_NAMES = {
    ('tests/_bash_resolver_scan.py', '_ModuleFacts'):
        'each guard analyses a different launcher surface, and the facts '
        'class each builds carries that surface own names, so neither is a '
        'copy of the other',
    ('tests/_bash_resolver_scan.py', '_analyze'):
        'the three analysers return their own violation records and take '
        'different memo arguments, so none composes the others',
    ('tests/_bash_resolver_scan.py', '_binding_of'):
        'the drain reader also accepts the = -less forms, so the two would '
        'answer differently for one node',
    ('tests/_bash_resolver_scan.py', '_check_launch'):
        'one reads the argv a resolver passes, the other refuses a cwd it '
        'cannot resolve, and the signatures do not meet',
    ('tests/_bash_resolver_scan.py', '_launch_method'):
        'the coverage guard also accepts any callee that spells cwd readably, '
        'which the bash resolver launcher set does not',
    ('tests/_bash_resolver_scan.py', '_synthetic_violations'):
        'each runs its own analyser over the synthetic source, and the '
        'coverage guard threads the memo keeps it needs',
    ('tests/_bash_resolver_scan.py', '_tree_violations'):
        'one enumerates test modules and the other every python source, so a '
        'shared reader would have to carry both sets',
    ('tests/_bash_resolver_scan.py', '_visit'):
        'each walks its own facts object under its own signature and its own '
        'per-call state',
    ('tests/_clientstate.py', '_output_text'):
        'the client-state reader strips the decoded value and the dashboard '
        'reader keeps it verbatim, so one would lose a behaviour',
    ('tests/_command_type_readers.py', '_literal_value'):
        'one reads a text literal and the other an evaluated expression node, '
        'so the argument types are not interchangeable',
    ('tests/_command_type_readers.py', '_parents'):
        'the scope map is built over memoised nodes only, so it is not the '
        'plain walk the command readers want',
    ('tests/_command_type_readers.py', '_refuse'):
        'each refuses with its own message shape: a where and what pair '
        'against a path, root, node and detail',
    ('tests/_control_writes.py', '_bound_names'):
        'one yields the names an assignment target binds and the other '
        'returns every name a node stores, so they answer differently',
    ('tests/_coverage_guard.py', '_ModuleFacts'):
        'each guard analyses a different launcher surface, and the facts '
        'class each builds carries that surface own names, so neither is a '
        'copy of the other',
    ('tests/_coverage_guard.py', '_analyze'):
        'the three analysers return their own violation records and take '
        'different memo arguments, so none composes the others',
    ('tests/_coverage_guard.py', '_check_launch'):
        'one reads the argv a resolver passes, the other refuses a cwd it '
        'cannot resolve, and the signatures do not meet',
    ('tests/_coverage_guard.py', '_launch_method'):
        'the coverage guard also accepts any callee that spells cwd readably, '
        'which the bash resolver launcher set does not',
    ('tests/_coverage_guard.py', '_synthetic_violations'):
        'each runs its own analyser over the synthetic source, and the '
        'coverage guard threads the memo keeps it needs',
    ('tests/_coverage_guard.py', '_visit'):
        'each walks its own facts object under its own signature and its own '
        'per-call state',
    ('tests/_coverage_scopes.py', '_bound_names'):
        'one yields the names an assignment target binds and the other '
        'returns every name a node stores, so they answer differently',
    ('tests/_coverage_scopes.py', '_parents'):
        'the scope map is built over memoised nodes only, so it is not the '
        'plain walk the command readers want',
    ('tests/_dashnode.py', '_output_text'):
        'the client-state reader strips the decoded value and the dashboard '
        'reader keeps it verbatim, so one would lose a behaviour',
    ('tests/_drain_scan.py', '_analyze'):
        'the three analysers return their own violation records and take '
        'different memo arguments, so none composes the others',
    ('tests/_drain_scan.py', '_binding_of'):
        'the drain reader also accepts the = -less forms, so the two would '
        'answer differently for one node',
    ('tests/_drain_scan.py', '_scan'):
        'one scans a module text and the other evaluates an expression '
        'against a binding set, so they share no argument',
    ('tests/_drain_scan.py', '_tree_violations'):
        'one enumerates test modules and the other every python source, so a '
        'shared reader would have to carry both sets',
    ('tests/_jsroute_sweep.py', '_indent'):
        'the sweep helper indents a block of generated JavaScript while the '
        'two yaml readers measure one line, so three unrelated meanings',
    ('tests/_mcp_code_eval.py', '_scan'):
        'one scans a module text and the other evaluates an expression '
        'against a binding set, so they share no argument',
    ('tests/_mcp_import_closure.py', '_refuse'):
        'each refuses with its own message shape: a where and what pair '
        'against a path, root, node and detail',
    ('tests/_pyroute_keys.py', '_literal_value'):
        'one reads a text literal and the other an evaluated expression node, '
        'so the argument types are not interchangeable',
    ('tests/_pyroute_live.py', '_argument_value'):
        'one reads a call-site entry and the other an expression with a '
        'caller and a sender resolver',
    ('tests/_pyroute_values.py', '_argument_value'):
        'one reads a call-site entry and the other an expression with a '
        'caller and a sender resolver',
    ('tests/_util.py', 'load'):
        'this one imports a module by path and the workflow one decodes a '
        'workflow file jobs, so neither name can serve the other',
    ('tests/_wfjobs.py', 'load'):
        'this one imports a module by path and the workflow one decodes a '
        'workflow file jobs, so neither name can serve the other',
    ('tests/_workflows.py', '_entry'):
        'the workflow reader decodes a mapping key through the bounded scalar '
        'reader, where the yaml one only splits at the first colon',
    ('tests/_workflows.py', '_indent'):
        'the sweep helper indents a block of generated JavaScript while the '
        'two yaml readers measure one line, so three unrelated meanings',
    ('tests/_yamllines.py', '_entry'):
        'the workflow reader decodes a mapping key through the bounded scalar '
        'reader, where the yaml one only splits at the first colon',
    ('tests/_yamllines.py', '_indent'):
        'the sweep helper indents a block of generated JavaScript while the '
        'two yaml readers measure one line, so three unrelated meanings',
    ('tests/test_aggregate_gate.py', '_run'):
        'this builds one workflow run as the actions API reports it, where '
        'the shared _run boots a node scenario',
    ('tests/test_aggregate_needs.py', '_fixture'):
        'this writes one fixture workflow into a fresh tmp directory, where '
        'the owner reads a fake-GitHub answer fragment',
    ('tests/test_bash_resolver_scan.py', '_synthetic'):
        'a one-line delegate to the bash resolver own synthetic entry, and '
        'the name it collides with is the drain analyser',
    ('tests/test_case_fold_parent.py', '_load'):
        'this loads a bridge module by path under a name of its own, where '
        'the owner is a JSON file reader',
    ('tests/test_ci_ratchets.py', '_git'):
        'this runs git with text output and a scrubbed child environment, '
        'which the shared runner does not set',
    ('tests/test_ci_wait.py', '_run'):
        'this builds one workflow run against a SHA, optionally without a '
        'workflow id, where the shared _run boots a node scenario',
    ('tests/test_cli_waits.py', '_run'):
        'this runs one argv under a supplied environment with a 60s bound, '
        'where the shared _run boots a node scenario',
    ('tests/test_cli.py', 'run_cli'):
        'this spawns the real CLI as a process against a live bridge and '
        'returns the CompletedProcess, where the owner parses in process, '
        'fakes ext_cmd and returns the recording with stdout',
    ('tests/test_cli_duplicate_admission.py', 'run_cli'):
        'this spawns the real CLI as a process against a live bridge, the '
        'half the owner deliberately is not, since a retried delivery has '
        'to survive a real process boundary',
    ('tests/test_cli_error_reporting.py', 'run_cli'):
        'this spawns the real CLI to read what it prints on a malformed '
        'argument, where the owner captures stdout from an in-process '
        'dispatch and never sees a traceback cross a process',
    ('tests/test_cli_waits.py', 'run_cli'):
        'this runs a typed subcommand that enqueues a command and answers '
        'it afterwards, where the owner dispatches with the answer already '
        'canned and nothing is ever enqueued',
    ('tests/test_screenshot_quality.py', 'run_cli'):
        'this patches three module attributes and records through its own '
        'RecordingApi, where the owner patches one ext_cmd and records '
        'through RecordingExtCmd',
    ('tests/test_coverage_bindings.py', '_scope_violations'):
        'this renders the expected violation strings for a synthetic source, '
        'where the owner orders real calls within a scope',
    ('tests/test_coverage_decorated_launch.py', '_planted'):
        'this writes one probe into the tree the control owns and reads the '
        'verdict, where the owner reverts one converted site in a scratch '
        'tree and reports where it drains',
    ('tests/test_dashboard_fanout.py', '_order'):
        'this returns the real daedalus_bridge.queue_order the loaded '
        'command_queue mints with, where the owner builds a JS case tuple',
    ('tests/test_dashboard_harness.py', '_harness_failure'):
        'this drives the shipped retry entry with bounded steps, where the '
        'owner reads a relay harness failure under a plan',
    ('tests/test_dashboard_tab_events.py', '_run'):
        'this runs the dashboard node and parses its JSON, where the shared '
        '_run boots a recorded boundary scenario',
    ('tests/test_delivery_stripe_acceptance.py', '_lines'):
        'this splits a file own text into lines, where the owner retains '
        'whether each physical line ended',
    ('tests/test_diff_coverage.py', '_git'):
        'this runs one git command in a fixture repository with text output, '
        'where the shared runner captures bytes only',
    ('tests/test_diff_coverage_javascript.py', '_run'):
        'this runs the reporter script inside the fixture directory, where '
        'the shared _run boots a node scenario',
    ('tests/test_env_publication.py', '_bound_names'):
        'this one yields each name with the value bound beside it, in source '
        'order, which neither owner takes an argument for',
    ('tests/test_extension_manifest.py', '_entry'):
        'this builds a manifest mutant from an index, key, subkey and value, '
        'so it is not a mapping-line reader at all',
    ('tests/test_js_coverage.py', '_git'):
        'this runs git with text output in a scratch index, where the shared '
        'runner captures bytes',
    ('tests/test_line_lengths.py', '_git'):
        'this runs git under a scrubbed child environment, which the shared '
        'runner does not set',
    ('tests/test_line_lengths.py', '_lines'):
        'this joins texts and encodes them as the byte-length source, where '
        'the owner splits a workflow keeping line endings',
    ('tests/test_mcp_live_tools.py', '_row'):
        'this builds one tool row from a command type, its fields and a '
        'builder, where the owner builds a getter-argument case',
    ('tests/test_mcp_refusal_drain.py', '_load_mcp'):
        'this drives the already-booted bridge with an empty token, which the '
        'shared loader base_url-first signature does not express',
    ('tests/test_overlap_bound.py', '_bound_source'):
        'this slices the shipped prelude bound machinery at the entry IIFE, '
        'where the owner reads a job field after timeout-minutes',
    ('tests/test_real_browser_harness.py', '_browser_version'):
        'this asserts a stubbed --version call, where the owner asks a '
        'browser object what it calls itself',
    ('tests/test_real_browser_harness_recovery.py', '_control_target'):
        'this names a different extension origin under test, which is the '
        'whole point of the case',
    ('tests/test_relay_example_placeholders.py', '_run'):
        'this runs the relay harness under one source and a plan, where the '
        'shared _run boots a recorded scenario',
    ('tests/test_repo_layout.py', '_enclosing_function'):
        'this finds the innermost function whose body spans a line, where the '
        'owner walks up from a node through a parent map',
    ('tests/test_result_routes.py', '_load'):
        'this loads result_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_result_stripe.py', '_load'):
        'this loads result_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_runner_refuses_unawaited.py', '_run'):
        'this runs the suite collector with warnings recorded, where the '
        'shared _run boots a node scenario',
    ('tests/test_static_routes.py', '_load'):
        'this loads static_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_tab_registry.py', '_load'):
        'this loads tab_registry by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_tab_routing_js_heads.py', '_literal'):
        'this builds a method entry plus a plain sibling, where the owner '
        'builds a getter-returning object',
    ('tests/test_tab_routing_positions.py', '_literal'):
        'this builds one member-access position case from a named shape, '
        'where the owner builds a getter-returning object',
    ('tests/test_tab_routing_positions.py', '_run'):
        'this runs one masked source through the position verdict, where the '
        'shared _run boots a node scenario',
    ('tests/test_tab_routing_unprovable.py', '_scan'):
        'this writes one synthetic module and asks the route scanner, where '
        'the owner scans a module text with a memo',
    ('tests/test_timed_planner.py', '_run'):
        'this runs the planner main with stdout captured, where the shared '
        '_run boots a node scenario',
    ('tests/test_timed_refresh.py', '_run'):
        'this runs the refresh main with both streams captured, where the '
        'shared _run boots a node scenario',
    ('tests/test_type_errors.py', '_git'):
        'this runs git under a scrubbed child environment, which the shared '
        'runner does not set',
    ('tests/test_upload_races.py', '_load'):
        'this loads upload_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_upload_routes.py', '_load'):
        'this loads upload_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_watch_all.py', '_run'):
        'this builds one shared-client workflow run against a SHA, where the '
        'shared _run boots a node scenario',
    ('tests/test_watcher_budget.py', '_comment'):
        'this builds one review-comment node, where the owner asks whether a '
        'line is a YAML comment',
    ('tests/test_worker_sources.py', '_run'):
        'this runs one shipped worker program and parses its JSON, where '
        'the shared _run boots the recorded boundary harness against a '
        'named scenario and an optional background path',
    ('tests/test_workflow_eslint.py', '_run'):
        'this returns one named step run block through the bounded reader, '
        'where the shared _run boots a node scenario',
    ('tests/test_workflow_job_timeouts.py', '_fixture'):
        'this writes one fixture workflow into a fresh tmp directory, where '
        'the owner reads a fake-GitHub answer fragment',
    ('tests/test_workflow_job_timeouts.py', '_planted'):
        'this copies the real workflow minus the aggregate job bound, where '
        'the owner reverts one converted site in a scratch tree',
    ('tests/test_workflow_job_timeouts.py', '_scan'):
        'this checks one workflow and through a local caller its target, '
        'where the owner scans a module text with a memo',
}
