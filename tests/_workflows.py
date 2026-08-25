"""Reading a workflow's `on:` block without a YAML parser.

Not a suite itself — run_tests.py only loads `test_*.py`.

PyYAML is not a dependency of this project and CI never installs it, so
the workflow suites scan text. What they need from that text is one
nesting level deep: which triggers an `on:` block declares, and which
path filters each one carries. Both answers have to survive the spellings
YAML allows for the same thing, which is why this is a module rather than
a substring check at each call site.

The bounded grammar is an allow-list. Mapping keys are only unquoted plain
keys made from lowercase letters, digits, `_`, and `-`; every quoted or other
key is refused. Path values are either unquoted scalars that YAML's core
schema resolves as strings, or single- or double-quoted scalars with no
backslash and no embedded quote of either kind. Implicit booleans in every
case (`true`, `false`, `yes`, `no`, `on`, `off`, `y`, and `n`), nulls (`null`,
`~`, and empty plain scalars), every core integer and float form (signs,
underscores, bases, sexagesimals, `.inf`, and `.nan`), and timestamps are
refused, as are tags, anchors, aliases, block scalars, continuations, flow
mappings, tabs,
duplicates, and non-empty inline trigger values. Empty trigger values (`{}`,
`null`, and `~`) remain accepted as event declarations. Correct or refusing,
never plausible: a construct this reader does not parse raises rather than
returning an answer that reads like one, because a policy test cannot tell a
wrong answer from a right one.
"""
import re

_PLAIN_KEY = re.compile(r'[a-z0-9_-]+')
_PLAIN_MAPPING = re.compile(r':(?:[ \t]|$)')
_NON_STRING_SCALARS = (
    re.compile(r'^(?:y|yes|n|no|true|false|on|off)$', re.IGNORECASE),
    re.compile(r'^(?:null|~)$', re.IGNORECASE),
    re.compile(
        r'^(?:[-+]?0[bB][0-1_]+|[-+]?0[oO][0-7_]+'
        r'|[-+]?0[xX][0-9a-fA-F_]+|[-+]?0[0-7_]+'
        r'|[-+]?(?:0|[1-9][0-9_]*)'
        r'|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$'),
    re.compile(
        r'^(?:[-+]?(?:[0-9][0-9_]*\.[0-9_]*|'
        r'\.[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)?'
        r'|[-+]?[0-9][0-9_]*[eE][-+]?[0-9_]+'
        r'|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*'
        r'|[-+]?\.(?:inf|nan))$', re.IGNORECASE),
    re.compile(
        r'^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}'
        r'(?:[Tt ]+[0-9]{1,2}:[0-9]{2}:[0-9]{2}'
        r'(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}'
        r'(?::[0-9]{2})?))?)?$'),
)


def _indent(line):
    """The indent width of `line`, refusing indentation holding a tab.

    YAML forbids a tab in indentation, so there is no width to give one.
    Measuring with spaces alone counted a tab as no indent at all, which
    dragged an event's option indent down to zero and hid every filter
    below it — read by both policy tests as "declares no filters".
    """
    width = len(line) - len(line.lstrip(' \t'))
    if '\t' in line[:width]:
        raise AssertionError(f'tab in the indentation of {line!r}')
    return width


def _mapping_key(stripped, filename='workflow'):
    """Return a positively understood plain mapping key and its value."""
    if '\t' in stripped:
        raise AssertionError(
            f'{filename}: tab in mapping entry: {stripped!r}')
    stripped = stripped.strip()
    if not stripped or stripped.startswith('#'):
        return None
    if stripped == '-' or stripped.startswith(('- ', '-\t')):
        return None
    quote = ''
    escaped = False
    colon = None
    for index, char in enumerate(stripped):
        if quote:
            if escaped:
                escaped = False
                continue
            if quote == '"' and char == '\\':
                escaped = True
                continue
            if char == quote:
                if (quote == "'" and index + 1 < len(stripped)
                        and stripped[index + 1] == quote):
                    escaped = True
                    continue
                quote = ''
            continue
        if char in '"\'':
            quote = char
            continue
        if char == '#' and (index == 0 or stripped[index - 1].isspace()):
            return None
        if char == ':' and (
                index + 1 == len(stripped)
                or stripped[index + 1].isspace()):
            colon = index
            break
    if colon is None:
        if stripped[0] in '"\'' and ':' in stripped:
            raise AssertionError(
                f'{filename}: unsupported mapping key: {stripped!r}')
        return None
    key = stripped[:colon].strip()
    if not _PLAIN_KEY.fullmatch(key):
        raise AssertionError(
            f'{filename}: unsupported mapping key: {key!r}')
    return key, stripped[colon + 1:].strip()


