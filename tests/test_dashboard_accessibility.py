#!/usr/bin/env python3
"""What a reader gets from the shipped markup, whichever way they read it.

A control with no accessible name is an unnamed node in the reading order, a
colour pair below 4.5:1 is text somebody cannot read, and a document with no
`lang` is read in whatever voice happens to load. Each check here ships with
a second test proving it can still fail, because a scan that cannot reject
anything passes everything.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _jsread import blank_js_comments  # noqa: E402
from _repo import ROOT, iter_tree_files  # noqa: E402


_CONTROL_TAGS = ('input', 'select', 'textarea')


def _js_call_stack(text, pos):
    """The `h(...)`/`field(...)` calls still open at `pos`.

    Comments are blanked by the caller and string literals are skipped whole,
    so the parenthesis depth this walks is the code's own. A regex literal is
    not modelled: the balance check below is what turns a misparse into a
    failure instead of a quietly wrong answer.
    """
    stack = []
    i, end = 0, len(text)
    while i < end:
        char = text[i]
        if char in '\'"`':
            i += 1
            while i < end and text[i] != char:
                i += 2 if text[i] == '\\' else 1
            i += 1
            continue
        if char == '(':
            head = i - 1
            while head >= 0 and text[head].isspace():
                head -= 1
            start = head
            while start >= 0 and (text[start].isalnum() or text[start] in '_$'):
                start -= 1
            name = text[start + 1:head + 1]
            tag = None
            if name == 'h':
                quoted = re.match(r"\s*'([^']*)'", text[i + 1:])
                if quoted:
                    tag = quoted.group(1)
            stack.append((name, tag))
            if i >= pos:
                return stack, None
            i += 1
            continue
        if char == ')':
            if not stack:
                return stack, f'unbalanced ) at offset {i}'
            stack.pop()
            i += 1
            continue
        i += 1
    return stack, None if not stack else 'unclosed call at end of file'


def _attrs_after(text, pos):
    """The object literal a `h('tag', {...})` call passes as its attributes."""
    brace = text.find('{', pos)
    if brace < 0:
        return ''
    depth, i, end = 0, brace, len(text)
    while i < end:
        char = text[i]
        if char in '\'"`':
            i += 1
            while i < end and text[i] != char:
                i += 2 if text[i] == '\\' else 1
            i += 1
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return text[brace:]


def test_every_dashboard_control_carries_an_accessible_name(tmp):
    """No form control reaches the page without a name a reader can hear.

    A headless accessibility-tree audit found 32 controls, 30 with no
    associated label and 14 with an empty accessible name: the sections
    placed a sibling <label> beside each control and never wrote the
    `for`/`id` pair that associates the two. Three shapes are accepted here,
    and they are the three that actually name a control: `field()`, which
    mints the id and the `for`; a wrapping <label>, which associates by
    containment; and an explicit `aria-label` for the one control that
    replaces the cell it edits and so can have no visible label at all.

    Placeholder text is deliberately NOT accepted. Chrome falls back to it
    when nothing else names a control, which is exactly what hid fourteen of
    these behind a plausible-looking name.
    """
    del tmp
    sources = sorted((ROOT / 'dashboard').rglob('*.js'))
    assert sources, 'no dashboard sources found'
    unnamed, misparsed = [], []
    seen = 0
    for path in sources:
        blanked = blank_js_comments(path.read_text(encoding='utf-8'))
        rel = path.relative_to(ROOT).as_posix()
        for tag in _CONTROL_TAGS:
            for match in re.finditer(r"\bh\(\s*'" + tag + r"'", blanked):
                seen += 1
                line = blanked.count('\n', 0, match.start()) + 1
                stack, problem = _js_call_stack(blanked, match.start())
                if problem:
                    misparsed.append(f'{rel}: {problem}')
                    continue
                enclosing = [name for name, _ in stack[:-1]]
                labelled = [t for name, t in stack[:-1] if t == 'label']
                attrs = _attrs_after(blanked, match.end())
                if 'field' in enclosing or labelled:
                    continue
                if "'aria-label':" in attrs or 'ariaLabel:' in attrs:
                    continue
                unnamed.append(f'{rel}:{line}: <{tag}> has no accessible name')
    assert not misparsed, misparsed
    assert seen >= 30, f'only {seen} controls found — the scan stopped seeing them'
    assert not unnamed, '\n'.join(unnamed)


def test_the_control_name_scan_still_catches_an_unnamed_control(tmp):
    """The scan above is worthless if it cannot fail."""
    del tmp
    naked = "const x = h('div', {}, h('input', { type: 'text' }));"
    stack, problem = _js_call_stack(naked, naked.index("h('input'"))
    assert problem is None, problem
    assert [name for name, _ in stack[:-1]] == ['h'], stack
    assert [t for _, t in stack[:-1]] == ['div'], stack

    named = "const x = h('div', {}, field('url', h('input', { type: 'text' })));"
    stack, problem = _js_call_stack(named, named.index("h('input'"))
    assert problem is None, problem
    assert 'field' in [name for name, _ in stack[:-1]], stack


def test_every_dashboard_image_carries_an_alternative(tmp):
    """A screenshot with no `alt` is an unnamed node in the reading order."""
    del tmp
    sources = sorted((ROOT / 'dashboard').rglob('*.js'))
    assert sources, 'no dashboard sources found'
    missing = []
    seen = 0
    for path in sources:
        blanked = blank_js_comments(path.read_text(encoding='utf-8'))
        rel = path.relative_to(ROOT).as_posix()
        for match in re.finditer(r"\bh\(\s*'img'", blanked):
            seen += 1
            line = blanked.count('\n', 0, match.start()) + 1
            attrs = _attrs_after(blanked, match.end())
            if 'alt:' not in attrs:
                missing.append(f'{rel}:{line}: <img> without alt')
    assert seen, 'no dashboard images found — the scan stopped seeing them'
    assert not missing, '\n'.join(missing)


def _css_variables(text):
    """The `--name: value;` declarations of a stylesheet's :root block."""
    block = re.search(r':root\s*\{(.*?)\}', text, re.S)
    assert block, 'no :root block in the stylesheet'
    return dict(re.findall(r'(--[\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;',
                           block.group(1)))


