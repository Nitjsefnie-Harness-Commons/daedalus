#!/usr/bin/env python3
"""Candidate program text cannot alter runtime probe grammar."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _worker_runtime  # noqa: E402


def _assert_candidate_does_not_enter_program(tmp, candidate, marker):
    root = Path(tmp)
    background = root / 'background.js'
    background.write_text('const backgroundMarker = true;\n',
                          encoding='utf-8')
    worker = root / 'property.js'
    worker.write_text(
        'Object.defineProperty(globalThis, '
        f'{json.dumps(candidate)}, {{ configurable: true, value: 1 }});\n',
        encoding='utf-8')

    observed = _worker_runtime.observe_worker_runtime([{
        'path': worker,
        'globals': (),
        'probes': {marker},
        'watched': (),
    }], background_path=background)['sources'][str(worker)]

    assert observed['bindingExecutionError'] is None
    assert observed['bindings'] == []


def test_candidate_program_text_does_not_enter_probe(tmp):
    """Statements, closing braces and newlines cannot alter probe grammar."""
    candidates = (
        ('undefined; { globalThis.taskThreeInjected = 1; }',
         'taskThreeInjected'),
        ('undefined; } globalThis.taskThreeBraceInjected = 1; {',
         'taskThreeBraceInjected'),
        ('undefined;\nglobalThis.taskThreeNewlineInjected = 1',
         'taskThreeNewlineInjected'),
    )
    for candidate, marker in candidates:
        _assert_candidate_does_not_enter_program(tmp, candidate, marker)


def main():
    return _util.runner(_util.collect(globals()))


if __name__ == '__main__':
    raise SystemExit(main())
