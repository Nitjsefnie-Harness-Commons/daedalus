"""The tracked keys one removal takes back before the send.

`del cmd[k]`, `cmd.pop(k)` and `cmd.clear()` are one mechanism: a key the
program removes is not a tracked key at the send. A spelling that names a
key folds it through the payload model's own reader, so a name-bound
removal removes what a literal one removes.
"""
from _pyroute_keys import payload_literal_key


def drop_key(dicts, literals, owner, key=None):
    """Drop the tracked key `key` names, or every one when it is None.

    A cleared payload is empty rather than untracked: an empty tracked
    dict spreads nothing, where an untracked name is one the model cannot
    vouch for, which is the fail-closed answer a `clear` is not.
    """
    if key is None:
        if owner in dicts: dicts[owner] = {}
        return
    name = payload_literal_key(key, literals)
    tracked = dicts.get(owner)
    if name is not None and tracked is not None: tracked.pop(name, None)
