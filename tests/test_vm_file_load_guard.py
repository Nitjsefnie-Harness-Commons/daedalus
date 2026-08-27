#!/usr/bin/env python3
"""The VM file-load spelling guard, and proof that nothing escapes it.

V8 coverage attributes executed bytes to the filename a VM load supplies,
so every harness load of a shipped file must name that file. The guard
cannot model JavaScript, so it enforces one canonical spelling instead:
`vm.runInContext(fs.readFileSync(<path>, 'utf8'), <context>,
{ filename: <the same path expression> })`. A correct-but-unusual spelling
must be rewritten to it rather than taught to the guard.

Discovery is the other half of the contract. A guard that only finds the
spelling it likes passes when a load leaves that spelling — it has stopped
looking and calls the result green. So discovery finds EVERY
vm.runInContext / vm.runInNewContext call site in the tracked test files,
however its source argument is spelled, and each site must be one of:

- the canonical inline read naming its file,
- a string-constant snippet, which holds no file bytes to name, or
- a run carrying `// vm-load-exempt: <reason>` on the line directly above
  it, saying why it does not name a shipped file.

A site that changes spelling moves between those categories; it cannot
vanish from the guard's view.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, iter_tree_files  # noqa: E402


_VM_CALL = re.compile(r'vm\s*\.\s*runIn(?:Context|NewContext)\s*\(')
_JS_COMMENT = re.compile(
    r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r'`(?:\\.|[^`\\])*`)|//[^\r\n]*|/\*.*?\*/', re.DOTALL)
_EXEMPTION = re.compile(
    r'^\s*//\s*vm-load-exempt\s*:\s*(?P<reason>\S.{11,}?)\s*$')


def _blank_js_comments(source):
    """Comments become spaces; strings and every newline stay put.

    A call site is found in the blanked text so that commented-out code
    cannot masquerade as a call, but the exemption marker IS a comment —
    keeping offsets and line breaks stable lets the site be mapped back to
    the raw line above it, where the marker lives.
    """
    def blank(match):
        if match.group(1):
            return match.group(1)
        return ''.join(
            '\n' if char == '\n' else ' ' for char in match.group(0))
    return _JS_COMMENT.sub(blank, source)


def _vm_harness_files(root=ROOT, tracked=None):
    """Tracked test files holding a VM call site the guard scans.

    The set is discovered rather than listed, so a harness file added
    later is scanned by default. This module is the single exemption:
    its call sites are the guard's own synthetic fixtures — deliberately
    non-canonical spellings feeding the rejection tests — not loads a
    harness performs.
    """
    if tracked is None:
        tracked = iter_tree_files(root)
    self_path = Path(__file__).resolve()
    files = []
    for path in tracked:
        path = Path(path)
        if path.resolve() == self_path:
            continue
        if path.relative_to(root).parts[0] != 'tests':
            continue
        source = _blank_js_comments(path.read_text(encoding='utf-8'))
        if _VM_CALL.search(source):
            files.append(path)
    return tuple(files)


def _vm_call_sites(source_files=None):
    """(path, line, call, raw_lines) for every discovered VM call site."""
    paths = _vm_harness_files() if source_files is None else source_files
    sites = []
    for path in paths:
        raw = path.read_text(encoding='utf-8')
        blanked = _blank_js_comments(raw)
        raw_lines = raw.splitlines()
        for match in _VM_CALL.finditer(blanked):
            end = _call_end(blanked, match.end() - 1)
            line = blanked.count('\n', 0, match.start()) + 1
            sites.append(
                (path, line, blanked[match.start():end], raw_lines))
    return sites


def _unguarded_sites(sites):
    """Sites that are neither canonical, snippets, nor visibly exempt."""
    problems = []
    for path, line, call, raw_lines in sites:
        arguments = _split_js_arguments(call)
        if not arguments:
            problems.append((path, line, 'unparseable call site'))
            continue
        if _is_string_constant(arguments[0]):
            continue
        if _is_canonical_file_load(arguments):
            continue
        if _exemption_reason(raw_lines, line) is not None:
            continue
        source_line = arguments[0].strip().split('\n')[0].strip()
        problems.append((path, line, source_line))
    return problems


def _exemption_reason(raw_lines, line):
    """The reason a site carries, or None when it carries none."""
    if line < 2:
        return None
    match = _EXEMPTION.match(raw_lines[line - 2])
    return match.group('reason') if match else None


def _is_canonical_file_load(arguments):
    """The one spelling a shipped-file load may use."""
    if len(arguments) != 3:
        return False
    read = _inline_read(arguments[0].strip())
    if read is None:
        return False
    expression, canonical_encoding = read
    if not canonical_encoding:
        return False
    expected = '{filename:' + ''.join(expression.split()) + '}'
    return ''.join(arguments[2].split()) == expected


def _inline_read(source):
    """(path expression, encoding is canonical) for an inline file read."""
    match = re.match(r'fs\s*\.\s*readFileSync\s*\(', source)
    if match is None:
        return None
    arguments = _split_js_arguments(source, match.end() - 1)
    if not arguments:
        return None
    canonical = (len(arguments) == 2
                 and arguments[1].strip() in ("'utf8'", '"utf8"'))
    return arguments[0].strip(), canonical


def _is_string_constant(expression):
    """A literal, or a chain of them: bytes no file could have supplied."""
    expression = expression.strip()
    while expression:
        if expression[0] not in '\'"`':
            return False
        end = _skip_js_string(expression, 0)
        if end > len(expression):
            return False
        if expression[0] == '`' and '${' in expression[:end]:
            return False
        expression = expression[end:].strip()
        if expression:
            if expression[0] != '+':
                return False
            expression = expression[1:].strip()
    return True


def _skip_js_string(source, index):
    quote = source[index]
    index += 1
    while index < len(source):
        if source[index] == '\\':
            index += 2
        elif source[index] == quote:
            return index + 1
        else:
            index += 1
    return index


def _call_end(source, open_paren):
    """The index past the ')' matching the one at `open_paren`."""
    depths = {'(': 1, '[': 0, '{': 0}
    index = open_paren + 1
    while index < len(source):
        char = source[index]
        if char in '\'"`':
            index = _skip_js_string(source, index)
            continue
        if char in '([{':
            depths[char] += 1
        elif char in ')]}':
            opener = {')': '(', ']': '[', '}': '{'}[char]
            depths[opener] -= 1
            if not any(depths.values()):
                return index + 1
        index += 1
    return len(source)


def _split_js_arguments(call, open_at=None):
    start = (call.index('(', call.index('runIn')) if open_at is None
             else open_at) + 1
    arguments = []
    argument_start = start
    depths = {'(': 0, '[': 0, '{': 0}
    index = start
    while index < len(call):
        char = call[index]
        if char in '\'"`':
            index = _skip_js_string(call, index)
            continue
        if char in '([{':
            depths[char] += 1
        elif char in ')]}':
            opener = {')': '(', ']': '[', '}': '{'}[char]
            if depths[opener]:
                depths[opener] -= 1
            elif char == ')':
                arguments.append(call[argument_start:index])
                return arguments
        elif char == ',' and not any(depths.values()):
            arguments.append(call[argument_start:index])
            argument_start = index + 1
        index += 1
    return arguments


def _guard_verdict(tmp, source):
    """(sites, problems) for a synthetic harness holding one call site."""
    path = Path(tmp) / 'synthetic_harness.py'
    path.write_text(source, encoding='utf-8')
    sites = _vm_call_sites((path,))
    return sites, _unguarded_sites(sites)


def _guard_accepts_source(tmp, source):
    sites, problems = _guard_verdict(tmp, source)
    return len(sites) == 1 and not problems


def test_every_vm_call_site_is_canonical_or_exempt(tmp):
    """No VM call site in the tree escapes the guard's categories.

    Asserting a discovered COUNT would hand the next editor a number to
    remember; asserting nothing about the set lets a load that leaves the
    guarded spelling shrink the guard's view unnoticed. Requiring every
    discovered site to be canonical, a snippet, or visibly exempt closes
    both holes at once.
    """
    del tmp
    sites = _vm_call_sites()
    assert sites, 'discovery found no VM call sites to check'
    problems = _unguarded_sites(sites)
    assert not problems, problems


def test_vm_load_guard_scans_the_worker_harness_files(tmp):
    """The guard scans the harness files the worker split introduced.

    The coverage collapse this guard exists to prevent came from two
    harness files its fixed list never named, so the scanned calls must
    include loads from both.
    """
    del tmp
    scanned = {path.name for path, _, _, _ in _vm_call_sites()}
    assert {'_worker_runtime.py', '_worker_sources.py'} <= scanned, (
        sorted(scanned))


def test_a_late_harness_with_an_unnamed_load_turns_the_guard_red(tmp):
    """A harness file added after this guard was written is still scanned.

    A guard over a hardcoded file list lets every file added later escape
    by default. Discovery handed a tree that gains a file containing an
    unnamed VM load must find that file and refuse the load.
    """
    harness = Path(tmp) / 'tests' / '_late_harness.py'
    harness.parent.mkdir()
    harness.write_text(
        'STUB = """\n'
        "vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context);\n"
        '"""\n', encoding='utf-8')
    files = _vm_harness_files(root=Path(tmp), tracked=(harness,))
    assert files == (harness,), files
    problems = _unguarded_sites(_vm_call_sites(files))
    assert problems, (
        'guard accepted an unnamed load in a file added after the guard')


