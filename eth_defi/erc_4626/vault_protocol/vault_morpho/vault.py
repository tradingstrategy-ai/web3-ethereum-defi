"""Deposit-cap and idle-liquidity support for the unattributed VaultMorpho implementation.

The contract uses Morpho Blue markets but is not canonical MetaMorpho. Its
verified deposit cap belongs to this implementation; it does not establish
Morpho protocol identity, API coverage, fees, permissions or withdrawal timing.

See :ref:`vault-morpho-unattributed` for source provenance and classification.
"""

import datetime
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from functools import cached_property

from eth_typing import HexAddress
from web3.types import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault, ERC4626HistoricalReader
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader
from eth_defi.vault.strategy_tag import StrategyTag


class VaultMorphoHistoricalReader(ERC4626HistoricalReader):
    """Retain measured idle assets without importing Morpho fee assumptions."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Read standard vault values and the unallocated token balance together.

        :return: Existing core calls plus one ABI-backed balance read, without a fee call.
        """
        yield from super().construct_multicalls()
        token = self.vault.denomination_token
        if token is not None:
            yield EncodedCall.from_contract_call(token.contract.functions.balanceOf(self.vault.address), extra_data={"function": "idle_assets", "vault": self.vault.address}, first_block_number=self.first_block)

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Extend the common result with the observed idle balance and allocation fraction.

        :param block_number: Source block shared by all observations.
        :param timestamp: Naive UTC timestamp of that block.
        :param call_results: Results of the reader's existing multicall batch.
        :return: Core prices and unknown fees, with idle metrics when their read succeeded.
        """
        result = super().process_result(block_number, timestamp, call_results)
        idle = self.dictify_multicall_results(block_number, call_results).get("idle_assets")
        token = self.vault.denomination_token
        if idle is None or not idle.success or len(idle.result) != 32 or token is None or result.total_assets is None:
            return result
        raw_idle = int.from_bytes(idle.result, "big")
        raw_total = token.convert_to_raw(result.total_assets)
        return replace(result, available_liquidity=token.convert_to_decimals(raw_idle), utilisation=(raw_total - raw_idle) / raw_total if raw_total else 0.0)


class VaultMorphoDepositManager(ERC4626DepositManager):
    """Retain the standard request flow while enforcing the fork's TVL cap."""

    def _is_redemption_capacity_rounding(self, raw_shares: int, maximum: int) -> bool:
        """Bound the verified maxRedeem round-down difference to one raw asset unit."""
        return 0 < maximum < raw_shares and self.vault.vault_contract.functions.previewRedeem(raw_shares - maximum).call() <= 1

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int:
        """Use remaining TVL capacity even when maxDeposit advertises more.

        :param owner: Account used for the ordinary ERC-4626 maximum.
        :return: Remaining raw denomination units, bounded by a reported maximum.
        """
        maximum = super().fetch_depositable_raw_assets(owner)
        remaining = self.vault.fetch_deposit_capacity()
        return min(maximum, remaining) if maximum is not None else remaining


class VaultMorphoVault(ERC4626Vault):
    """Read an unattributed capped vault without inheriting Morpho protocol metadata.

    The implementation uses Morpho Blue, but its operator and upstream source
    repository are unidentified. Generic metadata retains unknown fees, access
    policy and withdrawal timing instead of borrowing canonical Morpho defaults.
    """

    def get_strategy_tags(self) -> set[StrategyTag]:
        """Retain source-proven lending behaviour without asserting the operator's identity.

        :return: Lending plus independently maintained deployment tags.
        """
        return (super().get_strategy_tags() or set()) | {StrategyTag.lending}

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Preserve historical liquidity measurements independently of protocol attribution.

        :param stateful: Use the existing ordered-reader state when requested.
        :return: Standard ERC-4626 history with one additional idle-balance observation.
        """
        return VaultMorphoHistoricalReader(self, stateful)

    def fetch_lending_snapshot(self, total_assets: Decimal, block_identifier: BlockIdentifier) -> tuple[Decimal | None, Percent | None]:
        """Keep the verified idle-assets measurement while removing branded metadata.

        :param total_assets: TVL already observed in denomination-token units.
        :param block_identifier: Source block shared with the TVL read.
        :return: Idle token balance and the fraction allocated to underlying markets.
        """
        return self.fetch_idle_lending_snapshot(total_assets, block_identifier)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Report idle tokens, excluding liquidity that would require a market withdrawal.

        :param block_identifier: Source block for the token balance.
        :return: Idle denomination-token units, or None without a denomination token.
        """
        token = self.denomination_token
        if token is None:
            return None
        return token.convert_to_decimals(token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier))

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Retain the observed allocation fraction rather than a protocol default.

        :param block_identifier: Common source block for total and idle assets.
        :return: Fraction of assets outside the vault's idle balance.
        """
        return self.fetch_idle_lending_snapshot(self.fetch_total_assets(block_identifier), block_identifier)[1]

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the implementation ABI so the extra cap has a typed getter."""
        return get_deployed_contract(self.web3, "vault_morpho/VaultMorpho.json", self.address)

    def fetch_deposit_capacity(self) -> int:
        """Match the deposit's totalAssets + assets <= tvlCap condition.

        :return: Remaining raw denomination units, floored at zero.
        """
        block = self.default_block_identifier if self.default_block_identifier is not None else self.web3.eth.block_number
        functions = self.vault_contract.functions
        return max(0, functions.tvlCap().call(block_identifier=block) - functions.totalAssets().call(block_identifier=block))

    def fetch_deposit_closed_reason(self) -> str | None:
        """Make a full global cap visible to the existing closure checks.

        :return: Cap refusal or the inherited ERC-4626 assessment.
        """
        if self.fetch_deposit_capacity() == 0:
            return "VaultMorpho tvlCap reached"
        return super().fetch_deposit_closed_reason()

    def get_deposit_manager(self) -> VaultMorphoDepositManager:
        """Keep the cap check in the protocol's ordinary request path."""
        return VaultMorphoDepositManager(self)
