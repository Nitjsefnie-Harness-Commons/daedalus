#!/usr/bin/env python3
"""Generated programs the JavaScript tab-routing guard must not pass.

Node says whether the send routed and the guard says whether it
reported. Over-reporting is allowed, so the invariant is one-sided:
whenever the runtime routed, the guard reported.
"""
import os
import random
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _jsroute_sweep as sweep  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
from _jsroute_sweep_grammar import (BODIES, CONTAINERS,  # noqa: E402
                                    MEMBERS, key_forms)

# Two seeds: one alone re-confirms the sample the guard was tuned on.
SEEDS = (20260902, 7)
SAMPLE = 350

# The axes the whole-corpus sweep found the guard weakest on.
HEAVY_CONTAINERS = ('iife', 'class-inline-new', 'destructured-copy',
                    'proxy-wrapped')
HEAVY_ROUTES = ('value-paren-call', 'nested-destructured-param',
                'proxy-then-call', 'destructured-default',
                'paren-receiver-value')
HEAVY_BODIES = ('member-write-computed', 'alias', 'alias-chain',
                'helper-fn', 'member-write')
HEAVY_MEMBERS = ('method', 'getter', 'setter')


def _by_label(items, wanted):
    chosen = {item.label: item for item in items}
    return [chosen[label] for label in wanted]


def _heavy_product():
    axes = product(_by_label(CONTAINERS, HEAVY_CONTAINERS),
                   _by_label(sweep.ROUTES, HEAVY_ROUTES),
                   _by_label(BODIES, HEAVY_BODIES),
                   _by_label(MEMBERS, HEAVY_MEMBERS),
                   sweep.DIRECTIONS)
    found = []
    for container, route, body, member, direction in axes:
        name = 'hook' if member.trigger in ('read', 'write') else 'go'
        point = sweep.Point(container, member, key_forms(name)[0], route,
                            body, 'top-level', 'before', direction)
        if sweep.compatible(point):
            found.append(point)
    return found


def _sampled_points():
    chosen = []
    for seed in SEEDS:
        points = sweep.sample_points(seed, SAMPLE)
        chosen += random.Random(seed).sample(points, SAMPLE)
    return chosen + _heavy_product()


def _verdicts(path):
    node = shutil.which('node')
    assert node, 'node is required to execute JavaScript routing controls'
    ran = subprocess.run([node, str(path)], capture_output=True,
                         text=True, timeout=60)
    if ran.returncode != 0:
        return 'threw', ran.stderr.strip().split('\n')[0]
    return ran.stdout == '1', bool(js_tab_routing_violations(
        path, path.name))


def _run_all(tmp, points):
    """Every point's pair of verdicts, in order."""
    paths = []
    for index, point in enumerate(points):
        path = Path(tmp) / f'case_{index:05d}.js'
        path.write_text(sweep.program(point), encoding='utf-8')
        paths.append(path)
    workers = min(os.cpu_count() or 1, 8)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_verdicts, paths, chunksize=8))


def _families(points, verdicts):
    """False greens and runtime errors, by grammar family."""
    found = {}
    for point, (routed, reported) in zip(points, verdicts):
        if routed == 'threw':
            found.setdefault(('threw',) + point.family(), []).append(
                (point, reported))
        elif routed and not reported:
            found.setdefault(point.family(), []).append((point, None))
    return found


def _report(found):
    lines = [f'{len(found)} family/families the runtime routed past '
             'the guard:']
    for family, rows in sorted(found.items()):
        point, detail = rows[0]
        detail = '' if detail is None else ' ' + str(detail)
        lines.append(f"  {' | '.join(family)} ({len(rows)}){detail}")
        lines.append('    ' + sweep.render(point).replace('\n', ' '))
    return '\n'.join(lines)


def test_generated_programs_never_route_past_the_guard(tmp):
    assert shutil.which('node'), 'node runs the generated programs'
    points = _sampled_points()
    found = _families(points, _run_all(tmp, points))
    assert not found, _report(found)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jssweep_')


if __name__ == '__main__':
    raise SystemExit(main())