def _relative_luminance(colour):
    def channel(offset):
        value = int(colour[offset:offset + 2], 16) / 255
        return (value / 12.92 if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4)

    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)


def _contrast(foreground, background):
    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


_TEXT_VARS = ('--text', '--text-dim', '--text-dimmer',
              '--amber', '--cyan', '--red', '--green', '--violet')


_SURFACE_VARS = ('--bg', '--panel', '--panel-2')


def test_dashboard_text_colours_meet_wcag_contrast(tmp):
    """Every text colour clears 4.5:1 against every surface it can sit on.

    `--text-dim` measured 3.778:1 and `--text-dimmer` 2.119:1 on `--panel`,
    and the dashboard's labels are 10px — normal text, so 4.5:1 is the bar,
    not the 3:1 large-text one. Each text colour is checked against all three
    surfaces rather than the one it happens to be used on today, because the
    pairing is a class attribute away from changing.
    """
    del tmp
    variables = _css_variables(
        (ROOT / 'dashboard' / 'style.css').read_text(encoding='utf-8'))
    missing = [name for name in _TEXT_VARS + _SURFACE_VARS
               if name not in variables]
    assert not missing, f'stylesheet no longer declares {missing}'
    failures = []
    for text_var in _TEXT_VARS:
        for surface_var in _SURFACE_VARS:
            ratio = _contrast(variables[text_var], variables[surface_var])
            if ratio < 4.5:
                failures.append(
                    f'{text_var} {variables[text_var]} on {surface_var} '
                    f'{variables[surface_var]}: {ratio:.3f}:1')
    assert not failures, '\n'.join(failures)


def test_the_contrast_check_still_fails_a_failing_pair(tmp):
    """The ratio above is worthless if it cannot reject the old values."""
    del tmp
    assert round(_contrast('#6B7280', '#13151A'), 3) == 3.778
    assert round(_contrast('#474C56', '#13151A'), 3) == 2.119
    assert round(_contrast('#FFFFFF', '#000000'), 1) == 21.0


