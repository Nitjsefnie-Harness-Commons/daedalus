"""Read leading anchor and tag properties on workflow scalars.

Only `!!str` is supported. Bare `!` requires schema/tag resolution outside
this reader, even where `yaml.BaseLoader` could stringify the value.
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
    """Leave malformed properties for `validate_node_properties`.

    The document-wide scan also encounters values this reader never decodes.
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
                    f'{owner} has an unsupported YAML tag {tag}: this reader '
                    'only accepts !!str; other tag resolution is outside its '
                    'bounded grammar')


def parse_alias(value, owner):
    if not value.startswith('*'):
        return None
    match = _ALIAS.fullmatch(value)
    if match is None:
        raise YAMLReadError(f'{owner} has a malformed YAML alias')
    return match.group('name')


def has_anchor_spelling(value, name):
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
