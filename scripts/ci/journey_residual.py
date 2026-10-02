"""One journey's residual: its own work, net of the background it shares.

Arithmetic over three numbers with a refusal attached, and it depends on
nothing in this tree — a counter hands it three numbers and it hands back
a row or a sentence — so it can be imported by whichever module owns the
loop without either of them reaching the other.
"""
import statistics


def row(name, raw_values, bridge):
    """One journey's row, or the refusal a negative residual gets.

    The baseline is subtracted ONCE, and it is the whole of the
    `bridge-only` child rather than that child less a startup-only child
    beside it. The bridge-only baseline is itself a child of the same
    interpreter running the same module, so it already paid the startup
    child cost in full: subtracting `startup` again removes it twice, and
    the journey's own work comes out of the subtraction already gone.
    Measured on real profiles (CPython 3.13.14, valgrind 3.24.0), the
    startup-only child kept 1,028,521,011 instructions and the bridge-only
    child kept 1,624,572,928 through `command-round-trip`'s own exclusion
    list, against a `command-round-trip` raw of 1,577,825,117 — so the
    shipped three-operand subtraction reported -1,075,268,822 for a journey
    whose own work is a positive number, and the two-operand one reports
    -46,747,811 for the same two runs. Neither is the journey's cost; the
    second is at least the shape the rest of this module describes.

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
    net = [value - bridge for value in raw_values]
    if any(value < 0 for value in net):
        return None, (
            f'the {name} journey measured {raw_values} instructions net '
            f'{net}, against a bridge-only baseline of {bridge} read '
            f'through its own exclusion list, so its own work is smaller '
            'than the fixed background it shares and the run cannot '
            'separate them; that is reported rather than clamped to zero, '
            'because a clamp would absorb exactly this')
    return {'raw': raw_values,
            'net': net,
            'min': min(net) if net else None,
            'max': max(net) if net else None,
            'median': statistics.median(net) if net else None,
            'spread': max(net) - min(net) if net else None}, None