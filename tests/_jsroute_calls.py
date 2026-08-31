"""Callable parameters and member-call targets for the JavaScript guard."""
import re


def parameter_bindings(mask, text, start, end, pair_end, top_level,
                       split_top_level):
    """Return lexical names and call-order targets for a parameter list."""
    def binding_names(left, right):
        while left < right and mask[left].isspace():
            left += 1
        while right > left and mask[right - 1].isspace():
            right -= 1
        if mask[left:left + 3] == '...':
            return binding_names(left + 3, right)
        value = mask[left:right]
        if re.fullmatch(r'[\w$]+', value):
            return (value,)
        if not value or value[0] not in '({[':
            return ()
        opening = left
        if value[0] == '(':
            opening += 1
            while opening < right and mask[opening].isspace():
                opening += 1
        if mask[opening:opening + 1] not in '{[':
            return ()
        closing = pair_end.get(opening, right) - 1
        found = []
        for item_start, item_end in split_top_level(
                mask, text, opening + 1, closing):
            equals = top_level(mask, item_start, item_end, '=')
            item_end = item_end if equals is None else equals
            if mask[opening] == '{':
                colon = top_level(mask, item_start, item_end, ':')
                if colon is not None:
                    item_start = colon + 1
            found.extend(binding_names(item_start, item_end))
        return tuple(found)

    names = set()
    order = []
    for left, right in split_top_level(mask, text, start, end):
        equals = top_level(mask, left, right, '=')
        right = right if equals is None else equals
        targets = binding_names(left, right)
        names.update(targets)
        order.append(targets)
    return names, order


def known_member_binding(match, mask, text, bindings, pair_end,
                         visible_binding, split_top_level):
    """Resolve an identifier-valued object property invoked as a method."""
    prefix = mask[:match.start()].rstrip()
    owner_match = re.search(r'([\w$]+)\s*\.\s*$', prefix)
    if owner_match is None:
        return None
    owner = visible_binding(owner_match.group(1), match.start())
    for assignment in reversed(bindings):
        if assignment.start() >= match.start():
            continue
        if visible_binding(assignment.group(1), assignment.start()) != owner:
            continue
        value_start = assignment.end()
        while value_start < len(mask) and mask[value_start].isspace():
            value_start += 1
        if mask[value_start:value_start + 1] != '{':
            return None
        value_end = pair_end.get(value_start, value_start + 1) - 1
        for left, right in split_top_level(
                mask, text, value_start + 1, value_end):
            entry = mask[left:right].strip()
            name = re.escape(match.group(1))
            shorthand = re.fullmatch(name, entry)
            explicit = re.fullmatch(name + r'\s*:\s*([\w$]+)', entry)
            if shorthand or explicit:
                source = (match.group(1) if shorthand
                          else explicit.group(1))
                return visible_binding(source, left)
        return None
    return None
