"""KiloEx Hybrid Vault support."""

import datetime

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.kiloex.constants import KILOEX_EARN_URL, KILOEX_VAULT_LINK_MATRIX


class KiloExVault(ERC4626Vault):
    """KiloEx Hybrid Vault support.

    KiloEx is a multi-chain perpetual DEX. Its Hybrid Vault is the counterparty
    to traders, with ERC-4626 vault share tokens such as ``kUSDT`` and
    ``kUSDC`` representing claims on its VUSD accounting asset.

    KiloEx uses a Gains-compatible contract surface, so recognised deployments
    are selected by their hardcoded chain ID and contract address instead of a
    selector probe.

    - `KiloEx Earn <https://app.kiloex.io/earn/>`__
    - `Hybrid Vault documentation <https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault>`__
    - `Fee documentation <https://docs.kiloex.io/kiloex/trading/fees-and-spread>`__
    - `Example kUSDT vault <https://bscscan.com/address/0x1c3f35f7883fc4ea8c4bca1507144dc6087ad0fb>`__
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the management fee when KiloEx publishes it on-chain.

        KiloEx's `fee documentation <https://docs.kiloex.io/kiloex/trading/fees-and-spread>`__
        covers trading and funding fees, but these deployments do not expose a
        vault-specific ERC-4626 management fee accessor.

        :param block_identifier:
            Block at which to read fee data.

        :return:
            ``None`` because the management fee is not available.
        """
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the performance fee when KiloEx publishes it on-chain.

        The `Hybrid Vault documentation <https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault>`__
        describes revenue sharing with liquidity providers, but these deployments
        do not expose a vault performance-fee accessor.

        :param block_identifier:
            Block at which to read fee data.

        :return:
            ``None`` because the performance fee is not available.
        """
        del block_identifier
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta:  # noqa: PLR6301
        """Return KiloEx's longest documented withdrawal wait.

        The `Hybrid Vault documentation <https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault>`__
        states that withdrawals settle in three-day epochs and can require one
        to three epochs depending on the vault collateral ratio.

        :return:
            Nine days, the maximum documented withdrawal wait.
        """
        return datetime.timedelta(days=9)

    def get_link(self, referral: str | None = None) -> str:  # noqa: ARG002
        """Get the KiloEx Earn page for this vault.

        The `KiloEx Earn application <https://app.kiloex.io/earn/>`__ is
        chain-specific but does not expose an individual-vault route.

        :param referral:
            Unused because the KiloEx Earn page does not accept a referral
            parameter for vault navigation.

        :return:
            Chain-specific KiloEx Earn page for a known deployment, or the
            protocol Earn homepage.
        """
        return KILOEX_VAULT_LINK_MATRIX.get((self.chain_id, self.address.lower()), KILOEX_EARN_URL)
