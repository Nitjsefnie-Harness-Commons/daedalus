"""The fixture the extension boundary suite sizes a command line with.

`_command_line_length` is what remains of the fixtures the Node-subprocess
dashboard, overlap and boundary suites each copied; the rest were retired
with the suites that used them.
"""


def _command_line_length(argv):
    """The length Windows measures: the arguments joined by one space."""
    return sum(len(argument) + 1 for argument in argv)
