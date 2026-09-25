"""The finding half of the Python tab-routing guard: what one call routes.

Its own module so the flow module, which sits exactly at its size ceiling,
keeps the lines the flow needs. Nothing here reads or writes flow state; the
call, the tracked payload keys and the resolved sender are the whole input.
"""
import ast

from _pyroute_state import (OPAQUE_TAB_SPREAD, UNPROVABLE_SENDER,
                            is_extension_constant, payload_keys)


def _through_unprovable(node, dicts, rel, func):
    """What a call whose callee may be ext_cmd must not be allowed to hide."""
    callee = func.id if isinstance(func, ast.Name) else ast.unparse(func)
    found = []
    for kw in node.keywords:
        if kw.arg == 'tab' and not is_extension_constant(kw.value):
            found.append(f'{rel}:{kw.value.lineno}: `tab` passed through '
                         f'`{callee}`, which may be ext_cmd')
        elif kw.arg is None:
            keys = payload_keys(kw.value, dicts)
            if keys is None or OPAQUE_TAB_SPREAD in keys:
                found.append(f'{rel}:{kw.value.lineno}: opaque spread '
                             f'through `{callee}`, which may be ext_cmd')
            elif ('tab' in keys
                    and not is_extension_constant(keys['tab'][1])):
                found.append(f'{rel}:{keys["tab"][0]}: `tab` passed '
                             f'through `{callee}`, which may be ext_cmd')
    return found


def _through_ext_cmd(node, dicts, rel):
    """What a call that IS ext_cmd must not be allowed to route."""
    found = []
    for kw in node.keywords:
        if kw.arg == 'tab' and not is_extension_constant(kw.value):
            found.append(f'{rel}:{kw.value.lineno}: ext_cmd keyword `tab`')
        elif kw.arg is None:
            keys = payload_keys(kw.value, dicts)
            if keys is None or OPAQUE_TAB_SPREAD in keys:
                found.append(f'{rel}:{kw.value.lineno}: opaque **'
                             f'{ast.unparse(kw.value)} passed to ext_cmd; '
                             '`tab` cannot be verified')
            elif 'tab' in keys:
                lineno, value = keys['tab']
                if not is_extension_constant(value):
                    found.append(f'{rel}:{lineno}: `tab` in **'
                                 f'{ast.unparse(kw.value)} passed to ext_cmd')
    return found


def _typed_payload(node, dicts, rel, allowed_opaque_names):
    """What a typed `/command` payload must not be able to do to `tab`."""
    cmd_at = next((i for i, a in enumerate(node.args) if isinstance(
        a, ast.Constant) and a.value == '/command'), None)
    if cmd_at is None or cmd_at + 1 >= len(node.args): return []
    keys = payload_keys(node.args[cmd_at + 1], dicts)
    if keys and 'type' in keys and OPAQUE_TAB_SPREAD in keys:
        lineno, spread = keys[OPAQUE_TAB_SPREAD]
        if getattr(spread, 'id', None) not in allowed_opaque_names:
            return [f'{rel}:{lineno}: opaque spread may replace `tab` on a '
                    'typed /command payload']
    if keys and 'type' in keys and 'tab' in keys:
        lineno, value = keys['tab']
        if not is_extension_constant(value):
            return [f'{rel}:{lineno}: `tab` on a typed /command payload']
    return []


def call_violations(node, dicts, rel, allowed_opaque_names=frozenset(),
                    sender_name=None):
    """Every way this one call routes a `tab` the guard cannot verify."""
    if sender_name == UNPROVABLE_SENDER:
        return _through_unprovable(node, dicts, rel, node.func)
    if sender_name in ('ext_cmd', '_ext_cmd'):
        return _through_ext_cmd(node, dicts, rel)
    return _typed_payload(node, dicts, rel, allowed_opaque_names)
