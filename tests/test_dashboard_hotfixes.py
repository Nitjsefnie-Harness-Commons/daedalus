#!/usr/bin/env python3
"""The hotfix panel, run rather than read.

The panel is where an operator sets a fix's site scope and where they edit
an existing fix, so a scope the form collects and never sends, or an edit
action that does not refill it, destroys the operator's own setting through
the most ordinary workflow in the UI. The harness mounts
dashboard/sections/hotfixes.js into a small DOM in Node, drives its form
and its edit buttons, and judges the fields each command carries and the
cells each row renders.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

_DOM = _dashnode.DOM

_HOTFIX_HARNESS = _DOM + r"""
(async () => {
const SCOPE = '*://*.example.com/*';
const commands = [];
let listed = { version: '1.0', fixes: [
  { id: 'scoped', ts: 1750000000000, code: 'console.log(1)', match: SCOPE },
  { id: 'bare', ts: 1750000000000, code: 'console.log(2)' },
] };
let envelope;
globalThis.fetch = async (target, init = {}) => {
  if (target === '/command') {
    const command = JSON.parse(init.body);
    commands.push(command);
    const answers = {
      'list-hotfixes': () => listed,
      'store-hotfix': () => ({ stored: command.fixId, total: 1,
        permanent: command.permanent, match: command.match || null }),
    };
    envelope = {
      id: command.id, deliveryId: 'delivery-' + commands.length,
      resultGeneration: 'generation-' + commands.length,
      result: answers[command.type](), error: null, world: 'extension',
    };
    return jsonResponse({ ok: true, did: envelope.deliveryId });
  }
  if (target.startsWith('/result?')) {
    return jsonResponse({ ...envelope, consumed: true });
  }
  throw new Error('unexpected request ' + target);
};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
function rowFor(id) {
  return container.find('[data-role=list]').all().find(
    (el) => el.tag === 'tr' && el.textContent.includes(id));
}
function scopeCell(id) {
  return rowFor(id).all().find((el) => el.tag === 'td' && el.className
    .includes('scope'));
}
function clickEdit(id) {
  rowFor(id).byText('edit').click();
}
function form() {
  return {
    match: container.find('[data-role=match]').value,
    id: container.find('[data-role=id]').value,
  };
}
await bounded(settle(), 'initial load', _dashnodeStepTimeoutMs);
const rendered = {
  scoped: scopeCell('scoped').textContent,
  bare: scopeCell('bare').textContent,
};
const headers = container.find('[data-role=list]').all()
  .filter((el) => el.tag === 'th')
  .map((el) => el.textContent.trim());
clickEdit('scoped');
await bounded(settle(), 'edit a scoped fix', _dashnodeStepTimeoutMs);
const scopedForm = form();
container.find('[data-role=store]').click();
await bounded(settle(), 'store an edited fix', _dashnodeStepTimeoutMs);
listed = { version: '1.0', fixes: [
  { id: 'bare', ts: 1750000000000, code: 'console.log(2)' },
] };
container.find('[data-role=refresh]').click();
await bounded(settle(), 'refresh onto an unscoped fix',
               _dashnodeStepTimeoutMs);
clickEdit('bare');
await bounded(settle(), 'edit an unscoped fix', _dashnodeStepTimeoutMs);
const bareForm = form();
container.find('[data-role=store]').click();
await bounded(settle(), 'store an unscoped fix', _dashnodeStepTimeoutMs);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  rendered, headers, scopedForm, bareForm, commands: commands.filter(
    (c) => c.type === 'store-hotfix'),
}));
phase('dashboard harness finished');
})().catch(leave);
"""


def _hotfix_scope(_tmp):
    harness = _dashnode.DashboardNodeHarness(
        _HOTFIX_HARNESS, bounded_steps=7, module=True, arguments=(
            ROOT / 'dashboard' / 'sections' / 'hotfixes.js',))
    return json.loads(_dashnode.run_dashboard_node(harness).stdout)


def test_the_hotfix_form_carries_a_scope_into_the_command(_tmp):
    """C9: the dashboard's own create form sends the scope it was given.

    The store button is the only way a fix is created here, so a scope the
    form collects and never sends is a field the operator believes they set.
    """
    seen = _hotfix_scope(_tmp)

    assert seen['scopedForm']['match'] == '*://*.example.com/*', seen
    assert seen['commands'][0]['fixId'] == 'scoped', seen
    assert seen['commands'][0]['match'] == '*://*.example.com/*', seen


def test_the_hotfix_edit_action_keeps_the_scope_the_operator_set(_tmp):
    """C10: editing a scoped fix copies its scope back into the form.

    The most ordinary workflow in the panel is edit-then-store, so a scope
    the form does not refill is destroyed by the act of fixing a typo in
    the code — and it fails silently, widening the fix to every site.
    """
    seen = _hotfix_scope(_tmp)

    assert seen['scopedForm']['id'] == 'scoped', seen
    assert seen['scopedForm']['match'] == '*://*.example.com/*', seen
    assert seen['commands'][0]['match'] == '*://*.example.com/*', seen


def test_a_hotfix_stored_without_a_scope_reads_as_having_none(_tmp):
    """C11: absence is displayed as absence, on both surfaces of the panel.

    The list renders one fix with a scope and one without, and the edit
    action on the unscoped one leaves the field empty rather than holding
    something that reads like a pattern. Together the two rows are what
    stops the first test passing against a panel that shows the same thing
    either way.
    """
    seen = _hotfix_scope(_tmp)

    assert seen['rendered']['scoped'] == '*://*.example.com/*', seen
    assert seen['rendered']['bare'] == '—', seen
    assert seen['bareForm']['match'] == '', seen
    # Absent travels as absent: an empty pattern would be refused by the
    # extension as one that does not parse.
    assert 'match' not in seen['commands'][1], seen


def test_the_scope_column_carries_a_header(_tmp):
    """The scope cell is found by its class, so nothing else pins the header.

    Every other control here reads the scope through the cell's production
    class, which a header row does not carry: a table rendered with no
    `scope` <th> still passes all three, while the column above the scope
    cells goes unlabelled and an operator reading it as the code preview is
    not wrong about the markup. The header is what says which column the
    pattern is in.
    """
    seen = _hotfix_scope(_tmp)

    assert 'scope' in seen['headers'], seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashhotfix_')


if __name__ == '__main__':
    raise SystemExit(main())