def test_a_load_read_through_a_variable_is_seen_and_refused(tmp):
    """A file read staged in a variable is still a load the guard judges.

    Reading the source into a variable first is exactly how the guarded
    spelling was left behind once: the narrow pattern saw one fewer load
    and the suite stayed green. Discovery must still find the call site,
    and the site must be refused without the canonical spelling or an
    exemption.
    """
    source = (
        "const backgroundSource = fs.readFileSync(backgroundPath, 'utf8');\n"
        'vm.runInContext(backgroundSource, context,\n'
        '  { filename: backgroundPath });')
    sites, problems = _guard_verdict(tmp, source)
    assert len(sites) == 1, 'a variable-source load escaped discovery'
    assert problems, 'guard accepted a load that left the guarded spelling'


def test_guard_discovers_a_backtick_utf8_spelling(tmp):
    """A backtick encoding executes fine and used to escape discovery."""
    source = ('vm.runInContext(fs.readFileSync(backgroundPath, `utf8`), '
              'context, { filename: backgroundPath });')
    sites, problems = _guard_verdict(tmp, source)
    assert len(sites) == 1, 'a backtick-utf8 load escaped discovery'
    assert problems, 'guard accepted a non-canonical encoding spelling'


def test_guard_discovers_a_read_without_an_encoding_argument(tmp):
    """A readFileSync with no encoding argument is still discovered."""
    source = ('vm.runInContext(fs.readFileSync(backgroundPath), '
              'context, { filename: backgroundPath });')
    sites, problems = _guard_verdict(tmp, source)
    assert len(sites) == 1, 'an encoding-less load escaped discovery'
    assert problems, 'guard accepted a read without an encoding argument'


