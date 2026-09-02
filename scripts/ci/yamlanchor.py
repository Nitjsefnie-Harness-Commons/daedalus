"""Read the node properties a workflow scalar may carry before its value.

An anchor, a tag, or both in either order may precede the content.
"""
import re

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .yamlscalar import YAMLReadError, _strip_inline_comment
else:
    from yamlscalar import YAMLReadError, _strip_inline_comment


ALIAS = re.compile(r'\*(?P<name>[^\s\[\]{},]+)')
_ANCHOR_NAME = re.compile(r'[^\s\[\]{},]+')
_STRING_TAGS = ('!', '!!str')


def node_properties(value):
    """Split leading anchor and tag tokens off `value`, refusing nothing.

    A document-wide scan meets values this reader never decodes, so a
    malformed property is `strip_node_properties`'s refusal to make.
    """
    tokens = []
    while value[:1] in ('&', '!'):
        end = 0
        while end < len(value) and value[end] not in ' \t':
            end += 1
        tokens.append(value[:end])
        value = value[end:].lstrip(' \t')
    return tokens, value


def strip_node_properties(value, owner):
    """The content of `value` after its properties and one inline comment.

    The properties come off before the comment is read, so a quote that
    opens after an anchor or a tag keeps its `#` text.
    """
    tokens, content = node_properties(value)
    content = _strip_inline_comment(content.strip(' \t'))
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
                    f'{owner} has an unsupported YAML tag {tag}: only ! '
                    'and !!str resolve to a string, and a guess at what a '
                    'parser makes of any other is worse than a refusal')
    return content
