"""Frax lending and staking vault support.

Frax is a decentralised finance protocol offering stablecoins (frxUSD),
liquid staking (frxETH/sfrxETH), and lending markets (Fraxlend).

- Homepage: https://frax.com/
- Documentation: https://docs.frax.finance/
- Fraxlend documentation: https://docs.frax.finance/fraxlend/fraxlend-overview
- Smart contracts: https://github.com/FraxFinance/fraxlend
- Example Fraxlend pair: https://etherscan.io/address/0xee847a804b67f4887c9e8fe559a2da4278defb52
- sFRAX: https://etherscan.io/address/0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32
- sfrxUSD: https://etherscan.io/address/0xcf62f905562626cfcdd2261162a51fd02fc9c5b6
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.types import Percent
from eth_defi.vault.fee import VaultFeeMode

logger = logging.getLogger(__name__)


class FraxVault(ERC4626Vault):
    """Shared base and legacy Fraxlend reader for Frax protocol vaults.

    Frax exposes multiple ERC-4626 product families with different economics.
    Concrete readers keep those differences explicit while sharing the Frax
    protocol classification. The Fraxlend fee defaults remain on this class for
    backwards compatibility with callers that instantiated ``FraxVault`` before
    the product-family split.

    Fraxlend is a lending protocol by Frax that allows users to lend assets and
    earn interest from borrowers. Each Fraxlend pair is an isolated lending
    market with its own ERC-4626 compatible vault for lenders.

    - Frax homepage: https://frax.com/
    - Frax documentation: https://docs.frax.com/
    - Fraxlend overview: https://docs.frax.finance/fraxlend/fraxlend-overview
    - Protocol fees: 10% of interest revenue goes to the Frax protocol
    - Smart contracts: https://github.com/FraxFinance/fraxlend
    - Audits: https://docs.frax.finance/other/audits
    """

    def has_custom_fees(self) -> bool:  # noqa: PLR6301
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301, ARG002
        """Fraxlend has no management fee for lenders.

        The protocol takes a 10% cut of interest revenue via ``feeToProtocolRate``
        in the ``currentRateInfo`` struct, but this is already internalised
        in the share price.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:  # noqa: PLR6301, ARG002
        """Fraxlend protocol fee.

        The protocol takes 10% of interest earned as a fee.
        This is internalised in the share price via the ``feeToProtocolRate`` field.

        - https://docs.frax.finance/fraxlend/fraxlend-overview
        """
        return 0.10

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """No lock-up for Fraxlend lenders.

        Lenders can withdraw at any time, subject to available liquidity.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:  # noqa: ARG002
        return f"https://app.frax.finance/fraxlend/pair/{self.vault_address}"


class FraxlendPairVault(FraxVault):
    """Concrete reader for a Fraxlend isolated lending pair.

    This explicit class distinguishes lending pairs from Frax staking vaults
    while inheriting the legacy Fraxlend behaviour from :class:`FraxVault`.
    """


class FraxStakingVault(FraxVault):
    """Frax stablecoin staking vault.

    This reader covers the reviewed sFRAX and sfrxUSD deployments. Yield is
    distributed through the vault share price, with no explicit management,
    performance, deposit, withdrawal or lock-up fee in the reviewed contracts.

    - sFRAX documentation: https://docs.frax.finance/frax-v3-100-cr-and-more/sfrax
    - sfrxUSD documentation: https://docs.frax.com/protocol/assets/frxusd/sfrxusd
    - sfrxUSD staking fees: https://docs.frax.com/frxusd/stake-and-unstake-overview
    - Verified sFRAX contract: https://etherscan.io/address/0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32#code
    - Verified sfrxUSD implementation: https://eth.blockscout.com/address/0xAad4A1D92053a62cE7a787641d8b4E5883e96700?tab=contract
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301, ARG002
        """Return the explicit annual management fee.

        The reviewed staking vault contracts do not charge a management fee.

        :param block_identifier:
            Block used for the fee lookup. The fee is static.
        :return:
            Zero percent.
        """

        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:  # noqa: PLR6301, ARG002
        """Return the explicit performance fee.

        Frax distributes protocol yield into these vaults without an on-chain
        performance fee charged to vault shareholders.

        :param block_identifier:
            Block used for the fee lookup. The fee is static.
        :return:
            Zero percent.
        """

        return 0.0

    def get_fee_mode(self) -> VaultFeeMode:  # noqa: PLR6301
        """Return the staking-vault fee accounting mode.

        Frax protocol-level fee metadata describes Fraxlend. Override it for
        staking vaults so both product families can share the protocol name.

        :return:
            Feeless vault accounting.
        """

        return VaultFeeMode.feeless

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Return the staking lock-up period.

        sFRAX and sfrxUSD shares can be redeemed without a time lock.

        :return:
            Zero-day lock-up.
        """

        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301, ARG002
        """Return the Frax earn page for stablecoin staking.

        :param referral:
            Optional referral identifier. Frax does not expose one here.
        :return:
            Frax earn page URL.
        """

        return "https://frax.com/earn"