def _entry(line, filename='workflow'):
    """Return (indent, key, value) for one mapping line, or None."""
    indent = _indent(line)
    entry = _mapping_key(line, filename)
    if entry is None:
        return None
    return indent, entry[0], entry[1]


def _option_indent(lines, filename='workflow'):
    """The indent of an event's OWN options, or None when it has none.

    Only a key at that indent is one of the event's options. A deeper
    `paths-ignore:` belongs to something nested, and reading it as the
    event's would report an asymmetry this workflow does not have.
    """
    entries = [entry for entry in
               (_entry(line, filename) for line in lines)
               if entry is not None]
    if not entries:
        return None
    option_indent = min(entry[0] for entry in entries)
    seen = set()
    for entry in entries:
        if entry[0] != option_indent:
            continue
        if entry[1] in seen:
            raise AssertionError(
                f'{filename}: duplicate event option {entry[1]!r}')
        seen.add(entry[1])
    return option_indent


def _event_option_keys(lines, filename='workflow'):
    """The keys an event declares at its own option indent."""
    option_indent = _option_indent(lines, filename)
    return [entry[1] for entry in
            (_entry(line, filename) for line in lines)
            if entry is not None and entry[0] == option_indent]


def _uncommented(value):
    """`value` up to the unquoted ` #` that starts a YAML comment.

    A trailing comment belongs to nobody: keeping it made `- LICENSE  # not
    code` a different path from `- LICENSE`, so two filters YAML calls
    identical compared unequal. A `#` inside quotes, or one not preceded by
    whitespace, is part of the value and survives.
    """
    quote = ''
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = ''
            continue
        if char in '\'"':
            quote = char
            continue
        if char == '#' and index and value[index - 1].isspace():
            return value[:index]
    return value


def _scalar(value, filename='workflow', unsupported=None):
    """One path value, past its trailing comment and its quotes."""
    value = _uncommented(value).strip()
    failure = unsupported or (
        f'{filename}: unsupported scalar value: {value!r}')
    if re.fullmatch(r'[|>][0-9+-]*', value):
        raise AssertionError(failure)
    if (not value or value[0] in '!&*@`%]}|>,'
            or '\t' in value):
        raise AssertionError(failure)
    quoted = value and value[0] in "'\""
    if quoted or (value and value[-1] in "'\""):
        if not (quoted and len(value) >= 2 and value[-1] == value[0]):
            raise AssertionError(failure)
        body = value[1:-1]
        if '\\' in body or "'" in body or '"' in body:
            raise AssertionError(failure)
        return body
    if (value.startswith(('#', '[', '{'))
            or value in ('-', '?', ':')
            or value.startswith(('- ', '? ', ': '))
            or _PLAIN_MAPPING.search(value)
            or any(pattern.fullmatch(value)
                   for pattern in _NON_STRING_SCALARS)):
        raise AssertionError(failure)
    return value


def _flow_item_has_mapping_syntax(item):
    """Whether an unquoted flow item contains mapping delimiters."""
    return (item[-1] in "'\"[]{}:" or any(char in item for char in '[]{}')
            or ': ' in item)


def _flow_sequence(value, key, filename='workflow'):
    """Parse the bounded flow-sequence subset used by these workflows."""
    value = value.strip()
    unsupported = f'{filename}: {key} has an unsupported value: {value!r}'
    if not (value.startswith('[') and value.endswith(']')):
        raise AssertionError(unsupported)
    content = value[1:-1].strip()
    if not content:
        return []

    items = []
    start = 0
    quote = ''
    for index, char in enumerate(content):
        if quote:
            if char == quote:
                quote = ''
            continue
        if char in "'\"":
            quote = char
        elif char == ',':
            item = content[start:index].strip()
            if not item:
                raise AssertionError(unsupported)
            items.append(item)
            start = index + 1
    if quote:
        raise AssertionError(unsupported)
    item = content[start:].strip()
    if not item:
        raise AssertionError(unsupported)
    items.append(item)

    result = []
    for item in items:
        if item[0] in "'\"":
            result.append(_scalar(item, filename, unsupported))
            continue
        if item[0] in '!&*':
            raise AssertionError(unsupported)
        # `[{a: b}, c]` and `[a: b]` are mappings, not scalars — YAML makes
        # a colon-plus-space a key even without the braces.
        if _flow_item_has_mapping_syntax(item):
            raise AssertionError(unsupported)
        result.append(_scalar(item, filename, unsupported))
    return result


