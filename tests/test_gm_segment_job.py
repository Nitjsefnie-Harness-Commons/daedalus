#!/usr/bin/env python3
"""GM.segmentJob: the page's route to a job-scoped segment capability.

The page asks, the content script relays, and the service worker decides.
These run the shipped content and page scripts in a Node VM against a fake
runtime that records what the relay sent and answers as the worker would,
so what the page's promise settles to is pinned for every answer the worker
can give — and for the two ways Chrome reports that it gave none.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary_env import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402

NO_RESPONSE = 'no response from background (service worker dead?)'
NO_SIG = 'background returned no sig'

_SEGMENT_JOB_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

// run_node_program pushes the plan as an object literal, not text:
// `calls` are the jobs handed to GM.segmentJob, `answers[i]` is what the
// fake worker gives call i — `{resp}` (null for no response at all) or
// `{lastError}` — and `order` is the sequence the answers arrive in.
const [contentPath, pagePath, plan] = process.argv.slice(1);
const listeners = { message: [] };
const queued = [];
const relayed = [];
const sent = [];
const answerers = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    if (message.direction === 'daedalus-bg-to-page'
        && message.handler === 'segmentJob') {
      relayed.push(message);
    }
    queued.push(message);
  },
};

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    getManifest: () => ({ version: '0.0.0' }),
    connect: () => ({
      disconnect() {}, postMessage() {},
      onDisconnect: { addListener() {} },
    }),
    sendMessage(payload, callback) {
      sent.push(payload);
      if (typeof callback !== 'function') return;
      if (payload.type === 'segmentJob') answerers.push(callback);
      else callback({});
    },
  },
  storage: { local: {
    get(keys, cb) { cb({}); }, set(v, cb) { cb(); }, remove(k, cb) { cb(); },
  } },
};

const context = {
  window: windowObject,
  chrome,
  document: { documentElement: {}, addEventListener() {} },
  location: { hostname: 'segment-job-test.invalid', href: 'about:blank' },
  setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1,
  clearInterval() {},
  performance,
  console: { log() {}, error() {} },
};
context.globalThis = context;
for (const path of [contentPath, pagePath]) {
  vm.runInNewContext(fs.readFileSync(path, 'utf8'), context,
    { filename: path });
}

function deliverQueued() {
  let guard = 0;
  while (queued.length && guard++ < 100) {
    const data = queued.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

function answer(index) {
  const callback = answerers[index];
  if (!callback) return;
  const given = plan.answers[index];
  if (given.lastError) {
    // Chrome's shape for an undelivered message: lastError set for the
    // duration of the callback, and no response at all.
    chrome.runtime.lastError = { message: given.lastError };
    try { callback(undefined); } finally { chrome.runtime.lastError = null; }
  } else {
    callback(given.resp === null ? undefined : given.resp);
  }
}

(async () => {
  const settled = plan.calls.map(() => ({ state: 'pending', value: null }));
  plan.calls.forEach((job, index) => {
    let returned;
    try {
      returned = windowObject.GM.segmentJob(job);
    } catch (error) {
      settled[index] = { state: 'threw', value: String(error.message) };
      return;
    }
    if (!returned || typeof returned.then !== 'function') {
      settled[index] = { state: 'not-a-promise', value: String(returned) };
      return;
    }
    returned.then(
      (value) => { settled[index] = { state: 'resolved', value }; },
      (error) => {
        settled[index] = {
          state: 'rejected', value: String(error && error.message) };
      });
  });
  deliverQueued();
  for (const index of plan.order || plan.calls.map((_, i) => i)) {
    answer(index);
    deliverQueued();
  }
  for (let turn = 0; turn < 10; turn++) {
    await Promise.resolve();
    deliverQueued();
  }
  process.stdout.write(JSON.stringify({
    sent: sent.filter((m) => m.type === 'segmentJob'),
    relayed: relayed.map((m) => ({
      reqId: m.reqId,
      sig: m.sig === undefined ? null : m.sig,
      error: m.error === undefined ? null : m.error,
    })),
    settled,
  }), () => process.exit(0));
})();
"""


