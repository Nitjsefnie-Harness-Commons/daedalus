"""One journey's residual: its own work, net of the background it shares.

Arithmetic over four numbers with a refusal attached, and it depends on
nothing in this tree — a counter hands it four numbers and it hands back
a row or a sentence — so it can be imported by whichever module owns the
loop without either of them reaching the other.
"""
import statistics


def row(name, raw_values, startup, bridge):
    """One journey's row, or the refusal a negative residual gets.

    Both numbers are reported because only one of them is the budget: a
    journey's own total carries the interpreter start, the imports and the
    whole bridge every child pays whatever is measured, and a ratchet on
    that number would go red on a dependency bump rather than on a change
    to the work.

    A residual below zero is a REFUSAL naming every number in it, never a
    clamp. A journey whose own work is smaller than the run-to-run wobble
    of the threads it shares will produce one, and that is the answer:
    clamping to zero would report it as costing nothing and absorb the
    exact condition this subtraction exists to detect.
    """
    net = [value - startup - bridge for value in raw_values]
    if any(value < 0 for value in net):
        return None, (
            f'the {name} journey measured {raw_values} instructions net '
            f'{net}, against a startup-only baseline of {startup} and a '
            f'bridge-only baseline of {bridge} read through its own '
            f'exclusion list, so its own work is smaller than the fixed '
            'background it shares and the run cannot separate them; that '
            'is reported rather than clamped to zero, because a clamp '
            'would absorb exactly this')
    return {'raw': raw_values,
            'net': net,
            'min': min(net) if net else None,
            'max': max(net) if net else None,
            'median': statistics.median(net) if net else None,
            'spread': max(net) - min(net) if net else None}, None
