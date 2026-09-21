#!/usr/bin/env python3
"""Where an untargeted eval command runs, pinned at every surface.

This suite holds the eval-section Node harness and the pins around
untargeted commands: the explicit active-tab action sends no tab even
with a tab selected (the selected-tab run beside it is the control),
both Eval status surfaces - the pre-flight line and the post-run line
- name the active tab, the settled status prefers the tab a result
envelope names, the refusal sentence and the pre-flight label render
whole, the timeout renders clamped, and the Settings caveat says the
command runs once, in whichever tab is active. The eval harness drives
dashboard/sections/eval.js through its own buttons in a small Node
DOM; the settings harness mounts dashboard/sections/settings.js for
its caveat paragraph.
"""
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

_DOM = _dashnode.DOM


_EVAL_HARNESS = _dashnode.DashboardNodeHarness(_DOM + r"""
(async () => {
let tabs = [];
const puts = [];
const listeners = [];
let resultTabId = '';
const bus = { on: (fn) => listeners.push(fn) };
globalThis.fetch = async (target, init) => {
  const options = init || {};
  const where = String(target);
  if (options.method === 'PUT') {
    puts.push(JSON.parse(options.body));
    return jsonResponse({ ok: true, did: 'delivery' });
  }
  if (where.startsWith('/tabs')) return jsonResponse(tabs);
  if (where.includes('consume=1')) {
    return jsonResponse({ consumed: true, resultGeneration: 'gen' });
  }
  if (where.startsWith('/result')) {
    return jsonResponse({
      id: puts[puts.length - 1].id, deliveryId: 'delivery',
      resultGeneration: 'gen', result: 'ran', world: 'page',
      tabId: resultTabId,
    });
  }
  throw new Error('unexpected fetch ' + where);
};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container, bus);
await bounded(settle(), 'eval tab selector render', _dashnodeStepTimeoutMs);
const sel = container.find('[data-role=tab-select]');
const runBtn = container.find('[data-role=run]');
const timeoutEl = container.find('[data-role=timeout]');
const metaEl = container.find('[data-role=meta]');
const untargeted = container.all().find(
  (el) => el.tag === 'button' && el !== runBtn
    && el.dataset.role !== 'clear-history');
container.find('[data-role=code]').value = 'document.title';
runBtn.click();
await bounded(settle(), 'run with no target', _dashnodeStepTimeoutMs);
const refused = {
  selected: sel.value, puts: puts.length, status: metaEl.textContent,
};
untargeted.click();
// The pre-flight status stands only between the click and the await.
const statusBefore = metaEl.textContent;
await bounded(settle(), 'run in the active tab', _dashnodeStepTimeoutMs);
const activeTab = {
  puts: puts.length, tab: puts.length ? puts[puts.length - 1].tab : null,
  label: untargeted.textContent, title: untargeted.attrs.title || '',
  statusBefore, statusAfter: metaEl.textContent,
};
tabs = [{ tabId: '11', title: 'first' }];
for (const fn of listeners) fn({ type: 'tabs-synced' });
await bounded(settle(), 'tab sync refresh', _dashnodeStepTimeoutMs);
sel.value = '11';
runBtn.click();
const targetedLabel = metaEl.textContent;
await bounded(settle(), 'run against a chosen tab', _dashnodeStepTimeoutMs);
const targeted = {
  puts: puts.length, tab: puts.length ? puts[puts.length - 1].tab : null,
};
resultTabId = '11';
untargeted.click();
await bounded(settle(), 'active-tab run with a tab selected',
  _dashnodeStepTimeoutMs);
const chosenStatusAfter = metaEl.textContent;
const chosen = puts[puts.length - 1] || {};
const activeTabChosen = {
  puts: puts.length, tab: chosen.tab, token: chosen.token, code: chosen.code,
  id: typeof chosen.id === 'string' && chosen.id.length > 0,
  keys: Object.keys(chosen).sort(),
};
timeoutEl.value = '999999';
runBtn.click();
const highTimeout = metaEl.textContent;
await bounded(settle(), 'run with an over-range timeout',
  _dashnodeStepTimeoutMs);
timeoutEl.value = '500';
runBtn.click();
const lowTimeout = metaEl.textContent;
await bounded(settle(), 'run with an under-range timeout',
  _dashnodeStepTimeoutMs);
phase('dashboard call settled');
process.stdout.write(JSON.stringify(
  { refused, activeTab, targeted, activeTabChosen, targetedLabel,
    chosenStatusAfter, highTimeout, lowTimeout }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=9, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'eval.js',))


def test_run_refuses_an_empty_target_and_names_where_untargeted_code_runs(
        _tmp):
    """An empty selection is not a target; only the explicit button sends
    none, and it says the code runs in the browser's active tab. The
    refusal sentence is asserted whole, so rewriting it (the review
    mutation that replaced the message with 'nope') fails here and not
    merely at the non-empty check kept beside it."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['refused']['selected'] == '', seen
    assert seen['refused']['puts'] == 0, seen
    assert seen['refused']['status'].strip(), seen
    refusal = (
        'no target tab selected — choose one, or use "run in active tab"')
    assert seen['refused']['status'] == refusal, seen
    assert seen['activeTab']['puts'] == 1, seen
    assert seen['activeTab']['tab'] == '', seen
    for text in (seen['activeTab']['label'], seen['activeTab']['title']):
        assert 'active tab' in text.lower(), seen
        assert 'all tabs' not in text.lower(), seen
    assert seen['targeted'] == {'puts': 2, 'tab': '11'}, seen


def test_an_active_tab_run_sends_no_tab_even_with_a_tab_selected(_tmp):
    """Selecting tab 11 changes nothing for the explicit active-tab
    button: its command still names no tab and carries every field the
    section always sends, while plain RUN driven with the same selection
    sends '11' — the control that holds the selected half of the
    targeting conditional. The repro mutation that drops the
    conditional (`const tabId = sel.value`) turns the empty-target
    assertion below into tab '11' and fails here."""
    if sys.platform.startswith('win'):
        _util.skip(
            'issue 879: Node children stall on loaded windows-latest'
            ' runners; the case is starvation-sensitive on that platform')
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['targeted'] == {'puts': 2, 'tab': '11'}, seen
    assert seen['activeTabChosen'] == {
        'puts': 3, 'tab': '', 'token': 'dashboard-token',
        'code': 'document.title', 'id': True,
        'keys': ['code', 'id', 'tab', 'token']}, seen


def test_eval_status_names_the_active_tab_on_both_untargeted_surfaces(_tmp):
    """The two status surfaces of an untargeted run both name the active
    tab: the pre-flight line the section paints the moment the run
    starts, and the post-run line the settled envelope repaints. The two
    fallbacks are separate strings in the section, so each is asserted,
    and either regressing to the old 'broadcast' reading fails its own
    assertion."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['activeTab']['statusBefore'] == (
        'tab=active tab  timeout=10000ms'), seen
    assert re.fullmatch(r'tab=active tab  channel=page  \d+ms',
                        seen['activeTab']['statusAfter']), seen


def test_the_settled_status_names_where_the_broadcast_ran(_tmp):
    """An untargeted command's result carries the tab it really ran in:
    the stub answers the second untargeted run with tabId '11', and the
    post-run line must read tab=11. The envelope preference is the only
    spelling that gets there, because the submitted target was empty;
    reducing the expression to the submitted tabId (`String(tabId ||
    'active tab')`, the review mutation) reads 'active tab' and fails
    here. The first untargeted run keeps an empty envelope, so the
    'active tab' fallback stays held by the status test beside this."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert re.fullmatch(r'tab=11  channel=page  \d+ms',
                        seen['chosenStatusAfter']), seen


