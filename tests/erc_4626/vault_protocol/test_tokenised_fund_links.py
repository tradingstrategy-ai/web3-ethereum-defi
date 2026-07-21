"""Ensure tokenised funds cannot silently fall back to block explorers."""

import pytest

from eth_defi.tokenised_fund.vault import TokenisedFundVault


def test_tokenised_fund_base_requires_official_link() -> None:
    """Require every tokenised-fund adapter to select an official public link.

    The generic :class:`VaultBase` implementation links to a contract address
    on a block explorer. That is useful technical metadata but not a useful
    product destination, so tokenised-fund adapters must opt in to a product,
    announcement, curator or protocol URL instead.
    """

    with pytest.raises(NotImplementedError, match="official product, announcement, curator or protocol link"):
        TokenisedFundVault.get_link(None)  # type: ignore[arg-type]
