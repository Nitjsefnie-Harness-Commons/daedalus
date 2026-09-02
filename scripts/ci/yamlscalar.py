"""Decode the bounded inline YAML scalar subset used by workflow policy.

The workflow readers built on this admit one subset and refuse the rest, and
a refusal is one of exactly two things.

Admitted: plain, single-quoted and double-quoted scalars, folded and literal
block scalars, block mappings and block sequences, and flow collections of
any of those. A value spans lines only where its entry point admits the
construct. Wrapped plain scalars fold in `job_scalar`, `job_mapping`,
`top_level_mapping`, `step_scalar` values, `step_mappings` values,
`workflow_mapping`, and `complete_job_mapping`. `step_scalars`,
`step_mapping_scalar`, nested scalar mappings under `step_mappings`, name and
id fields in `workflow_step_items`, and the step-name lookup used by
`step_scalar` and `step_mapping_scalar` refuse them. Folding joins with one
space, and each line of a flow collection contributes its own comment strip
because a comment between entries is content YAML skips; a comment ENDS a
plain scalar instead, so nothing continues one after it.
Equivalent spellings decode to the same value, so quoting a scalar or
rewriting a block collection in flow style changes nothing a reader sees.
Node properties are the one place the entry points diverge: a name or id in
`workflow_step_items` reads an anchor, the tags `!` and `!!str`, and an
alias resolved to the last definition of its anchor before that line, while
every other entry point refuses all three.

Refused as invalid YAML: an unterminated quote or bracket, an unquoted flow
scalar carrying any of `,[]{}`, an interior empty flow item, an empty mapping
key, a duplicate key, a tab in indentation, inconsistent indentation, a
malformed block-scalar header or escape, a malformed or repeated node
property, an alias naming no earlier anchor, and a control or surrogate
character.

Refused as a boundary of this subset, which every such message names with
the word `unsupported`: a tab inside a value, a multiline quoted scalar, a
blank line or a comment inside a plain one, nested content beside a value
that already closed, an anchor, an alias or a tag where the entry point
reads none, a tag not known to resolve to a string, an alias to a node this
reader decodes as no scalar, and a plain scalar outside the character set
the reader admits. A shape refusal ("... is not a mapping") names the shape
the reader requires instead.

One boundary is spelled as an invalid-YAML refusal and is written down here
instead. A field is sliced out by indentation before its brackets are read,
so a flow collection whose continuation is indented no further than its key
is cut in half and the decoder is handed something genuinely unbalanced.
Telling that apart from a collection that never closes needs the rest of the
document, which is the one thing a field slice does not have.

Deciding full YAML validity is not this module's job. `actionlint` is the
CI-side oracle for that and gates every workflow change.
"""
import re


class YAMLReadError(ValueError):
    """The requested YAML value is malformed or outside the reader's scope."""


_PLAIN_INLINE = re.compile(r'^[A-Za-z0-9_.][-A-Za-z0-9_.@/+$()]*$')
_DOUBLE_ESCAPES = {
    '0': '\0', 'a': '\a', 'b': '\b', 't': '\t', 'n': '\n',
    'v': '\v', 'f': '\f', 'r': '\r', 'e': '\x1b', ' ': ' ',
    '"': '"', '/': '/', '\\': '\\', 'N': '\x85', '_': '\xa0',
    'L': '\u2028', 'P': '\u2029',
}


def _unquoted(text, owner='', flow=False):
    """Yield `(index, char, depth)` for every character outside a quote.

    YAML opens a quoted node only at node start -- the start of the text, or
    after `,`, `[`, `{`, or a `: ` value indicator -- so a quote reached
    anywhere else is plain content and `[a, 5 o'clock]` is two items.  In
    `flow` mode the rest of the flow grammar is enforced with it: an
    unquoted flow scalar carries none of `,[]{}`, and every bracket closes.
    """
    quote = None
    depth = 0
    node_start = True
    index = 0
    while index < len(text):
        char = text[index]
        if quote == "'":
            if text[index:index + 2] == "''":
                index += 2
                continue
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == '\\':
                index += 2
                continue
            if char == '"':
                quote = None
            index += 1
            continue
        if node_start and char in ('"', "'"):
            quote = char
        elif char in '[{':
            if flow and not node_start:
                raise YAMLReadError(
                    f'{owner} has a flow indicator in a plain scalar')
            depth += 1
        elif char in ']}':
            depth -= 1
            if flow and depth < 0:
                raise YAMLReadError(
                    f'{owner} has an unbalanced flow collection')
        yield index, char, depth
        if char in '[{,':
            node_start = True
        elif char == ':' and text[index + 1:index + 2] in ('', ' ', '\t'):
            node_start = True
        elif char not in ' \t':
            node_start = False
        index += 1
    if flow and (quote is not None or depth):
        raise YAMLReadError(f'{owner} has an unbalanced flow collection')


