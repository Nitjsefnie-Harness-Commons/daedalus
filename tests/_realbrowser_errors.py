"""Typed real-browser failures shared across fixture boundaries."""


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
        self.request_arrived = request_arrived
        self.selected_owner = None
        arrival = ('received a request for that page'
                   if request_arrived
                   else 'did not receive a request for that page')
        owners = ', '.join(self.candidate_owners[:-1])
        ownership_candidates = f'{owners}, or {self.candidate_owners[-1]}'
        owner_selection = ('no owner selected'
                           if self.selected_owner is None
                           else f'owner selected: {self.selected_owner}')
        super().__init__(
            f'the first fixture navigation reached its deadline: {page_url}; '
            f'the in-process handler {arrival}. {owner_selection}; '
            f'candidates are {ownership_candidates}')
