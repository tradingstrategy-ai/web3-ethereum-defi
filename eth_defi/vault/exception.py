"""Explicit adapter-version failures and unsupported protocol decisions."""


class UnsupportedVaultVersion(NotImplementedError):
    """A deployed version has no reviewed adapter implementation.

    Scanners may defer this candidate while processing unrelated vaults.
    Generic ``NotImplementedError`` remains a programming failure.
    """


class UnsupportedVaultProtocol(Exception):
    """The protocol is data-only and must not receive new investment.

    This is an explicit support decision, distinct from unknown permissions or
    an actual depositor allowlist. Scanners persist ``unsupported_protocol``;
    consumers reject new investment with the same effect as permissioned status.
    Existing holdings remain visible and recovery is assessed independently.

    This deliberately does not inherit ``NotImplementedError``: optional-policy
    fallbacks catch that exception and could otherwise erase the explicit block.
    Introduced for the VaultMorpho case in
    https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1782.
    """
