#!/usr/bin/env python3
"""How the tabs section treats the bus's tab lifecycle events.

Each scenario mounts dashboard/sections/tabs.js into the shared DOM double,
opens the inline editor and arms a close confirmation on one row, then fires
the event the bus delivers and judges what survived: the row the event did
not concern keeps its element, the row it did concern is patched or replaced
in place, a burst coalesces into one listing fetch, a dead tab's row leaves
with its armed confirm, and the section counter follows the listing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


# The shared DOM double learns parent links and the reconcile's tree
# surgeries, failing loud on an unmodeled call. Timers of a second or more
# park (the interaction resets must not run) while shorter ones run real, so
# the coalescing window elapses and the pins stay fetch counts.
_TABS_PREFIX = _dashnode.DOM + r"""
import { setTimeout as realSetTimeout, clearTimeout as realClearTimeout }
  from 'node:timers';
El.prototype.appendChild = function (child) {
  this.children.push(child);
  child.parent = this;
  return child;
};
El.prototype.removeChild = function (child) {
  const at = this.children.indexOf(child);
  if (at >= 0) this.children.splice(at, 1);
  child.parent = null;
  return child;
};
El.prototype.replaceWith = function (next) {
  if (!this.parent) throw new Error('replaceWith outside the tree');
  const p = this.parent;
  p.children[p.children.indexOf(this)] = next;
  next.parent = p;
  this.parent = null;
};
El.prototype.remove = function () {
  if (!this.parent) throw new Error('remove outside the tree');
  this.parent.removeChild(this);
};
El.prototype.select = function () {};
El.prototype.buttons = function () {
  return this.all().filter((el) => el.tag === 'button');
};
const counterEl = new El('span');
document.querySelector = (sel) => {
  if (sel !== '#s01 [data-sub]') {
    throw new Error('unmodeled selector ' + sel);
  }
  return counterEl;
};
const parked = [];
globalThis.setTimeout = (callback, delay) => {
  if ((delay || 0) >= 1000) {
    parked.push(callback);
    return parked.length;
  }
  return realSetTimeout(callback, delay || 0);
};
globalThis.clearTimeout = (id) => {
  if (typeof id === 'number' && id >= 1 && id <= parked.length) {
    parked[id - 1] = null;
    return;
  }
  realClearTimeout(id);
};
const pause = (ms) => new Promise((resolve) => realSetTimeout(resolve, ms));
const until = async (check) => {
  while (!check()) await pause(10);
};
const collect = async (check, capMs) => {
  const started = Date.now();
  while (!check()) {
    if (Date.now() - started >= capMs) return;
    await pause(10);
  }
};
const tab = (id, title, url, age) => ({ tabId: id, title, url, age });
let listing = [];
let tabsFetches = 0;
let sentCommand = null;
const commands = [];
globalThis.fetch = async (target, init = {}) => {
  if (target === '/tabs') {
    tabsFetches += 1;
    return jsonResponse(listing.slice());
  }
  if (init.method === 'PUT') {
    sentCommand = JSON.parse(init.body);
    commands.push(sentCommand);
    return jsonResponse({ ok: true, did: 'delivery-1' });
  }
  if (String(target).includes('/result')) {
    if (!sentCommand) return jsonResponse({ pending: true });
    const envelope = {
      id: sentCommand.id, deliveryId: 'delivery-1',
      resultGeneration: 'generation-1', error: null,
      result: { closed: true }, world: 'extension',
    };
    if (String(target).includes('consume=1')) {
      return jsonResponse({ ...envelope, consumed: true });
    }
    return jsonResponse(envelope);
  }
  throw new Error('unexpected request ' + target);
};
const container = new El('div');
const listeners = [];
const bus = { on: (fn) => { listeners.push(fn); } };
function emit(type) {
  for (const fn of listeners) fn({ type });
}
const findRow = (tid) => container.all().find(
  (el) => el.tag === 'tr' && el.dataset.tid === tid) || null;
const rowOrder = () => container.all()
  .filter((el) => el.tag === 'tr' && el.dataset.tid)
  .map((el) => el.dataset.tid);
const cellText = (tr, at) => tr.children[at].textContent;
const urlInputOf = (tr) => tr.children[3].children.find(
  (el) => el.tag === 'input') || null;
const buttonOf = (tr, text) => tr.buttons().find(
  (el) => el.textContent === text) || null;
