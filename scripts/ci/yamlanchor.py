"""Read the node properties a workflow scalar may carry before its value.

Bare `!` is outside this bounded textual subset because honoring it requires
schema and tag resolution that this reader does not perform.  The
`yaml.BaseLoader` oracle still returns text for explicitly string-shaped
values; this module only recognizes the explicit ``!!str`` tag.
"""
import re

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .yamlscalar import YAMLReadError
else:
    from yamlscalar import YAMLReadError


_ALIAS = re.compile(r'\*(?P<name>[^\s\[\]{},]+)')
_ANCHOR_NAME = re.compile(r'[^\s\[\]{},]+')
_STRING_TAGS = ('!!str',)


def node_properties(value):
    """Split leading anchor and tag tokens off `value`, refusing nothing.

    A document-wide scan meets values this reader never decodes, so malformed
    properties are left for `validate_node_properties` to refuse.
    """
    tokens = []
    while value[:1] in ('&', '!'):
        end = 0
        while end < len(value) and value[end] not in ' \t':
            end += 1
        tokens.append(value[:end])
        value = value[end:].lstrip(' \t')
    return tokens, value


def validate_node_properties(tokens, owner):
    """Validate already-lexed anchor and tag tokens."""
    anchor = tag = None
    for token in tokens:
        if token.startswith('&'):
            if anchor is not None:
                raise YAMLReadError(f'{owner} has two YAML anchors')
            anchor = token[1:]
            if not _ANCHOR_NAME.fullmatch(anchor):
                raise YAMLReadError(
                    f'{owner} has a malformed YAML anchor: {token}')
        else:
            if tag is not None:
                raise YAMLReadError(f'{owner} has two YAML tags')
            tag = token
            if tag not in _STRING_TAGS:
                raise YAMLReadError(
                    f'{owner} has an unsupported YAML tag {tag}: only !!str '
                    'resolves to a string, and a guess at what a parser '
                    'makes of any other is worse than a refusal')


def parse_alias(value, owner):
    """Return a complete alias name, or refuse malformed alias text."""
    if not value.startswith('*'):
        return None
    match = _ALIAS.fullmatch(value)
    if match is None:
        raise YAMLReadError(f'{owner} has a malformed YAML alias')
    return match.group('name')


def has_anchor_spelling(value, name):
    """Return whether `value` contains the exact spelling ``&name``."""
    spelling = f'&{name}'
    for index in range(len(value) - len(spelling) + 1):
        if value[index:index + len(spelling)] != spelling:
            continue
        before = value[index - 1:index]
        after = value[index + len(spelling):index + len(spelling) + 1]
        if _is_property_boundary(before) and _is_property_boundary(after):
            return True
    return False


def _is_property_boundary(value):
    return not value or value.isspace() or value in '[] {},'
