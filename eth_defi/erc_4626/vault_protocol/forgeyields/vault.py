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
On-chain ``convertToAssets(totalSupply())`` returns only the gateway's residual balance
(~$12K), not the true cross-chain AUM (~$1.8M). The canonical TVL comes from
ForgeYields' proprietary API at ``https://api.forgeyields.com/strategies``.

See :py:mod:`~eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata` for
the API integration.

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
from functools import cached_property
from typing import Iterable

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata import (
    ForgeYieldsVaultMetadata,
    fetch_forgeyields_vault_metadata,
)
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)


class ForgeYieldsHistoricalReader(ERC4626HistoricalReader):
    """Read ForgeYields vault data — share price only, no TVL.

    Historical TVL is not available for ForgeYields vaults:

    - ``totalAssets()`` reverts on the TokenGateway contract
    - ``convertToAssets(totalSupply())`` returns only the gateway residual (~$12K),
      not the true cross-chain AUM (~$1.8M)
    - The proprietary API at ``api.forgeyields.com`` provides current TVL only,
      not historical snapshots

    This reader emits ``total_assets = None`` so the scanner does not report
    the misleading on-chain residual. Share price is still tracked via
    ``convertToAssets(1e<decimals>)`` from the standard multicall.
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

        # Strip the expected totalAssets revert error — we cannot derive
        # historical TVL for this vault, so this is not a real error.
        if errors:
            errors = [e for e in errors if "total_assets" not in e]

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=None,
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
    On-chain ``convertToAssets(totalSupply())`` returns only the gateway residual.
    The canonical TVL comes from the offchain API.

    See :py:mod:`~eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata`.
    """

    @cached_property
    def forgeyields_metadata(self) -> ForgeYieldsVaultMetadata | None:
        """Offchain metadata from ForgeYields' proprietary API.

        - Fetched from ``api.forgeyields.com/strategies``
        - Cached on first access (in-process + disk)
        - Returns None if vault address is not a known ForgeYields Ethereum gateway
        """
        return fetch_forgeyields_vault_metadata(self.vault_address)

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return ForgeYieldsHistoricalReader(self, stateful=stateful)

    def fetch_tvl_usd(self) -> Decimal | None:
        """Fetch total cross-chain TVL in USD from the ForgeYields API.

        The on-chain ``convertToAssets(totalSupply())`` only returns the Ethereum
        gateway's residual balance, not the true cross-chain AUM.
        The canonical TVL comes from ``api.forgeyields.com/strategies``.

        :return:
            Total vault value in USD across all chains, or None if unavailable.
        """
        meta = self.forgeyields_metadata
        if meta is not None:
            return meta["tvl_usd"]
        return None

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Not available — on-chain value is the gateway residual, not the true AUM.

        The TokenGateway's ``convertToAssets(totalSupply())`` returns only
        the small residual held by the Ethereum gateway (~$12K), not the
        cross-chain AUM (~$1.8M). Use :py:meth:`fetch_tvl_usd` for the
        canonical current TVL from the ForgeYields API.

        :return:
            Always ``None``.
        """
        return None

    def fetch_nav(self, block_identifier=None) -> Decimal | None:
        """Not available — on-chain value is the gateway residual, not the true AUM.

        See :py:meth:`fetch_total_assets` for rationale. Use
        :py:meth:`fetch_tvl_usd` for the canonical current TVL.

        :return:
            Always ``None``.
        """
        return None

    def is_historical_tvl_supported(self) -> bool:
        """On-chain TVL is not available — returns ``False``.

        The TokenGateway only holds a residual; the canonical cross-chain
        TVL comes from the ForgeYields API via :py:meth:`fetch_tvl_usd`.
        """
        return False

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
