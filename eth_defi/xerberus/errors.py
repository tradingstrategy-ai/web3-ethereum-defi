"""Xerberus API error types."""


class XerberusAPIError(RuntimeError):
    """Raised when a Xerberus API response cannot be used.

    Covers non-success JSON envelopes, auth middleware bodies without a
    ``status`` field, validation errors, and unexpected response shapes.
    """
