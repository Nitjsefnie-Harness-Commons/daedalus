"""The same-named JavaScript locals that are not re-implementations.

The residue of the JavaScript half of the re-implementation rule, keyed
by (repo-relative path, name) for the same reason and on the same terms
as `_unconsolidated_names`: a row cannot be widened by a prefix or a
substring match, and every row says why its site is not a copy.

The boundary on this table is the same one the Python table's is, and
the same comparison: a row may name any declaration the merge base
already carried, and may NOT name one this branch added. It is keyed on
the DECLARATION rather than on the (path, name) pair, so a second, new
declaration of an already-tabled name in the same file is refused — a
pair-keyed rule covers it for free, because the first declaration's row
already matches the pair.

It reads the base tree's own declarations rather than a list of the
files the branch touched. That form is not a preference: 95 files carry
a row in the Python table and 31 in this one, this branch edits seven
of the ninety-five and eighteen of the thirty-one, and a path list
cannot tell a site the branch wrote from one it did not. It cost that
branch five renames in test files it was not asked to touch, every one
of them honest and every one of them undone when the boundary changed.

Like every row in the Python table, each of these is a site the rule
finds and consolidation has not reached. The `response`, `delay`, `copy`
and `streamResponse` factories each have one copy now, in
`tests/_worker_sources.py`, spliced where the local declaration stood.
What is left below answers something a shared factory does not: a
headers map, a `SyntaxError` from `json()`, a never-settling body, or a
ledger of one stream's own answers.
"""

