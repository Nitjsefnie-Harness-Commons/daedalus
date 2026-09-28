"""The injectable sharing violation behind the collector-TTL handshake.

The handshake is the only place in the tree where one process publishes a file
and another polls for it, which is why a handle neither side owns can refuse an
operation on it there and nowhere else. These are the marker's names and the
two injectors that plant that refusal at the real filesystem operation, in the
test process and in the bridge child alike, so a control can be shown to meet
one rather than assumed to.
"""
import contextlib
import os
from pathlib import Path

import _util

_GC_TRIGGER = '.gc-trigger'
_GC_DONE = '.gc-done'
_GC_DONE_TEMP = '.gc-done.tmp'
_GC_PREFIX = '.gc-'
# Deliberately outside the prefix, so a refusal the injector raises can still
# be logged and the injector cannot refuse its own bookkeeping.
_GC_REFUSED_PARENT = 'gc-refused-parent.txt'
_GC_REFUSED_CHILD = 'gc-refused-child.txt'

# No queue or command name in this tree begins with the marker prefix -- they
# are `<token>_<tab>` and `<token>.json` -- so filtering on it cannot hide a
# real entry, and it covers whatever state a sweep leaves beside the record.


def _child_refusal_source(attempts):
    """The child's copy of the injector, empty unless a count is asked for.

    Composed apart from the base so a zero-attempt fixture writes the bytes
    it always did. `os` is imported here rather than in the base because
    only the patched `os.replace` needs it. The log name carries no marker
    prefix, so a refusal can still be logged and the injector cannot refuse
    its own bookkeeping.
    """
    if not attempts:
        return ''
    return (
        'import os\n'
        f'_ATTEMPTS = {attempts!r}\n'
        f'_PREFIX = {_GC_PREFIX!r}\n'
        f'_LOGNAME = {_GC_REFUSED_CHILD!r}\n'
        '_spent = {}\n'
        '_real = {"replace": os.replace,\n'
        '         "unlink": pathlib.Path.unlink,\n'
        '         "read_text": pathlib.Path.read_text,\n'
        '         "write_text": pathlib.Path.write_text}\n'
        'def _refuse(op, target):\n'
        '    _spent[op] = _spent.get(op, 0) + 1\n'
        '    if _spent[op] > _ATTEMPTS:\n'
        '        return\n'
        '    name = os.path.basename(target)\n'
        '    try:\n'
        '        log = os.path.join(os.path.dirname(target), _LOGNAME)\n'
        '        with open(log, "a", encoding="utf-8") as _handle:\n'
        '            _handle.write(op + " " + name + "\\n")\n'
        '    except OSError:\n'
        '        pass\n'
        '    raise PermissionError(13, "Permission denied", target)\n'
        'def _wrap(op, real_call, name_of):\n'
        '    def call(*args, **kwargs):\n'
        '        target = name_of(args, kwargs)\n'
        '        if (isinstance(target, str)\n'
        '                and os.path.basename(target).startswith(_PREFIX)):\n'
        '            _refuse(op, target)\n'
        '        return real_call(*args, **kwargs)\n'
        '    return call\n'
        'os.replace = _wrap("replace", _real["replace"],\n'
        '                   lambda a, k: str(a[1] if len(a) > 1 else\n'
        '                                        k.get("dst", "")))\n'
        'pathlib.Path.unlink = _wrap("unlink", _real["unlink"],\n'
        '                            lambda a, k: str(a[0]))\n'
        'pathlib.Path.read_text = _wrap("read_text", _real["read_text"],\n'
        '                               lambda a, k: str(a[0]))\n'
        'pathlib.Path.write_text = _wrap("write_text", _real["write_text"],\n'
        '                                lambda a, k: str(a[0]))\n')


def _on_demand_command_gc(fault_dir, refusals=0):
    """Install a collector the test sweeps on demand, and return its path.

    A collector on a wall clock spends the TTL while the test is still
    setting itself up, so what an assertion finds removed is partly a
    measure of how long setup took. The record each sweep leaves is taken
    by name and not by kind: a namespace the sweep left where a queue
    directory was is a leftover, and a directory filter would drop it from
    the record that has to show it.
    """
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        'import sys\n'
        'import time\n'
        f'sys.path.insert(0, {str(_util.ROOT)!r})\n'
        'from daedalus_bridge import atomic_file\n'
        'from daedalus_bridge import command_queue\n'
        'def gc_loop(cmd_dir, ttl):\n'
        '    root = pathlib.Path(cmd_dir)\n'
        f'    trigger = root / "{_GC_TRIGGER}"\n'
        f'    done = root / "{_GC_DONE}"\n'
        f'    temp = root / "{_GC_DONE_TEMP}"\n'
        '    while True:\n'
        '        while not trigger.exists():\n'
        '            time.sleep(0.01)\n'
        '        atomic_file.unlink_retrying(trigger)\n'
        '        command_queue.collect_expired(cmd_dir, ttl)\n'
        f'        left = sorted(p.name for p in root.iterdir()\n'
        f'                      if not p.name.startswith("{_GC_PREFIX}"))\n'
        '        atomic_file.write_text_retrying(\n'
        '            temp, "\\n".join(left), encoding="utf-8")\n'
        '        atomic_file.replace_atomically(temp, done)\n'
        + _child_refusal_source(refusals)
        + 'command_queue.gc_loop = gc_loop\n',
        encoding='utf-8')
    return str(fault_dir)


@contextlib.contextmanager
def _refuse_marker_operations(command_root, attempts):
    """Refuse the marker's own filesystem operations, `attempts` times each.

    The refusal comes out of `os.replace`, `Path.unlink`, `Path.read_text`
    and `Path.write_text` themselves rather than out of a stand-in, so what
    the handshake meets is the refusal the bridge is meant to survive. Each
    operation counts separately, and a path outside the marker prefix goes
    straight through, so nothing else in the tree sees a fault.
    """
    real = {
        'replace': os.replace,
        'unlink': Path.unlink,
        'read_text': Path.read_text,
        'write_text': Path.write_text,
    }
    spent = {}
    log = command_root / _GC_REFUSED_PARENT

    def refuse(operation, target):
        spent[operation] = spent.get(operation, 0) + 1
        if spent[operation] > attempts:
            return False
        name = os.path.basename(target)
        try:
            with log.open('a', encoding='utf-8') as handle:
                handle.write(f'{operation} {name}\n')
        except OSError:
            pass  # a lost log line must not mask the refusal itself
        raise PermissionError(13, 'Permission denied', target)

    def patched(operation, real_call, target_of):
        def call(*args, **kwargs):
            target = target_of(args, kwargs)
            if isinstance(target, str) and os.path.basename(
                    target).startswith(_GC_PREFIX):
                refuse(operation, target)
            return real_call(*args, **kwargs)
        return call

    os.replace = patched(
        'replace', real['replace'],
        lambda args, kwargs: str(
            args[1] if len(args) > 1 else kwargs.get('dst', '')))
    Path.unlink = patched('unlink', real['unlink'],
                          lambda args, kwargs: str(args[0]))
    Path.read_text = patched('read_text', real['read_text'],
                             lambda args, kwargs: str(args[0]))
    Path.write_text = patched('write_text', real['write_text'],
                              lambda args, kwargs: str(args[0]))
    try:
        yield log
    finally:
        os.replace = real['replace']
        Path.unlink = real['unlink']
        Path.read_text = real['read_text']
        Path.write_text = real['write_text']
