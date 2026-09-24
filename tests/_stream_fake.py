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

The gate answers a request only while the plan declares it, refuses
everything else with status 599, and records every request it sees. A
request refused for its origin spends no route allowance, so the next
legitimate request on that route still answers 200. The Python helpers
below drive a spliced harness and pin a scenario's recorded requests
against the plan it declared.
"""
import json
import shutil

from _boundary_env import run_node_program

STRICT_FETCH = r"""
// A request is planned as often as the recording shows; beyond that it is
// refused and recorded. No route is special-cased, so a new one is caught.
// The parsed body rides along so a harness reads the worker's request
// without wrapping its own fetch.
function accountRequest(request, init) {
  const seen = nonStreamFetches.filter((i) => i.request === request).length;
  const planned = (plan.planned || []).filter((i) => i === request).length;
  const refused = seen >= planned;
  let body = null;
  if (init && init.body) {
    try { body = JSON.parse(init.body); } catch (_) { body = init.body; }
  }
  nonStreamFetches.push({ request, refused, body });
  if (refused) refusedFetches.push(request);
  return refused;
}

// The count keys on the route alone, so a scenario that legitimately posts
// one route to two bridges can declare it once each. The ORIGIN is gated
// separately and first, and a refusal there is NOT recorded in
// `nonStreamFetches`: it must spend no declared route's allowance, or the
// legitimate request that follows it would be refused on the foreign
// one's account. It is reported in `badOrigins` instead.
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
  const origin = originOf(url);
  if (!permittedOrigins().includes(origin)) {
    badOrigins.push(origin);
    return response(599, { ok: false, error: 'origin not permitted' });
  }
  return accountRequest(request, init)
    ? response(599, { ok: false, error: 'more often than declared' })
    : response(200, { ok: true });
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


def assert_gate_clean(non_stream, refused, bad_origins, planned):
    """Pin a scenario's recorded requests against the plan it declared.

    The comparison is over the whole recorded list, so an undeclared or
    extra request is a mismatch by count and by route — never a membership
    question. A refusal is loud here even when the worker swallowed it.
    """
    assert bad_origins == [], (
        'bridge origin(s) the scenario did not permit:', bad_origins)
    assert refused == [], (
        'bridge request(s) seen more often than declared:', refused)
    assert sorted(non_stream) == sorted(planned), (
        'non-stream requests differ from the declaration:',
        non_stream, planned)
