"""The same-named JavaScript locals that are not re-implementations.

The residue of the JavaScript half of the re-implementation rule, keyed
by (repo-relative path, name) for the same reason and on the same terms
as `_unconsolidated_names`: a row cannot be widened by a prefix or a
substring match, and every row says why its site is not a copy.

The boundary on this table is the same principle the Python one states —
the table may not excuse a line the branch wrote — in the form that
principle can take for a rule this branch introduces. A row may name any
site that already existed at the merge base, and may NOT name a site
this branch added. The file-scoped form, which is what the Python
table's boundary is, cannot be used here: this branch edits twelve of the
eighteen files this residue lives in, for the unrelated `eventTarget`
migration, so a file-scoped boundary would forbid recording rows for
sites that predate it. The site-scoped form absorbs what `main` lands
underneath the branch the same way, and still cannot excuse a
declaration the branch itself added.

Like every row in the Python table, each of these is a site the rule
finds and consolidation has not reached. The `response` factory is the
largest of them — thirteen harnesses carry the same nine lines, which is
the same mechanism this issue is about and under the same fifteen-line
duplicate threshold — and it is the second wave of that consolidation,
not this one.
"""

UNCONSOLIDATED_JS_NAMES = {
    ('tests/_boundary_env.py', 'copy'):
        'the structural-clone helper each harness writes for itself, one of '
        'five copies of the same expression',
    ('tests/_boundary_env.py', 'delay'):
        'the next-turn delay each harness writes for itself, one of seven '
        'copies of the same three lines',
    ('tests/_boundary_env.py', 'response'):
        'the fetch-response factory each harness writes for itself, one of '
        'thirteen copies of the same nine lines',
    ('tests/_boundary_env.py', 'waitFor'):
        'the boundary fake polls a predicate a thousand times, where the '
        'overlap wait takes a deadline and the stream wait polls two '
        'thousand',
    ('tests/_boundary_scenarios.py', 'run'):
        'the boundary scenario driver, which runs one declared scenario and '
        'reports its gate, where every other run is a harness own entry and '
        'none of them is interchangeable with it',
    ('tests/_bridge_fake_oracle_harness.py', 'main'):
        'the oracle harness own entry, which starts the upstream server a '
        'plan forwards to, where the GM harness runs storage cases',
    ('tests/_bridge_fake_oracle_harness.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/_bridge_fake_oracle_harness.py', 'run'):
        'the oracle harness own entry, which serves a plan against a real '
        'upstream server',
    ('tests/_bridge_fake_oracle_harness.py', 'streamResponse'):
        'the oracle harness own stream factory, which carries a hang body '
        'the shared copy also carries, so the two are copies of one another',
    ('tests/_cdpharness.py', 'delay'):
        'the next-turn delay, the same three lines as the other six copies',
    ('tests/_cdpharness.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/_dashnode.py', 'bounded'):
        'the dashboard harness own deadline, which samples wall time and '
        'caps a late sample, where the overlap harness reads the clock it '
        'was handed',
    ('tests/_gm_harness.py', 'dispatch'):
        'the GM harness delivers one page message to the registered '
        'listener and reads the answer, where the tabs harness runs one '
        'command',
    ('tests/_gm_harness.py', 'main'):
        'the GM harness own scenario list, where the oracle harness starts '
        'a server and the two-origin harness runs cross-origin cases',
    ('tests/_gm_harness.py', 'makeStorage'):
        'the GM harness binds this name twice, once for the recording '
        'storage and once for the always-failing one, where the two-origin '
        'harness binds it once',
    ('tests/_gm_harness.py', 'storedValues'):
        'the GM harness reads the store own keys, one of two copies of the '
        'same eight lines',
    ('tests/_gm_two_origin.py', 'main'):
        'the two-origin harness own scenario, 208 lines of cross-origin '
        'cases no other harness runs',
    ('tests/_gm_two_origin.py', 'makeStorage'):
        'the two-origin storage defers every callback so a write can be '
        'held open, where the GM storage calls back immediately',
    ('tests/_gm_two_origin.py', 'storedValues'):
        'the GM harness store reader, one of two copies of the same eight '
        'lines',
    ('tests/_mainworldharness.py', 'delay'):
        'the next-turn delay, the same three lines as the other six copies',
    ('tests/_mainworldharness.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/_mainworldharness.py', 'run'):
        'the MAIN-world harness own entry, 190 lines of step machine over '
        'the injected function',
    ('tests/_overlap.py', 'bounded'):
        'the overlap harness own deadline, which reads an injected clock '
        'and takes a null to disable itself, where the dashboard harness '
        'samples wall time and has no such parameter',
    ('tests/_overlap.py', 'delay'):
        'the overlap delay takes a millisecond count the shared shape has '
        'no parameter for, so the signatures do not meet',
    ('tests/_overlap.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/_overlap.py', 'waitFor'):
        'the overlap wait takes a deadline and can be disabled outright, '
        'which neither of the two polled forms has',
    ('tests/_relayharness.py', 'delay'):
        'the next-turn delay, the same three lines as the other six copies',
    ('tests/_relayharness.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/_relayharness.py', 'run'):
        'the eval-relay harness own entry, 236 lines driving the three '
        'shipped scripts',
    ('tests/_relayharness.py', 'waitFor'):
        'the relay harness polls the same thousand times as the boundary '
        'fake, and the overlap wait takes a deadline neither has',
    ('tests/_stream_fake.py', 'bridgeFetch'):
        'the gate fake own fetch, which refuses a foreign origin before it '
        'answers anything, where the tabs harness records a result post and '
        'never refuses',
    ('tests/_tabs_harness.py', 'bridgeFetch'):
        'the tabs harness own fetch, which records a result post and '
        'answers a disabled stream, where the gate fake refuses by origin '
        'first and records a bad origin',
    ('tests/_tabs_harness.py', 'copy'):
        'the structural-clone helper, the same expression as the other four '
        'copies',
    ('tests/_tabs_harness.py', 'dispatch'):
        'the tabs harness runs the next queued command in the VM, where the '
        'GM harness delivers a page message',
    ('tests/_tabs_harness.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/_tabs_harness.py', 'run'):
        'the tabs harness own entry, which applies a plan over chrome.tabs',
    ('tests/_worker_sources.py', 'streamResponse'):
        'the shared factory, reported because the oracle harness carries '
        'its own copy of it rather than importing it',
    ('tests/test_config_boot_generation.py', 'copy'):
        'the structural-clone helper under a parameter named v where the '
        'other copies name it value, so the same expression with a '
        'different local',
    ('tests/test_config_boot_generation.py', 'delay'):
        'the next-turn delay, the same three lines as the other six copies',
    ('tests/test_config_boot_generation.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/test_config_boot_generation.py', 'run'):
        'the boot-generation harness own entry, 120 lines over the config '
        'read',
    ('tests/test_config_boot_generation.py', 'settle'):
        'the boot harness drains twenty-five turns of its own delay, where '
        'the boundary scenario owner drains one and the throttle harness '
        'drains one',
    ('tests/test_config_boot_generation.py', 'streamResponse'):
        'the boot harness answers a never-settling stream and nothing else, '
        'where the shared factory answers a hang and a disabled error',
    ('tests/test_dashboard_behaviour.py', 'clearScheduled'):
        'the dashboard harness clears an item out of a collection, where '
        'the boundary fake marks a timer and the throttle harness drops a '
        'pending flag',
    ('tests/test_dashboard_behaviour.py', 'response'):
        'this one carries a headers map the other twelve do not, because '
        'the dashboard reads a content type off the answer',
    ('tests/test_gate_extensions.py', 'response'):
        'the gate extension suite writes two response factories into its '
        'three harnesses, one of which answers ok from the caller rather '
        'than from the status range',
    ('tests/test_gate_extensions.py', 'streamResponse'):
        'the gate extension suite writes the disabled-error line into each '
        'of its three harnesses, and the shared factory carries it with a '
        'hang body these do not',
    ('tests/test_gm_relay_authority.py', 'response'):
        'this one carries a reader body and a status text, because the '
        'relay harness answers its own fetch through it',
    ('tests/test_gm_relay_authority.py', 'run'):
        'the GM relay harness own entry, 98 lines over one page call',
    ('tests/test_gm_relay_authority.py', 'streamResponse'):
        'the GM relay harness answers only the disabled error, where the '
        'shared factory also answers a hang',
    ('tests/test_gm_transfers.py', 'flushMessages'):
        'the transfer suite writes the drain loop into each of its own '
        'three harnesses, and two of them bound it with a guard the owner '
        'has no counter for',
    ('tests/test_relay_example_placeholders.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/test_relay_example_placeholders.py', 'streamResponse'):
        'the placeholder harness answers only the disabled error, where the '
        'shared factory also answers a hang',
    ('tests/test_segment_mint.py', 'copy'):
        'the structural-clone helper, the same expression as the other four '
        'copies',
    ('tests/test_segment_mint.py', 'response'):
        'this one throws SyntaxError from json() on a null body, which the '
        'mint harness needs and none of the other twelve models',
    ('tests/test_segment_mint.py', 'run'):
        'the mint harness own entry, 42 lines over one job capability',
    ('tests/test_segment_mint.py', 'streamResponse'):
        'the mint harness answers only the disabled error, where the shared '
        'factory also answers a hang',
    ('tests/test_starvation_bounds.py', 'delay'):
        'the next-turn delay, the same three lines as the other six copies',
    ('tests/test_starvation_bounds.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/test_starvation_bounds.py', 'sendCommand'):
        'the starvation harness answers two CDP methods and never settles '
        'one, where the CDP harness answers the whole protocol and compiles '
        'through it',
    ('tests/test_starvation_bounds.py', 'streamResponse'):
        'the starvation harness answers only a never-settling body, where '
        'the shared factory answers a hang and a disabled error',
    ('tests/test_stream_backoff.py', 'copy'):
        'the structural-clone helper, the same expression as the other four '
        'copies',
    ('tests/test_stream_backoff.py', 'delay'):
        'the next-turn delay, the same three lines as the other six copies',
    ('tests/test_stream_backoff.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/test_stream_backoff.py', 'run'):
        'the stream harness own entry, 137 lines over the reconnect ledger',
    ('tests/test_stream_backoff.py', 'settle'):
        'the stream harness drains its own delay, where the boundary '
        'scenario owner drains one and the throttle harness drains one',
    ('tests/test_stream_backoff.py', 'streamResponse'):
        'the stream harness carries 93 lines of its own stream answers, '
        'silent and ok-data among them, where the shared factory carries '
        'one',
    ('tests/test_stream_backoff.py', 'waitFor'):
        'the stream harness polls two thousand times where the boundary '
        'fake polls a thousand, so the two would give up at different steps',
    ('tests/test_tab_routing_js_operations.py', 'run'):
        'the routing suite writes two five-line run helpers into its '
        'operation cases, and neither is a copy of a harness entry',
    ('tests/test_worker_close_tab.py', 'copy'):
        'the structural-clone helper, the same expression as the other four '
        'copies',
    ('tests/test_worker_close_tab.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/test_worker_close_tab.py', 'run'):
        'the close-tab harness own entry, 30 lines over one closed tab',
    ('tests/test_worker_close_tab.py', 'setTimeoutStandIn'):
        'the close-tab harness defers timers behind its own flag, where the '
        'tabs harness records each timer and its error',
    ('tests/test_worker_close_tab.py', 'streamResponse'):
        'the close-tab harness answers only the disabled error, where the '
        'shared factory also answers a hang',
    ('tests/test_worker_register_throttle.py', 'clearScheduled'):
        'the throttle harness records the clear and drops the pending flag, '
        'where the boundary fake only marks the timer and neither records',
    ('tests/test_worker_register_throttle.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/test_worker_register_throttle.py', 'run'):
        'the register harness own entry, 35 lines over the registration '
        'window',
    ('tests/test_worker_register_throttle.py', 'schedule'):
        'the throttle fake arms a timer and returns its id with no '
        'immediate fire, where the boundary fake arms one and fires it on '
        'the route scenarios itself',
    ('tests/test_worker_register_throttle.py', 'settle'):
        'the throttle harness drains one turn without the owner comment, '
        'where the boot harness drains twenty-five',
    ('tests/test_worker_register_throttle.py', 'streamResponse'):
        'the throttle harness answers only the disabled error, where the '
        'shared factory also answers a hang',
    ('tests/test_worker_result_post.py', 'response'):
        'the fetch-response factory, one of thirteen copies of the same '
        'nine lines',
    ('tests/test_worker_result_post.py', 'run'):
        'the postResult harness own entry, 31 lines over one result post',
    ('tests/test_worker_result_post.py', 'settle'):
        'the postResult harness drains one turn, the same shape as the '
        'throttle harness and not the boot harness own loop',
    ('tests/test_worker_result_post.py', 'streamResponse'):
        'the postResult harness answers only the disabled error, where the '
        'shared factory also answers a hang',
}
