"""Typed real-browser failures shared across fixture boundaries."""


class CDPEvaluationError(AssertionError):
    """CDP returned exception details after evaluating submitted source."""

    def __init__(self, response):
        self.response = response
        super().__init__(response)
