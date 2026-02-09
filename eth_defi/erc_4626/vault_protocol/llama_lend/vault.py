"""Llama Lend vault support.

Llama Lend is powered by the liquidation protection mechanism provided by LLAMMA
(Lending Liquidating Automated Market Maker Algorithm).
"""

import datetime
import logging
from decimal import Decimal
from functools import cached_property
from typing import Iterable

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader, VaultTechnicalRisk


logger = logging.getLogger(__name__)


class LlamaLendVaultHistoricalReader(ERC4626HistoricalReader):
    """Read Llama Lend vault core data + utilisation."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        yield from self.construct_core_erc_4626_multicall()
        yield from self.construct_utilisation_calls()

    def construct_utilisation_calls(self) -> Iterable[EncodedCall]:
        """Add idle assets call for utilisation calculation.

        Llama Lend uses idle assets pattern: asset().balanceOf(vault)
        """
        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return

        idle_call = EncodedCall.from_contract_call(
            denomination_token.contract.functions.balanceOf(self.vault.address),
            extra_data={
                "function": "idle_assets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield idle_call

    def process_utilisation_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
        total_assets: Decimal | None,
    ) -> tuple[Decimal | None, Percent | None]:
        """Decode Llama Lend utilisation data.

        Utilisation = (totalAssets - idle) / totalAssets
        """
        idle_result = call_by_name.get("idle_assets")

        if idle_result is None or total_assets is None:
            return None, None

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return None, None

        idle_raw = int.from_bytes(idle_result.result[0:32], byteorder="big")
        available_liquidity = denomination_token.convert_to_decimals(idle_raw)

        if total_assets == 0:
            utilisation = 0.0
        else:
            total_assets_raw = denomination_token.convert_to_raw(total_assets)
            utilisation = (total_assets_raw - idle_raw) / total_assets_raw

        return available_liquidity, utilisation

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
        available_liquidity, utilisation = self.process_utilisation_result(call_by_name, total_assets)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=0.0,
            management_fee=0.0,
            errors=errors,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )


class LlamaLendVault(ERC4626Vault):
    """Llama Lend vaults.

    Llama Lend is Curve Finance's lending protocol powered by the liquidation protection mechanism
    provided by LLAMMA (Lending Liquidating Automated Market Maker Algorithm).

    LLAMMA is the market-making contract that rebalances the collateral of a loan. It is an algorithm
    implemented into a smart contract which is responsible for liquidating and de-liquidating collateral
    based on market conditions through arbitrage traders. Each individual market has its own AMM
    containing the collateral and borrowable asset. E.g. the AMM of the ETH<>crvUSD contains of ETH and crvUSD.

    - `LLAMMA explained <https://docs.curve.finance/crvUSD/amm/>`__
    - `Vault smart contract code: <https://arbiscan.io/address/0xe296ee7f83d1d95b3f7827ff1d08fe1e4cf09d8d#code>`__
    - `Llama Lend markets <https://www.curve.finance/llamalend/ethereum/markets>`__
    """

    @cached_property
    def name(self) -> str:
        """Get vault name."""
        return f"Llama Lend {self.collateral_token.symbol} / {self.denomination_token.symbol}"

    def get_risk(self) -> VaultTechnicalRisk | None:
        return VaultTechnicalRisk.low

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="llama-lend/Vault.json",
        )

    @cached_property
    def borrowed_token(self) -> TokenDetails:
        """The token we are lending against."""
        addr = self.vault_contract.functions.borrowed_token().call()
        return fetch_erc20_details(
            self.web3,
            addr,
            cache=self.token_cache,
        )

    @cached_property
    def collateral_token(self) -> TokenDetails:
        """The token we are lending against."""
        addr = self.vault_contract.functions.collateral_token().call()
        return fetch_erc20_details(
            self.web3,
            addr,
            cache=self.token_cache,
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """AMM fee is not exposed and internalised."""
        return 0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """AMM fee is not exposed and internalised."""
        return 0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://www.curve.finance/lend/{chain_name}/markets/{self.vault_address}/"

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get Llama Lend-specific historical reader with utilisation metrics."""
        return LlamaLendVaultHistoricalReader(self, stateful)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Get the amount of denomination token available for immediate withdrawal.

        Uses the idle assets pattern: asset().balanceOf(vault) returns unallocated assets.

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Amount in denomination token units (human-readable Decimal).
        """
        try:
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None
            idle_raw = denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier)
            return denomination_token.convert_to_decimals(idle_raw)
        except Exception:
            return None

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Get the percentage of assets currently lent out.

        Utilisation = (totalAssets - idle) / totalAssets

        :param block_identifier:
            Block to query. Defaults to "latest".

        :return:
            Utilisation as float between 0.0 and 1.0 (0% to 100%).
        """
        try:
            denomination_token = self.denomination_token
            if denomination_token is None:
                return None

            total_assets = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
            idle = denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier)

            if total_assets == 0:
                return 0.0
            return (total_assets - idle) / total_assets
        except Exception:
            return None
