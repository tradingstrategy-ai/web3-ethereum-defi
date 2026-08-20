"""Pallas asynchronous trading-vault support.

`Pallas <https://app.pallas.fund/>`__ runs USDT0-denominated trading vaults on
HyperEVM. The reviewed deployments issue ``PALLAS`` shares and settle deposits
and redemptions asynchronously, while their strategies trade across HyperEVM
and HyperCore markets.

The vault proxies point to verified implementations named
``ERC7540NonCustodialTradingVaultUpgradeable``. They use a request-and-claim
life cycle rather than the inherited synchronous ERC-4626 transaction manager,
so this adapter intentionally declares no public deposit-manager capability.

- `Basis Trading HIP-3 vault <https://hyperevmscan.io/address/0x9b3aa83BD833123437d4efa656E7121B7F317899>`__
- `Directional Volatility vault <https://hyperevmscan.io/address/0xa642188e1345AEe1809f6db5431464b079978c68>`__
- `Current Basis implementation <https://hyperevmscan.io/address/0xe324e4a5C9f8ea9Db2F957702d4Bb164DE3caF17>`__
"""

import datetime

from web3.types import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.pallas.constants import PALLAS_VAULT_LINK_MATRIX


class PallasVault(ERC4626Vault):
    """Pallas ERC-7540 non-custodial trading vault.

    Pallas' public app lists strategy-specific management and performance fees,
    including separate standard and premium-pass rates. The reviewed contracts
    do not expose a stable public fee accessor, so callers must not infer a
    universal rate from this reader.
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the published management fee when a canonical onchain value exists.

        Pallas presents tier-dependent strategy fees in its application but the
        reviewed vault ABI has no stable no-argument management-fee accessor.

        :param block_identifier:
            Block at which fee data would be read.
        :return:
            ``None`` because management fees are not exposed as a canonical
            onchain value.
        """
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the published performance fee when a canonical onchain value exists.

        Pallas presents tier-dependent strategy fees in its application but the
        reviewed vault ABI has no stable no-argument performance-fee accessor.

        :param block_identifier:
            Block at which fee data would be read.
        :return:
            ``None`` because performance fees are not exposed as a canonical
            onchain value.
        """
        del block_identifier
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Return the documented redemption waiting period when Pallas publishes one.

        The vaults have an asynchronous request-and-claim redemption flow, but
        Pallas does not publish a fixed settlement duration for the reviewed
        deployments. A queue should not be represented as a deterministic lock-up.

        :return:
            ``None`` because no fixed redemption waiting period is documented.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the strategy-specific Pallas app page when the vault is reviewed.

        Pallas exposes separate pages for its active strategies. Unknown future
        deployments fall back to the vault list, preserving a useful user-facing
        destination without assuming an unsupported URL format.

        :param referral:
            Unused because Pallas' vault pages do not provide a documented
            referral query parameter.
        :return:
            Strategy page for a reviewed deployment, otherwise the Pallas app.
        """
        del referral
        return PALLAS_VAULT_LINK_MATRIX.get((self.chain_id, self.address.lower()), "https://app.pallas.fund/")
