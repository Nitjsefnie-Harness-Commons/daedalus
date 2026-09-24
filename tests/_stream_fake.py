"""The strict bridge-fetch gate shared by the worker-VM harnesses.

Not a suite itself — run_tests.py only loads `test_*.py`. The JS text is
spliced into a Node harness, so it shares that harness's scope.

A harness that splices `STRICT_FETCH` must define, before the splice:

  plan        the scenario's object. `planned` lists the declared request
              keys; `hosts` optionally lists the permitted BRIDGE origins
              (a bridge request keys on `"METHOD /path"`, so a new bridge URL
              the config rotates to is the same route), `relayHosts`
              optionally lists permitted NON-bridge origins (a relay request
              keys on `"METHOD <full-url>"`, so it can never collide with a
              bridge request to the same path), `statuses` queues the stream
              branch's answers, and `answers` optionally maps a declared key
              to the answer the scenario planned for it — `{status, body}`,
              `{throw: msg}` for a bridge the scenario models as unreachable,
              `{stream: N}` to have the harness build an N-chunk relay answer,
              or a LIST of these for a per-attempt sequence (one answer per
              attempt; a sequence that runs out is a recorded refusal, never
              a 200).
  BRIDGE_URL  the default permitted origin.
  response(status, data)  plain response factory.
  streamResponse(answer)  response factory for the stream branch.
  chunkedResponse(count)  builds a declared `{stream: N}` answer. Required by
              the contract exactly when a plan declares a `{stream: N}`
              answer; otherwise it need not exist.
  streamFetches, resultPosts, nonStreamFetches, refusedFetches,
  badOrigins  arrays the gate records into.

Every one of those names is load-bearing: the gate checks its own contract
the moment it is spliced, records any missing or wrong-typed name in
`gateContractFaults`, and each harness asserts that list empty. Without
that check a missing name throws a `ReferenceError` the worker's stream
loop swallows, so the omission would leave the suite green.

The gate answers a request only while the plan declares it, refuses
everything else with status 599, and records every request it sees. A
declared request is answered 200 `{ok: true}` unless `plan.answers` names a
status, body or throw for its key — so a scenario can model a bridge's own
error answer, and a declared throw models an unreachable bridge. A planned
answer never applies to a request past its declared count: that
one is refused by status whatever the plan says. The origin is checked
first — the stream URL is the one request a worker derives from runtime
config, so a foreign-origin or relative stream is refused and recorded like
any other. A request refused for its origin spends no route allowance, so
the next legitimate request on that route still answers 200. The Python
helpers below drive a spliced harness and pin a scenario's recorded traffic
against the plan it declared.
"""
import json
import shutil

from _noderun import run_node_program

