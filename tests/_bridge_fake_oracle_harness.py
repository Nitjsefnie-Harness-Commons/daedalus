"""The shared gate's own probe harness, driven without a worker.

The probe drives the gate directly: every request is one the scenario chose,
and the answer, the record, the refusal and the contract fault come back for
the oracle rows in `test_bridge_fake_oracle.py` to pin. A real local server
stands in for the upstream a declared forward names, so a forwarded request
is answered by that server's own status rather than anything the gate could
synthesise. The JS text is spliced into a Node program, so it shares that
program's scope.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stream_fake import STRICT_FETCH  # noqa: E402

_ORACLE_HARNESS = r"""
const planArg = process.argv[1];
const plan = typeof planArg === 'string' ? JSON.parse(planArg) : planArg;
const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
const chunkCalls = [];
// A real local server stands in for the upstream a forward names: the gate
// must reach it through `forwardRequest` and return ITS answer, so the probe
// records the real server's own status — a distinctive 4xx the gate would
// never synthesise — and how many requests actually arrived.
const http = require('http');
const forwardedFetches = [];
const upstream = { hits: 0, status: 418, body: 'upstream said no' };
const upstreamServer = http.createServer((req, res) => {
  upstream.hits += 1;
  req.resume();
  req.on('end', () => {
    res.writeHead(plan.upstreamStatus === undefined
      ? upstream.status : plan.upstreamStatus,
    { 'Content-Type': 'text/plain' });
    res.end(upstream.body);
  });
});

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function streamResponse(answer) {
  // The gate routes every stream answer through this factory, so a harness
  // that serves 'hang' models the connected-but-idle body here.
  if (answer === 'hang') {
    return {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: () => new Promise(() => {}),
          cancel: () => Promise.resolve(),
        }),
      },
    };
  }
  return response(answer, { error: 'disabled' });
}

// The gate builds a declared {stream: N} answer through the harness's chunk
// factory; `noChunkFactory` withholds it so the splice-time contract check can
// be exercised.
let chunkedResponse = plan.noChunkFactory
  ? undefined
  : (count) => {
    chunkCalls.push(count);
    return response(200, { chunked: count });
  };
// A plan that declares a forward must bring the hook that performs it, so the
// contract check below requires both together. The hook forwards through the
// real `fetch` and stamps the real server's status on the record.
let forwardRequest;
if (!plan.noForwardHook) {
  forwardRequest = async (url, init, entry) => {
    if (plan.upstreamThrows) throw new TypeError(plan.upstreamThrows);
    // The plan's forward names the origin the real answer lives on, so the
    // hook retargets the request's origin and keeps its path.
    const target = url.replace(/^https?:\/\/[^/]+/, plan.forwards[
      Object.keys(plan.forwards)[0]]);
    const result = await fetch(target, init);
    entry.status = result.status;
    forwardedFetches.push(target);
    return result;
  };
}
""" + STRICT_FETCH + r"""

async function run() {
  const statuses = [];
  const answered = [];
  const streamBodies = [];
  for (const step of plan.probe) {
    const init = { method: step.method };
    if (step.body !== undefined) init.body = JSON.stringify(step.body);
    if (step.headers !== undefined) init.headers = step.headers;
    // A step that expects the gate to throw says so, so the probe runs to
    // completion and the throw is evidence rather than a dead child.
    if (step.expectThrow) {
      let thrown = null;
      try { await bridgeFetch(step.url, init); }
      catch (error) { thrown = error.message; }
      statuses.push(thrown === null ? 'no throw' : 'throw: ' + thrown);
      answered.push(null);
      continue;
    }
    const answer = await bridgeFetch(step.url, init);
    statuses.push(answer.status);
    // A stream answer's body shape is the hang the watchdog exercises: a
    // connected 200 whose reader never settles. Recorded without awaiting a
    // promise that (correctly) never resolves.
    if (step.url.includes('/stream?')) {
      streamBodies.push(
        answer && answer.body
          ? typeof answer.body.getReader === 'function' : null);
    }
    answered.push(answer && answer.json
      ? await answer.json().catch(() => null) : null);
  }
  return {
    statuses,
    answered,
    streamBodies,
    nonStream: nonStreamFetches.map((i) => i.request),
    refused: refusedFetches,
    badOrigins,
    records: nonStreamFetches,
    streamAnswered: streamFetches.map((f) => f.answered),
    contractFaults: gateContractFaults,
    bodies: nonStreamFetches.map(
      (i) => ({ request: i.request, body: i.body })),
    auths: nonStreamFetches.map((i) => i.auth),
    resultPosts,
    chunkCalls,
    forwarded: forwardedFetches,
    upstreamHits: upstream.hits,
  };
}

async function main() {
  if (!plan.noForwardHook && plan.forwards !== undefined) {
    await new Promise((resolve) => {
      upstreamServer.listen(0, '127.0.0.1', resolve);
    });
    // A forward names the origin its target lives on; 'UPSTREAM' is this
    // real server's origin, filled in once it is listening.
    const origin = `http://127.0.0.1:${upstreamServer.address().port}`;
    for (const key of Object.keys(plan.forwards)) {
      if (plan.forwards[key] === 'UPSTREAM') plan.forwards[key] = origin;
    }
    if (plan.hosts !== undefined) {
      plan.hosts = plan.hosts.map(
        (host) => (host === 'UPSTREAM' ? origin : host));
    }
  }
  const result = await run();
  if (upstreamServer.listening) {
    await new Promise((resolve) => upstreamServer.close(resolve));
  }
  process.stdout.write(JSON.stringify(result));
}

main().catch((error) => {
  if (upstreamServer.listening) upstreamServer.close();
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""
