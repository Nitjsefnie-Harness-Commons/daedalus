"""Compatibility imports for the shared workflow scalar decoder."""
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
from yamlscalar import (  # noqa: E402
    YAMLReadError,
    _strip_inline_comment,
    decode_inline_scalar,
    split_mapping_field,
)


__all__ = (
    'YAMLReadError',
    '_strip_inline_comment',
    'decode_inline_scalar',
    'split_mapping_field',
)
