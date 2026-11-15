"""Explicit adapter-version failures and unsupported protocol decisions."""


class UnsupportedVaultVersion(NotImplementedError):
    """A deployed version has no reviewed adapter implementation.

    Scanners may defer this candidate while processing unrelated vaults.
    Generic ``NotImplementedError`` remains a programming failure.
    """


class UnsupportedVaultProtocol(Exception):
    """The protocol is data-only and must not receive new investment.

    This is an explicit support decision, distinct from unknown permissions or
    an actual depositor allowlist. Existing position recovery remains separate.
    """
