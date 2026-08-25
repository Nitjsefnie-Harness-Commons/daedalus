"""Reading a workflow's `on:` block without a YAML parser.

Not a suite itself — run_tests.py only loads `test_*.py`.

PyYAML is not a dependency of this project and CI never installs it, so
the workflow suites scan text. What they need from that text is one
nesting level deep: which triggers an `on:` block declares, and which
path filters each one carries. Both answers have to survive the spellings
YAML allows for the same thing, which is why this is a module rather than
a substring check at each call site.

Correct or refusing, never plausible: a construct this reader does not
parse raises rather than returning an answer that reads like one, because
a policy test cannot tell a wrong answer from a right one.
"""
import re

_ENTRY_PATTERN = re.compile(
    r'''(?:(['"])([^'"]+)\1|([A-Za-z0-9_-]+))\s*:\s*(.*)$''')


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


def _mapping_key(stripped):
    """The (key, remainder) of a `key:` entry, or None if it is not one.

    A trigger is not always written bare. `workflow_dispatch: # manual` and
    `workflow_dispatch: {}` are the same key with a comment and with an empty
    map, and a reader that only accepts a line ENDING in a colon records
    neither — so a test asking which triggers exist would answer that one it
    was looking for is absent while the workflow declares it.

    The remainder after the colon is returned rather than dropped: it is
    what says whether the entry's whole value is written on this line.
    """
    quote = ''
    for index, char in enumerate(stripped):
        if quote:
            if char == quote:
                quote = ''
            continue
        if char in '"\'':
            quote = char
            continue
        if char == '#':
            # A comment reached before any colon: the line is not an entry.
            return None
        if char == ':':
            rest = stripped[index + 1:]
            if rest and not rest[0].isspace():
                # `a:b` is one scalar; YAML needs the space to make it a map.
                return None
            key = stripped[:index].strip().strip('\'"').strip()
            return key, rest.strip()
    return None


def _entry(line):
    """Return (indent, key, value) for one mapping line, or None."""
    indent = _indent(line)
    match = _ENTRY_PATTERN.fullmatch(line.strip())
    if not match:
        return None
    return indent, match.group(2) or match.group(3), match.group(4)


def _option_indent(lines):
    """The indent of an event's OWN options, or None when it has none.

    Only a key at that indent is one of the event's options. A deeper
    `paths-ignore:` belongs to something nested, and reading it as the
    event's would report an asymmetry this workflow does not have.
    """
    indents = [entry[0] for entry in map(_entry, lines) if entry is not None]
    return min(indents) if indents else None


def _event_option_keys(lines):
    """The keys an event declares at its own option indent."""
    option_indent = _option_indent(lines)
    return [entry[1] for entry in map(_entry, lines)
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


def _scalar(value):
    """One path value, past its trailing comment and its quotes."""
    value = _uncommented(value).strip()
    if (len(value) >= 2 and value[0] in "'\""
            and value[-1] == value[0]):
        return value[1:-1]
    return value


def _flow_sequence(value, key):
    """Parse the bounded flow-sequence subset used by these workflows."""
    value = value.strip()
    unsupported = f'{key} has an unsupported value: {value!r}'
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
            body = item[1:-1]
            # An escape or a doubled quote is a value this reader would
            # hand back without having decoded it: `["a\\", b]` is `a\`
            # and `['a''b']` is `a'b`, neither of which it produces.
            if (len(item) < 2 or item[-1] != item[0]
                    or '\\' in body or item[0] * 2 in body):
                raise AssertionError(unsupported)
            result.append(body)
            continue
        # `[{a: b}, c]` and `[a: b]` are mappings, not scalars — YAML makes
        # a colon-plus-space a key even without the braces.
        if (item[-1] in "'\"[]{}:" or '[' in item or '{' in item
                or ': ' in item):
            raise AssertionError(unsupported)
        result.append(item)
    return result


def _workflow_path_filters(lines):
    """Return normalized `paths` and `paths-ignore` entries for an event."""
    filters = {}
    option_indent = _option_indent(lines)

    for index, line in enumerate(lines):
        current = _entry(line)
        if current is None or current[1] not in ('paths', 'paths-ignore'):
            continue
        if current[0] != option_indent:
            continue
        indent, key, value = current
        if key in filters:
            # Silent last-wins describes a document the workflow does not
            # contain, which is why a second `on:` block and a second
            # trigger key are both refused too.
            raise AssertionError(f'duplicate event option {key!r}')
        value = value.strip()
        if value.startswith('#'):
            # An explanation on the key's own line is not the key's value;
            # the block sequence under it is. `_mapping_key` reads a
            # trigger key the same way, and this file's own style puts such
            # explanations exactly there.
            value = ''
        if value:
            filters[key] = _flow_sequence(value, key)
            continue

        values = []
        for following in lines[index + 1:]:
            next_entry = _entry(following)
            if next_entry is not None and next_entry[0] <= indent:
                break
            stripped = following.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('-'):
                values.append(_scalar(stripped[1:]))
        filters[key] = values
    return filters


def _workflow_triggers(workflow, filename='workflow'):
    """The `on:` mapping as {trigger name: its indented lines}.

    Text rather than a YAML parse: the suites are stdlib only, and the shape
    asked about here is one nesting level deep.
    """
    lines = workflow.splitlines()
    on_index = None
    on_pattern = re.compile(r'''(?:(['"])on\1|on)\s*:(.*)$''')
    for index, line in enumerate(lines):
        if line.startswith(' '):
            continue
        match = on_pattern.fullmatch(line)
        if not match:
            continue
        if on_index is not None:
            raise AssertionError(f'{filename}: duplicate on: blocks')
        value = match.group(2).strip()
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
            entry = _mapping_key(stripped)
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
    """Trigger keys of an `on:` block, past YAML quoting and stray spacing."""
    return {key.strip().strip('\'"').strip()
            for key in _workflow_triggers(workflow)}
