#!/usr/bin/env python3
"""Bound-count metadata validation of the dashboard Node harness sources.

A shipped harness declares its bounded step count and module mode; the
`DashboardNodeHarness` constructor refuses a source whose declared count
disagrees with the bounds its source really performs, or a shape it cannot
inspect. These cases pin those refusals and the shipped metadata.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
import test_dashboard_accessibility as accessibility  # noqa: E402
import test_dashboard_behaviour as behaviour  # noqa: E402


_SLASH_REFUSAL = (
    'dashboard harness bound count cannot inspect slash token in dashboard '
    'harness source; slash tokens include division and regex literals, so '
    'hoist the expression out of the harness source or restructure it')


def _metadata_failure(source, bounded_steps):
    try:
        _dashnode.DashboardNodeHarness(
            source, bounded_steps=bounded_steps, module=True)
    except ValueError as failure:
        return str(failure)
    raise AssertionError('invalid dashboard harness was accepted')


def test_shipped_harnesses_pin_their_process_metadata(tmp):
    """Each shipped source carries its validated timeout and module mode."""
    del tmp
    expected = {
        'content': (behaviour._CONTENT_KEEPALIVE_HARNESS, 0, False),
        'consume': (behaviour._DASHBOARD_CONSUME_HARNESS, 2, False),
        'world': (behaviour._DASHBOARD_WORLD_HARNESS, 1, False),
        'selector': (behaviour._TAB_SELECTOR_HARNESS, 5, True),
        'field': (accessibility._FIELD_HARNESS, 1, True),
    }
    for name, (harness, bounded_steps, module) in expected.items():
        assert isinstance(harness, _dashnode.DashboardNodeHarness), name
        actual = (harness.bounded_steps, harness.module)
        assert actual == (bounded_steps, module), (name, actual)


def test_harness_metadata_rejects_an_under_declared_bound_count(tmp):
    """One real bound cannot be declared as zero bounded steps."""
    del tmp
    failure = _metadata_failure(
        "await bounded(Promise.resolve(), 'work', 1);", 0)
    assert failure == (
        'dashboard harness declares 0 bounded steps but performs 1')


def test_harness_metadata_rejects_an_over_declared_bound_count(tmp):
    del tmp
    failure = _metadata_failure(
        "await bounded(Promise.resolve(), 'work', 1);", 2)
    assert failure == (
        'dashboard harness declares 2 bounded steps but performs 1')


def test_harness_metadata_counts_a_bound_split_by_a_comment(tmp):
    """A comment between `await` and `bounded` cannot hide a real bound."""
    del tmp
    source = "await /* diagnostic label */ bounded(work, 'work', 1);"
    failure = _metadata_failure(source, 0)
    assert failure == (
        'dashboard harness declares 0 bounded steps but performs 1')


def test_harness_metadata_ignores_a_bound_inside_a_comment(tmp):
    """A commented-out `await bounded` cannot inflate the bound count."""
    del tmp
    source = r"""
await bounded(first, 'first', 1);
/* await bounded(disabled, 'disabled', 1); */
"""
    failure = _metadata_failure(source, 2)
    assert failure == (
        'dashboard harness declares 2 bounded steps but performs 1')


def test_harness_metadata_refuses_a_template_expression(tmp):
    del tmp
    source = "const value = `${/* await bounded(x, 'y', 1) */ 1}`;"
    failure = _metadata_failure(source, 1)
    assert failure == (
        'dashboard harness bound count cannot inspect '
        'template expression in dashboard harness source')


def test_harness_metadata_refuses_a_regex_literal(tmp):
    del tmp
    source = r"""
const slashOrStar = /[/*]/;
await bounded(work, 'work', 1);
"""
    assert _metadata_failure(source, 1) == _SLASH_REFUSAL


def test_harness_metadata_refuses_a_division_slash(tmp):
    del tmp
    source = 'const half = value / 2;'
    assert _metadata_failure(source, 0) == _SLASH_REFUSAL


def test_all_shipped_harnesses_pass_bound_shape_validation(tmp):
    """Supported source shapes in all shipped harnesses remain valid."""
    del tmp
    shipped = (
        behaviour._CONTENT_KEEPALIVE_HARNESS,
        behaviour._DASHBOARD_CONSUME_HARNESS,
        behaviour._DASHBOARD_WORLD_HARNESS,
        behaviour._TAB_SELECTOR_HARNESS,
        accessibility._FIELD_HARNESS,
    )
    assert all(isinstance(harness, _dashnode.DashboardNodeHarness)
               for harness in shipped), shipped
    rebuilt = tuple(
        _dashnode.DashboardNodeHarness(
            harness.source, harness.bounded_steps, harness.module,
            harness.arguments)
        for harness in shipped)
    assert rebuilt == shipped


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashmeta_')


if __name__ == '__main__':
    raise SystemExit(main())