const openEditor = (tid) => { buttonOf(findRow(tid), 'nav…').click(); };
const armClose = (tid) => { buttonOf(findRow(tid), 'close').click(); };
const rowSnapshot = (tid) => {
  const tr = findRow(tid);
  if (!tr) return null;
  const input = urlInputOf(tr);
  const close = buttonOf(tr, 'close');
  const armed = buttonOf(tr, 'sure?');
  const label = close ? 'close' : (armed ? 'sure?' : 'none');
  return {
    tr,
    table: tr.parent.parent,
    age: cellText(tr, 1),
    title: cellText(tr, 2),
    url: cellText(tr, 3),
    editing: !!input,
    editingValue: input ? input.value : '',
    closeLabel: label,
  };
};
"""

_IMPORT_AND_MOUNT_HEAD = r"""
phase('dashboard harness started');
(async () => {
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
"""

_TWO_TAB_LISTING = r"""
listing = [tab('11', 'first', 'https://a.example.com/x', 5),
           tab('22', 'second', 'https://b.example.com/y', 3)];
"""

_THREE_TAB_LISTING = r"""
listing = [tab('11', 'first', 'https://a.example.com/x', 5),
           tab('22', 'second', 'https://b.example.com/y', 3),
           tab('44', 'fourth', 'https://c.example.com/w', 7)];
"""

_MOUNT_AND_SETTLE = r"""
mount(container, bus);
await until(() => tabsFetches >= 1);
await pause(25);
"""

_CLOSE = r"""
})().catch(leave);
"""


def _harness(body, listing=_TWO_TAB_LISTING):
    return _dashnode.DashboardNodeHarness(
        _TABS_PREFIX + _IMPORT_AND_MOUNT_HEAD + listing
        + _MOUNT_AND_SETTLE + body + _CLOSE,
        bounded_steps=1, module=True,
        arguments=(ROOT / 'dashboard' / 'sections' / 'tabs.js',))


def _run(harness):
    result = _dashnode.run_dashboard_node(harness)
    return json.loads(result.stdout)


_SURVIVES_EVENT_ABOUT_ANOTHER_ROW = r"""
openEditor('11');
armClose('11');
const before = rowSnapshot('11');
const bBefore = rowSnapshot('22');
if (!before.editing) throw new Error('editor did not open');
if (before.closeLabel !== 'sure?') throw new Error('close did not arm');
listing = [tab('11', 'first', 'https://a.example.com/x', 5),
           tab('22', 'RETITLED', 'https://b.example.com/z', 3)];
emit('tab-updated');
await until(() => tabsFetches >= 2);
await pause(25);
const after = rowSnapshot('11');
const bAfter = rowSnapshot('22');
const eventFetches = tabsFetches - 1;
let armedClickIssuedClose = false;
const armed = buttonOf(after.tr, 'sure?');
if (armed) {
  armed.click();
  await collect(() => commands.length >= 1, 3000);
  armedClickIssuedClose = commands.some((cmd) =>
    cmd.type === 'close-tab' && String(cmd.tabId) === '11');
}
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  editorBefore: before.editing,
  confirmBefore: before.closeLabel,
  sameTr: after.tr === before.tr,
  sameTable: after.table === before.table,
  editorStill: after.editing,
  editorValue: after.editingValue,
  confirmStill: after.closeLabel,
  armedClickIssuedClose,
  bReplaced: bAfter.tr !== bBefore.tr,
  bTitle: bAfter.title,
  bUrl: bAfter.url,
  eventFetches,
}));
phase('dashboard harness finished');
"""

_OWN_URL_CHANGE_REPLACES_THE_ROW = r"""
openEditor('11');
armClose('11');
const before = rowSnapshot('11');
const bBefore = rowSnapshot('22');
if (!before.editing) throw new Error('editor did not open');
if (before.closeLabel !== 'sure?') throw new Error('close did not arm');
listing = [tab('11', 'first', 'https://a.example.com/moved', 5),
           tab('22', 'second', 'https://b.example.com/y', 3)];
emit('tab-updated');
await until(() => tabsFetches >= 2);
await pause(25);
const after = rowSnapshot('11');
const bAfter = rowSnapshot('22');
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  editorGone: !after.editing,
  urlText: after.url,
  closeFresh: after.closeLabel === 'close',
  bSameTr: bAfter.tr === bBefore.tr,
  eventFetches: tabsFetches - 1,
}));
phase('dashboard harness finished');
"""

_BURST_COALESCES = r"""
listing = [tab('11', 'first', 'https://a.example.com/x', 5),
           tab('22', 'second', 'https://b.example.com/y', 3),
           tab('33', 'third', 'https://d.example.com/w', 1)];
