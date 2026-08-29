"""Typed real-browser failures shared across fixture boundaries."""
import json


class CDPEvaluationError(AssertionError):
    """CDP returned exception details after evaluating submitted source."""

    def __init__(self, response):
        self.response = response
        super().__init__(response)


class CDPTimeout(AssertionError):
    """The JavaScript CDP harness reached its response deadline."""


class FirstNavigationTimeout(AssertionError):
    """A first navigation deadline with its in-process arrival observation."""

    candidate_owners = (
        'the browser', 'the CDP transport', 'this repository', 'the machine')

    def __init__(self, page_url, request_arrived):
        self.page_url = page_url
        self.request_arrived = request_arrived
        self.selected_owner = None
        super().__init__(
            f'{type(self).__name__}: '
            + json.dumps(self.record(), sort_keys=True))

    def record(self):
        return {
            'page_url': self.page_url,
            'request_arrived': self.request_arrived,
            'candidate_owners': list(self.candidate_owners),
            'selected_owner': self.selected_owner,
        }
