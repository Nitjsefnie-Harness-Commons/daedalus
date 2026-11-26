#!/usr/bin/env python3
"""Executable contracts for the CI scripts' package imports."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ci_imports import (  # noqa: E402
    package_closure, package_edges, package_selectors)
from _repo import ROOT  # noqa: E402

sys.path[:0] = [str(ROOT), str(ROOT / 'scripts' / 'ci')]


PROMOTED_MODULES = (
    'scripts.ci.js_module_coverage',
    'scripts.ci.line_lengths',
    'scripts.ci.ratchet_push',
    'scripts.ci.release_dispatch',
    'scripts.ci.thresholds',
    'scripts.ci.thresholds_guard',
    'scripts.ci.type_error_baseline',
    'scripts.ci.workflow_yaml',
)


def test_promoted_modules_import_by_package(tmp):
    del tmp
    command = '; '.join(f'import {name}' for name in PROMOTED_MODULES)
    imported = subprocess.run(
        [sys.executable, '-c', command], cwd=str(ROOT), capture_output=True,
        text=True, timeout=60)
    assert imported.returncode == 0, (imported.stdout, imported.stderr)
    assert all(__import__(name, fromlist=['*'])
               for name in PROMOTED_MODULES)


def test_every_package_selector_is_reachable_by_a_package_import(_tmp):
    """A `__package__:` half nothing reaches is the half CI never runs."""
    selectors = package_selectors(ROOT)
    assert selectors, 'nothing selects on __package__; this cannot be true'
    reached = package_closure(PROMOTED_MODULES, package_edges(ROOT))
    orphaned = sorted(set(selectors) - reached)
    assert not orphaned, (
        f'these select an import on __package__ and no package import '
        f'reaches them, so that half runs nowhere: {orphaned}')