emit('tab-updated');
emit('tab-updated');
emit('tab-updated');
await until(() => tabsFetches >= 2);
const immediate = tabsFetches - 1;
await pause(25);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  immediate,
  total: tabsFetches - 1,
  dShown: !!findRow('33'),
  orderTids: rowOrder(),
}));
phase('dashboard harness finished');
"""

_UNREGISTER_REMOVES_THE_ARMED_ROW = r"""
armClose('11');
const bBefore = rowSnapshot('22');
listing = [tab('22', 'second', 'https://b.example.com/y', 3)];
emit('tab-unregistered');
await until(() => tabsFetches >= 2);
await pause(25);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  aGone: !findRow('11'),
  bSameTr: rowSnapshot('22').tr === bBefore.tr,
  eventFetches: tabsFetches - 1,
}));
phase('dashboard harness finished');
"""

_BOTH_ROWS_PINNED_IN_ONE_EVENT = r"""
openEditor('11');
armClose('11');
const before = rowSnapshot('11');
const bBefore = rowSnapshot('22');
if (!before.editing) throw new Error('editor did not open');
if (before.closeLabel !== 'sure?') throw new Error('close did not arm');
listing = [tab('11', 'first', 'https://a.example.com/x', 5),
           tab('22', 'RETITLED', 'https://b.example.com/z', 3)];
emit('tab-updated');
await until(() => tabsFetches >= 2);
await pause(25);
const after = rowSnapshot('11');
const bAfter = rowSnapshot('22');
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  aSameTr: after.tr === before.tr,
  editorStill: after.editing,
  confirmStill: after.closeLabel,
  bReplaced: bAfter.tr !== bBefore.tr,
  bTitle: bAfter.title,
  bUrl: bAfter.url,
  eventFetches: tabsFetches - 1,
}));
phase('dashboard harness finished');
"""

_AGE_ONLY_PATCHES_IN_PLACE = r"""
openEditor('11');
armClose('11');
const before = rowSnapshot('11');
const bBefore = rowSnapshot('22');
if (!before.editing) throw new Error('editor did not open');
if (before.closeLabel !== 'sure?') throw new Error('close did not arm');
listing = [tab('11', 'first', 'https://a.example.com/x', 40),
           tab('22', 'second', 'https://b.example.com/y', 51)];
emit('tab-updated');
await until(() => tabsFetches >= 2);
await pause(25);
const after = rowSnapshot('11');
const bAfter = rowSnapshot('22');
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  aSameTr: after.tr === before.tr,
  editorStill: after.editing,
  confirmStill: after.closeLabel,
  aAge: after.age,
  bSameTr: bAfter.tr === bBefore.tr,
  bAge: bAfter.age,
  eventFetches: tabsFetches - 1,
}));
phase('dashboard harness finished');
"""

_COUNTER_FOLLOWS_THE_LISTING = r"""
listing = [tab('11', 'first', 'https://a.example.com/x', 5),
           tab('22', 'second', 'https://b.example.com/y', 3),
           tab('33', 'third', 'https://d.example.com/w', 1)];
emit('tab-updated');
await until(() => tabsFetches >= 2);
await pause(25);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  counter: counterEl.textContent,
  dShown: !!findRow('33'),
}));
phase('dashboard harness finished');
"""


def test_an_event_about_another_row_leaves_the_editing_row_alive(_tmp):
    seen = _run(_harness(_SURVIVES_EVENT_ABOUT_ANOTHER_ROW))
    assert seen['editorBefore'] is True, seen
    assert seen['confirmBefore'] == 'sure?', seen
    assert seen['sameTr'] is True, seen
    assert seen['sameTable'] is True, seen
    assert seen['editorStill'] is True, seen
    assert seen['editorValue'] == 'https://a.example.com/x', seen
    assert seen['confirmStill'] == 'sure?', seen
    assert seen['armedClickIssuedClose'] is True, seen
    assert seen['bReplaced'] is True, seen
    assert seen['bTitle'] == 'RETITLED', seen
    assert seen['bUrl'] == 'https://b.example.com/z', seen
    assert seen['eventFetches'] == 1, seen


def test_a_row_own_url_change_replaces_the_row_and_ends_the_edit(_tmp):
    seen = _run(_harness(_OWN_URL_CHANGE_REPLACES_THE_ROW))
    assert seen['editorGone'] is True, seen
    assert seen['urlText'] == 'https://a.example.com/moved', seen
    assert seen['closeFresh'] is True, seen
    assert seen['bSameTr'] is True, seen
    assert seen['eventFetches'] == 1, seen


def test_a_burst_of_events_starts_one_listing_fetch(_tmp):
    seen = _run(_harness(_BURST_COALESCES))
    assert seen['immediate'] == 1, seen
    assert seen['total'] == 1, seen
    assert seen['dShown'] is True, seen
    assert seen['orderTids'] == ['22', '11', '33'], seen


def test_an_unregistered_tab_takes_its_armed_confirm_with_it(_tmp):
    seen = _run(_harness(_UNREGISTER_REMOVES_THE_ARMED_ROW))
    assert seen['aGone'] is True, seen
    assert seen['bSameTr'] is True, seen
    assert seen['eventFetches'] == 1, seen


def test_one_event_pins_both_the_changed_row_and_the_interactive_one(_tmp):
    seen = _run(_harness(_BOTH_ROWS_PINNED_IN_ONE_EVENT))
    assert seen['aSameTr'] is True, seen
    assert seen['editorStill'] is True, seen
    assert seen['confirmStill'] == 'sure?', seen
    assert seen['bReplaced'] is True, seen
    assert seen['bTitle'] == 'RETITLED', seen
    assert seen['bUrl'] == 'https://b.example.com/z', seen
    assert seen['eventFetches'] == 1, seen


def test_an_age_only_change_patches_text_and_keeps_the_interaction(_tmp):
    seen = _run(_harness(_AGE_ONLY_PATCHES_IN_PLACE))
    assert seen['aSameTr'] is True, seen
    assert seen['editorStill'] is True, seen
    assert seen['confirmStill'] == 'sure?', seen
    assert seen['aAge'] == '40s', seen
    assert seen['bSameTr'] is True, seen
    assert seen['bAge'] == '51s', seen
    assert seen['eventFetches'] == 1, seen


def test_the_section_counter_follows_the_listing(_tmp):
    seen = _run(_harness(_COUNTER_FOLLOWS_THE_LISTING))
    assert seen['counter'] == '3/3 tabs', seen
    assert seen['dShown'] is True, seen


_TITLE_ONLY_REPLACES_INTERACTIVE_ROW = r"""
openEditor('11');
armClose('11');
const before = rowSnapshot('11');
const bBefore = rowSnapshot('22');
const cBefore = rowSnapshot('44');
if (!before.editing) throw new Error('editor did not open');
if (before.closeLabel !== 'sure?') throw new Error('close did not arm');
listing = [tab('11', 'RETITLED', 'https://a.example.com/x', 5),
           tab('22', 'second', 'https://b.example.com/y', 3),
           tab('44', 'FOURTH', 'https://c.example.com/w', 7)];