def find_mapping_field(text, owner, allow_tabs=False):
    """Split one inline mapping field, or None when it carries no separator.

    Absence of a separator is the caller's question -- a flow entry may be a
    scalar rather than a single-pair mapping -- while a malformed field is
    this module's refusal either way.
    """
    if '\t' in text and not allow_tabs:
        raise YAMLReadError(f'{owner} contains an unsupported tab')
    separation = ' \t' if allow_tabs else ' '
    for index, char, _depth in _unquoted(text, owner):
        if char != ':' or text[index + 1:index + 2] not in ('', *separation):
            continue
        key = text[:index].strip(separation)
        if not key:
            raise YAMLReadError(f'{owner} has an empty mapping key')
        return key, text[index + 1:]
    return None


def split_mapping_field(text, owner, allow_tabs=False):
    """Split one inline mapping field without mistaking quoted colons."""
    field = find_mapping_field(text, owner, allow_tabs)
    if field is None:
        raise YAMLReadError(f'{owner} has an unsupported mapping field')
    return field


def _strip_inline_comment(value):
    """Remove one YAML comment outside quoted scalar content."""
    for index, char, _depth in _unquoted(value):
        if char == '#' and (index == 0 or value[index - 1] == ' '):
            return value[:index].rstrip(' ')
    return value.rstrip(' ')


def _decode_single_quoted(value, owner):
    if len(value) < 2 or not value.endswith("'"):
        raise YAMLReadError(f'{owner} has an incomplete quoted scalar')
    body = value[1:-1]
    decoded = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "'":
            if body[index:index + 2] != "''":
                raise YAMLReadError(
                    f'{owner} has an unsupported quoted scalar')
            decoded.append("'")
            index += 2
            continue
        if ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff:
            raise YAMLReadError(f'{owner} contains an unsupported character')
        decoded.append(char)
        index += 1
    return ''.join(decoded)


def _decode_double_quoted(value, owner):
    if len(value) < 2 or not value.endswith('"'):
        raise YAMLReadError(f'{owner} has an incomplete quoted scalar')
    body = value[1:-1]
    decoded = []
    index = 0
    widths = {'x': 2, 'u': 4, 'U': 8}
    while index < len(body):
        char = body[index]
        if char == '"':
            raise YAMLReadError(f'{owner} has an unsupported quoted scalar')
        if char != '\\':
            if ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff:
                raise YAMLReadError(
                    f'{owner} contains an unsupported character')
            decoded.append(char)
            index += 1
            continue
        if index + 1 == len(body):
            raise YAMLReadError(f'{owner} has an incomplete YAML escape')
        escaped = body[index + 1]
        if escaped in _DOUBLE_ESCAPES:
            decoded.append(_DOUBLE_ESCAPES[escaped])
            index += 2
            continue
        width = widths.get(escaped)
        digits = body[index + 2:index + 2 + (width or 0)]
        if (width is None or len(digits) != width
                or not all(char in '0123456789abcdefABCDEF'
                           for char in digits)):
            raise YAMLReadError(f'{owner} has an unsupported YAML escape')
        codepoint = int(digits, 16)
        if codepoint > 0x10ffff or 0xd800 <= codepoint <= 0xdfff:
            raise YAMLReadError(f'{owner} has an invalid Unicode escape')
        decoded.append(chr(codepoint))
        index += 2 + width
    return ''.join(decoded)


def decode_inline_scalar(value, owner):
    """Decode the bounded one-line scalar subset used by policy maps."""
    value = _strip_inline_comment(value.strip(' '))
    if not value:
        raise YAMLReadError(f'{owner} has no scalar value')
    if value.startswith("'"):
        return _decode_single_quoted(value, owner)
    if value.startswith('"'):
        return _decode_double_quoted(value, owner)
    if not _PLAIN_INLINE.fullmatch(value):
        raise YAMLReadError(f'{owner} has an unsupported plain scalar')
    return value
