"""Shared JavaScript helpers for Node VM test harnesses.

`eventTarget` is duplicated in both harnesses as string literals; the
duplicate-code checker only compares parsed Python ASTs so JavaScript
embedded as strings is not caught. This module provides the shared
definitions that each harness imports and uses via string concatenation.
"""

# Simple eventTarget used by _mainworldharness.py.
SHARED_EVENT_TARGET = r"""
function eventTarget() {
  return { addListener() {} };
}
"""

# Hotfix-style eventTarget used by _hotfixharness.py.
# Accepts an optional listeners array parameter so the harness can
# pre-populate the listener list.
HOTFIX_EVENT_TARGET = r"""
function eventTarget(listeners) {
  return {
    addListener(listener) { if (listeners) listeners.push(listener); },
  };
}
"""
