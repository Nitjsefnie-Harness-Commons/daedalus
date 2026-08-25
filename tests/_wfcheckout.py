"""Read checkout refs from a workflow's structure without a YAML library."""

import re


class YAMLReadError(ValueError):
    """The workflow uses YAML syntax this reader cannot prove it decoded."""


_DOUBLE_ESCAPES = {
    '0': '\0', 'a': '\a', 'b': '\b', 't': '\t',
    'n': '\n', 'v': '\v', 'f': '\f', 'r': '\r',
    'e': '\x1b', ' ': ' ', '"': '"', '/': '/',
    '\\': '\\', 'N': '\u0085', '_': '\u00a0',
    'L': '\u2028', 'P': '\u2029',
}


class _WorkflowReader:
    """Read only the YAML paths needed by the checkout pin."""

    def __init__(self, workflow):
        self.lines = workflow.splitlines()
        self.jobs_line = None
        self.jobs_rest = ''
        self._scan_document()

    def _refuse(self, what, index, context):
        raise YAMLReadError(
            f'{what} at line {index + 1} while reading {context}')

    def _raw_indent(self, index):
        line = self.lines[index]
        prefix = line[:len(line) - len(line.lstrip(' \t'))]
        return len(prefix), prefix

    def _indent(self, index, context):
        indent, prefix = self._raw_indent(index)
        if '\t' in prefix:
            self._refuse('tab in indentation', index, context)
        return indent

    def _blank(self, index):
        line = self.lines[index]
        return not line.strip() or line.lstrip(' \t').startswith('#')

    def _reject_prefix(self, value, index, context):
        for prefix, what in (('{', 'flow mapping'), ('[', 'flow sequence'),
                             ('&', 'anchor'), ('*', 'alias'),
                             ('!', 'explicit tag')):
            if value.startswith(prefix):
                self._refuse(what, index, context)

    def _reject_scalar_shape(self, value, index, context):
        for prefix, what in (('?', 'explicit key'),
                             ('-', 'sequence where scalar was required')):
            if value == prefix or value.startswith(prefix + ' '):
                self._refuse(what, index, context)

    def _strip_comment(self, text):
        quote = None
        escaped = False
        for index, char in enumerate(text):
            if quote == '"' and escaped:
                escaped = False
                continue
            if quote == '"' and char == '\\':
                escaped = True
                continue
            if char in ('"', "'"):
                if quote is None:
                    quote = char
                elif quote == char:
                    if quote == "'" and index + 1 < len(text) \
                            and text[index + 1] == "'":
                        continue
                    quote = None
                continue
            if char == '#' and quote is None \
                    and (index == 0 or text[index - 1].isspace()):
                return text[:index].rstrip()
        return text.rstrip()

    def _mapping_colon(self, body, index, context):
        quote = None
        escaped = False
        for position, char in enumerate(body):
            if quote == '"' and escaped:
                escaped = False
                continue
            if quote == '"' and char == '\\':
                escaped = True
                continue
            if quote is None and char in ('"', "'"):
                quote = char
                continue
            if quote is not None:
                if char == quote:
                    if quote == "'" and position + 1 < len(body) \
                            and body[position + 1] == "'":
                        continue
                    quote = None
                continue
            if (char == ':' and (position + 1 == len(body)
                                 or body[position + 1].isspace())):
                return position
        self._refuse('mapping entry without a colon', index, context)
        return None

    def _mapping_parts(self, index, context, body=None):
        if body is None:
            body = self.lines[index].lstrip(' ')
        body = self._strip_comment(body).strip()
        if body.startswith('?') and (len(body) == 1 or body[1].isspace()):
            self._refuse('explicit key', index, context)
        position = self._mapping_colon(body, index, context)
        key = body[:position].strip()
        if not key:
            self._refuse('empty mapping key', index, context)
        value = body[position + 1:].strip()
        return self._decode_scalar(key, index, f'{context} key'), value

    def _decode_scalar(self, text, index, context):
        value = self._strip_comment(text).strip()
        if not value:
            return ''
        self._reject_prefix(value, index, context)
        if value.startswith("'"):
            return self._single_quoted(value, index, context)
        if value.startswith('"'):
            return self._double_quoted(value, index, context)
        if re.search(r'(^|\s)&\S+', value):
            self._refuse('anchor', index, context)
        if re.search(r'(^|\s)\*\S+', value):
            self._refuse('alias', index, context)
        return value

    def _single_quoted(self, value, index, context):
        position = 1
        result = []
        while position < len(value):
            char = value[position]
            if char != "'":
                result.append(char)
                position += 1
                continue
            if position + 1 < len(value) and value[position + 1] == "'":
                result.append("'")
                position += 2
                continue
            tail = self._strip_comment(value[position + 1:]).strip()
            if tail:
                self._refuse('trailing text after single-quoted scalar',
                             index, context)
            return ''.join(result)
        self._refuse('unterminated single-quoted scalar', index, context)
        return None

    def _double_quoted(self, value, index, context):
        position = 1
        result = []
        while position < len(value):
            char = value[position]
            if char == '"':
                tail = self._strip_comment(value[position + 1:]).strip()
                if tail:
                    self._refuse('trailing text after double-quoted scalar',
                                 index, context)
                return ''.join(result)
            if char != '\\':
                result.append(char)
                position += 1
                continue
            if position + 1 >= len(value):
                self._refuse('unterminated double-quoted escape', index,
                             context)
            escaped = value[position + 1]
            if escaped in _DOUBLE_ESCAPES:
                result.append(_DOUBLE_ESCAPES[escaped])
                position += 2
                continue
            lengths = {'x': 2, 'u': 4, 'U': 8}
            if escaped in lengths:
                width = lengths[escaped]
                digits = value[position + 2:position + 2 + width]
                if len(digits) != width or not re.fullmatch(
                        r'[0-9A-Fa-f]+', digits):
                    self._refuse('invalid double-quoted escape', index,
                                 context)
                if int(digits, 16) > 0x10ffff:
                    self._refuse('invalid double-quoted escape', index,
                                 context)
                result.append(chr(int(digits, 16)))
                position += 2 + width
                continue
            if escaped in '\n\r':
                position += 2
                continue
            self._refuse('unknown double-quote escape', index, context)
        self._refuse('unterminated double-quoted scalar', index, context)
        return None

    def _next_nonblank(self, start, end, context):
        index = start
        while index < end:
            if not self._blank(index):
                self._indent(index, context)
                return index
            self._indent(index, context)
            index += 1
        return None

    def _value_end(self, start, end, parent_indent, context,
                   check_tabs=True):
        index = start
        while index < end:
            if self._blank(index):
                if check_tabs:
                    self._indent(index, context)
                index += 1
                continue
            indent = (self._indent(index, context) if check_tabs
                      else self._raw_indent(index)[0])
            if indent <= parent_indent:
                return index
            index += 1
        return end

    def _block_scalar(self, header, index, parent_indent, context):
        header = self._strip_comment(header).strip()
        match = re.fullmatch(r'([|>])([1-9]?)([+-]?)([1-9]?)', header)
        if not match or (match.group(2) and match.group(4)):
            self._refuse('unsupported block scalar header', index, context)
        style, first_indent, chomping, second_indent = match.groups()
        indent_indicator = first_indent or second_indent
        body_start = index + 1
        body_end = body_start
        while body_end < len(self.lines):
            if self._blank(body_end):
                self._indent(body_end, context)
                body_end += 1
                continue
            indent = self._indent(body_end, context)
            if indent <= parent_indent:
                break
            body_end += 1

        nonblank = [
            self._indent(line, context)
            for line in range(body_start, body_end)
            if not self._blank(line)
        ]
        content_indent = (parent_indent + int(indent_indicator)
                          if indent_indicator else
                          (min(nonblank) if nonblank else parent_indent + 1))
        if any(indent < content_indent for indent in nonblank):
            self._refuse('inconsistent block scalar indentation', index,
                         context)
        parts = []
        for line in range(body_start, body_end):
            raw = self.lines[line]
            if not raw.strip():
                parts.append('')
                continue
            parts.append(raw[content_indent:])
        if not parts:
            return ''
        if style == '|':
            text = '\n'.join(parts) + '\n'
        else:
            text = ''
            line = 0
            while line < len(parts):
                if parts[line]:
                    if line and parts[line - 1]:
                        text += ('\n' if parts[line - 1].startswith(' ')
                                 or parts[line].startswith(' ') else ' ')
                    text += parts[line]
                    line += 1
                    continue
                blank = line
                while line < len(parts) and not parts[line]:
                    line += 1
                text += '\n' * (line - blank)
            text += '\n'
        if chomping == '-':
            return text.rstrip('\n')
        if chomping == '+':
            return text
        return text.rstrip('\n') + '\n'

    def _plain_scalar(self, value, index, key_indent, value_end, context):
        pieces = [self._decode_scalar(value, index, context)]
        line = index + 1
        while line < value_end:
            if self._blank(line):
                self._indent(line, context)
                line += 1
                continue
            indent = self._indent(line, context)
            if indent <= key_indent:
                break
            continuation = self._strip_comment(self.lines[line].strip())
            if continuation:
                pieces.append(continuation)
            line += 1
        return ' '.join(pieces)

    def _scalar_value(self, value, index, key_indent, value_end, context):
        value = self._strip_comment(value).strip()
        if value.startswith('|') or value.startswith('>'):
            return self._block_scalar(value, index, key_indent, context)
        if value:
            self._reject_scalar_shape(value, index, context)
            self._reject_prefix(value, index, context)
            if value.startswith("'"):
                return self._single_quoted(value, index, context)
            if value.startswith('"'):
                return self._double_quoted(value, index, context)
            return self._plain_scalar(value, index, key_indent, value_end,
                                      context)
        nested = self._next_nonblank(index + 1, value_end, context)
        if nested is None:
            return ''
        indent = self._indent(nested, context)
        nested_value = self._strip_comment(self.lines[nested][indent:]).strip()
        if nested_value.startswith('|') or nested_value.startswith('>'):
            return self._block_scalar(nested_value, nested, indent, context)
        self._reject_scalar_shape(nested_value, nested, context)
        self._reject_prefix(nested_value, nested, context)
        if self._looks_like_mapping(nested_value):
            self._refuse('mapping where scalar was required', nested, context)
        return self._plain_scalar(nested_value, nested, indent, value_end,
                                  context)

    def _looks_like_mapping(self, body):
        try:
            self._mapping_colon(body, 0, 'scalar')
        except YAMLReadError:
            return False
        return True

    def _reject_root_quoted_scalar(self, value, index):
        value = self._strip_comment(value).strip()
        scalar = value
        while scalar.startswith(('&', '!')):
            parts = scalar.split(None, 1)
            if len(parts) == 1:
                return
            scalar = parts[1].lstrip()
            if scalar.startswith(("'", '"')):
                self._reject_prefix(value, index, 'workflow value')
        if scalar.startswith(("'", '"')) \
                and not self._looks_like_mapping(scalar):
            self._decode_scalar(scalar, index, 'workflow value')

    def _find_jobs(self):
        if self.jobs_line is None:
            self._refuse('top-level jobs mapping not found', 0, 'workflow')
        rest = self.jobs_rest
        if rest:
            self._reject_prefix(rest, self.jobs_line, 'jobs mapping')
            self._refuse('jobs value is not a block mapping',
                         self.jobs_line, 'jobs mapping')
        first = self._next_nonblank(self.jobs_line + 1, len(self.lines),
                                    'jobs mapping')
        if first is None:
            self._refuse('jobs value is not a block mapping',
                         self.jobs_line, 'jobs mapping')
        job_indent = self._indent(first, 'jobs mapping')
        if job_indent == 0:
            self._refuse('jobs value is not a block mapping',
                         self.jobs_line, 'jobs mapping')
        results = []
        index = self.jobs_line + 1
        while index < len(self.lines):
            if self._blank(index):
                self._indent(index, 'jobs mapping')
                index += 1
                continue
            indent = self._indent(index, 'jobs mapping')
            if indent == 0:
                break
            if indent != job_indent:
                self._refuse('inconsistent jobs mapping indentation', index,
                             'jobs mapping')
            body = self.lines[index][indent:]
            if body.startswith('-'):
                self._refuse('block sequence where jobs mapping was required',
                             index, 'jobs mapping')
            job, rest = self._mapping_parts(index, 'jobs mapping')
            if job == '<<':
                self._refuse('merge key', index, 'jobs mapping')
            end = self._value_end(index + 1, len(self.lines), job_indent,
                                  f'job {job}', check_tabs=False)
            if rest:
                self._reject_prefix(rest, index, f'job {job}')
                self._refuse('job value is not a block mapping', index,
                             f'job {job}')
            results.extend(self._job_steps(job, index + 1, end, job_indent))
            index = end
        return results

    def _job_steps(self, job, start, end, job_indent):
        first = self._next_nonblank(start, end, f'job {job}')
        if first is None:
            self._refuse('job value is not a block mapping', start,
                         f'job {job}')
        key_indent = self._indent(first, f'job {job}')
        results = []
        seen_steps = False
        index = first
        while index < end:
            if self._blank(index):
                self._indent(index, f'job {job}')
                index += 1
                continue
            indent = self._indent(index, f'job {job}')
            if indent <= job_indent:
                break
            if indent != key_indent:
                self._refuse('inconsistent job mapping indentation', index,
                             f'job {job}')
            key, rest = self._mapping_parts(index, f'job {job}')
            value_end = self._value_end(
                index + 1, end, key_indent, f'job {job}',
                check_tabs=key == 'steps')
            if key == '<<':
                self._refuse('merge key', index, f'job {job}')
            if key == 'steps':
                if seen_steps:
                    self._refuse('duplicate steps key', index, f'job {job}')
                seen_steps = True
                if not rest and value_end == index + 1:
                    next_key = self._next_nonblank(
                        value_end, end, f'job {job}')
                    if next_key is not None \
                            and self._indent(next_key, f'job {job}') \
                            == key_indent:
                        next_name, _ = self._mapping_parts(
                            next_key, f'job {job}')
                        if next_name == 'steps':
                            index = value_end
                            continue
                results.extend(self._steps(job, index, rest, value_end,
                                           key_indent))
            index = value_end
        return results

    def _steps(self, job, index, rest, end, key_indent):
        context = f'job {job} steps'
        rest = self._strip_comment(rest).strip()
        if rest:
            self._reject_prefix(rest, index, context)
            self._refuse('steps value is not a block sequence', index,
                         context)
        first = self._next_nonblank(index + 1, end, context)
        if first is None:
            self._refuse('steps value is not a block sequence', index,
                         context)
        step_indent = self._indent(first, context)
        if step_indent <= key_indent:
            self._refuse('steps value is not a block sequence', first,
                         context)
        if self.lines[first][step_indent:].startswith('['):
            self._refuse('flow sequence', first, context)
        if not self.lines[first][step_indent:].startswith('-'):
            self._refuse('steps value is not a block sequence', first,
                         context)
        results = []
        current = first
        while current < end:
            if self._blank(current):
                self._indent(current, context)
                current += 1
                continue
            indent = self._indent(current, context)
            if indent <= key_indent:
                break
            if indent != step_indent:
                self._refuse('inconsistent steps indentation', current,
                             context)
            body = self.lines[current][indent:]
            if not body.startswith('-'):
                self._refuse('step is not a mapping sequence item', current,
                             context)
            item_end = self._value_end(
                current + 1, end, step_indent, context, check_tabs=False)
            results.extend(self._step(job, current, item_end, step_indent))
            current = item_end
        return results

    def _step(self, job, start, end, step_indent):
        context = f'job {job} checkout step'
        body = self.lines[start][step_indent:]
        first = body[1:].strip() if body.startswith('-') else body
        if first.startswith('{'):
            self._refuse('flow mapping', start, context)
        if first.startswith('['):
            self._refuse('flow sequence', start, context)
        entries = []
        key_indent = None
        if first and not first.startswith('#'):
            key_indent = step_indent + 2
            key, rest = self._mapping_parts(start, context, first)
            if key == '<<':
                self._refuse('merge key', start, context)
            entries.append((key, rest, start, key_indent, end))
        index = start + 1
        while index < end:
            if self._blank(index):
                self._indent(index, context)
                index += 1
                continue
            indent = self._indent(index, context)
            if indent <= step_indent:
                break
            if key_indent is None:
                key_indent = indent
            if indent > key_indent:
                index = self._value_end(
                    index, end, key_indent, context, check_tabs=False)
                continue
            if indent < key_indent:
                self._refuse('inconsistent step mapping indentation', index,
                             context)
            key, rest = self._mapping_parts(index, context)
            value_end = self._value_end(
                index + 1, end, key_indent, context, check_tabs=False)
            if key == '<<':
                self._refuse('merge key', index, context)
            entries.append((key, rest, index, key_indent, value_end))
            index = value_end
        if not entries:
            self._refuse('step is not a mapping sequence item', start,
                         context)
        uses = None
        with_entry = None
        seen = set()
        for key, rest, line, entry_indent, value_end in entries:
            if key in ('uses', 'with'):
                if key in seen:
                    self._refuse(f'duplicate {key} key', line, context)
                seen.add(key)
            if key == 'uses':
                uses = self._scalar_value(rest, line, entry_indent, value_end,
                                          f'{context} uses')
            elif key == 'with':
                with_entry = (rest, line, entry_indent, value_end)
        if uses is None or not uses.startswith('actions/checkout@'):
            return []
        if with_entry is None:
            return []
        ref = self._with_ref(job, with_entry)
        return [] if ref is None else [(job, ref)]

    def _with_ref(self, job, entry):
        rest, index, key_indent, end = entry
        context = f'job {job} checkout with'
        rest = self._strip_comment(rest).strip()
        if rest:
            self._reject_prefix(rest, index, context)
            self._refuse('with value is not a block mapping', index, context)
        first = self._next_nonblank(index + 1, end, context)
        if first is None:
            return None
        map_indent = self._indent(first, context)
        if map_indent <= key_indent:
            return None
        ref = None
        seen_ref = False
        current = first
        while current < end:
            if self._blank(current):
                self._indent(current, context)
                current += 1
                continue
            indent = self._indent(current, context)
            if indent < map_indent:
                self._refuse('inconsistent with mapping indentation',
                             current, context)
            if indent > map_indent:
                current = self._value_end(
                    current, end, map_indent, context, check_tabs=False)
                continue
            key, value = self._mapping_parts(current, context)
            value_end = self._value_end(
                current + 1, end, map_indent, context, check_tabs=False)
            if key == '<<':
                self._refuse('merge key', current, context)
            if key == 'ref':
                if seen_ref:
                    self._refuse('duplicate ref key', current, context)
                seen_ref = True
                ref = self._scalar_value(value, current, map_indent,
                                         value_end, f'job {job} checkout ref')
            current = value_end
        return ref

    def _scan_document(self):
        started = False
        ended = False
        root_indent = None
        for index, line in enumerate(self.lines):
            stripped = line.strip()
            if not stripped or line.lstrip(' ').startswith('#'):
                continue
            prefix = line[:len(line) - len(line.lstrip(' \t'))]
            if '\t' in prefix:
                self._refuse('tab in indentation', index, 'workflow')
            marker = self._strip_comment(stripped)
            if not prefix and marker == '---':
                if started:
                    self._refuse('multiple YAML documents', index,
                                 'workflow')
                started = True
                continue
            if not prefix and marker == '...':
                ended = True
                continue
            if ended and not prefix:
                self._refuse('multiple YAML documents', index, 'workflow')
            started = True
            if root_indent is None:
                root_indent = len(prefix)
                if root_indent:
                    self._refuse('nonzero root mapping indentation', index,
                                 'workflow')
            if prefix:
                continue
            body = self._strip_comment(stripped)
            if body.startswith('?') and (len(body) == 1
                                         or body[1].isspace()):
                explicit_key = self._decode_scalar(
                    body[1:].strip(), index, 'jobs mapping explicit key')
                if explicit_key == 'jobs':
                    self._refuse('explicit key', index, 'jobs mapping')
                continue
            key, rest = self._mapping_parts(index, 'workflow', body)
            self._reject_root_quoted_scalar(rest, index)
            if not rest:
                nested = self._next_nonblank(
                    index + 1, len(self.lines), 'workflow value')
                if nested is not None:
                    indent = self._indent(nested, 'workflow value')
                    if indent > root_indent:
                        self._reject_root_quoted_scalar(
                            self.lines[nested][indent:], nested)
            if key != 'jobs':
                continue
            if self.jobs_line is not None:
                self._refuse('second top-level jobs mapping', index,
                             'workflow')
            self.jobs_line = index
            self.jobs_rest = rest

    def read(self):
        return self._find_jobs()


def checkout_refs(workflow):
    """Every ``actions/checkout`` step's ``with.ref``, as the runner
    receives it.

    This reader decodes only the path ``jobs`` → job → ``steps`` → step
    mapping → ``uses``/``with`` → ``with.ref``. It accepts block mappings and
    sequences, plain and quoted scalars, and folded or literal block scalars;
    unsupported flow structures, anchors, aliases, tags, explicit or merge
    keys, duplicate top-level ``jobs``, multiple documents, and tabs in read
    indentation raise :class:`YAMLReadError` rather than guessing.
    """
    return _WorkflowReader(workflow).read()