UNCONSOLIDATED_JS_NAMES = {
    ('tests/_boundary_scenarios.py', 'settle'):
        'this is the one-turn drain a boundary scenario awaits, where the '
        'attachment harness own settle loops a caller-named number of turns '
        '(times || 6) so an in-flight dispatch can be awaited',
    ('tests/_debugger_attachment_harness.py', 'settle'):
        'this loops a caller-named number of turns (times || 6) so an '
        'in-flight dispatch can be awaited, where the boundary scenario own '
        'settle drains exactly one turn',
    ('tests/_debugger_attachment_harness.py', 'dispatch'):
        'this runs one command through dispatchCommand in the VM and parks '
        'the promise in inFlight, where the GM harnesses deliver a page '
        'message to a registered listener and the tabs harness runs the next '
        'queued command',
    ('tests/_dashfield.py', 'describe'):
        'this destructures a [label, control] pair and returns the '
        'label/control association record the a11y assertions read, where the '
        'shell own describe renders an arbitrary value to a display string',
    ('tests/_dashshell.py', 'describe'):
        'this renders an arbitrary value to a display string and answers '
        '[unprintable value] when rendering throws, where the field own '
        'describe destructures a label/control pair',
    ('tests/_boundary_env.py', 'clearScheduled'):
        'the boundary fake clears a timer by id out of its own array, and '
        'the hotfix harness clearing one is the other side of this pair '
        'rather than a suite reading a shared helper',
    ('tests/_boundary_env.py', 'schedule'):
        'the boundary fake arms a timer and fires it itself on the route '
        'scenarios, where the hotfix harness arms one and never fires it, '
        'so the two would answer differently for one callback',
    ('tests/_hotfixharness.py', 'clearScheduled'):
        'the hotfix fake clears the timer at an index it minted, where the '
        'boundary fake finds the timer by id out of a list it searched',
    ('tests/_hotfixharness.py', 'schedule'):
        'the hotfix fake arms a timer and returns its index without ever '
        'firing it, where the boundary fake fires it on the route '
        'scenarios itself',
    ('tests/_hotfixharness.py', 'waitFor'):
        'the hotfix wait polls two thousand times and answers a boolean, '
        'where the boundary wait throws on exhaustion and the overlap one '
        'takes a deadline',
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
    ('tests/_bridge_fake_oracle_harness.py', 'run'):
        'the oracle harness own entry, which serves a plan against a real '
        'upstream server',
    ('tests/_bridge_fake_oracle_harness.py', 'streamResponse'):
        'the oracle harness own stream factory, which carries a hang body '
        'the shared copy also carries, so the two are copies of one another',
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
    ('tests/_mainworldharness.py', 'run'):
        'the MAIN-world harness own entry, 190 lines of step machine over '
        'the injected function',
    ('tests/_overlap.py', 'bounded'):
        'the overlap harness own deadline, which reads an injected clock '
        'and takes a null to disable itself, where the dashboard harness '
        'samples wall time and has no such parameter',
    ('tests/_overlap.py', 'waitFor'):
        'the overlap wait takes a deadline and can be disabled outright, '
        'which neither of the two polled forms has',
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
    ('tests/_tabs_harness.py', 'dispatch'):
        'the tabs harness runs the next queued command in the VM, where the '
        'GM harness delivers a page message',
    ('tests/_tabs_harness.py', 'run'):
        'the tabs harness own entry, which applies a plan over chrome.tabs',
    ('tests/_worker_sources.py', 'streamResponse'):
        'the shared factory, reported because the oracle harness answers '
        'from a ledger of its own stream bodies and the dashboard shell '
        'answers with a headers map and an abort signal, so neither can '
        'import it',
    ('tests/test_config_boot_generation.py', 'copy'):
        'the structural-clone helper under a parameter named v where the '
        'shared copy names it value, so the same expression with a '
        'different local',
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
        'this one carries a headers map the shared factory does not, '
        'because the dashboard reads a content type off the answer',
    ('tests/test_gate_extensions.py', 'response'):
        'the unparsed-plan harness answers ok from the caller over six '
        'body lines rather than from the status range, and the other two '
        'of the three harnesses now splice the shared factory',
    ('tests/test_gm_relay_authority.py', 'response'):
        'this one carries a reader body and a status text, because the '
        'relay harness answers its own fetch through it',
    ('tests/test_gm_relay_authority.py', 'run'):
        'the GM relay harness own entry, 98 lines over one page call',
    ('tests/test_gm_transfers.py', 'flushMessages'):
        'the transfer suite writes the drain loop into each of its own '
        'three harnesses, and two of them bound it with a guard the owner '
        'has no counter for',
    ('tests/test_segment_mint.py', 'response'):
        'this one throws SyntaxError from json() on a null body, which the '
        'mint harness needs and the shared factory does not model',
    ('tests/test_segment_mint.py', 'run'):
        'the mint harness own entry, 42 lines over one job capability',
    ('tests/test_starvation_bounds.py', 'delay'):
        'the next-turn delay, the same three lines as the other seven '
        'copies',
    ('tests/test_starvation_bounds.py', 'response'):
        'the fetch-response factory, one of sixteen copies of the same '
        'nine lines',
    ('tests/test_starvation_bounds.py', 'sendCommand'):
        'the starvation harness answers two CDP methods and never settles '
        'one, where the CDP harness answers the whole protocol and compiles '
        'through it',
    ('tests/test_starvation_bounds.py', 'streamResponse'):
        'the starvation harness answers only a never-settling body, where '
        'the shared factory answers a hang and a disabled error',
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
    ('tests/test_worker_close_tab.py', 'run'):
        'the close-tab harness own entry, 30 lines over one closed tab',
    ('tests/test_worker_close_tab.py', 'setTimeoutStandIn'):
        'the close-tab harness defers timers behind its own flag, where the '
        'tabs harness records each timer and its error',
    ('tests/test_worker_register_throttle.py', 'clearScheduled'):
        'the throttle harness records the clear and drops the pending flag, '
        'where the boundary fake only marks the timer and neither records',
    ('tests/test_worker_register_throttle.py', 'response'):
        'the same answer as the shared factory over six body lines '
        'instead of nine, so the two do not meet',
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
    ('tests/test_worker_result_post.py', 'run'):
        'the postResult harness own entry, 31 lines over one result post',
    ('tests/test_worker_result_post.py', 'settle'):
        'the postResult harness drains one turn, the same shape as the '
        'throttle harness and not the boot harness own loop',
    ('tests/_dashdom.py', 'textNode'):
        'this is the text-node factory the jsdom-less shell document '
        'uses. Its twin is the row for tests/_dashnode.py, five body '
        'lines each, and that twin is the owner',
    ('tests/_dashnode.py', 'jsonResponse'):
        'this is the JSON answer the dashboard node own reader expects, '
        'where the owner is the shell harness one',
    ('tests/_dashnode.py', 'textNode'):
        'this is the text-node factory the dashboard node document uses, '
        'five body lines, and its twin in tests/_dashdom.py is the owner. '
        'A separate file, tests/_dashshell.py, defines no textNode at all',
    ('tests/_dashshell.py', 'jsonResponse'):
        'this is the JSON answer the shell harness own reader expects, '
        'where the owner belongs to the dashboard node harness',
    ('tests/_dashshell.py', 'streamResponse'):
        'this answers the shell harness own stream shape, where the owner '
        'is the shared hang/disabled factory five harnesses splice in',
    ('tests/_netcapture_harness.py', 'maybeReject'):
        'this refuses a declarativeNetRequest entry by RULESET key, where '
        'the tabs harness refuses a chrome surface by API name',
    ('tests/_netcapture_harness.py', 'record'):
        'byte-identical to the row for tests/_tabs_harness.py, and '
        'neither is a copy of the owner: the same chrome-double helper '
        'written into two harnesses whose chrome doubles differ '
        'elsewhere, and a third copy of it does not exist, so a shared '
        'one would have exactly two users',
    ('tests/_netcapture_harness.py', 'run'):
        'this is the capture harness own driver, 45 body lines. Ranked '
        'from 1 over the 21 JavaScript `run` declarations in the tree '
        'by body lines, it is the seventh, where the owner is '
        'tests/_relayharness.py at 236, the largest; the order runs '
        '236, 198, 137, 120, 98, 50, 45. No shared copy of either '
        'driver would serve both harnesses',
    ('tests/_tabs_harness.py', 'maybeReject'):
        'this refuses a chrome surface by API name, where the netcapture '
        'harness refuses a declarativeNetRequest entry by RULESET key',
    ('tests/_tabs_harness.py', 'record'):
        'byte-identical to the row for tests/_netcapture_harness.py, and '
        'neither is a copy of the owner: the same chrome-double helper '
        'written into two harnesses whose chrome doubles differ '
        'elsewhere, and a third copy of it does not exist, so a shared '
        'one would have exactly two users',
    ('tests/test_tab_routing.py', 'load'):
        'this is the routing suite own payload loader, where the owner is '
        'the shell harness node loader',
}
