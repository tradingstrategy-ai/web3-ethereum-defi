"""Frankencoin savings vault support.

Frankencoin is an over-collateralised, oracle-free Swiss franc stablecoin
protocol. Its Savings Vaults are ERC-4626 wrappers for the Frankencoin savings
module, allowing users to deposit ZCHF and receive svZCHF shares.

The vaults do not have protocol-wide management, performance, deposit, or
withdrawal fees. They do support an optional per-account referral fee that is
deducted from earned interest and paid to the configured referrer.

- Homepage: https://frankencoin.com/
- Token and savings vault page: https://frankencoin.com/token/
- Documentation: https://docs.frankencoin.com/
- GitHub: https://github.com/Frankencoin-ZCHF/Frankencoin
- Savings module source: https://github.com/Frankencoin-ZCHF/Frankencoin/blob/main/contracts/minting/v2/SavingsV2.sol
"""

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract

from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader

logger = logging.getLogger(__name__)

#: Minimal ABI for the svZCHF wrapper contract.
FRANKENCOIN_SAVINGS_VAULT_ABI = [
    {"inputs": [], "name": "savings", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
]

#: Frankencoin Savings Vault on Ethereum.
#:
#: https://etherscan.io/token/0xE5F130253fF137f9917C0107659A4c5262abf6b0
FRANKENCOIN_ETHEREUM_SAVINGS_VAULT = "0xe5f130253ff137f9917c0107659a4c5262abf6b0"

#: Legacy Frankencoin Savings Vault on Ethereum picked up by vault discovery.
#:
#: This wrapper points at the same Frankencoin savings module as the official
#: Ethereum svZCHF wrapper.
#:
#: https://etherscan.io/token/0x637F00cAb9665cB07d91bfB9c6f3fa8faBFEF8BC
FRANKENCOIN_ETHEREUM_LEGACY_SAVINGS_VAULT = "0x637f00cab9665cb07d91bfb9c6f3fa8fabfef8bc"

#: Frankencoin Savings Vault on Base.
#:
#: https://basescan.org/address/0xa09EBdf8A01b9ef04149319D64F83b9C01a5b585
FRANKENCOIN_BASE_SAVINGS_VAULT = "0xa09ebdf8a01b9ef04149319d64f83b9c01a5b585"

#: Frankencoin Savings Vault on Gnosis.
#:
#: https://gnosisscan.io/token/0x6165946250dd04740ab1409217e95a4f38374fe9
FRANKENCOIN_GNOSIS_SAVINGS_VAULT = "0x6165946250dd04740ab1409217e95a4f38374fe9"

#: ERC-4626 Frankencoin Savings Vault wrappers by chain.
FRANKENCOIN_SAVINGS_VAULTS_BY_CHAIN = {
    1: frozenset({FRANKENCOIN_ETHEREUM_SAVINGS_VAULT, FRANKENCOIN_ETHEREUM_LEGACY_SAVINGS_VAULT}),
    8453: frozenset({FRANKENCOIN_BASE_SAVINGS_VAULT}),
    100: frozenset({FRANKENCOIN_GNOSIS_SAVINGS_VAULT}),
}

#: Frankencoin Savings Vault addresses across supported chains.
FRANKENCOIN_SAVINGS_VAULTS = frozenset(vault_address for vaults in FRANKENCOIN_SAVINGS_VAULTS_BY_CHAIN.values() for vault_address in vaults)

#: Frankencoin wrappers that represent the full savings product TVL.
#:
#: Ethereum has two svZCHF wrappers that point at the same savings module. Only
#: the legacy wrapper is currently discovered in production data, so it is the
#: canonical row for Ethereum product-level TVL.
FRANKENCOIN_PRODUCT_TVL_VAULTS_BY_CHAIN = {
    1: frozenset({FRANKENCOIN_ETHEREUM_LEGACY_SAVINGS_VAULT}),
    8453: frozenset({FRANKENCOIN_BASE_SAVINGS_VAULT}),
    100: frozenset({FRANKENCOIN_GNOSIS_SAVINGS_VAULT}),
}

#: Frankencoin Savings Vault addresses that report full savings product TVL.
FRANKENCOIN_PRODUCT_TVL_VAULTS = frozenset(vault_address for vaults in FRANKENCOIN_PRODUCT_TVL_VAULTS_BY_CHAIN.values() for vault_address in vaults)

#: Maximum optional referral fee in the Frankencoin savings module.
#:
#: Source: ``AbstractSavings.setReferrer()`` rejects values above 250,000 ppm.
MAX_REFERRAL_FEE = 0.25

#: Number of ZCHF balance calls needed for Frankencoin savings TVL.
FRANKENCOIN_SAVINGS_BALANCE_CALL_COUNT = 2


class FrankencoinHistoricalReader(ERC4626HistoricalReader):
    """Read Frankencoin savings TVL across the wrapper and savings module.

    Frankencoin's ERC-4626 ``totalAssets()`` only reports assets attributed to
    the svZCHF wrapper inside the savings module. Most savings deposits sit
    directly in the underlying savings module, outside the ERC-4626 wrapper.

    For Trading Strategy vault TVL we treat the savings product as a whole and
    write ``ZCHF.balanceOf(savings_module) + ZCHF.balanceOf(svZCHF_wrapper)`` to
    ``total_assets``. The share price and total supply still come from the
    ERC-4626 wrapper, so performance calculations keep using the wrapper's own
    exchange rate.
    """

    def get_warmup_calls(self) -> Iterable[tuple[str, callable, object]]:
        """Yield warmup calls for Frankencoin vaults.

        Includes the standard ERC-4626 calls plus the ZCHF balances that define
        the savings product TVL.

        :return:
            Tuples consumed by the vault warmup scanner.
        """
        yield from super().get_warmup_calls()

        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return

        if not self.vault.reports_savings_product_tvl:
            return

        wrapper_balance_call = denomination_token.contract.functions.balanceOf(self.vault.address)
        yield ("wrapper_balance", wrapper_balance_call.call, wrapper_balance_call)

        savings_balance_call = denomination_token.contract.functions.balanceOf(self.vault.savings_module_address)
        yield ("savings_module_balance", savings_balance_call.call, savings_balance_call)

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Create calls for Frankencoin historical reads.

        :return:
            Encoded calls for ERC-4626 state and Frankencoin savings TVL.
        """
        yield from self.construct_core_erc_4626_multicall()
        if self.vault.reports_savings_product_tvl:
            yield from self.construct_savings_balance_multicalls()

    def construct_savings_balance_multicalls(self) -> Iterable[EncodedCall]:
        """Create ZCHF balance calls used to calculate savings product TVL.

        :return:
            Encoded ZCHF ``balanceOf`` calls for the savings module and wrapper.
        """
        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return

        wrapper_balance_call = EncodedCall.from_contract_call(
            denomination_token.contract.functions.balanceOf(self.vault.address),
            extra_data={
                "function": "wrapper_balance",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield wrapper_balance_call

        savings_balance_call = EncodedCall.from_contract_call(
            denomination_token.contract.functions.balanceOf(self.vault.savings_module_address),
            extra_data={
                "function": "savings_module_balance",
                "vault": self.vault.address,
                "savings_module": self.vault.savings_module_address,
            },
            first_block_number=self.first_block,
        )
        yield savings_balance_call

    def process_savings_tvl_result(self, call_by_name: dict[str, EncodedCallResult]) -> tuple[Decimal | None, list[str]]:
        """Decode Frankencoin savings TVL calls.

        :param call_by_name:
            Multicall results keyed by ``extra_data["function"]``.

        :return:
            ``(total_assets, errors)`` where ``total_assets`` is denominated in
            ZCHF.
        """
        errors = []
        denomination_token = self.vault.denomination_token
        if denomination_token is None:
            return None, ["denomination token missing"]

        balances = []
        for function_name in ("savings_module_balance", "wrapper_balance"):
            result = call_by_name.get(function_name)
            if result is None:
                errors.append(f"{function_name} call missing")
                continue
            if not result.success:
                errors.append(f"{function_name} call failed")
                continue

            raw_balance = convert_int256_bytes_to_int(result.result)
            balances.append(denomination_token.convert_to_decimals(raw_balance))

        if len(balances) != FRANKENCOIN_SAVINGS_BALANCE_CALL_COUNT:
            return None, errors

        return sum(balances, Decimal(0)), errors

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Process a Frankencoin historical read result.

        :param block_number:
            Block number used for the read.
        :param timestamp:
            Naive UTC block timestamp.
        :param call_results:
            Multicall results for this vault.

        :return:
            Historical row with Frankencoin savings product TVL for canonical
            wrappers, or regular ERC-4626 wrapper TVL for duplicate wrappers.
        """
        if not self.vault.reports_savings_product_tvl:
            return super().process_result(block_number, timestamp, call_results)

        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # The generic ERC-4626 decoder updates adaptive reader state from
        # totalAssets(); Frankencoin needs the state update to use combined TVL.
        total_assets_result = call_by_name.get("total_assets")
        original_total_assets_state = total_assets_result.state if total_assets_result is not None else None
        if total_assets_result is not None:
            total_assets_result.state = None
        try:
            share_price, total_supply, erc4626_total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)
        finally:
            if total_assets_result is not None:
                total_assets_result.state = original_total_assets_state

        savings_total_assets, savings_errors = self.process_savings_tvl_result(call_by_name)
        if savings_errors:
            errors = list(errors or [])
            errors.extend(savings_errors)

        total_assets = savings_total_assets if savings_total_assets is not None else erc4626_total_assets

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


