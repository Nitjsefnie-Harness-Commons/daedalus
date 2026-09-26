"""`field()`'s label association: the Node harness and the check it earns.

Not a suite itself — run_tests.py only loads `test_*.py`.

The source scan in tests/test_dashboard_accessibility.py proves each
control is built inside a `field()` call; it cannot prove `field()` writes
a `for` that resolves. The harness proving the second half, and the check
reading its output, both lived in that suite — which two sibling suites
then imported whole to reach one name. They live here now, so an importing
suite runs the mechanism without executing another suite's body.
"""
import json

import _dashnode
from _repo import ROOT


FIELD_HARNESS = _dashnode.DashboardNodeHarness(
    r"""
import { pathToFileURL } from 'node:url';

phase('dashboard harness started');
(async () => {
// Enough DOM for `h`; the helpers under test are the real ones.
class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.text = '';
    this.attrs = {};
    this.style = {};
    this.dataset = {};
  }
  // A real element reflects the id attribute onto the property, and `field`
  // reads the property; a double that does not reflect would report an
  // association the browser never makes.
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, v) { this.attrs[name] = String(v); }
  addEventListener() {}
}
globalThis.document = {
  createElement: (tag) => new El(tag),
  createTextNode: (t) => ({ tag: '#text', text: String(t), children: [] }),
};

phase('dashboard module import started');
const { h, field, spacer } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');

function describe(pair) {
  const [label, control] = pair;
  return {
    labelTag: label.tag,
    labelFor: label.attrs.for,
    labelText: label.children.map((c) => c.text).join(''),
    controlId: control.id,
    associated: label.attrs.for === control.id,
  };
}

phase('dashboard call started');
const first = describe(field('url', h('input', { type: 'text' })));
const second = describe(field('name', h('select', {})));
const preset = describe(field('code', h('textarea', { id: 'chosen-id' })));
const styled = describe(field('css', h('input', {}), { style: { margin:"""
    r""" '0' } }));
const blank = spacer();
phase('dashboard call settled');

process.stdout.write(JSON.stringify({
  first, second, preset, styled,
  unique: first.controlId !== second.controlId,
  spacerTag: blank.tag,
  spacerHidden: blank.attrs['aria-hidden'],
  spacerClass: blank.className,
}));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=1, module=True, arguments=(
        ROOT / 'dashboard' / 'sections' / '_util.js',))


def read_field_associations():
    """What the real `field()` calls reported, parsed out of the child."""
    return json.loads(_dashnode.run_dashboard_node(FIELD_HARNESS).stdout)


_FIELD_KEYS = ('first', 'second', 'preset', 'styled')


def _wanted(entry, field_name, wanted):
    value = entry.get(field_name)
    if value == wanted or (wanted is True and value):
        return None
    return f'{field_name} is {value!r}, not {wanted!r}'


def field_association_failures(seen):
    """Every way `field()` failed to associate a label with its control.

    Messages rather than asserts, so a suite can run the same check
    against a planted defect as against the harness: the whole point of
    the check is that a `field()` which stopped emitting `for` is
    rejected, and only a check that can be handed a bad reading proves
    it. An empty list is the pass.
    """
    failures = []
    # `styled` earns the same three checks as the others: the `for` the
    # helper adds has to survive alongside the caller's own attributes
    # rather than replace them.
    for key in _FIELD_KEYS:
        entry = seen.get(key)
        if entry is None:
            failures.append(f'{key}: the harness reported no such field')
            continue
        for field_name, wanted in (('labelTag', 'label'),
                                   ('labelFor', True),
                                   ('controlId', True)):
            problem = _wanted(entry, field_name, wanted)
            if problem:
                failures.append(f'{key}: {problem}')
        target = entry.get('labelFor')
        if not entry.get('associated'):
            failures.append(
                f'{key}: no control is associated with the label, '
                f'for={target!r} names no element')

    if not seen.get('unique'):
        failures.append('two fields minted the same control id')
    first = seen.get('first') or {}
    if first.get('labelText') != 'url':
        failures.append(f"first: label text is {first.get('labelText')!r}, "
                        "not the field name 'url'")

    # An id the caller already chose is kept: overwriting it would break
    # whatever else names that element.
    preset = seen.get('preset') or {}
    for field_name in ('controlId', 'labelFor'):
        problem = _wanted(preset, field_name, 'chosen-id')
        if problem:
            failures.append(f'preset: {problem}')

    # The alignment cell is presentation, and says so.
    for field_name, wanted in (('spacerTag', 'span'),
                               ('spacerHidden', 'true'),
                               ('spacerClass', 'label-spacer')):
        problem = _wanted(seen, field_name, wanted)
        if problem:
            failures.append(f'spacer: {problem}')
    return failures
