"""JSON body parsing for the bridge: the depth bound and the carrier record.

`json_nests_deeper_than` settles the depth question on the raw bytes before
`json.loads` builds anything, and `JSONObject` remembers whether an
authority carrier arrived twice. Body parsing rather than transport, so
this module binds no bridge configuration.
"""
from daedalus_cli import ambiguous_request_carrier


def json_nests_deeper_than(raw, limit):
    """True when `raw` opens more than `limit` unclosed containers at once.

    A scan of the bytes rather than a parse: the answer has to be settled
    before json.loads builds anything, and before the interpreter's own
    recursion limit gets to decide it — which it did, differently per version.

    Bytes rather than text, so a hostile body is never decoded to be measured.
    Only ASCII structure counts, and a UTF-8 continuation byte is never an
    ASCII byte, so a multi-byte character cannot be mistaken for a brace. The
    string state matters for the same reason: a `{` inside a string literal
    opens nothing, and a `\\"` inside one does not close it.

    Malformed input is not this function's problem — a body with more closers
    than openers drives the count negative and json.loads rejects it on its
    own terms. This answers one question only.
    """
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:      # backslash
                escaped = True
            elif byte == 0x22:      # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            if depth > limit:
                return True
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
    return False


class JSONObject(dict):
    """A parsed JSON object that remembers whether a key arrived twice.

    Duplicate keys collapse when the pairs become a dict, so the carrier says
    something the items no longer can. Equality has to include it for the same
    reason: two bodies with identical items are not the same request when one
    of them named an authority carrier twice, and inherited dict equality
    would call them equal. Comparison against a plain dict is unchanged.

    `__ne__` is overridden alongside `__eq__` for the same reason: `dict`
    defines its own `__ne__`, so without this `!=` reports plain dict
    inequality and misses the repeated carrier `==` exists to catch.
    `__eq__` can return `NotImplemented`, which is truthy, so `__ne__`
    checks for it rather than negating blindly.
    """

    def __init__(self, pairs):
        super().__init__(pairs)
        self.duplicate_carrier = ambiguous_request_carrier(
            key for key, _value in pairs)

    def __eq__(self, other):
        if isinstance(other, JSONObject):
            return (super().__eq__(other)
                    and self.duplicate_carrier == other.duplicate_carrier)
        return super().__eq__(other)

    def __ne__(self, other):
        equal = self.__eq__(other)
        if equal is NotImplemented:
            return equal
        return not equal
