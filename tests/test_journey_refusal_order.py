#!/usr/bin/env python3
"""Which refusal a tighten reports when one run trips two at once.

The rounds guard sits LAST in `journey_budget.py`'s `if args.tighten:` block
and its comment claims the precedence: a run that both met a rise and took a
single draw is reported for the rise, because the rise is the one that is a
red. That is a claim about an ORDERING, and no case that trips one refusal at
a time can see it move — every other subject of that guard is measured with a
measurement which is under budget or over it, never both.

So the one subject here trips both at once, through the real command as the
subprocess a workflow step drives, over a copy of the budget this repository
ships. Hoisting the guard to the top of its block turns this case red and
leaves every other row in every other journey suite green.

Its own file because both suites that could hold it are at their ceilings —
`test_journey_budget_cli.py` at 692 and `test_journey_budget_gates.py` at 685
against 700 — and a file under the ceiling needs no threshold entry.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _journey_contract import (  # noqa: E402
    ARTIFACT,
    ROOT,
    measured_report,
    summaries,
)


def test_a_tighten_reports_a_rise_over_a_single_draw(tmp):
    """A run that both rose and drew once is a red, not a quiet success.

    One journey is measured at DOUBLE its recorded count and every other one
    at half, over the shipped budget and its own tolerances, so `over` and the
    rounds guard both fire and only the order between them decides whether
    the step is red. Half of every journey keeps the run a real tighten on
    the other reading — a run that refused for want of measurements has
    nothing to tighten — so the case is the one the comment describes and not
    a measurement that trips a refusal on its way to nothing.
    """
    document = json.loads(ARTIFACT.read_bytes())
    rose = next(iter(document['journeys']))
    counts = {name: seen // 2 for name, seen in document['journeys'].items()}
    counts[rose] = document['journeys'][rose] * 2
    report = measured_report(counts, rounds=1,
                             toolchain=document['toolchain'],
                             excluded_threads=document['excluded_threads'],
                             shas=document['shas'])
    # The counter this budget is recorded against, which is not the one the
    # fixture builds: a report whose counts are filed under another counter
    # is a run that measured no journey under the one the check reads.
    report['counters'] = {document['counter']:
                          report['counters']['perf-instructions']}
    budget = Path(tmp) / 'journey-budget.json'
    budget.write_bytes(ARTIFACT.read_bytes())
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')

    done = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/ci/journey_budget.py'),
         'check', '--artifact', str(budget), '--measurements',
         str(measurements), '--tighten'],
        capture_output=True, text=True, timeout=120)

    assert done.returncode != 0, (
        'a run that measured a rise and took a single draw reported '
        f'success, so a regressed journey would go green: {done.stdout}')
    # The remedy for the RISE, which is the refusal the run has to be
    # reported under: the guard that outranks it prints its own line and no
    # remedy at all, so naming the one that must be there is what makes the
    # order between them observable here.
    assert summaries().OVER_REMEDY in done.stderr, (
        "the rise is the red, so the remedy a reader meets is the rise's "
        f"and not the round count's: {done.stderr}")


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeyorder__')


if __name__ == '__main__':
    raise SystemExit(main())