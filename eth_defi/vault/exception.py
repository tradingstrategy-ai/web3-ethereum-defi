"""Expected adapter compatibility failures retained as pending scanner work."""


class UnsupportedVaultVersion(NotImplementedError):
    """A deployed version has no reviewed adapter implementation.

    Scanners may defer this candidate while processing unrelated vaults.
    Generic ``NotImplementedError`` remains a programming failure.
    """
