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
Onchain ``convertToAssets(totalSupply())`` returns only the gateway's residual balance
(~$12K), not the true cross-chain AUM (~$1.8M). The canonical TVL comes from
ForgeYields' proprietary API at ``https://api.forgeyields.com/strategies``.

See :py:mod:`~eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata` for
the retained metadata reader; API fetching is disabled.

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

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata import (
    ForgeYieldsVaultMetadata,
    fetch_forgeyields_strategies,
    fetch_forgeyields_vault_metadata,
)
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)


class ForgeYieldsHistoricalReader(ERC4626HistoricalReader):
    """Read ForgeYields vault data — onchain share price and retained offchain TVL.

    The onchain ``totalAssets()`` reverts on the TokenGateway and
    ``convertToAssets(totalSupply())`` returns only the gateway residual.

    For rows near the chain head (within 24 hours of now), this reader
    writes the retained denomination-token TVL from the saved metadata copy
    into ``total_assets``. Older rows get ``total_assets=None`` — the
    previously collected history is preserved, but API backfills are disabled.

    The retained TVL is also fed into the reader state so adaptive polling
    does not degrade to faded/tiny cadence.
    """

    #: Restrict retained TVL to near-head rows to avoid rewriting old observations.
    #: Older rows remain unavailable; the retired API cannot supply a backfill.
    NEAR_HEAD_WINDOW = datetime.timedelta(hours=24)

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        share_price, total_supply, _total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        # Strip the expected totalAssets revert error
        if errors:
            errors = [e for e in errors if "total_assets" not in e]

        # Offchain fetching is disabled. Retain the last known snapshot value
        # without issuing an API request for each historical observation.
        retained_tvl = self.vault.fetch_tvl()

        # Only write TVL for near-head rows — the retained metadata is a latest-known
        # snapshot and would be incorrect for older historical blocks.
        # Existing older observations must be preserved while API backfills are disabled.
        total_assets = None
        if retained_tvl is not None and timestamp is not None:
            age = native_datetime_utc_now() - timestamp
            if age <= self.NEAR_HEAD_WINDOW:
                total_assets = retained_tvl

        # Feed the retained TVL into the reader state so adaptive polling does not
        # degrade to faded/tiny cadence due to zero-TVL classification.
        convert_to_assets_result = call_by_name.get("convertToAssets")
        if convert_to_assets_result is not None and convert_to_assets_result.state is not None:
            convert_to_assets_result.state.on_called(
                convert_to_assets_result,
                total_assets=retained_tvl,
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
    - Atomic Transparency Ledger for real-time onchain-verifiable reporting
    - Asynchronous request-then-claim redemption; funds keep earning until claimed
    - `Homepage <https://www.forgeyields.com/>`__
    - `Documentation <https://forge-labs.gitbook.io/forge-docs>`__
    - `Audits <https://forge-labs.gitbook.io/forge-docs/other/audits>`__

    The TokenGateway contract does not implement ``totalAssets()``.
    Onchain ``convertToAssets(totalSupply())`` returns only the gateway residual.
    Cross-chain TVL uses the retained offchain metadata snapshot; API fetching is disabled.

    See :py:mod:`~eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata`.
    """

    @cached_property
    def forgeyields_metadata(self) -> ForgeYieldsVaultMetadata | None:
        """Read retained metadata for this Ethereum gateway.

        Cache the saved strategy entry for the adapter's lifetime without
        requesting the retired API or modifying the disk snapshot.

        :return: Last-known metadata, or ``None`` when no saved entry exists.
        """
        return fetch_forgeyields_vault_metadata(self.vault_address)

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return ForgeYieldsHistoricalReader(self, stateful=stateful)

    def fetch_tvl(self) -> Decimal | None:
        """Read retained cross-chain TVL in denomination token units.

        Returns the TVL in the vault's denomination token (ETH, USDC, WBTC),
        suitable for writing to ``total_assets`` in the price parquet.

        Offchain fetching is disabled because the API is no longer working.
        The saved metadata copy is read without expiry or modification; its
        TVL is the last known value, not a fresh API observation.

        :return:
            Total vault value in denomination token units, or ``None`` if unavailable.
        """
        strategies = fetch_forgeyields_strategies()
        key = self.vault_address.lower()
        meta = strategies.get(key)
        if meta is not None:
            return meta["tvl"]
        return None

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Not available — onchain value is the gateway residual, not the true AUM.

        The TokenGateway's ``convertToAssets(totalSupply())`` returns only
        the small residual held by the Ethereum gateway (~$12K), not the
        cross-chain AUM (~$1.8M). Use :py:meth:`fetch_nav` for the
        last-known TVL from the retained offchain metadata copy.

        :return:
            Always ``None``.
        """
        return None

    def fetch_nav(self, block_identifier=None) -> Decimal | None:
        """Read the last known TVL from retained offchain metadata.

        Returns saved cross-chain AUM in the vault's denomination token
        (ETH, USDC, WBTC). No fresh offchain observation is available.

        :return:
            Total vault value in denomination token units, or ``None``.
        """
        return self.fetch_tvl()

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
