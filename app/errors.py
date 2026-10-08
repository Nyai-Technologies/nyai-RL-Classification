"""Errors the service raises on purpose. The API layer turns them into JSON answers."""


class LLMError(Exception):
    """One LLM call (or its answer) failed. `tokens` = (in, out) already spent on this file; `fatal` = a configuration
    problem (bad key / unknown model) that makes retrying other files pointless."""

    def __init__(self, msg, tokens=(0, 0), fatal=False):
        super().__init__(msg)
        self.tokens = tokens
        self.fatal = fatal


class RunAborted(RuntimeError):
    """The whole run was stopped because of a configuration problem (key, model, access)."""


class RequestRejected(Exception):
    """The request cannot be served as sent (too many files, service busy, ...). `status_code` is the HTTP code to answer."""

    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
