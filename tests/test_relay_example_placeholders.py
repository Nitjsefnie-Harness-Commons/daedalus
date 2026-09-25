#!/usr/bin/env python3
"""The HLS relay example's placeholders survive the substitution they ask for.

The example's contract is textual: every `__NAME__` is replaced throughout
the file before `daedalus put` sends it. A sentinel spelled as the
placeholder itself is rewritten by that same replacement, so a script
handed a real sig would compare the sig with itself and mint anyway. These
run the substituted text the way the bridge does, wrapped as an async
function body, against a stubbed `window.GM`, and read which branch ran.

The example's own `fetch` goes through the shared gate on the bridge origin
the substituted text carries: each scenario declares the requests the
substituted example makes, the gate answers only those and refuses and
records everything else, and `assert_gate_clean` reads that record. The
`GM.xmlhttpRequest` stub answers the playlist read and leaves the segment
read unresolved, as it always has, so the run reads the status, learns
nothing from it, and parks on the first segment it tries to relay — the
recording the plan below is taken from.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)

EXAMPLE = ROOT / 'examples' / 'hls-segment-relay.js'
SIG_HEADER = 'X-Daedalus-Segment-Sig'
PLAYLIST = '#EXTM3U\n' + ''.join(f'seg{i}.ts\n' for i in range(1, 7))
FIXED = {
    '__SERVER__': 'https://bridge.example.com',
    '__JOB__': 'relay_job-1',
    '__PLAYLIST__': 'https://media.example.com/live/index.m3u8',
}
# The recording, from a run of the substituted example on the pre-change tree
# (a temporary recorder, no tracked file changed): with the segment read
# unresolved, the substituted text makes exactly one fetch — the job's
# status read, which the scenario answers 500 so the run has nothing to skip
# — and then relays its first segment through the unresolved read. The
# status is declared as refused-not-ok on purpose: "the relay is not wired"
# is a claim about the status answer, not a blanket fallback for every
# route, so a plan that gave the example a 200 status would be a different
# test. The status read goes to the bridge origin the substituted text
# carries, so the gate keys it on the bare route.
STATUS = 'GET /segment-status?job=relay_job-1'

_HARNESS = r"""
const vm = require('vm');

const planArg = process.argv[process.argv.length - 1];
const plan = typeof planArg === 'string'
  ? JSON.parse(planArg) : planArg;
const calls = [];

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared and records every request the substituted example makes;
// the example's own SERVER constant is the bridge origin the gate permits,
// so its status and segment reads key on the same route the real bridge
// would see.
const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
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
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

const context = {
  window: { GM: {
    segmentJob: async (job) => {
      calls.push(['segmentJob', job]);
      return 'MINTED';
    },
    xmlhttpRequest(opts) {
      calls.push(['xhr', opts.responseType]);
      if (opts.responseType === 'text') {
        opts.onload({ status: 200, responseText: plan.playlist });
      }
    },
  } },
  document: {
    getElementById: () => null,
    createElement: () => ({ style: {} }),
    body: { appendChild() {} },
  },
  // A thin wrapper that DELEGATES to the gate: the answer, the accounting,
  // the refusal and the record are the gate's. The wrapper only keeps the
  // suite's own `['fetch', url, headers]` projection, which the sig tests
  // read to see which capability header the example sent.
  fetch: async (url, init) => {
    calls.push(['fetch', url, (init && init.headers) || {}]);
    return bridgeFetch(url, init);
  },
  setTimeout: () => 1,
  console: { warn() {} },
  encodeURIComponent,
  URL,
};
// vm-load-exempt: runs placeholder-substituted example text, not a file
const started = vm.runInNewContext(
  '(async () => {' + plan.source + '\n})()', context);
(async () => {
  const returned = await started;
  for (let turn = 0; turn < 20; turn++) await Promise.resolve();
  process.stdout.write(JSON.stringify({
    returned, calls,
    gate: {
      records: nonStreamFetches,
      refused: refusedFetches,
      badOrigins,
      streamAnswered: streamFetches.map((f) => f.answered),
      contractFaults: gateContractFaults,
    },
  }));
})();
"""


def _substitute(**placeholders):
    """The example with every named placeholder replaced throughout."""
    source = EXAMPLE.read_text(encoding='utf-8')
    for name, value in {**FIXED, **placeholders}.items():
        source = source.replace(name, value)
    return source


def _run(source):
    outcome = run_gate(require_node(), _HARNESS, [], cwd=ROOT, plan={
        'source': source, 'playlist': PLAYLIST,
        'planned': [STATUS],
        'hosts': [FIXED['__SERVER__']],
        'answers': {STATUS: {'status': 500}},
    })
    gate = outcome.pop('gate')
    assert_gate_clean(
        contract_faults=gate['contractFaults'],
        records=gate['records'], refused=gate['refused'],
        bad_origins=gate['badOrigins'],
        stream_answered=gate['streamAnswered'],
        planned=[STATUS], planned_stream=[])
    return outcome


def _sig_in_use(outcome):
    """The sig the status read carried, and whether the mint ran first."""
    minted = [c for c in outcome['calls'] if c[0] == 'segmentJob']
    status = [c for c in outcome['calls']
              if c[0] == 'fetch' and '/segment-status?' in c[1]]
    assert len(status) == 1, outcome
    return status[0][2].get(SIG_HEADER), minted


def test_an_unsubstituted_sig_is_minted_through_the_extension(tmp):
    del tmp
    sig, minted = _sig_in_use(_run(_substitute()))
    assert minted == [['segmentJob', FIXED['__JOB__']]], minted
    assert sig == 'MINTED', sig


def test_a_substituted_sig_is_used_as_given_and_never_minted(tmp):
    """Replace-all rewrites the sentinel too; the branch must not notice.

    One sig begins with `__`, the prefix a placeholder shares: base64url
    includes `_`, so one mint in 4096 does.
    """
    del tmp
    for given in ('abc123DEF-ghi_JKL',
                  '__tYoqhiDO07I9pRMyVhi6VGu1IIzZPNSBElo3-3utc'):
        sig, minted = _sig_in_use(_run(_substitute(__SIG__=given)))
        assert minted == [], (given, minted)
        assert sig == given, (given, sig)


def test_concurrency_defaults_when_unsubstituted_and_reads_a_value(tmp):
    del tmp
    assert _run(_substitute())['returned'].endswith('concurrency=3')
    assert _run(_substitute(__CONC__='4'))['returned'].endswith(
        'concurrency=4')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
