"""The sitecustomize directory one injected write refusal rides in.

Both retry suites inject the refusal through the same channel: a
`sitecustomize.py` on the child's `PYTHONPATH` that wraps one write. The
prelude the injected body sits on is the suite's own and travels with it,
so it is an argument: the result suite's prelude opens the marker the
injected body records into, and the segment suite's has no marker at all.
"""
from pathlib import Path


def _write_fault_dir(tmp, body, header):
    fault_dir = Path(tmp) / 'fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        header + body, encoding='utf-8')
    return fault_dir