def test_an_exemption_marker_excuses_a_run_that_names_no_file(tmp):
    """A visible marker with a reason exempts a non-file run."""
    source = (
        '// vm-load-exempt: runs the probe assembled above, not a file\n'
        'vm.runInContext(probeSource, context);')
    sites, problems = _guard_verdict(tmp, source)
    assert len(sites) == 1, sites
    assert not problems, problems


def test_an_exemption_marker_must_state_a_reason(tmp):
    """A bare or perfunctory marker is not an exemption."""
    for marker in ('// vm-load-exempt:', '// vm-load-exempt: nope'):
        source = (marker + '\n'
                  'vm.runInContext(probeSource, context);')
        sites, problems = _guard_verdict(tmp, source)
        assert len(sites) == 1, sites
        assert problems, f'guard accepted a reason-free marker: {marker}'


def test_an_exemption_marker_belongs_to_the_line_above(tmp):
    """A marker detached from its site exempts nothing."""
    source = (
        '// vm-load-exempt: runs the probe assembled above, not a file\n'
        '\n'
        'vm.runInContext(probeSource, context);')
    sites, problems = _guard_verdict(tmp, source)
    assert len(sites) == 1, sites
    assert problems, 'guard honored a marker that is not at the site'


def test_guard_rejects_a_filename_value_with_a_suffix(tmp):
    """A filename expression with a suffix is not the read expression."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath + '.wrong' });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a filename value with a suffix')


def test_guard_rejects_filename_text_inside_a_comment(tmp):
    """A comment mentioning filename is not an options property."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "backgroundContext /* filename: backgroundPath */);")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted filename text inside a comment')


def test_guard_accepts_whitespace_between_file_load_tokens(tmp):
    """Whitespace and line breaks do not change a valid file-load call."""
    source = (
        'vm.runInContext(\n'
        '  fs.readFileSync(\n'
        '    backgroundPath,\n'
        "    'utf8'\n"
        '  ),\n'
        '  context,\n'
        '  { filename: backgroundPath });')
    assert _guard_accepts_source(tmp, source), (
        'guard rejected harmless whitespace and line breaks')


def test_guard_rejects_a_duplicate_filename_key(tmp):
    """A later duplicate filename key is outside the spelling contract."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath, "
              "filename: backgroundPath + '.wrong' });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a duplicate filename key')


def test_guard_rejects_a_later_filename_spread(tmp):
    """A spread that can overwrite filename is outside the contract."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath, "
              "...{ filename: backgroundPath + '.wrong' } });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a later filename spread')


def test_guard_rejects_a_filename_from_a_spread(tmp):
    """A spread-only filename is outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { ...{ filename: backgroundPath } });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a filename supplied by a spread')


def test_guard_rejects_a_second_options_property(tmp):
    """A second options property is outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: backgroundPath, context });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a second options property')


def test_guard_rejects_a_computed_filename_key(tmp):
    """A computed filename key is outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { ['filename']: backgroundPath });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a computed filename key')


def test_guard_rejects_an_options_variable(tmp):
    """An options object held in a variable is outside the contract."""
    source = ("const backgroundOptions = { filename: backgroundPath };\n"
              "vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, backgroundOptions);")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted an options variable')


def test_guard_rejects_a_parenthesized_filename_value(tmp):
    """Parentheses around filename are outside the canonical spelling."""
    source = ("vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), "
              "context, { filename: (backgroundPath) });")
    assert not _guard_accepts_source(tmp, source), (
        'guard accepted a parenthesized filename value')


def test_guard_accepts_the_canonical_filename_spelling(tmp):
    """The canonical spelling accepts whitespace and line breaks."""
    source = (
        'vm . runInContext ( fs . readFileSync ( backgroundPath ,\n'
        "  'utf8' ) , context , {\n"
        '  filename :\n  backgroundPath\n} );')
    assert _guard_accepts_source(tmp, source), (
        'guard rejected the canonical filename spelling')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='vmloadguard_')


if __name__ == '__main__':
    raise SystemExit(main())