STRICT_FETCH = r"""
// A missing contract name must be loud, not swallowed by the worker. This
// runs once, at splice time, before any fetch.
const gateContractFaults = [];
// A per-attempt answer sequence is a list; when it runs out the attempt is a
// recorded refusal, never a silent fallback to the default 200.
const EXHAUSTED = { exhausted: true };
const answerCursors = new Map();

function answerEntries(value) {
  return Array.isArray(value) ? value : [value];
}

function declaresChunkedAnswer(answers) {
  return Object.keys(answers).some((key) => answerEntries(answers[key])
    .some((entry) => entry && entry.stream !== undefined));
}

if (typeof plan === 'undefined') {
  gateContractFaults.push('plan');
} else if (!Array.isArray(plan.planned)) {
  gateContractFaults.push('plan.planned');
} else if (plan.answers !== undefined
  && (typeof plan.answers !== 'object' || Array.isArray(plan.answers))) {
  gateContractFaults.push('plan.answers');
} else {
  // hosts, relayHosts and statuses are origin/answer lists. A wrong-typed one
  // must be named, not silently accepted: a string-typed `hosts` makes
  // permittedOrigins().includes a substring match, which would admit a foreign
  // origin the plan never permitted.
  for (const field of ['hosts', 'relayHosts', 'statuses']) {
    if (plan[field] !== undefined && !Array.isArray(plan[field])) {
      gateContractFaults.push('plan.' + field);
    }
  }
}
// A {stream: N} answer is built by the harness, so the chunk factory is part
// of the contract exactly when a plan declares such an answer.
if (typeof plan !== 'undefined' && plan.answers !== undefined
    && typeof plan.answers === 'object' && !Array.isArray(plan.answers)
    && declaresChunkedAnswer(plan.answers)
    && typeof chunkedResponse !== 'function') {
  gateContractFaults.push('chunkedResponse');
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
// The full target URL, the parsed body and the Authorization header ride along
// so a harness reads the worker's request without wrapping its own fetch; the
// key alone would lose the origin for a request the config rotated. The entry
// is returned so bridgeFetch can stamp the answer status on it only after the
// answer really was built.
function accountRequest(request, url, init) {
  const seen = nonStreamFetches.filter((i) => i.request === request).length;
  const planned = (plan.planned || []).filter((i) => i === request).length;
  const refused = seen >= planned;
  let body = null;
  if (init && init.body) {
    try { body = JSON.parse(init.body); } catch (_) { body = init.body; }
  }
  const auth = (init && init.headers && init.headers.Authorization) || null;
  const entry = { request, url, refused, body, auth, status: null };
  nonStreamFetches.push(entry);
  if (refused) refusedFetches.push(request);
  return entry;
}

// What the scenario planned for this key, or null for the default answer. A
// list is a per-attempt sequence: each attempt consumes the next entry, and
// the marker EXHAUSTED is returned once the sequence runs out so the caller
// refuses it by status instead of answering 200.
function plannedAnswer(request) {
  const answers = plan.answers || {};
  if (!Object.prototype.hasOwnProperty.call(answers, request)) {
    return null;
  }
  const declared = answers[request];
  if (!Array.isArray(declared)) return declared;
  const used = answerCursors.get(request) || 0;
  answerCursors.set(request, used + 1);
  return used < declared.length ? declared[used] : EXHAUSTED;
}

function originOf(url) {
  const match = String(url).match(/^https?:\/\/[^/]+/);
  return match ? match[0] : '(no origin)';
}

// `hosts` are the permitted BRIDGE origins (the count is per route, keyed on
// the bare path, so a new bridge URL the config rotates to is the same
// route). `relayHosts` are permitted NON-bridge origins — a relay fetch is
// keyed on its full URL, so it can never collide with a bridge request to the
// same path. Both lists pass the origin gate; only their keying differs.
function bridgeOrigins() {
  return plan.hosts || [BRIDGE_URL];
}

function permittedOrigins() {
  return bridgeOrigins().concat(plan.relayHosts || []);
}

// Route, not host: a bridge request keys on the bare path; a permitted
// non-bridge (relay) origin keeps its full URL, so the two can never share
// one key.
function requestKey(url, init) {
  const path = bridgeOrigins().includes(originOf(url))
    ? url.replace(/^https?:\/\/[^/]+/, '') : url;
  return (init.method || 'GET') + ' ' + path;
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
    // Every stream answer, including a declared 'hang', is built by the
    // harness's streamResponse — the gate hardcodes none of them, so a
    // defective factory is observable on every path it serves.
    return streamResponse(next);
  }
  const request = requestKey(url, init);
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    resultPosts.push({ ...payload, did: payload._did || null });
  }
  const entry = accountRequest(request, url, init);
  if (entry.refused) {
    entry.status = 599;
    return response(599, { ok: false, error: 'more often than declared' });
  }
  const planned = plannedAnswer(request);
  if (planned === EXHAUSTED) {
    // The sequence is the contract; past its end nothing is declared, so the
    // attempt is refused by status and recorded rather than answered 200.
    entry.refused = true;
    refusedFetches.push(request);
    entry.status = 599;
    return response(599, {
      ok: false, error: 'declared answer sequence exhausted',
    });
  }
  if (planned && planned.throw) {
    // A scenario that models an unreachable bridge declares the throw; the
    // record carries it, so a worker that swallows it still shows the call.
    entry.status = 'throw';
    throw new TypeError(planned.throw);
  }
  if (planned && planned.stream !== undefined
      && typeof chunkedResponse === 'function') {
    entry.status = 200;
    return chunkedResponse(planned.stream);
  }
  const status = planned && planned.status !== undefined
    ? planned.status : 200;
  const body = planned && planned.body !== undefined
    ? planned.body : { ok: true };
  const answer = response(status, body);
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


def run_gate(node, program, arguments, *, cwd, plan, timeout=30):
    result = run_node_program(node, program, arguments, cwd=cwd,
                              payload=plan, timeout=timeout)
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
