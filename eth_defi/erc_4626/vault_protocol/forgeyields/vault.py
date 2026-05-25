"""ForgeYields vault support.

ForgeYields is a cross-chain, non-custodial yield aggregator deploying into
frontier DeFi strategies underwritten by the Hallmark public risk methodology.

- `Homepage <https://www.forgeyields.com/>`__
- `Documentation <https://forge-labs.gitbook.io/forge-docs>`__
- `App <https://app.forgeyields.com/>`__
- `GitHub <https://github.com/ForgeYields>`__
- `Audits <https://forge-labs.gitbook.io/forge-docs/other/audits>`__

The fyUSDC, fyETH and fyWBTC vaults issue auto-compounding ERC-4626 tokens (fyTokens).
The Ethereum vault is built on Veda Labs' BoringVault and allocates across Aave, Morpho,
Curve, Pendle and others.

The TokenGateway contract does not implement ``totalAssets()`` — NAV is derived
from ``convertToAssets(totalSupply())``.

Fee model:

- 20 % daily performance fee, internalised into the share price
- No management fee, no deposit/withdrawal fees
- `Fee documentation <https://forge-labs.gitbook.io/forge-docs>`__

Example contracts:

- `fyUSDC <https://etherscan.io/address/0x943109DC7C950da4592d85ebd4Cfed007Af64670>`__
- `fyETH <https://etherscan.io/address/0x98CD770b4e9905B1263f0c9ae6cdE34E1923508E>`__
- `fyWBTC <https://etherscan.io/address/0xeDca8230366B9eaFf06becdD1D261577836AA507>`__
"""

import datetime
import logging
from decimal import Decimal

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class ForgeYieldsVault(ERC4626Vault):
    """ForgeYields vault.

    Cross-chain, non-custodial yield aggregator for underwritten frontier DeFi strategies.

    - Built on Veda Labs' BoringVault with TokenGateway cross-chain deposit architecture
    - Hallmark-underwritten strategies with public risk methodology
    - Atomic Transparency Ledger for real-time on-chain-verifiable reporting
    - Asynchronous request-then-claim redemption; funds keep earning until claimed
    - `Homepage <https://www.forgeyields.com/>`__
    - `Documentation <https://forge-labs.gitbook.io/forge-docs>`__
    - `Audits <https://forge-labs.gitbook.io/forge-docs/other/audits>`__

    The TokenGateway contract does not implement ``totalAssets()``.
    NAV is derived from ``convertToAssets(totalSupply())``.
    """

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Compute total assets from ``convertToAssets(totalSupply())``.

        TokenGateway does not implement ``totalAssets()`` — it reverts.
        We derive NAV from the share supply and the price-per-share conversion.

        :param block_identifier:
            Block number to read.

        :return:
            Total vault value in the denomination token.
        """
        total_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        if total_supply == 0:
            return Decimal(0)
        raw_assets = self.vault_contract.functions.convertToAssets(total_supply).call(block_identifier=block_identifier)
        if self.underlying_token is not None:
            return self.underlying_token.convert_to_decimals(raw_assets)
        return None

    def has_custom_fees(self) -> bool:
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No management fee.

        `Fee documentation <https://forge-labs.gitbook.io/forge-docs>`__.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """20 % daily performance fee, internalised into the share price.

        `Fee documentation <https://forge-labs.gitbook.io/forge-docs>`__.
        """
        return 0.20

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Asynchronous request-then-claim redemption.

        Redemptions are processed in epochs. Typical turnaround is within a few days
        but can vary depending on vault liquidity.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Link to the ForgeYields app."""
        return "https://app.forgeyields.com/"