def test_every_shipped_document_declares_its_language(tmp):
    """A document with no `lang` is read in whatever voice happens to load.

    The extension options page had no `lang` and no `<title>`, so it had no
    accessible name of its own either.
    """
    del tmp
    documents = [ROOT / 'dashboard' / 'index.html',
                 ROOT / 'extension' / 'options.html']
    for path in documents:
        markup = path.read_text(encoding='utf-8')
        rel = path.relative_to(ROOT).as_posix()
        assert re.search(r'<html[^>]*\slang="[a-zA-Z-]+"', markup), (
            f'{rel} declares no document language')
        assert re.search(r'<title>\s*\S', markup), f'{rel} has no title'


def test_every_served_page_declares_its_encoding(tmp):
    """An HTML page without a charset is decoded as windows-1252.

    A browser given no declaration falls back to a legacy code page, and the
    fallback applies to every classic script the document loads as well as to
    the markup — so a single em dash in a sibling .js file renders as three
    wrong characters. The extension's options page shipped that way and
    displayed its own success message mangled.

    Cheap to state and cheap to keep: any page added later inherits the same
    requirement instead of rediscovering it in a screenshot.
    """
    del tmp
    pages = [p for p in iter_tree_files(ROOT) if p.suffix == '.html']
    assert pages, 'no HTML pages found to check'
    missing = [
        str(p.relative_to(ROOT)) for p in pages
        if not re.search(r'<meta[^>]*charset', p.read_text(encoding='utf-8'),
                         re.IGNORECASE)]
    assert not missing, f'HTML without a charset declaration: {missing}'


def test_every_static_label_names_a_control(tmp):
    """A <label> in shipped markup carries `for`, or it labels nothing."""
    del tmp
    documents = [ROOT / 'dashboard' / 'index.html',
                 ROOT / 'extension' / 'options.html']
    dangling = []
    seen = 0
    for path in documents:
        markup = path.read_text(encoding='utf-8')
        rel = path.relative_to(ROOT).as_posix()
        for match in re.finditer(r'<label\b([^>]*)>', markup):
            seen += 1
            line = markup.count('\n', 0, match.start()) + 1
            target = re.search(r'\sfor="([^"]+)"', match.group(1))
            if not target:
                dangling.append(f'{rel}:{line}: <label> without for=')
                continue
            if f'id="{target.group(1)}"' not in markup:
                dangling.append(
                    f'{rel}:{line}: for="{target.group(1)}" names no element')
    assert seen, 'no labels found in shipped markup'
    assert not dangling, '\n'.join(dangling)


_FIELD_HARNESS = _dashnode.DashboardNodeHarness(r"""
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
const styled = describe(field('css', h('input', {}), { style: { margin: '0' } }));
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


def test_field_associates_every_label_with_its_control(tmp):
    """`field()` is the association, so it is checked by running it.

    The source scan proves each control is built inside a `field()` call; it
    cannot prove `field()` writes a `for` that resolves. Both halves are
    needed — the shape and the behaviour — because a helper that quietly
    stopped emitting `for` would leave the scan perfectly green.
    """
    del tmp
    result = _dashnode.run_dashboard_node(_FIELD_HARNESS)
    seen = json.loads(result.stdout)

    for key in ('first', 'second', 'preset', 'styled'):
        entry = seen[key]
        assert entry['labelTag'] == 'label', entry
        assert entry['associated'], entry
        assert entry['labelFor'], entry
        assert entry['controlId'], entry

    assert seen['first']['labelText'] == 'url', seen
    assert seen['unique'], seen['first']['controlId']

    # An id the caller already chose is kept: overwriting it would break
    # whatever else names that element.
    assert seen['preset']['controlId'] == 'chosen-id', seen['preset']
    assert seen['preset']['labelFor'] == 'chosen-id', seen['preset']

    # Caller attributes survive alongside the `for` the helper adds.
    assert seen['styled']['associated'], seen['styled']

    # The alignment cell is presentation, and says so.
    assert seen['spacerTag'] == 'span', seen
    assert seen['spacerHidden'] == 'true', seen
    assert seen['spacerClass'] == 'label-spacer', seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dasha11y_')


if __name__ == '__main__':
    raise SystemExit(main())
