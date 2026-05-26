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

NAV calculation
~~~~~~~~~~~~~~~

The TokenGateway contract does not implement ``totalAssets()`` — the call reverts.
NAV is derived from ``convertToAssets(totalSupply())``, which uses the internal
price-per-share (``pps()``) to convert shares to assets.

Both :py:meth:`ForgeYieldsVault.fetch_total_assets`,
:py:meth:`ForgeYieldsVault.fetch_nav` and :py:class:`ForgeYieldsHistoricalReader`
use this corrected calculation.

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
from typing import Iterable

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)


class ForgeYieldsHistoricalReader(ERC4626HistoricalReader):
    """Read ForgeYields vault data with corrected NAV.

    TokenGateway's ``totalAssets()`` reverts, so the standard ERC-4626
    ``total_assets`` multicall will fail.  This reader derives
    ``total_assets = share_price * total_supply`` from the successful
    ``convertToAssets`` and ``totalSupply`` calls instead.
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables — total_assets will be None because totalAssets() reverts
        share_price, total_supply, _total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # Strip the expected totalAssets revert error — we derive NAV below,
        # so this is not a real error and must not increment rpc_error_count.
        if errors:
            errors = [e for e in errors if "total_assets" not in e]

        # Override total_assets with the true NAV: share_price * total_supply
        total_assets = _total_assets
        if share_price is not None and total_supply is not None and total_supply > 0:
            total_assets = share_price * total_supply

        # Fix VaultReaderState that was updated with the failed totalAssets() value
        # inside process_core_erc_4626_result(). The state uses TVL for adaptive polling
        # frequency and peaked/faded detection, so it must reflect the true NAV.
        convert_to_assets_result = call_by_name.get("convertToAssets")
        if convert_to_assets_result is not None and convert_to_assets_result.state is not None:
            convert_to_assets_result.state.on_called(
                convert_to_assets_result,
                total_assets=total_assets,
                share_price=share_price,
            )

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            max_deposit=max_deposit,
        )


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

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return ForgeYieldsHistoricalReader(self, stateful=stateful)

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Compute total assets from ``convertToAssets(totalSupply())``.

        TokenGateway does not implement ``totalAssets()`` — it reverts.
        We derive NAV from the share supply and the price-per-share conversion.

        :param block_identifier:
            Block number to read.

        :return:
            Total vault value in the denomination token.
        """
        if self.underlying_token is None:
            return None

        total_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        if total_supply == 0:
            return Decimal(0)
        raw_assets = self.vault_contract.functions.convertToAssets(total_supply).call(block_identifier=block_identifier)
        return self.underlying_token.convert_to_decimals(raw_assets)

    def fetch_nav(self, block_identifier=None) -> Decimal:
        """Fetch the most recent onchain NAV value.

        Uses ``convertToAssets(totalSupply())`` instead of ``totalAssets()``
        because TokenGateway does not implement ``totalAssets()``.

        :return:
            Vault NAV, denominated in :py:meth:`denomination_token`
        """
        token = self.denomination_token
        raw_total_supply = self.vault_contract.functions.totalSupply().call(block_identifier=block_identifier)
        if raw_total_supply == 0:
            return Decimal(0)
        raw_nav = self.vault_contract.functions.convertToAssets(raw_total_supply).call(block_identifier=block_identifier)
        return token.convert_to_decimals(raw_nav)

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
