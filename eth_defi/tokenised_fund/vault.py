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