def test_the_pre_flight_label_names_the_chosen_tab(_tmp):
    """With 11 selected the pre-flight line reads tab=11 before any
    result exists — the label tracks the selection, so hard-coding the
    active-tab spelling into it (the review mutation that stopped the
    line following the target) fails here; the untargeted run's
    identical-looking label remains the status test's to hold."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['targetedLabel'] == 'tab=11  timeout=10000ms', seen


def test_the_timeout_renders_clamped_to_the_section_bounds(_tmp):
    """The pre-flight line cannot carry a raw field value: 999999 clamps
    to 60000 and 500 to 1000, so dropping the clamp (the review mutation
    that rendered the field bare) reads the raw numbers and fails here.
    Only the timeout suffix is asserted, leaving the tab prefix to the
    label pin beside it."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['highTimeout'].endswith('  timeout=60000ms'), seen
    assert seen['lowTimeout'].endswith('  timeout=1000ms'), seen


_SETTINGS_HARNESS = _dashnode.DashboardNodeHarness(_DOM + r"""
(async () => {
globalThis.window = { addEventListener() {} };
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
const caveat = container.all().find(
  (el) => el.tag === 'p' && el.textContent.startsWith('Caveat:'));
if (!caveat) throw new Error('settings caveat paragraph not found');
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ caveat: caveat.textContent }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=1, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'settings.js',))


def test_settings_caveat_says_where_an_untargeted_command_runs(_tmp):
    """The settings caveat must keep saying an untargeted command runs
    once, in whichever tab is active — the sentence the issue's
    mutation replaced with an all-tabs claim, which would tell an
    operator that one command fans out over every tab. The sentence is
    asserted whole, and both all-tabs spellings are refused beside it."""
    result = _dashnode.run_dashboard_node(_SETTINGS_HARNESS)
    seen = json.loads(result.stdout)
    assert ('A command that names no tab (exec -b, or "run in active tab"'
            ' above) runs once, in whichever tab is active — this one, if'
            ' the dashboard is in front.') in seen['caveat'], seen
    lowered = seen['caveat'].lower()
    assert 'all tabs' not in lowered, seen
    assert 'every tab' not in lowered, seen


_SETTINGS_STREAM_HARNESS = _dashnode.DashboardNodeHarness(_dashnode.DOM + r"""
(async () => {
const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, value),
};
globalThis.window = { addEventListener() {} };
document.querySelectorAll = () => [];
const timers = new Map();
let timerId = 0;
globalThis.setTimeout = (callback) => {
  timers.set(++timerId, callback);
  return timerId;
};
globalThis.clearTimeout = (id) => timers.delete(id);
const streams = [];
const probes = [];
let available = false;
globalThis.fetch = (target, init) => {
  if (target.endsWith('/tabs')) {
    return new Promise((resolve, reject) => probes.push({ resolve, reject }));
  }
  if (!target.endsWith('/stream?tab=dashboard')) {
    throw new Error('unexpected request ' + target);
  }
  streams.push({ target, auth: init.headers.Authorization,
    signal: init.signal });
  if (!available) return Promise.reject(new Error('bridge unavailable'));
  return Promise.resolve({ ok: true, body: { getReader: () => ({
    read: () => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(
        Object.assign(new Error('aborted'), { name: 'AbortError' })));
    }),
  }) } });
};
phase('dashboard module import started');
const [settings, sse] = await bounded(Promise.all([
  import(pathToFileURL(process.argv[1]).href),
  import(pathToFileURL(process.argv[2]).href),
]), 'dashboard module import', _dashnodeStepTimeoutMs);
phase('dashboard module imported');
phase('dashboard call started');
const statuses = [];
sse.subscribe((event) => statuses.push(event.status));
sse.start();
const initial = statuses.slice();
const container = new El('div');
settings.mount(container);
container.find('[data-role=token]').value = 'first-token';
container.find('[data-role=server]').value = 'https://example.com/first';
container.find('[data-role=save]').click();
probes[0].reject(new Error('bridge unavailable'));
await bounded(settle(), 'failed first save', _dashnodeStepTimeoutMs);
const failed = { statuses: statuses.slice(), retries: timers.size,
  status: container.find('[data-role=status]').textContent,
  token: localStorage.getItem('daedalus-token') };
