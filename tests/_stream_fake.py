"""The strict bridge-fetch gates for the stream-backoff harness.

Not a suite itself — run_tests.py only loads `test_*.py`. The text is
concatenated into tests/test_stream_backoff.py's Node harness, so it
shares that scope and assumes `plan`, `BRIDGE_URL`, `response`,
`streamResponse`, and the `nonStreamFetches` / `refusedFetches` /
`badOrigins` / `resultPosts` arrays are already defined above it.
"""

STRICT_FETCH = r"""
// A request is planned as often as the recording shows; beyond that it is
// refused and recorded. No route is special-cased, so a new one is caught.
function accountRequest(request) {
  const seen = nonStreamFetches.filter((i) => i.request === request).length;
  const planned = (plan.planned || []).filter((i) => i === request).length;
  const refused = seen >= planned;
  nonStreamFetches.push({ request, refused });
  if (refused) refusedFetches.push(request);
  return refused;
}

// The count keys on the route alone, so a scenario that legitimately posts
// one route to two bridges can declare it once each. The ORIGIN is gated
// separately and first: a target the plan does not name — a foreign bridge,
// or a relative URL that names no bridge — is refused before it can spend a
// declared route's allowance.
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
    resultPosts.push({ did: payload._did || null });
  }
  const origin = originOf(url);
  if (!permittedOrigins().includes(origin)) {
    nonStreamFetches.push({ request, refused: true });
    badOrigins.push(origin);
    return response(599, { ok: false, error: 'origin not permitted' });
  }
  return accountRequest(request)
    ? response(599, { ok: false, error: 'more often than declared' })
    : response(200, { ok: true });
}
"""
