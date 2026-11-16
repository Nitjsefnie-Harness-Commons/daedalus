"""The fixture the extension boundary suite sizes a command line with.

`_command_line_length` is the only fixture this module holds.
"""


def _command_line_length(argv):
    """The length Windows measures: the arguments joined by one space."""
    return sum(len(argument) + 1 for argument in argv)
