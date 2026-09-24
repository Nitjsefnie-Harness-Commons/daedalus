"""The strict bridge-fetch gate shared by the worker-VM harnesses.

Not a suite itself — run_tests.py only loads `test_*.py`. The JS text is
spliced into a Node harness, so it shares that harness's scope.

A harness that splices `STRICT_FETCH` must define, before the splice:

  plan        the scenario's object. `planned` lists the `"METHOD /path"`
              keys it declares; `hosts` optionally narrows the permitted
              origins and `statuses` queues the stream branch's answers.
  BRIDGE_URL  the default permitted origin.
  response(status, data)  plain response factory.
  streamResponse(answer)  response factory for the stream branch.
  streamFetches, resultPosts, nonStreamFetches, refusedFetches,
  badOrigins  arrays the gate records into.

Every one of those names is load-bearing: the gate checks its own contract
the moment it is spliced, records any missing or wrong-typed name in
`gateContractFaults`, and each harness asserts that list empty. Without
that check a missing name throws a `ReferenceError` the worker's stream
loop swallows, so the omission would leave the suite green.

The gate answers a request only while the plan declares it, refuses
everything else with status 599, and records every request it sees. The
origin is checked first — the stream URL is the one request a worker
derives from runtime config, so a foreign-origin or relative stream is
refused and recorded like any other. A request refused for its origin
spends no route allowance, so the next legitimate request on that route
still answers 200. The Python helpers below drive a spliced harness and pin
a scenario's recorded traffic against the plan it declared.
"""
import json
import shutil

from _boundary_env import run_node_program

STRICT_FETCH = r"""
// A missing contract name must be loud, not swallowed by the worker. This
// runs once, at splice time, before any fetch.
const gateContractFaults = [];
if (typeof plan === 'undefined') {
  gateContractFaults.push('plan');
} else if (!Array.isArray(plan.planned)) {
  gateContractFaults.push('plan.planned');
}
if (typeof BRIDGE_URL === 'undefined') gateContractFaults.push('BRIDGE_URL');
if (typeof response !== 'function') gateContractFaults.push('response');
if (typeof streamResponse !== 'function') {
  gateContractFaults.push('streamResponse');
}
if (typeof streamFetches === 'undefined') {
  gateContractFaults.push('streamFetches');
}
if (typeof resultPosts === 'undefined') gateContractFaults.push('resultPosts');
if (typeof nonStreamFetches === 'undefined') {
  gateContractFaults.push('nonStreamFetches');
}
if (typeof refusedFetches === 'undefined') {
  gateContractFaults.push('refusedFetches');
}
if (typeof badOrigins === 'undefined') gateContractFaults.push('badOrigins');

// A request is planned as often as the recording shows; beyond that it is
// refused and recorded. No route is special-cased, so a new one is caught.
// The parsed body rides along so a harness reads the worker's request
// without wrapping its own fetch. The entry is returned so bridgeFetch can
// stamp the answer status on it only after the answer really was built.
function accountRequest(request, init) {
  const seen = nonStreamFetches.filter((i) => i.request === request).length;
  const planned = (plan.planned || []).filter((i) => i === request).length;
  const refused = seen >= planned;
  let body = null;
  if (init && init.body) {
    try { body = JSON.parse(init.body); } catch (_) { body = init.body; }
  }
  const entry = { request, refused, body, status: null };
  nonStreamFetches.push(entry);
  if (refused) refusedFetches.push(request);
  return entry;
}

function originOf(url) {
  const match = String(url).match(/^https?:\/\/[^/]+/);
  return match ? match[0] : '(no origin)';
}

function permittedOrigins() {
  return plan.hosts || [BRIDGE_URL];
}

// Route, not host: the count is per route, and the origin gate above
// decides which bridges the plan admits at all.
function requestKey(url, init) {
  return (init.method || 'GET') + ' ' + url.replace(/^https?:\/\/[^/]+/, '');
}

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  // The origin gate runs FIRST: the stream URL is derived from runtime
  // config, so a foreign-origin or relative stream must be refused and
  // recorded here, not silently answered by the stream branch below.
  const origin = originOf(url);
  if (!permittedOrigins().includes(origin)) {
    badOrigins.push(origin);
    return response(599, { ok: false, error: 'origin not permitted' });
  }
  if (url.includes('/stream?')) {
    const next = plan.statuses && plan.statuses.length
      ? plan.statuses.shift()
      : 503;
    streamFetches.push({
      auth: (init.headers || {}).Authorization || null,
      answered: next,
    });
    if (next === 'down') {
      throw new TypeError('Failed to fetch');
    }
    return streamResponse(next);
  }
  const request = requestKey(url, init);
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    resultPosts.push({ ...payload, did: payload._did || null });
  }
  const entry = accountRequest(request, init);
  const status = entry.refused ? 599 : 200;
  const answer = response(status, status === 200
    ? { ok: true }
    : { ok: false, error: 'more often than declared' });
  // Stamped only after the answer was built: a missing `response` throws
  // here, and the record must show a request that was never answered.
  entry.status = status;
  return answer;
}
"""


def require_node():
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    return node


def run_gate(node, program, arguments, *, cwd, plan):
    result = run_node_program(node, program, arguments, cwd=cwd,
                              payload=plan)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def assert_gate_clean(*, contract_faults, records, refused, bad_origins,
                      stream_answered, planned, planned_stream):
    """Pin a scenario's recorded bridge traffic against the plan it declared.

    The non-stream routes are compared as a whole list and the stream
    fetches as a whole list, so an undeclared, missing or extra request is
    a mismatch by count and by route — never a membership or count-only
    question. A refusal is loud here even when the worker swallowed it, a
    foreign origin is recorded, and every accounted request must carry a
    real answer status (a missing `response` leaves `status` null).
    """
    assert contract_faults == [], (
        'gate contract name(s) missing or wrong-typed:', contract_faults)
    assert bad_origins == [], (
        'bridge origin(s) the scenario did not permit:', bad_origins)
    assert refused == [], (
        'bridge request(s) seen more often than the scenario declared:',
        refused)
    seen = [record['request'] for record in records]
    assert sorted(seen) == sorted(planned), (
        'non-stream requests differ from the declaration:', seen, planned)
    unanswered = [r['request'] for r in records if r.get('status') is None]
    assert unanswered == [], (
        'accounted request(s) the gate recorded but never answered:',
        unanswered)
    assert list(stream_answered) == list(planned_stream), (
        'stream fetches differ from the declaration:',
        stream_answered, planned_stream)
