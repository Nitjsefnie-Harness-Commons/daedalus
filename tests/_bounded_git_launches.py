"""Every in-scope bounded git launch the tree may keep, and why.

The keys are the sites: (path, function, signature, ordinal) says
what the call IS and which of its shape in its function, so an
edit above a baselined launch cannot move a row onto another
launch. `tests/test_repo_layout.py` computes the live keys from
the analyser's own classification and matches them here; both
sides read the same string, so a finding about a live site names
the key in the form this table writes.
"""
BOUNDED_GIT_LAUNCHES = {
    # Every reason says what the call is and why it cannot hang a git
    # launch; a receiver the analyser cannot prove is reported rather
    # than passed, and this table is that report's disposition.
    ('.claude/skills/changing-daedalus/watch_all.py', '_aggregate',
     'sink.get(timeout)', 1):
         'a queue read with a deadline: the queue is drained,'
         'nothing is launched',
    ('.claude/skills/changing-daedalus/watch_all.py', '_repo_root',
     'subprocess.run(capture_output, text, timeout)', 1):
         'a standalone skill script an operator runs by hand; no'
         'suite or CI bound sits above it, so a wedged git hangs an'
         'operator',
    ('.claude/skills/changing-daedalus/watch_all.py', '_repo_slug',
     'subprocess.run(capture_output, check, text, timeout)', 1):
         'a standalone skill script an operator runs by hand; no'
         'enclosing bound sits above it, so a wedged git hangs an'
         'operator',
    ('run_tests.py', '_terminate_and_reap', 'process.wait(timeout)', 1):
         'a child wait while tearing the suite down; the runner is'
         'the bound',
    ('run_tests.py', '_terminate_and_reap', 'process.wait(timeout)', 2):
         'a second child wait in the same teardown, after a kill',
    ('scripts/gen_gitignore.py', '_check_ignore',
     'subprocess.run(capture_output, input, text, timeout)', 1):
         'a standalone generator an operator runs by hand; no'
         'enclosing bound sits above it, so a wedged git hangs an'
         'operator',
    ('scripts/gen_gitignore.py', 'main',
     'subprocess.run(capture_output, check, text, timeout)', 1):
         'a standalone generator an operator runs by hand; no suite'
         'or CI bound sits above it, so a wedged git hangs an'
         'operator',
    ('tests/_drain.py', 'kill_and_drain', 'process.communicate(timeout)', 1):
         'a drain of an already-killed process: it can only return',
    ('tests/_drain.py', 'kill_and_drain', 'process.wait(timeout)', 1):
         'the reap that follows that drain, on the same dead process',
    ('tests/_realbrowser_workers.py', '_devtools_targets',
     'urllib.request.urlopen(timeout)', 1):
         'an HTTP read: a socket, not a git process, and the'
         ' timeout is the bound itself',
    ('tests/_realbrowser_workers.py', '_retire_browser',
     'process.wait(timeout)', 1):
         'a browser-process wait while retiring it',
    ('tests/_realbrowser_workers.py', '_retire_browser',
     'process.wait(timeout)', 2):
         'the reap after that wait, on the same process',
    ('tests/_repo.py', 'git_index', 'str()', 1):
         'a generic git runner: the bound covers the index-writing'
         'commands its callers pass, not only the reads',
    ('tests/_processtree.py', '_reap', 'process.wait(timeout)', 1):
         'a bounded reap of a process that has already stopped'
         ' answering, in the shared tree-kill cleanup',
    ('tests/_processtree.py', '_reap', 'process.wait(timeout)', 2):
         'the fallback reap that follows the direct kill, on the'
         ' same process',
    ('tests/_util.py', '_startup_observations', 'thread.join(timeout)', 1):
         'a thread join on a thread this helper started',
    ('tests/_util.py', 'bridge', 'await_listening_line(timeout)', 1):
         'a helper waiting for a port line; the caller bounds it',
    ('tests/_util.py', 'get', 'request()', 1):
         'an HTTP helper opening a socket; no git '
         'process is behind it',
    ('tests/_util.py', 'get_json', 'get()', 1):
         'an HTTP helper opening a socket; no git '
         'process is behind it',
    ('tests/_util.py', 'header_stream', 'http.client.HTTPConnection(timeout)',
     1):
         'a connection constructor: it opens a socket and returns'
         ' a client, and no git process sits behind a socket',
    ('tests/_util.py', 'post_json', 'request(body)', 1):
         'an HTTP helper opening a socket; no git '
         'process is behind it',
    ('tests/_util.py', 'request', 'urllib.request.urlopen(timeout)', 1):
         'an HTTP read: a socket, not a git process, and the'
         ' timeout is the bound itself',
    ('tests/test_aggregate_gate.py',
     'test_every_single_dependency_result_is_tabled', '_needs()', 1):
         'a table builder called with a keyword mapping; it builds a'
         'table, it launches nothing',
    ('tests/test_aggregate_gate.py',
     'test_two_dependencies_are_decided_jointly', 'zip()', 1):
         'the same table builder, keyed from a zipped mapping',
    ('tests/test_bridge_startup.py',
     'test_dashboard_responses_refuse_cross_origin_framing',
     'urllib.request.urlopen(timeout)', 1):
         'an HTTP read: a socket, not a git process, and the'
         ' timeout is the bound itself',
    ('tests/test_dashboard_behaviour.py', 'popen', '_ControlledProcess()', 1):
         'a test double constructed with a keyword mapping; it'
         'records, it does not launch',
    ('tests/test_dashboard_gate.py',
     'test_gate_is_released_by_the_os_when_the_holder_is_killed',
     'holder.wait(timeout)', 1):
         'a wait on a holder this test started, in a teardown that'
         ' kills it only while it is still running; the timeout is'
         ' the bound itself',
    ('tests/test_dashboard_gate.py',
     'test_gate_is_released_by_the_os_when_the_holder_is_killed',
     'waiter.communicate(timeout)', 1):
         'the gate child this test started; the timeout is the'
         ' bound itself',
    ('tests/test_dashboard_node_retry.py',
     'test_two_dashboard_children_cannot_be_inside_the_gate_together',
     'worker.communicate(timeout)', 1):
         'a wait on the two gate children this test started;'
         ' the timeout is the bound itself',
    ('tests/test_mcp_entry_point.py', '_cleanup_mcp', 'proc.wait(timeout)', 1):
         'an MCP process wait while the test tears it down',
    ('tests/test_mcp_entry_point.py', '_cleanup_mcp', 'proc.wait(timeout)', 2):
         'the reap after that wait, on the same process',
    ('tests/test_mcp_server.py', '_surface_responder_errors',
     'thread.join(timeout)', 1):
         'a thread join on a thread the fixture started',
    ('tests/test_mcp_server.py', 'callers',
     'mod.bridge.ext_cmd(domain, timeout)', 1):
        "an MCP tool call whose timeout is the tool's, not a bound"
         'on a process',
    ('tests/test_mcp_server.py',
     'test_a_nonpositive_mcp_timeout_admits_no_command', 'getattr()', 1):
        "the test's subject: an MCP call the server must reject for"
         'its timeout',
    ('tests/test_mcp_server.py',
     'test_bearer_middleware_rejects_duplicate_authorization_headers',
     'http.client.HTTPConnection(timeout)', 1):
         'a connection constructor: it opens a socket and returns'
         ' a client, and no git process sits behind a socket',
    ('tests/test_mcp_server.py',
     'test_mcp_port_zero_announces_the_actual_bound_port',
     'mod._bound.wait(timeout)', 1):
         'an assertion on a bound event the module sets; the wait IS'
         'the assertion',
    ('tests/test_parent_watch.py',
     'test_bounded_wait_reports_live_child_port_and_watch_state',
     '_wait_for_exit(timeout)', 1):
         'a helper waiting for a child to exit; the test bounds it',
    ('tests/test_real_browser_classification.py',
     'test_answering_control_worker_twice_marks_worker_absence_our_failure',
     'process.wait.assert_called_once_with(timeout)', 1):
         'a mock assertion on a wait call: it asserts, it does not'
         'wait',
    ('tests/test_real_browser_classification.py',
     'test_control_browser_exit_ends_the_diagnosis_without_a_verdict',
     'processes[0].wait.assert_called_once_with(timeout)', 1):
         'the same mock assertion, on the exit control',
    ('tests/test_real_browser_classification.py',
     'test_control_diagnosis_launches_both_extensions_twice_before_guilt',
     'process.wait.assert_called_once_with(timeout)', 1):
         'the same mock assertion, in the twice-launched control',
    ('tests/test_real_browser_classification.py',
     'test_indeterminate_e2big_diagnostics_are_harness_failures',
     'mock.patch.object()', 1):
         'a mock patch whose keyword mapping substitutes the launch'
         'the test is asserting on',
    ('tests/test_real_browser_classification.py',
     'test_unanswered_control_worker_leaves_the_skip_with_the_machine',
     'processes[0].wait.assert_called_once_with(timeout)', 1):
         'the same mock assertion, on the unanswered control',
    ('tests/test_real_browser_classification.py',
     'test_unreadable_control_answer_polls_again_instead_of_settling',
     'process.wait.assert_called_once_with(timeout)', 1):
         'the same mock assertion, on the unreadable control',
    ('tests/test_real_browser_harness.py', 'exercise',
     'urllib.request.urlopen(timeout)', 1):
        "the same navigation, on the harness's own page",
    ('tests/test_real_browser_harness.py', 'first_navigation',
     'urllib.request.urlopen(timeout)', 1):
         'a browser navigation whose timeout is the bound itself',
    ('tests/test_segment_routes.py', 'refusing', 'real()', 1):
         'a test double delegating with its arguments; it launches'
         'nothing of its own',
    ('tests/test_stream_lifecycle.py', '_open_stream',
     'http.client.HTTPConnection(timeout)', 1):
         'a connection constructor: it opens a socket and returns'
         ' a client, and no git process sits behind a socket',
    ('tests/test_suite_runner.py',
     'test_output_close_failure_reaps_the_spawned_suite',
     'spawned[0].wait(timeout)', 1):
         'a suite-process wait inside the reaping the test asserts',
    ('tests/test_suite_runner.py',
     'test_output_close_failure_reaps_the_spawned_suite',
     'spawned[0].wait(timeout)', 2):
         'the reap that follows, on the same process',
    ('tests/test_suite_runner.py',
     'test_output_close_failure_reaps_the_spawned_suite',
     'spawned[0].wait(timeout)', 3):
         'the final reap, on the same process',
    ('tests/_watcher_waits.py', 'stop', 'self.proc.wait(timeout)', 1):
         'a reap that follows a group kill, on a process already signalled',
    ('tests/test_watcher_budget.py',
     'test_a_graceful_exit_leaves_no_children_behind',
     'parent.proc.wait(timeout)', 1):
         'a parent handle reaping a child it signalled itself, in the'
         ' graceful-exit control',
    ('tests/test_watcher_budget.py', 'test_the_children_die_with_their_parent',
     'parent.proc.wait(timeout)', 1):
         'a parent handle stopping a child the test started',
    ('tests/test_watcher_waits.py',
     'test_a_cancel_ends_the_whole_tree_and_not_only_the_child',
     'child.proc.wait(timeout)', 1):
         'a cleanup reap on a real child the control started itself, in the'
         ' finally of the tree-kill control',
}
