"""The same-named locals that are not re-implementations.

Keyed by (repo-relative path, name), so a row cannot be widened by a
prefix or a substring match. Every entry is a pre-existing collision: a
row may name a site the branch did not create, and a row naming a file
the branch adds or edits is itself a finding, so the table can absorb
what `main` lands underneath it while excusing nothing the branch wrote.
"""
UNCONSOLIDATED_NAMES = {
    ('tests/_binding_assertions.py', '_scope_violations'):
        'this renders the expected violation strings for a synthetic source '
        'from (relative, source, expected), where the drain owner orders real '
        'calls within a scope and appends to a violations list it was handed',
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
    ('tests/_cli_dispatch.py', 'run_cli'):
        'this dispatches argv in process through _dispatch, raises '
        'SystemExit on a refusal code and returns the recording with stdout, '
        'where the other spawns the real CLI as a subprocess under a supplied '
        'env and timeout; only the process form can lose a traceback at the '
        'boundary, which is the whole point of the pair',
    ('tests/_cli_helpers.py', 'run_cli'):
        'this spawns the real CLI as a subprocess under a supplied env and '
        'timeout, where the other dispatches argv in process and asserts the '
        'plan was consumed',
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
    ('tests/_drain_scan.py', '_scope_violations'):
        'this orders real calls within a scope and appends every violation to '
        'the list it was handed, where the binding-assertions owner renders '
        'expected strings for a synthetic source off a fixed marker list, so '
        'neither body would answer for the other',
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
    ('tests/_realbrowser_fixture_controls.py', '_browser_version'):
        'this asserts the exact subprocess call the control makes - the argv, '
        'capture_output, text and a 15s timeout - and answers a canned '
        'Chromium version, where the owner really runs --version and formats '
        'whatever came back',
    ('tests/_realbrowser_workers.py', '_browser_version'):
        'this runs --version and formats what the browser called itself, so a '
        'skip says which browser refused, where the fixture control asserts a '
        'canned answer for a call it has already fixed',
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
        'byte-identical to the row for tests/test_workflow_job_timeouts.py, '
        'and neither is a copy of the owner: this writes one fixture '
        'workflow into a fresh tmp directory where the owner reads a '
        'fake-GitHub answer fragment. The pair is a real duplicate and '
        'consolidating it is deferred, not dismissed',
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
    ('tests/test_cli_duplicate_admission.py', 'run_cli'):
        'byte-identical to the row for tests/test_cli_error_reporting.py, '
        'and neither is a copy of the owner: both spawn the real CLI as a '
        'process against a live bridge, the half the owner deliberately is '
        'not, since a retried delivery and a printed traceback both have to '
        'survive a process boundary. The pair is a real duplicate and '
        'consolidating it is deferred, not dismissed',
    ('tests/test_cli_error_reporting.py', 'run_cli'):
        'byte-identical to the row for tests/test_cli_duplicate_admission.'
        'py, and neither is a copy of the owner: the owner captures stdout '
        'from an in-process dispatch and never sees a traceback cross a '
        'process, which is the whole point of these two. The pair is a real '
        'duplicate and consolidating it is deferred, not dismissed',
    ('tests/test_cli_waits.py', 'run_cli'):
        'this runs a typed subcommand that enqueues a command and answers '
        'it afterwards, where the owner dispatches with the answer already '
        'canned and nothing is ever enqueued',
    ('tests/test_config_boot_generation.py', '_run'):
        'this drives the worker under node for one plan and reads back its '
        'streams, where the shared _run boots a recorded scenario',
    ('tests/test_fetch_timings_count.py', 'run_cli'):
        'this dispatches commands_browser through build_parser and fakes '
        'ext_cmd, where the owner dispatches commands_content through '
        '_cli_parse.accepted, so the module under dispatch differs and '
        'a refused argument leaves as SystemExit rather than as an '
        'AssertionError',
    ('tests/test_screenshot_quality.py', 'run_cli'):
        'this patches three module attributes and records through its own '
        'RecordingApi, where the owner patches one ext_cmd and records '
        'through RecordingExtCmd',
    ('tests/test_cli_dispatch.py', '_refused'):
        'this drives a call the CLI harness must refuse and returns the '
        'AssertionError text it read, where the owner takes a zero-argument '
        'scan and returns the SystemExit text; a different exception over a '
        'different argument',
    ('tests/test_coverage_environment.py', '_module_text'):
        'byte-identical to the test_unresolved_routes site (both '
        'c79201d1572322de) and the owner bar this suite own one-line '
        'docstring, which the owner does not carry. The pair is a real '
        'duplicate and consolidating it means editing one of the two suites, '
        'which is deferred, not dismissed',
    ('tests/test_coverage_unfollowable_forms.py', '_refused'):
        'this compiles each named synthetic case and asserts exactly one '
        'binding verdict at its marker, where the owner takes a zero-argument '
        'scan and returns the SystemExit text',
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
        'byte-identical to the row for tests/test_type_errors.py, and '
        'neither is a copy of the owner: this runs git under a scrubbed '
        'child environment, which the shared runner does not set. The pair '
        'is a real duplicate and consolidating it is deferred, not '
        'dismissed',
    ('tests/test_line_lengths.py', '_lines'):
        'this joins texts and encodes them as the byte-length source, where '
        'the owner splits a workflow keeping line endings',
    ('tests/test_mcp_live_tools.py', '_row'):
        'this builds one tool row from a command type, its fields and a '
        'builder, where the owner builds a getter-argument case',
    ('tests/test_mcp_hotfix_scope.py', '_load_composition'):
        'this patches MCPServer and BridgeSession the way the owner does but '
        'names its own _ToolRegistry and _BridgeProbe classes and its own '
        'mcp_server_hotfix_ module suffix, where the owner uses the '
        'unprefixed names and the mcp_server_tools_ suffix',
    ('tests/test_mcp_refusal_drain.py', '_load_mcp'):
        'this drives the already-booted bridge with an empty token, which the '
        'shared loader base_url-first signature does not express',
    ('tests/test_overlap_bound.py', '_bound_source'):
        'this slices the shipped prelude bound machinery at the entry IIFE, '
        'where the owner reads a job field after timeout-minutes',
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
    ('tests/test_segment_mint.py', '_refused'):
        'this asserts a refused mint answer is exactly {error: message} and '
        'that the plan sent nothing else to the bridge, where the owner '
        'returns the SystemExit text a scan raised',
    ('tests/test_segment_routes.py', '_refused'):
        'this is a context manager that makes one pathlib.Path method raise '
        'for one final path component and yields the calls that fired, where '
        'the owner takes a zero-argument scan and returns the SystemExit text',
    ('tests/test_stream_backoff.py', '_run'):
        'this runs one backoff plan against the shipped worker, where the '
        'shared _run boots a recorded scenario',
    ('tests/test_tab_registry.py', '_load'):
        'this loads tab_registry by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_suite_import_boundaries.py', '_scan'):
        'this walks one module AST for sibling-suite imports and returns '
        '(lineno, leaf, spelling) hits, where the drain owner scans a module '
        'text for unbounded drains and the code-eval owner walks an '
        'expression value; three different arguments',
    ('tests/test_unresolved_routes.py', '_module_text'):
        'byte-identical to the test_coverage_environment site (both '
        'c79201d1572322de) and the owner bar this suite own one-line '
        'docstring, which the owner does not carry. The pair is a real '
        'duplicate and consolidating it means editing one of the two suites, '
        'which is deferred, not dismissed',
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
        'byte-identical to the row for tests/test_line_lengths.py, and '
        'neither is a copy of the owner: this runs git under a scrubbed '
        'child environment, which the shared runner does not set. The pair '
        'is a real duplicate and consolidating it is deferred, not '
        'dismissed',
    ('tests/test_upload_races.py', '_load'):
        'byte-identical to the row for tests/test_upload_routes.py, and '
        'neither is a copy of the owner: this loads upload_routes by path '
        'under a name of its own where the owner is a JSON file reader. The '
        'pair is a real duplicate and consolidating it is deferred, not '
        'dismissed',
    ('tests/test_upload_routes.py', '_load'):
        'byte-identical to the row for tests/test_upload_races.py, and '
        'neither is a copy of the owner: this loads upload_routes by path '
        'under a name of its own where the owner is a JSON file reader. The '
        'pair is a real duplicate and consolidating it is deferred, not '
        'dismissed',
    ('tests/test_version_empty_values.py', '_assert_duplicate_refused'):
        'this asserts the empty string reached stderr and that no ok: line '
        'reached stdout, where the owner takes both competing values and '
        'asserts each repr in stderr',
    ('tests/test_watch_all.py', '_run'):
        'this builds one shared-client workflow run against a SHA, where the '
        'shared _run boots a node scenario',
    ('tests/test_watcher_budget.py', '_comment'):
        'this builds one review-comment node, where the owner asks whether a '
        'line is a YAML comment',
    ('tests/test_yamlread_anchor_edges.py', '_refused'):
        'this calls workflow_step_items and asserts the YAMLReadError carries '
        'a detail and is not the unknown-alias refusal, where the owner takes '
        'a zero-argument scan and returns the SystemExit text',
    ('tests/test_worker_register_throttle.py', '_observe'):
        'this drives the register-throttle harness under a plan with '
        'expected streams, where the owner reads one relay mode answer',
    ('tests/test_worker_result_post.py', '_post'):
        'this drives one postResult call through the shipped worker '
        'source, where the owner builds one MCP answer tuple',
    ('tests/test_worker_sources.py', '_run'):
        'this runs one shipped worker program and parses its JSON, where '
        'the shared _run boots the recorded boundary harness against a '
        'named scenario and an optional background path',
    ('tests/test_workflow_eslint.py', '_run'):
        'this returns one named step run block through the bounded reader, '
        'where the shared _run boots a node scenario',
    ('tests/test_workflow_job_timeouts.py', '_fixture'):
        'byte-identical to the row for tests/test_aggregate_needs.py, and '
        'neither is a copy of the owner: this writes one fixture workflow '
        'into a fresh tmp directory where the owner reads a fake-GitHub '
        'answer fragment. The pair is a real duplicate and consolidating it '
        'is deferred, not dismissed',
    ('tests/test_workflow_job_timeouts.py', '_planted'):
        'this copies the real workflow minus the aggregate job bound, where '
        'the owner reverts one converted site in a scratch tree',
    ('tests/test_workflow_job_timeouts.py', '_scan'):
        'this checks one workflow and through a local caller its target, '
        'where the owner scans a module text with a memo',
    ('tests/_netcapture_harness.py', 'request'):
        'this answers one recorded request, where the owner in _util is '
        'the process-wide request the worker client made',
    ('tests/_util.py', 'request'):
        'this is the client-side request the whole suite shares, where '
        'the recorder in _netcapture_harness is one capture own request',
    ('tests/test_cli_browser_commands.py', '_ext'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_tabs.py, test_cli_content_css.py and '
        'test_cli_content_hotfixes.py sites (digest 8584b91dc2), and none '
        'is a copy of the owner, whose own digest is 8d27de7d6',
    ('tests/test_cli_browser_commands.py', '_put'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_handlers.py, test_cli_content_block.py and '
        'test_cli_eval_handlers.py sites (digest bae1155da1), and none is '
        'a copy of the owner, whose own digest is bb7f4f04',
    ('tests/test_cli_browser_handlers.py', '_put'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_handlers.py, test_cli_content_block.py and '
        'test_cli_eval_handlers.py sites (digest bae1155da1), and none is '
        'a copy of the owner, whose own digest is bb7f4f04',
    ('tests/test_cli_browser_tabs.py', '_ext'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_tabs.py, test_cli_content_css.py and '
        'test_cli_content_hotfixes.py sites (digest 8584b91dc2), and none '
        'is a copy of the owner, whose own digest is 8d27de7d6',
    ('tests/test_cli_content_block.py', '_put'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_handlers.py, test_cli_content_block.py and '
        'test_cli_eval_handlers.py sites (digest bae1155da1), and none is '
        'a copy of the owner, whose own digest is bb7f4f04',
    ('tests/test_cli_content_capture.py', '_ext'):
        'this one differs from the other four _ext sites (digest '
        '04c21b05e5), and the owner builds the answer tuple for the whole '
        'tool surface',
    ('tests/test_cli_content_css.py', '_ext'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_tabs.py, test_cli_content_css.py and '
        'test_cli_content_hotfixes.py sites (digest 8584b91dc2), and none '
        'is a copy of the owner, whose own digest is 8d27de7d6',
    ('tests/test_cli_content_hotfixes.py', '_ext'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_tabs.py, test_cli_content_css.py and '
        'test_cli_content_hotfixes.py sites (digest 8584b91dc2), and none '
        'is a copy of the owner, whose own digest is 8d27de7d6',
    ('tests/test_cli_content_hotfixes.py', '_stored'):
        'this is the stored-hotfix record this suite reads back, where '
        'the owner store key is the one the route answers with',
    ('tests/test_cli_eval_handlers.py', '_get'):
        'byte-identical to the other test_cli_eval_handlers.py and '
        'test_cli_result_handlers.py sites (digest b58cc8c27a), and none '
        'is a copy of the owner, whose own digest is d10f3df3d',
    ('tests/test_cli_eval_handlers.py', '_put'):
        'byte-identical to the other test_cli_browser_commands.py, '
        'test_cli_browser_handlers.py, test_cli_content_block.py and '
        'test_cli_eval_handlers.py sites (digest bae1155da1), and none is '
        'a copy of the owner, whose own digest is bb7f4f04',
    ('tests/test_cli_result_handlers.py', '_get'):
        'byte-identical to the other test_cli_eval_handlers.py and '
        'test_cli_result_handlers.py sites (digest b58cc8c27a), and none '
        'is a copy of the owner, whose own digest is d10f3df3d',
    ('tests/test_dashboard_app_shell.py', '_run'):
        'this runs one dashboard scenario in this suite own shell, where '
        'the owner runs the recorded boundary harness a plan names',
    ('tests/test_dashboard_sse.py', '_run'):
        'this runs one SSE scenario in this suite own shell, where the '
        'owner runs the recorded boundary harness a plan names',
}
