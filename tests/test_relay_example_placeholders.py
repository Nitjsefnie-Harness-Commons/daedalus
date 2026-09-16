#!/usr/bin/env python3
"""The HLS relay example's placeholders survive the substitution they ask for.

The example's contract is textual: every `__NAME__` is replaced throughout
the file before `daedalus put` sends it. A sentinel spelled as the
placeholder itself is rewritten by that same replacement, so a script
handed a real sig would compare the sig with itself and mint anyway. These
run the substituted text the way the bridge does, wrapped as an async
function body, against a stubbed `window.GM`, and read which branch ran.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary_env import run_node_program  # noqa: E402
from _repo import ROOT  # noqa: E402

EXAMPLE = ROOT / 'examples' / 'hls-segment-relay.js'
SIG_HEADER = 'X-Daedalus-Segment-Sig'
PLAYLIST = '#EXTM3U\n' + ''.join(f'seg{i}.ts\n' for i in range(1, 7))
FIXED = {
    '__SERVER__': 'https://bridge.example.com',
    '__JOB__': 'relay_job-1',
    '__PLAYLIST__': 'https://media.example.com/live/index.m3u8',
}

_HARNESS = r"""
const vm = require('vm');

const [plan] = process.argv.slice(1);
const calls = [];
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
  fetch: async (url, init) => {
    calls.push(['fetch', url, (init && init.headers) || {}]);
    return { ok: false, status: 500 };
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
  process.stdout.write(JSON.stringify({ returned, calls }));
})();
"""


def _substitute(**placeholders):
    """The example with every named placeholder replaced throughout."""
    source = EXAMPLE.read_text(encoding='utf-8')
    for name, value in {**FIXED, **placeholders}.items():
        source = source.replace(name, value)
    return source


def _run(source):
    node = shutil.which('node')
    assert node, 'node is required to execute the relay example'
    result = run_node_program(
        node, _HARNESS, [], cwd=ROOT,
        payload={'source': source, 'playlist': PLAYLIST})
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


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
