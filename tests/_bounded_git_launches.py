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
    ('.claude/skills/changing-daedalus/plant.py', '_git',
     'subprocess.run(capture_output, check, text, timeout)', 1):
         'the work-tree and porcelain reads that report whether a saved'
         ' path is dirty against HEAD; a standalone skill script an'
         ' operator runs by hand, so a wedged git hangs an operator',
    ('daedalus_cli/transport.py', 'wait_for_result', '_query_path()', 1):
         'a path builder over `urllib.parse.urlencode`; the timeout'
         'belongs to the poll loop that calls it, and no process is behind'
         'it',
    ('daedalus_mcp/tools_cookies.py', 'clear_cookies', 'bridge.ext_cmd(wait)',
     1):
         'the clear-cookies tool; the mapping is empty here and the'
         'tool\'s own deadline is the only bound',
    ('daedalus_mcp/tools_cookies.py', 'get_cookies', 'bridge.ext_cmd(wait)',
     1):
         'the cookie-listing tool; the mapping is its domain and URL'
         'filter, and the deadline is the tool\'s',
    ('daedalus_mcp/tools_cookies.py', 'set_cookie', 'bridge.ext_cmd(wait)', 1):
         'the set-cookie tool; the mapping is the cookie, and nothing it'
         'names starts a process',
    ('daedalus_mcp/tools_css.py', 'block_requests', 'bridge.ext_cmd(wait)', 1):
         'the block-requests tool; the mapping is the pattern list the'
         'extension matches on',
    ('daedalus_mcp/tools_css.py', 'inject_css', 'bridge.ext_cmd(wait)', 1):
         'the inject-css tool; the stylesheet rides in the mapping and is'
         'applied by the extension, not by a process here',
    ('daedalus_mcp/tools_css.py', 'remove_css', 'bridge.ext_cmd(wait)', 1):
         'the remove-css tool, the counterpart of the inject row; the'
         'mapping names the rule to drop',
    ('daedalus_mcp/tools_css.py', 'unblock_requests', 'bridge.ext_cmd(wait)',
     1):
         'the unblock-requests tool; the mapping names the pattern to'
         'release, and the deadline is the tool\'s',
    ('daedalus_mcp/tools_eval.py', 'navigate', '_send_eval(timeout, wait)', 1):
         'a navigation submitted as an eval; `wait=False` asks for the id'
         'alone, and the timeout is the round trip the tool applies',
    ('daedalus_mcp/tools_eval.py', 'reload', '_send_eval(timeout, wait)', 1):
         'a reload submitted as an eval; the page does the work and the'
         'timeout bounds the round trip, not a process',
    ('daedalus_mcp/tools_eval.py', 'result', 'bridge.get()', 1):
         'the result tool peeking at a result slot over HTTP; the params'
         'are its selector, not a deadline',
    ('daedalus_mcp/tools_eval.py', 'title', '_send_eval(timeout, wait)', 1):
         'reading `document.title` from a tab; a 10s round trip over the'
         'bridge, and no child is behind it',
    ('daedalus_mcp/tools_eval.py', 'url', '_send_eval(timeout, wait)', 1):
         'reading `location.href` from a tab; the same 10s round trip, and'
         'nothing here can wedge a git command',
    ('daedalus_mcp/tools_hotfixes.py', 'store_hotfix', 'bridge.ext_cmd(wait)',
     1):
         'the store-hotfix tool; the source and its scope ride in the'
         'mapping, and the extension is what evaluates them',
    ('daedalus_mcp/tools_media.py', 'screenshot',
     'bridge.ext_cmd(timeout, wait)', 1):
         'asking the extension for a capture; the timeout is the'
         'capture\'s own deadline, and the call returns the payload',
    ('daedalus_mcp/tools_media.py', 'screenshot', 'bridge.get_raw()', 1):
         'the same tool downloading the capture it just took: an HTTP GET'
         'over the stored path, and a socket is not a git process',
    ('daedalus_mcp/tools_media.py', 'uploads', 'bridge.get()', 1):
         'listing stored uploads; the params are the page selector the'
         'listing route takes',
    ('daedalus_mcp/tools_network.py', 'cdp', 'bridge.ext_cmd(timeout, wait)',
     1):
         'issuing a CDP command through the extension; a 30s round trip,'
         'and the debugger attach is the extension\'s own',
    ('daedalus_mcp/tools_network.py', 'fetch_timings', 'bridge.ext_cmd(wait)',
     1):
         'the fetch-timings tool; the mapping is its selector and names no'
         'deadline, so `ext_cmd`\'s own 10s default bounds the wait',
    ('daedalus_mcp/tools_network.py', 'net_capture',
     'bridge.ext_cmd(timeout, wait)', 1):
         'starting a capture in the service worker; a 15s round trip, and'
         'the buffer it fills lives in the worker',
    ('daedalus_mcp/tools_network.py', 'net_capture_get',
     'bridge.ext_cmd(timeout, wait)', 1):
         'reading the capture buffer back; a 30s round trip over the'
         'extension, bounded by the tool itself',
    ('daedalus_mcp/tools_network.py', 'net_capture_stop',
     'bridge.ext_cmd(timeout, wait)', 1):
         'stopping that capture; a 30s round trip, and the stop is what'
         'releases the buffer rather than a child process',
    ('daedalus_mcp/tools_tabs.py', 'close_tab', 'bridge.ext_cmd(wait)', 1):
         'the close-tab tool; the mapping names the tab and the tool\'s'
         'deadline bounds the round trip',
    ('daedalus_mcp/tools_tabs.py', 'ext_navigate', 'bridge.ext_cmd(wait)', 1):
         'a typed navigate command; the URL rides in the mapping and the'
         'extension performs it',
    ('daedalus_mcp/tools_tabs.py', 'ext_reload', 'bridge.ext_cmd(wait)', 1):
         'a typed reload command; the mapping names the tab and nothing'
         'here waits on a process',
    ('daedalus_mcp/tools_tabs.py', 'open_tab',
     'bridge.ext_cmd(include_roundtrip, wait)', 1):
         'opening one tab and reporting how long the round trip took; the'
         'flag is a measurement, not a wait',
    ('daedalus_mcp/tools_tabs.py', 'open_tabs',
     'bridge.ext_cmd(include_roundtrip, timeout, wait)', 1):
         'opening every URL in order and waiting for each; a 30s round'
         'trip per tab, and a wedged tab fails the call rather than a'
         'process',
    ('daedalus_mcp/transport.py', 'poll_result', 'self.http_client()', 1):
         'the transport handing out its HTTP client; it returns a client'
         'object and starts no request, let alone a child',
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
    ('tests/_bridge.py', 'frame', 'next_stream_data()', 1):
         'the same frame reader called with the test\'s own deadline,'
         'which is a bound on a socket and not on a process',
    ('tests/_bridge.py', 'read_stream_data', 'next_stream_data(timeout)', 1):
         'reading the next SSE data frame off an open response; the helper'
         'ends on a monotonic deadline, so it cannot hang',
    ('tests/_cli_arg_audit_support.py', 'add_storage_probe', 'dest.replace()',
     1):
         'spelling an option\'s flag from its destination string;'
         '`str.replace` cannot launch anything',
    ('tests/_cli_arg_audit_support.py', 'add_storage_probe',
     'parser.add_argument()', 1):
         'argparse registering one probe option; the mapping is its'
         'configuration and no process sits behind it',
    ('tests/_cli_arg_audit_support.py', 'add_storage_probe',
     'parser.add_argument(nargs)', 1):
         'the same registration for the shapes that take an `nargs`;'
         'argparse builds a parser, it does not run one',
    ('tests/_cli_dispatch.py', '_dispatch', 'RecordingExtCmd(plan)', 1):
         'the same double built by the driver itself, so the options and'
         'the plan reach the handler that reads them',
    ('tests/_cli_dispatch.py', 'drive', 'RecordingExtCmd(plan)', 1):
         'constructing the recorder double the CLI handler calls in place'
         'of the bridge; the plan is what it asserts against',
    ('tests/_cli_dispatch.py', 'run_cli', '_dispatch()', 1):
         'the driver\'s own dispatch helper; the options are the'
         'recorder\'s, and it returns before any handler runs',
    ('tests/_cli_dispatch.py', 'run_cli_exit', '_dispatch()', 1):
         'the same helper for the arm that exits; it returns an exit code'
         'and stdout, never a child',
    ('tests/_cmdqueue_faults.py', '_native_read_handle', 'original()', 1):
         'the uninstrumented open the injector wraps, called with the'
         'candidate path so the handle can be classified',
    ('tests/_cmdqueue_faults.py', '_path_open_failure', 'path.open()', 1):
         'opening the caller\'s Path so the failure can be caught; a file'
         'open blocks on the filesystem, not on a child',
    ('tests/_dashnode_retry_control.py', 'popen', '_ControlledProcess()', 1):
         'a test double constructed with a keyword mapping; it'
         'records, it does not launch',
    ('tests/_dashsection.py', '_run', 'scenario(answers, plan, setup)', 1):
         'the dashboard-section harness running one scenario body with the'
         'test\'s answers and plan; it renders, it does not launch',
    ('tests/_drain.py', 'kill_and_drain', 'process.communicate(timeout)', 1):
         'a drain of an already-killed process: it can only return',
    ('tests/_drain.py', 'kill_and_drain', 'process.wait(timeout)', 1):
         'the reap that follows that drain, on the same dead process',
    ('tests/_gc_handshake.py', 'call', 'real_call()', 1):
         'the captured operation the generated wrapper reaches after its'
         'refusal check; the wrapper is a fixture for a filesystem call',
    ('tests/_mcp_load.py', '_start_mcp_in_process', 'mod._bound.wait(timeout)',
     1):
         'a threading event the in-process server sets once it is'
         'listening; 50ms is a startup poll, not a process wait',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(include_roundtrip, timeout, urls, wait)', 1):
         'a fixture naming the ext_cmd the open-tabs tool should send; the'
         'timeout and the flag are the fields under test',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(active, include_roundtrip, pinned, timeout, urls, wait)', 1):
         'the same fixture with the background and pinned flags added; the'
         'builder is what the recorder is compared against',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(include_roundtrip, timeout, urls, wait)', 2):
         'the open-tabs fixture sent unwaited; the same field names at a'
         'second call site, and the ordinal keeps the two apart',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(format, timeout, wait)', 1):
         'the screenshot fixture naming only a format; the builder'
         'launches nothing and the timeout is the capture field',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(format, quality, tabId, timeout, wait)', 1):
         'the screenshot fixture at a mid quality, so the tool must pass'
         'it through rather than drop or clamp it',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(format, quality, tabId, timeout, wait)', 2):
         'the same fixture at the floor quality; one row per boundary'
         'value, and each is a dict the recorder reads',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(format, quality, tabId, timeout, wait)', 3):
         'the same fixture at the ceiling quality, closing the triple the'
         'quality bounds are read from',
    ('tests/_mcp_tool_commands.py', '<module>', '_ext(timeout, wait)', 1):
         'the bare screenshot fixture; only the id, the type and the'
         'timeout are named',
    ('tests/_mcp_tool_commands.py', '<module>', '_ext(format, timeout, wait)',
     2):
         'the format-only fixture at a second call site, so both'
         'screenshots the tool can send are spelled out',
    ('tests/_mcp_tool_commands.py', '<module>', '_ext(format, timeout, wait)',
     3):
         'the screenshot fixture sent unwaited; a third spelling of the'
         'same field names, kept apart by its ordinal',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(maxRequests, timeout, wait)', 1):
         'the net-capture fixture at the default request ceiling; the'
         'field is the bound the tool advertises',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(maxRequests, tabId, timeout, wait)', 1):
         'the same capture fixture aimed at one tab; the maxRequests floor'
         'the tool refuses is what the row reads',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(maxRequests, timeout, wait)', 2):
         'the capture fixture at the ceiling, closing the pair the'
         'maxRequests bounds are read from',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(maxRequests, timeout, wait)', 3):
         'the capture fixture sent unwaited; a third call site sharing the'
         'ceiling fixture\'s field names',
    ('tests/_mcp_tool_commands.py', '<module>', '_ext(timeout, wait)', 2):
         'the net-capture-stop fixture; the only field is the deadline the'
         'tool applies to the stop',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(bodies, tabId, timeout, wait)', 1):
         'the stop fixture asking for bodies as well; a flag, not a bound,'
         'and the recorder compares the whole mapping',
    ('tests/_mcp_tool_commands.py', '<module>', '_ext(timeout, wait)', 3):
         'the stop fixture sent unwaited; the deadline again, at a third'
         'site under this spelling',
    ('tests/_mcp_tool_commands.py', '<module>', '_ext(timeout, wait)', 4):
         'the net-capture-get fixture; the deadline is the read the tool'
         'waits for and nothing here launches',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(bodies, filter, tabId, timeout, wait)', 1):
         'the read fixture with a filter and bodies, so the tool has to'
         'pass the selector through unchanged',
    ('tests/_mcp_tool_commands.py', '<module>', '_ext(timeout, wait)', 5):
         'the read fixture sent unwaited; the fifth site this deadline'
         'spelling names, and the ordinal is what tells them apart',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(method, params, timeout, wait)', 1):
         'the cdp fixture naming a method and its params; the timeout is'
         'the round trip, and the attach is the extension\'s',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(keep_session, method, params, tabId, timeout, wait)', 1):
         'the cdp fixture asking for a kept session; the flag decides'
         'whether the attachment survives, and this row is the dict',
    ('tests/_mcp_tool_commands.py', '<module>',
     '_ext(method, params, timeout, wait)', 2):
         'the cdp fixture sent unwaited; the method and params again at a'
         'second site under this spelling',
    ('tests/_mcp_tools_helpers.py', 'checked_timeout', 'self.record(timeout)',
     1):
         'the probe recording that the transport was asked for a client;'
         'it appends to a list and returns None',
    ('tests/_netcapture_harness.py', 'buffered', 'read()', 1):
         'appending the read step to a plan and returning the entries the'
         'capture holds; the plan is data the harness replays',
    ('tests/_netcapture_harness.py', 'finished', 'event(requestId)', 1):
         'the matching loading-finished event; the request id is the only'
         'field and it names a buffered entry',
    ('tests/_netcapture_harness.py', 'read', 'cmd(tabId)', 1):
         'the read command dict; it names a tab and a type, and the worker'
         'answers it rather than a process',
    ('tests/_netcapture_harness.py', 'request', 'event(request, requestId)',
     1):
         'building one `Network.requestWillBeSent` event; the fields are'
         'the event the harness feeds the worker',
    ('tests/_netcapture_harness.py', 'start', 'cmd(tabId)', 1):
         'building one net-capture command dict; the fields are the'
         'command the harness posts, not arguments to a process',
    ('tests/_netcapture_harness.py', 'stop', 'cmd(tabId)', 1):
         'the stop command dict, the counterpart of the start row; the id'
         'is minted here and the command is posted by the harness',
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
    ('tests/_util.py', 'post_json', 'request(body)', 1):
         'an HTTP helper opening a socket; no git '
         'process is behind it',
    ('tests/test_aggregate_gate.py',
     'test_every_single_dependency_result_is_tabled', '_needs()', 1):
         'a table builder called with a keyword mapping; it builds a'
         'table, it launches nothing',
    ('tests/test_aggregate_gate.py',
     'test_two_dependencies_are_decided_jointly', 'zip()', 1):
         'the same table builder, keyed from a zipped mapping',
    ('tests/test_atomic_file.py', 'replace', 'self._publish()', 1):
         'the replacement the fake `os.replace` was constructed with; it'
         'writes a temp file into place and spawns nothing',
    ('tests/test_client_credentials.py', '_eval', 'getattr(mod, name)()', 1):
         'the loaded MCP tool reached by name, called with the arguments'
         'under test; the getattr is a lookup, not a launch',
    ('tests/test_client_credentials.py', '_mcp_preserves_application_paths',
     'mod.mcp.registered[name]()', 1):
         'the same loaded tool through the registry mapping; the envelope'
         'it returns is what the assertion reads',
    ('tests/test_cmdqueue_injectors.py', 'open_outcome', 'opener()', 1):
         'the queue\'s own opener called with the receiver and arguments'
         'under test; it opens a path and records its mode',
    ('tests/test_cmdqueue_injectors.py', 'open_outcome', 'opener(mode)', 1):
         'the same opener in each mode the loop tries; a queued path is'
         'opened, and the outcome is the state tuple it returns',
    ('tests/test_cmdqueue_injectors.py', 'path_failure',
     'getattr(Path, operation)()', 1):
         '`Path.<operation>` reached by name on the receiver the test'
         'passes; the point is the failure it catches, not a process',
    ('tests/test_cmdqueue_injectors.py',
     'test_injectors_preserve_target_failures_and_untargeted_create',
     'path_failure()', 1):
         'the injector\'s own failure probe with the open arguments; it'
         'returns the caught type, and the loop asserts on it',
    ('tests/test_cmdqueue_injectors.py',
     'test_injectors_preserve_target_failures_and_untargeted_create',
     'path_failure()', 2):
         'the same probe against the exploding receiver, which is what the'
         'first of these two rows is compared with',
    ('tests/test_cmdqueue_injectors.py',
     'test_injectors_preserve_target_failures_and_untargeted_create',
     'path_failure()', 3):
         'the exploding receiver again, on the untargeted half of the'
         'pair; the probe is what proves the target was preserved',
    ('tests/test_cmdqueue_injectors.py',
     'test_injectors_preserve_target_failures_and_untargeted_create',
     'path_failure()', 4):
         'the refusing receiver closing the pair, so the two halves are'
         'read beside each other rather than alone',
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
    ('tests/test_gate_freshness_run.py', '_process_capturing_stderr',
     'm.process()', 1):
         'the gate module\'s own step runner, called with the stubbed'
         'reader this test installs instead of a real git',
    ('tests/test_gate_freshness_run.py', '_writing_read', '_flow_read()', 1):
         'building a reader double whose recorded writes carry the head'
         'sha; the flow is data the fake reader replays',
    ('tests/test_mcp_entry_point.py', '_cleanup_mcp', 'proc.wait(timeout)', 1):
         'an MCP process wait while the test tears it down',
    ('tests/test_mcp_entry_point.py', '_cleanup_mcp', 'proc.wait(timeout)', 2):
         'the reap after that wait, on the same process',
    ('tests/test_mcp_server.py', '_surface_responder_errors',
     'thread.join(timeout)', 1):
         'a thread join on a thread the fixture started',
    ('tests/test_mcp_live_tools.py',
     '_surface_responder_errors', 'thread.join(timeout)', 1):
         'a thread join on a responder this suite started; the'
         'failure it surfaces is the responder\'s own',
    ('tests/test_mcp_server.py', 'callers',
     'mod.bridge.ext_cmd(domain, timeout)', 1):
        "an MCP tool call whose timeout is the tool's, not a bound"
         'on a process',
    ('tests/test_mcp_server.py',
     'test_a_nonpositive_mcp_timeout_admits_no_command',
     'getattr()', 1):
        "the test's subject: an MCP call the server must reject for"
         'its timeout',
    ('tests/test_mcp_server.py',
     'test_mcp_port_zero_announces_the_actual_bound_port',
     'mod._bound.wait(timeout)', 1):
         'an assertion on a bound event the module sets; the wait IS'
         'the assertion',
    ('tests/test_mcp_tools.py', '_bridge_interactions', 'tool()', 1):
         'the recorded tool the bridge probe dispatches; the arguments are'
         'the tool\'s, and the probe answers them',
    ('tests/test_mcp_transport_close.py', 'exercise', 'ClosingClient()', 1):
         'the double standing in for the HTTP client, constructed with the'
         'factories and close bookkeeping the test asserts on',
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
    ('tests/test_segment_mint.py',
     'test_allow_refuses_anything_but_an_http_origin', '_command()', 1):
         'building a typed allow-segment-origin command; the fields are'
         'the origin under test and the dict is posted by the harness',
    ('tests/test_segment_mint.py',
     'test_allow_refuses_anything_but_an_http_origin', '_command()', 2):
         'the same builder wrapped in the list form, so the refusal is'
         'read from a batched dispatch as well as a single one',
    ('tests/test_segment_mint.py', 'test_revoke_refuses_an_invalid_origin',
     '_command()', 1):
         'the revoke counterpart; the command dict is the subject the test'
         'dispatches and the mint route answers',
    ('tests/test_segment_routes.py', 'refusing', 'real()', 1):
         'a test double delegating with its arguments; it launches'
         'nothing of its own',
    ('tests/test_segment_storage.py',
     'test_concurrent_segment_writes_share_one_quota_snapshot',
     'future.result(timeout)', 1):
         'a future this test\'s own thread pool submitted; the 10s bounds'
         'the assertion, and the suite bounds the process above it',
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
}
