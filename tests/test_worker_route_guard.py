#!/usr/bin/env python3
"""The classic worker route probe's handler-mutation controls."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary import run_extension_capability_routes  # noqa: E402
from _repo import ROOT  # noqa: E402


def _background_with_route(tmp, route, mutated):
    extension = Path(tmp) / 'extension'
    shutil.copytree(ROOT / 'extension', extension)
    background_path = extension / 'background.js'
    background = background_path.read_text(encoding='utf-8')
    assert background.count(route) == 1
    background_path.write_text(
        background.replace(route, mutated), encoding='utf-8')
    return background_path


def test_route_reports_a_cross_module_handler_write(tmp):
    """A module batch monitors handlers owned by every loaded module."""
    route = "    case 'block-requests': return handleBlockRequests(cmd);"
    mutated = """    case 'block-requests':
      handleCookies = function corruptedCookies() { return false; };
      return handleBlockRequests(cmd);"""
    background = _background_with_route(tmp, route, mutated)

    observations = run_extension_capability_routes([{
        'symbol': 'handleBlockRequests',
        'publishedSymbols': ['handleBlockRequests', 'handleCookies'],
        'command': {'id': 'cross-module', 'type': 'block-requests'},
    }], background_path=background)

    assert observations[0].get('mutatedSymbols') == [
        'handleCookies',
    ], observations


def test_route_reports_a_transient_handler_write(tmp):
    """Restoring a sibling before return does not erase its write."""
    route = "    case 'block-requests': return handleBlockRequests(cmd);"
    mutated = """    case 'block-requests': {
      const originalUnblock = handleUnblockRequests;
      handleUnblockRequests = function corruptedUnblock() { return false; };
      handleUnblockRequests = originalUnblock;
      return handleBlockRequests(cmd);
    }"""
    background = _background_with_route(tmp, route, mutated)

    observations = run_extension_capability_routes([{
        'symbol': 'handleBlockRequests',
        'publishedSymbols': [
            'handleBlockRequests', 'handleUnblockRequests',
        ],
        'command': {'id': 'transient', 'type': 'block-requests'},
    }], background_path=background)

    assert observations[0].get('mutatedSymbols') == [
        'handleUnblockRequests',
    ], observations


def test_route_reports_a_descriptor_replacement(tmp):
    """Replacing the accessor descriptor still mutates the sibling."""
    route = "    case 'block-requests': return handleBlockRequests(cmd);"
    mutated = """    case 'block-requests':
      Object.defineProperty(globalThis, 'handleUnblockRequests', {
        value: function corruptedUnblock() { return false; },
        writable: true, enumerable: true, configurable: true,
      });
      return handleBlockRequests(cmd);"""
    background = _background_with_route(tmp, route, mutated)

    observations = run_extension_capability_routes([{
        'symbol': 'handleBlockRequests',
        'publishedSymbols': [
            'handleBlockRequests', 'handleUnblockRequests',
        ],
        'command': {'id': 'descriptor', 'type': 'block-requests'},
    }], background_path=background)

    assert observations[0].get('mutatedSymbols') == [
        'handleUnblockRequests',
    ], observations


def test_route_reports_delete_then_recreate(tmp):
    """Deleting an accessor cannot erase the sibling mutation."""
    route = "    case 'block-requests': return handleBlockRequests(cmd);"
    mutated = """    case 'block-requests': {
      delete globalThis.handleUnblockRequests;
      globalThis.handleUnblockRequests = function corruptedUnblock() {
        return false;
      };
      return handleBlockRequests(cmd);
    }"""
    background = _background_with_route(tmp, route, mutated)

    observations = run_extension_capability_routes([{
        'symbol': 'handleBlockRequests',
        'publishedSymbols': [
            'handleBlockRequests', 'handleUnblockRequests',
        ],
        'command': {'id': 'delete-recreate', 'type': 'block-requests'},
    }], background_path=background)

    assert observations[0].get('mutatedSymbols') == [
        'handleUnblockRequests',
    ], observations


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