def _workflow_path_filters(lines, filename='workflow'):
    """Return normalized `paths` and `paths-ignore` entries for an event."""
    filters = {}
    option_indent = _option_indent(lines, filename)

    for index, line in enumerate(lines):
        current = _entry(line, filename)
        if current is None or current[1] not in ('paths', 'paths-ignore'):
            continue
        if current[0] != option_indent:
            continue
        indent, key, value = current
        if key in filters:
            # Silent last-wins describes a document the workflow does not
            # contain, which is why a second `on:` block and a second
            # trigger key are both refused too.
            raise AssertionError(
                f'{filename}: duplicate event option {key!r}')
        value = value.strip()
        if value.startswith('#'):
            # An explanation on the key's own line is not the key's value;
            # the block sequence under it is. `_mapping_key` reads a
            # trigger key the same way, and this file's own style puts such
            # explanations exactly there.
            value = ''
        if value:
            filters[key] = _flow_sequence(value, key, filename)
            continue

        values = []
        item_indent = None
        for following in lines[index + 1:]:
            next_entry = _entry(following, filename)
            if next_entry is not None and next_entry[0] <= indent:
                break
            stripped = following.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('-'):
                following_indent = _indent(following)
                if item_indent is None:
                    item_indent = following_indent
                elif following_indent != item_indent:
                    raise AssertionError(
                        f'{filename}: unsupported block sequence line: '
                        f'{stripped!r}')
                values.append(_scalar(stripped[1:], filename))
            elif item_indent is not None and _indent(following) > item_indent:
                raise AssertionError(
                    f'{filename}: unsupported block sequence continuation: '
                    f'{stripped!r}')
        if item_indent is None:
            raise AssertionError(
                f'{filename}: unsupported scalar value: {value!r}')
        filters[key] = values
    return filters


def _workflow_triggers(workflow, filename='workflow'):
    """The `on:` mapping as {trigger name: its indented lines}.

    Text rather than a YAML parse: the suites are stdlib only, and the shape
    asked about here is one nesting level deep.
    """
    lines = workflow.splitlines()
    on_index = None
    for index, line in enumerate(lines):
        if _indent(line):
            continue
        entry = _mapping_key(line, filename)
        if entry is None or entry[0] != 'on':
            continue
        if on_index is not None:
            raise AssertionError(f'{filename}: duplicate on: blocks')
        value = entry[1]
        if value and not value.startswith('#'):
            raise AssertionError(
                f'{filename}: inline on: block is not understood: {line!r}')
        on_index = index
        # No `break`: the scan continues so a second top-level `on:` is seen
        # and refused above. Stopping here would make that check unreachable.

    assert on_index is not None, (
        f'{filename}: no understood top-level on: block')
    triggers, current = {}, None
    for line in lines[on_index + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = _indent(line)
        if not indent:
            break
        # A `-` item at the trigger indent is a block sequence belonging to
        # the trigger above it — `schedule:` with its crons at indent 2 is
        # idiomatic YAML — not a key of its own. Read as a key it becomes a
        # trigger named `- cron`, and a second cron a duplicate of that.
        if indent == 2 and not stripped.startswith('-'):
            # `_mapping_key` rather than a bare `endswith(':')`: a trigger
            # written `workflow_dispatch: {}` or with a trailing comment is
            # still that trigger, and a reader that missed it would report
            # the one it was looking for as absent.
            entry = _mapping_key(line, filename)
            if entry is not None:
                name, rest = entry
                # Options written on the key's own line are options this
                # reader never collects: `push: {branches: [main]}` would
                # read as a push trigger declaring no filters at all.
                if rest not in ('', '{}', 'null', '~') and not (
                        rest.startswith('#')):
                    raise AssertionError(
                        f'{filename}: inline value for {name!r} is not'
                        ' understood')
                if name in triggers:
                    raise AssertionError(
                        f'{filename}: duplicate trigger {name!r}')
                triggers[name] = []
                current = name
                continue
        if current is not None:
            triggers[current].append(line)
    assert triggers, f'{filename}: on: block has no understood triggers'
    return triggers


def _trigger_names(workflow):
    """Trigger keys of an `on:` block in the accepted key grammar."""
    return set(_workflow_triggers(workflow))