class FrankencoinVault(ERC4626Vault):
    """Frankencoin ERC-4626 savings vault support.

    Frankencoin Savings Vaults tokenise deposits into the Frankencoin savings
    module. The underlying contract source documents an interest delay of up to
    three days before deposits start earning yield.
    """

    @property
    def name(self) -> str:
        """Return a human-readable name for this vault.

        The on-chain share token name is ``SavingsVault ZCHF``. Use the
        protocol-facing product name in vault listings.
        """
        return "Frankencoin Savings Vault"

    @cached_property
    def frankencoin_vault_contract(self) -> Contract:
        """Return the svZCHF wrapper contract with Frankencoin-specific ABI.

        :return:
            Web3 contract instance exposing the ``savings()`` accessor.
        """
        return self.web3.eth.contract(
            address=self.address,
            abi=FRANKENCOIN_SAVINGS_VAULT_ABI,
        )

    @cached_property
    def savings_module_address(self) -> HexAddress:
        """Return the underlying Frankencoin savings module address.

        :return:
            Savings module address used by this svZCHF wrapper.
        """
        return self.frankencoin_vault_contract.functions.savings().call()

    @cached_property
    def reports_savings_product_tvl(self) -> bool:
        """Whether this wrapper is the canonical full-product TVL row.

        :return:
            ``True`` if this wrapper should report savings-module TVL.
        """
        return self.address.lower() in FRANKENCOIN_PRODUCT_TVL_VAULTS

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Return Frankencoin savings product TVL.

        Frankencoin's ERC-4626 wrapper only reports assets attributed to wrapper
        shareholders. The public savings product also includes direct deposits
        in the savings module. For canonical vault discovery rows, report the
        ZCHF held by both the module and the wrapper contract.

        Duplicate wrappers fall back to regular ERC-4626 ``totalAssets()`` so
        exports do not count the same savings module TVL twice.

        :param block_identifier:
            Block number to read.

        :return:
            Total ZCHF held by the Frankencoin savings module and wrapper, or
            wrapper-only TVL for duplicate wrappers.
        """
        if not self.reports_savings_product_tvl:
            return super().fetch_total_assets(block_identifier)

        denomination_token = self.denomination_token
        if denomination_token is None:
            return None

        savings_balance = denomination_token.contract.functions.balanceOf(self.savings_module_address).call(block_identifier=block_identifier)
        wrapper_balance = denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier)
        return denomination_token.convert_to_decimals(savings_balance + wrapper_balance)

    def fetch_nav(self, block_identifier: BlockIdentifier | None = None) -> Decimal | None:
        """Fetch the Frankencoin savings product NAV.

        :param block_identifier:
            Block number to read.

        :return:
            Same value as :py:meth:`fetch_total_assets`.
        """
        return self.fetch_total_assets(block_identifier or "latest")

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal:
        """Get the svZCHF wrapper share price.

        The Frankencoin TVL override represents the whole savings product, not
        only svZCHF shareholders. Therefore share price must still come from
        ``convertToAssets(1 share)`` instead of ``total_assets / total_supply``.

        :param block_identifier:
            Block number to read.

        :return:
            svZCHF share price in ZCHF.
        """
        one_share = self.share_token.convert_to_raw(Decimal(1))
        raw_amount = self.vault_contract.functions.convertToAssets(one_share).call(block_identifier=block_identifier)
        return self.denomination_token.convert_to_decimals(raw_amount)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:  # noqa: FBT001
        """Return the Frankencoin historical reader.

        :param stateful:
            Whether the reader maintains adaptive polling state.

        :return:
            Frankencoin-specific historical reader.
        """
        return FrankencoinHistoricalReader(self, stateful=stateful)

    def has_custom_fees(self) -> bool:
        """Frankencoin has an optional per-account referral fee.

        Frankencoin does not charge fixed vault-level management, performance,
        deposit, or withdrawal fees. However, a user can configure a referrer
        that receives up to 25% of the earned interest, which is account-level
        fee data outside the shared protocol fee fields.
        """
        _ = self.vault_address
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return the vault management fee.

        Frankencoin Savings Vaults do not charge a protocol-wide management fee
        at the vault layer. Yield comes from the Frankencoin savings module.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Management fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return the vault performance fee.

        Frankencoin Savings Vaults do not charge a protocol-wide performance
        fee at the vault layer. A separate optional referral fee can skim up to
        25% of earned interest for accounts that configure a referrer.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Performance fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Return the savings module interest delay as a lock-up estimate.

        The verified ``SavingsV2`` source documents that saved ZCHF is subject
        to a lock-up of up to three days before it starts earning interest.

        :return:
            Estimated savings delay.
        """
        _ = self.vault_address
        return datetime.timedelta(days=3)

    def get_link(self, referral: str | None = None) -> str:
        """Return the Frankencoin token and savings vault page."""
        _ = self.vault_address, referral
        return "https://frankencoin.com/token/#svzchf"