available = true;
for (const [id, callback] of [...timers]) {
  timers.delete(id);
  callback();
}
await bounded(settle(), 'stream recovery', _dashnodeStepTimeoutMs);
const recovered = statuses.at(-1);
const previous = streams.at(-1);
container.find('[data-role=token]').value = 'replacement-token';
container.find('[data-role=server]').value = 'https://example.com/second';
container.find('[data-role=save]').click();
await bounded(settle(), 'save with pending probe', _dashnodeStepTimeoutMs);
const pending = { status: statuses.at(-1),
  auth: streams.at(-1)?.auth, previousAborted: previous?.signal.aborted };
const beforeProbe = { count: streams.length, signal: streams.at(-1)?.signal };
if (process.argv[3] === 'success') probes[1].resolve(jsonResponse([]));
else probes[1].reject(new Error('probe unavailable'));
await bounded(settle(), 'replacement probe settled', _dashnodeStepTimeoutMs);
const afterProbe = { count: streams.length,
  sameSignal: streams.at(-1)?.signal === beforeProbe.signal,
  aborted: beforeProbe.signal?.aborted,
  status: container.find('[data-role=status]').textContent };
const final = statuses.at(-1);
sse.stop();
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ initial, failed, recovered,
  pending, final, beforeProbeCount: beforeProbe.count, afterProbe,
  requests: streams.map(({ target, auth }) => ({ target, auth })) }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=5, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'settings.js',
    ROOT / 'dashboard' / 'sse.js'))


def test_saving_token_restarts_stream_independently_of_probe(_tmp):
    for outcome in ('failure', 'success'):
        result = _dashnode.run_dashboard_node(
            replace(_SETTINGS_STREAM_HARNESS, arguments=(
                *_SETTINGS_STREAM_HARNESS.arguments, outcome)))
        seen = json.loads(result.stdout)
        assert seen['initial'] == ['no-token'], seen
        assert seen['failed']['token'] == 'first-token', seen
        assert 'bridge unavailable' in seen['failed']['status'], seen
        assert seen['failed']['statuses'][-1] == 'reconnecting', seen
        assert seen['failed']['retries'] == 1, seen
        assert seen['recovered'] == 'connected', seen
        assert seen['pending'] == {
            'status': 'connected', 'auth': 'Bearer replacement-token',
            'previousAborted': True,
        }, seen
        assert seen['final'] == 'connected', seen
        assert seen['afterProbe'] == {
            'count': seen['beforeProbeCount'], 'sameSignal': True,
            'aborted': False,
            'status': ('connected' if outcome == 'success' else
                       'server check failed: probe unavailable. '
                       'Cross-origin bridge URLs need a CORS-enabled proxy.'),
        }, seen
        assert seen['requests'] == [
            {'target': 'https://example.com/first/stream?tab=dashboard',
             'auth': 'Bearer first-token'},
            {'target': 'https://example.com/first/stream?tab=dashboard',
             'auth': 'Bearer first-token'},
            {'target': 'https://example.com/second/stream?tab=dashboard',
             'auth': 'Bearer replacement-token'},
        ], seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dasheval_')


if __name__ == '__main__':
    raise SystemExit(main())