emit('tab-updated');
await until(() => tabsFetches >= 2);
await pause(25);
const after = rowSnapshot('11');
const bAfter = rowSnapshot('22');
const cAfter = rowSnapshot('44');
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  aReplaced: after.tr !== before.tr,
  editorGone: !after.editing,
  closeFresh: after.closeLabel === 'close',
  aTitle: after.title,
  aUrl: after.url,
  bSameTr: bAfter.tr === bBefore.tr,
  cReplaced: cAfter.tr !== cBefore.tr,
  cTitle: cAfter.title,
  eventFetches: tabsFetches - 1,
}));
phase('dashboard harness finished');
"""

_CHANGE_THEN_REVERT_RESTORES = r"""
const bBefore = rowSnapshot('22');
listing = [tab('11', 'first', 'https://a.example.com/moved', 5),
           tab('22', 'second', 'https://b.example.com/y', 3)];
emit('tab-updated');
await until(() => tabsFetches >= 2);
await pause(25);
const moved = rowSnapshot('11');
listing = [tab('11', 'first', 'https://a.example.com/x', 5),
           tab('22', 'second', 'https://b.example.com/y', 3)];
emit('tab-updated');
await until(() => tabsFetches >= 3);
await pause(25);
const restored = rowSnapshot('11');
const bAfter = rowSnapshot('22');
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  movedUrl: moved.url,
  restoredUrl: restored.url,
  restoredTr: restored.tr !== moved.tr,
  bSameTr: bAfter.tr === bBefore.tr,
  totalFetches: tabsFetches - 1,
}));
phase('dashboard harness finished');
"""


def test_a_title_only_change_replaces_the_interactive_row(_tmp):
    seen = _run(_harness(
        _TITLE_ONLY_REPLACES_INTERACTIVE_ROW, _THREE_TAB_LISTING))
    assert seen['aReplaced'] is True, seen
    assert seen['editorGone'] is True, seen
    assert seen['closeFresh'] is True, seen
    assert seen['aTitle'] == 'RETITLED', seen
    assert seen['aUrl'] == 'https://a.example.com/x', seen
    assert seen['bSameTr'] is True, seen
    assert seen['cReplaced'] is True, seen
    assert seen['cTitle'] == 'FOURTH', seen
    assert seen['eventFetches'] == 1, seen


def test_a_reverted_url_change_shows_the_restored_value(_tmp):
    seen = _run(_harness(_CHANGE_THEN_REVERT_RESTORES))
    assert seen['movedUrl'] == 'https://a.example.com/moved', seen
    assert seen['restoredUrl'] == 'https://a.example.com/x', seen
    assert seen['restoredTr'] is True, seen
    assert seen['bSameTr'] is True, seen
    assert seen['totalFetches'] == 2, seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashtabevents_')


if __name__ == '__main__':
    raise SystemExit(main())