def _drive(calls, answers, order=None):
    """Run GM.segmentJob for each job in `calls` under a fake worker."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension segment relay'
    plan = {'calls': calls, 'answers': answers}
    if order is not None:
        plan['order'] = order
    result = run_node_program(
        node, _SEGMENT_JOB_HARNESS,
        [str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js')],
        cwd=ROOT, payload=plan)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_the_relay_sends_the_job_and_the_page_receives_the_sig(tmp):
    """One message per mint, carrying exactly the job, answered with the sig.

    The worker answers a `segmentJob` message with `{sig}`; the content
    script relays it and the page's promise resolves to that string and
    nothing else. The shape is pinned exactly: a relay that forwarded more
    than the job would be a page choosing what the worker authorizes on.
    """
    del tmp
    outcome = _drive(['job_1'], [{'resp': {'sig': 'S'}}])
    assert outcome['sent'] == [{'type': 'segmentJob', 'job': 'job_1'}], (
        outcome)
    assert outcome['settled'] == [{'state': 'resolved', 'value': 'S'}], (
        outcome)
    assert outcome['relayed'] == [
        {'reqId': 1, 'sig': 'S', 'error': None}], outcome


def test_a_worker_refusal_rejects_with_its_message(tmp):
    """The worker's refusal reaches the page verbatim, as a rejection."""
    del tmp
    outcome = _drive(['job_1'], [{'resp': {'error': 'origin not allowed'}}])
    assert outcome['settled'] == [
        {'state': 'rejected', 'value': 'origin not allowed'}], outcome
    assert outcome['relayed'] == [
        {'reqId': 1, 'sig': None, 'error': 'origin not allowed'}], outcome


def test_no_response_at_all_rejects_as_a_dead_worker(tmp):
    """A callback with nothing in it is a failure, not a mint of undefined.

    Chrome invokes the callback with no response when the worker never
    answered; a relay that resolved on `resp.sig` would hand the page
    `undefined` as its capability.
    """
    del tmp
    outcome = _drive(['job_1'], [{'resp': None}])
    assert outcome['settled'] == [
        {'state': 'rejected', 'value': NO_RESPONSE}], outcome


def test_an_undelivered_message_rejects_with_last_error(tmp):
    """lastError is Chrome's only report of a message that never arrived."""
    del tmp
    outcome = _drive(
        ['job_1'], [{'lastError': 'Could not establish connection.'}])
    assert outcome['settled'] == [
        {'state': 'rejected', 'value': 'Could not establish connection.'}], (
        outcome)


def test_an_answer_without_a_sig_rejects(tmp):
    """`{}` from the worker is neither a sig nor an error; it is refused."""
    del tmp
    outcome = _drive(['job_1'], [{'resp': {}}])
    assert outcome['settled'] == [
        {'state': 'rejected', 'value': NO_SIG}], outcome
    # A sig that is present but not a nonempty string is the same case.
    outcome = _drive(['job_1'], [{'resp': {'sig': ''}}])
    assert outcome['settled'] == [
        {'state': 'rejected', 'value': NO_SIG}], outcome
    outcome = _drive(['job_1'], [{'resp': {'sig': 7}}])
    assert outcome['settled'] == [
        {'state': 'rejected', 'value': NO_SIG}], outcome


def test_concurrent_mints_settle_by_their_own_request_id(tmp):
    """Two in-flight mints answered out of order each get their own sig.

    The pending table is keyed by reqId, so the second call's answer
    arriving first must settle the second promise, not the first.
    """
    del tmp
    outcome = _drive(
        ['job_a', 'job_b'],
        [{'resp': {'sig': 'SIG-A'}}, {'resp': {'sig': 'SIG-B'}}],
        order=[1, 0])
    assert outcome['sent'] == [
        {'type': 'segmentJob', 'job': 'job_a'},
        {'type': 'segmentJob', 'job': 'job_b'}], outcome
    assert outcome['settled'] == [
        {'state': 'resolved', 'value': 'SIG-A'},
        {'state': 'resolved', 'value': 'SIG-B'}], outcome
    assert [m['reqId'] for m in outcome['relayed']] == [2, 1], outcome


def test_a_non_string_job_is_relayed_unvalidated(tmp):
    """The worker is the one authority on what a job is.

    Neither the page nor the content script checks the job: a non-string
    goes across as-is and the worker's `Missing job` refusal is what the
    page sees. Two validators would be two places to keep in agreement.
    """
    del tmp
    outcome = _drive([42], [{'resp': {'error': 'Missing job'}}])
    assert outcome['sent'] == [{'type': 'segmentJob', 'job': 42}], outcome
    assert outcome['settled'] == [
        {'state': 'rejected', 'value': 'Missing job'}], outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmsegjob_')


if __name__ == '__main__':
    raise SystemExit(main())
