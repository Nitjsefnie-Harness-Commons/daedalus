"""Reading a workflow's `on:` block without a YAML parser.

Not a suite itself — run_tests.py only loads `test_*.py`.

PyYAML is not a dependency of this project and CI never installs it, so
the workflow suites scan text. What they need from that text is one
nesting level deep: which triggers an `on:` block declares, and which
path filters each one carries. Both answers have to survive the spellings
YAML allows for the same thing, which is why this is a module rather than
a substring check at each call site.
"""
import re


def _mapping_key(stripped):
    """The key of a `key:` or `key: value` entry, or None if it is neither.

    A trigger is not always written bare. `workflow_dispatch: # manual` and
    `workflow_dispatch: {}` are the same key with a comment and with an empty
    map, and a reader that only accepts a line ENDING in a colon records
    neither — so a test asking which triggers exist would answer that one it
    was looking for is absent while the workflow declares it.
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
            return stripped[:index].strip().strip('\'"').strip()
    return None


def _workflow_path_filters(lines):
    """Return normalized `paths` and `paths-ignore` entries for an event."""
    filters = {}
    entry_pattern = re.compile(
        r'''(?:(['"])([^'"]+)\1|([A-Za-z0-9_-]+))\s*:\s*(.*)$''')

    def _entry(line):
        """Return (indent, key, value) for one mapping line, or None."""
        indent = len(line) - len(line.lstrip(' '))
        match = entry_pattern.fullmatch(line.strip())
        if not match:
            return None
        return indent, match.group(2) or match.group(3), match.group(4)

    def _scalar(value):
        """Remove one matching pair of quotes around a path value."""
        value = value.strip()
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
                if len(item) < 2 or item[-1] != item[0]:
                    raise AssertionError(unsupported)
                result.append(item[1:-1])
                continue
            if item[-1] in "'\"[]" or '[' in item:
                raise AssertionError(unsupported)
            result.append(item)
        return result

    # Only a key at the event's OWN option indent is one of its filters. A
    # deeper `paths-ignore:` belongs to something nested, and reading it as
    # the event's would report an asymmetry this workflow does not have.
    entry_indents = [entry[0] for entry in map(_entry, lines)
                     if entry is not None]
    option_indent = min(entry_indents) if entry_indents else None

    for index, line in enumerate(lines):
        current = _entry(line)
        if current is None or current[1] not in ('paths', 'paths-ignore'):
            continue
        if current[0] != option_indent:
            continue
        indent, key, value = current
        value = value.strip()
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
        if line.startswith((' ', '\t')):
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
        if not line.startswith((' ', '\t')):
            break
        indent = len(line) - len(line.lstrip(' '))
        if indent == 2:
            # `_mapping_key` rather than a bare `endswith(':')`: a trigger
            # written `workflow_dispatch: {}` or with a trailing comment is
            # still that trigger, and a reader that missed it would report
            # the one it was looking for as absent.
            name = _mapping_key(stripped)
            if name is not None:
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
