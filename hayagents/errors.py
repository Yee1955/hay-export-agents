"""The two ways a request can fail on purpose."""


class Denied(Exception):
    """The rules refuse this request. The reason is shown to the caller and logged."""


class ReplyLost(Exception):
    """Demo fault: the purchase order was saved, but the reply never reached the caller."""
