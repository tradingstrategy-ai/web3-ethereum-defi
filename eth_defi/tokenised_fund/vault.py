"""Shared base class for tokenised fund vault adapters."""

from eth_defi.vault.base import VaultBase
from eth_defi.vault.flag import VaultFlag


class TokenisedFundVault(VaultBase):
    """Base class for every tokenised fund protocol adapter.

    Tokenised fund classification belongs to the adapter type instead of a
    manually maintained address list. This also covers products discovered
    dynamically from issuer registries, such as Asseto funds.
    """

    def get_flags(self) -> set[VaultFlag]:
        """Return vault flags including the tokenised fund classification.

        Preserve address- and protocol-specific flags supplied by the generic
        vault implementation, then add the descriptive flag used by tokenised
        fund listings.

        :return:
            A new set containing all generic flags and
            :py:data:`VaultFlag.tokenised_fund`.
        """

        return super().get_flags() | {VaultFlag.tokenised_fund}

    def get_link(self, referral: str | None = None) -> str:
        """Return the issuer's most useful public fund link.

        Tokenised-fund adapters must provide a product landing page where one
        exists, then fall back to an official announcement, curator page or
        protocol page.  A block-explorer address is technical contract
        metadata, not an investor-facing product link, and is never a valid
        fallback for these products.

        :param referral:
            Optional referral code. Tokenised-fund products currently do not
            use it.
        :return:
            An official issuer, curator or protocol URL.
        :raise NotImplementedError:
            Always. Concrete adapters must select the appropriate official
            link rather than inheriting :class:`VaultBase`'s explorer URL.
        """

        _ = self, referral
        message = "Tokenised fund adapters must define an official product, announcement, curator or protocol link"
        raise NotImplementedError(message)
