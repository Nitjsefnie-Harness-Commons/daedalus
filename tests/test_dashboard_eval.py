#!/usr/bin/env python3
"""Where an untargeted eval command runs, pinned at every surface.

This suite holds the eval-section Node harness and the three
untargeted-command pins: the explicit active-tab action sends no tab
even with a tab selected (the selected-tab run beside it is the
control), both Eval status surfaces - the pre-flight line and the
post-run line - name the active tab, and the Settings caveat says the
command runs once, in whichever tab is active. The eval harness drives
dashboard/sections/eval.js through its own buttons in a small Node
DOM; the settings harness mounts dashboard/sections/settings.js for
its caveat paragraph.
"""
import json
import re
import sys
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
await bounded(settle(), 'run against a chosen tab', _dashnodeStepTimeoutMs);
const targeted = {
  puts: puts.length, tab: puts.length ? puts[puts.length - 1].tab : null,
};
untargeted.click();
await bounded(settle(), 'active-tab run with a tab selected',
  _dashnodeStepTimeoutMs);
const chosen = puts[puts.length - 1] || {};
const activeTabChosen = {
  puts: puts.length, tab: chosen.tab, token: chosen.token, code: chosen.code,
  id: typeof chosen.id === 'string' && chosen.id.length > 0,
  keys: Object.keys(chosen).sort(),
};
phase('dashboard call settled');
process.stdout.write(JSON.stringify(
  { refused, activeTab, targeted, activeTabChosen }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=7, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'eval.js',))


def test_run_refuses_an_empty_target_and_names_where_untargeted_code_runs(
        _tmp):
    """An empty selection is not a target; only the explicit button sends
    none, and it says the code runs in the browser's active tab."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['refused']['selected'] == '', seen
    assert seen['refused']['puts'] == 0, seen
    assert seen['refused']['status'].strip(), seen
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dasheval_')


if __name__ == '__main__':
    raise SystemExit(main())
