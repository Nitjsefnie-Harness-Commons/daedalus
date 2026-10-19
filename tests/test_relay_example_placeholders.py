#!/usr/bin/env python3
"""Drive substituted HLS examples through the shipped MAIN-world evaluator.

The GM stub answers the playlist and parks on a segment. The fetch gate
records the refused status read without talking to a browser or bridge.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)
from _worker_sources import (  # noqa: E402
    RESPONSE_STUB, STREAM_RESPONSE)

EXAMPLE = ROOT / 'examples' / 'hls-segment-relay.js'
SIG_HEADER = 'X-Daedalus-Segment-Sig'
PLAYLIST = '#EXTM3U\n' + ''.join(f'seg{i}.ts\n' for i in range(1, 7))
FIXED = {
    '__SERVER__': 'https://bridge.example.com',
    '__JOB__': 'relay_job-1',
    '__PLAYLIST__': 'https://media.example.com/live/index.m3u8',
}
STATUS = 'GET /segment-status?job=relay_job-1'

_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');

const planArg = process.argv[process.argv.length - 1];
const plan = typeof planArg === 'string'
  ? JSON.parse(planArg) : planArg;
const calls = [];

const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
""" + RESPONSE_STUB + r"""
""" + STREAM_RESPONSE + r"""
""" + STRICT_FETCH + r"""

const context = {
  performance,
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
  fetch: async (url, init) => {
    calls.push(['fetch', url, (init && init.headers) || {}]);
    return bridgeFetch(url, init);
  },
  setTimeout: () => 1,
  console: { warn() {} },
  encodeURIComponent,
  URL,
};
const evaluator = require('path').resolve('extension/worker/evaluate.js');
vm.runInNewContext(fs.readFileSync(evaluator, 'utf8'), context,
  { filename: evaluator });
(async () => {
  const result = await context._executeMainWorldEval(plan.source);
  if (result.e) throw new Error(result.e);
  const returned = result.r === undefined ? '<undefined>' : result.r;
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
    default = _run(_substitute())['returned']
    assert default == 'relay started: job=relay_job-1 concurrency=3', default
    selected = _run(_substitute(__CONC__='4'))['returned']
    assert selected == 'relay started: job=relay_job-1 concurrency=4', selected


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
