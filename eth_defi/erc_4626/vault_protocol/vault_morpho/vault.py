"""Deposit-cap and idle-liquidity support for the unattributed VaultMorpho implementation.

The contract uses Morpho Blue markets but is not canonical MetaMorpho. Its
verified deposit cap belongs to this implementation; it does not establish
Morpho protocol identity, API coverage, fees, permissions or withdrawal timing.

See :ref:`vault-morpho-unattributed` for verified contract sources and the
limits of what they establish about the operator.
"""

import datetime
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from functools import cached_property

from eth_typing import HexAddress
from web3 import Web3
from web3.types import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault, ERC4626HistoricalReader
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader
from eth_defi.vault.strategy_tag import StrategyTag


#: ERC-1967 defines this storage slot as keccak256(label) - 1. Transparent
#: proxies need not expose an implementation() ABI getter, so read storage.
#: https://eips.ethereum.org/EIPS/eip-1967#logic-contract-address
ERC1967_IMPLEMENTATION_SLOT = int.from_bytes(Web3.keccak(text="eip1967.proxy.implementation"), "big") - 1

#: Only Ethereum deployments have been reviewed for this permission policy.
#: This limits our evidence; it is not a protocol-level chain restriction.
REVIEWED_PERMISSION_CHAIN_ID = 1

#: Implementation contracts, not depositor/Safe addresses. Verified sources
#: contain no account gates in deposit/mint/transfer/withdraw/redeem. The proxy
#: mapping, explorer links, source hash and review date are documented under
#: "Permission inspection" in docs/source/vaults/vault_morpho/index.rst and
#: eth_defi/abi/vault_morpho/README.md. Unknown upgrades must stay unknown.
REVIEWED_PUBLIC_IMPLEMENTATIONS = frozenset({
    "0xf392a5b96d3248440bb4841b4f50f0862060c1a3",
    "0xfa0d6e598d13e9220e5de48db4ed087c8f8847b3",
    "0x816dcddcb7949bd8556058b47e2f638b163d658d",
    "0x13d8602fa8dc916704194ec25bf73bffd6b39fed",
})


class VaultMorphoHistoricalReader(ERC4626HistoricalReader):
    """Preserve lending measurements without assuming Morpho fees apply.

    :class:`~eth_defi.erc_4626.vault.ERC4626HistoricalReader` supplies prices
    and asset totals. A separate token-balance read measures assets held in the
    vault itself; it does not estimate what can be withdrawn from its markets.
    """

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Read standard vault values and the unallocated token balance together.

        :return: Calls for :meth:`process_result`, including the idle token balance.
            The generic reader leaves unsupported fee information unknown.
        """
        yield from super().construct_multicalls()
        token = self.vault.denomination_token
        if token is not None:
            yield EncodedCall.from_contract_call(token.contract.functions.balanceOf(self.vault.address), extra_data={"function": "idle_assets", "vault": self.vault.address}, first_block_number=self.first_block)

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Keep missing liquidity observations distinct from a measured zero balance.

        An unsuccessful or malformed balance response leaves the inherited result
        unchanged. Valid balances use the same token units as total assets;
        utilisation is the fraction outside the vault, with zero for an empty vault.
        See :meth:`~eth_defi.erc_4626.vault.ERC4626Vault.fetch_idle_lending_snapshot`
        for the corresponding single-block calculation.

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
    """Apply the implementation's deposit cap before constructing a transaction.

    Its ``maxDeposit()`` can exceed the limit enforced by ``deposit()``.
    :meth:`fetch_depositable_raw_assets` applies
    :meth:`VaultMorphoVault.fetch_deposit_capacity` through the standard
    :class:`~eth_defi.erc_4626.deposit_redeem.ERC4626DepositManager` capacity check.
    """

    def _is_redemption_capacity_rounding(self, raw_shares: int, maximum: int) -> bool:
        """Permit exact simulation for a share excess worth at most one raw asset unit.

        The verified ``maxRedeem()`` rounds its asset-to-share conversion down;
        see :ref:`vault-morpho-unattributed` for the implementation source.
        The excess must convert to at most one smallest denomination-token unit.
        This only permits the parent request builder to simulate the exact
        redemption; that call must still succeed and return a positive payout.

        :param raw_shares: Requested shares in the share token's smallest units.
        :param maximum: Advertised share limit in the same units.
        :return: Whether the excess is small enough to attempt the exact simulation.
        """
        return 0 < maximum < raw_shares and self.vault.vault_contract.functions.previewRedeem(raw_shares - maximum).call() <= 1

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int:
        """Prevent a request from passing the market limit but exceeding the vault cap.

        :param owner: Account used for the ordinary ERC-4626 maximum.
        :return: Remaining raw denomination units, bounded by a reported maximum.
        """
        maximum = super().fetch_depositable_raw_assets(owner)
        remaining = self.vault.fetch_deposit_capacity()
        return min(maximum, remaining) if maximum is not None else remaining


class VaultMorphoVault(ERC4626Vault):
    """Read an unattributed capped vault without inheriting Morpho protocol metadata.

    The implementation uses Morpho Blue, but its operator and upstream source
    repository are unidentified. Generic metadata retains unknown fees and
    withdrawal timing instead of borrowing canonical Morpho defaults. Account
    policy is established only for the explicitly reviewed implementations.
    """

    def fetch_implementation(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Read the logic contract selected by this ERC-1967 proxy.

        This is proxy storage, not a VaultMorpho ABI function. Reading the slot
        avoids assuming that matching ERC-4626 getters imply matching account
        policy. It follows the existing Atoma adapter's proxy-reader pattern.

        :param block_identifier: Exact chain state whose implementation is read.
        :return: Lowercase, 0x-prefixed implementation address.
        :raises NotImplementedError: If the node returns a malformed storage word.
        """
        word = self.web3.eth.get_storage_at(self.address, ERC1967_IMPLEMENTATION_SLOT, block_identifier=block_identifier)
        if len(word) != 32:
            raise NotImplementedError(f"Malformed ERC-1967 implementation slot for {self.address} at block {block_identifier}")
        return HexAddress("0x" + bytes(word[-20:]).hex())

    def is_whitelisted_deposit(self) -> bool:
        """Classify only source-reviewed implementations as public deposits.

        This is an implementation-version support boundary, not a depositor
        whitelist. At Ethereum block 26402309 the four reviewed proxies used
        different implementation addresses with identical verified Solidity
        source. That source has no account gate in its ERC-4626 entry/exit or
        share-transfer methods. See :ref:`vault-morpho-unattributed` for the
        proxy-to-implementation table and source provenance.

        Re-read the proxy's implementation at the selected block on every
        assessment. A new implementation or chain is unknown until reviewed;
        matching getters, a successful deposit or a zero cap cannot establish
        its policy. Cap, liquidity and administrative roles remain separate.

        :return: False (no whitelist required) for a reviewed public implementation.
        :raises NotImplementedError: If chain or implementation policy is unknown.
        """
        if self.chain_id != REVIEWED_PERMISSION_CHAIN_ID:
            raise NotImplementedError(f"VaultMorpho permission policy is unreviewed on chain {self.chain_id}")
        block = self.default_block_identifier if self.default_block_identifier is not None else "latest"
        implementation = self.fetch_implementation(block)
        if implementation not in REVIEWED_PUBLIC_IMPLEMENTATIONS:
            raise NotImplementedError(f"Unreviewed VaultMorpho implementation {implementation} for proxy {self.address} at block {block}")
        return False

    def is_account_whitelisted(self, address: HexAddress) -> bool:
        """Allow any account only after verifying the public implementation.

        :param address: Prospective depositor.
        :return: True for a source-proven permissionless implementation.
        """
        return not self.is_whitelisted_deposit()

    def is_permissionless(self, owner: HexAddress) -> bool:
        """Use the reviewed implementation's public entry and exit policy.

        :param owner: Intended custody account.
        :return: True when the implementation is reviewed and ungated.
        """
        return not self.is_whitelisted_deposit()

    def get_strategy_tags(self) -> set[StrategyTag]:
        """Describe the lending confirmed by contract source without guessing its operator.

        :return: Lending plus independently maintained deployment tags.
        """
        return (super().get_strategy_tags() or set()) | {StrategyTag.lending}

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Preserve historical liquidity measurements independently of protocol attribution.

        :param stateful: Passed to the shared reader to retain state between blocks.
        :return: :class:`VaultMorphoHistoricalReader`, which also measures idle tokens.
        """
        return VaultMorphoHistoricalReader(self, stateful)

    def fetch_lending_snapshot(self, total_assets: Decimal, block_identifier: BlockIdentifier) -> tuple[Decimal | None, Percent | None]:
        """Derive the allocated fraction without assuming a Morpho protocol identity.

        Delegate to
        :meth:`~eth_defi.erc_4626.vault.ERC4626Vault.fetch_idle_lending_snapshot`
        so metadata scanning and direct reads use the same units and calculation.

        :param total_assets: Observed assets as a decimal token amount, not smallest units.
        :param block_identifier: Block used for the total-assets read.
        :return: Idle token balance and the fraction allocated to underlying markets.
        """
        return self.fetch_idle_lending_snapshot(total_assets, block_identifier)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Report idle tokens, excluding liquidity that would require a market withdrawal.

        :param block_identifier: Source block for the token balance.
        :return: Idle balance as a decimal token amount, or ``None`` if the
            denomination token is unknown.
        """
        token = self.denomination_token
        if token is None:
            return None
        return token.convert_to_decimals(token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier))

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Retain the observed allocation fraction rather than a protocol default.

        :param block_identifier: Common source block for total and idle assets.
        :return: Fraction of assets outside the vault's idle balance, zero for an
            empty vault, or ``None`` when the denomination token is unknown.
        """
        return self.fetch_idle_lending_snapshot(self.fetch_total_assets(block_identifier), block_identifier)[1]

    @cached_property
    def vault_contract(self) -> Contract:
        """Expose ``tvlCap()`` through the verified implementation's contract interface.

        :return: Contract proxy loaded by :func:`~eth_defi.abi.get_deployed_contract`.
        """
        return get_deployed_contract(self.web3, "vault_morpho/VaultMorpho.json", self.address)

    def fetch_deposit_capacity(self) -> int:
        """Account for the vault-wide limit omitted by ``maxDeposit()``.

        ``deposit()`` requires ``totalAssets() + assets <= tvlCap``. Read both
        values at the configured block, or one selected current block, so a new
        deposit between reads cannot distort the remaining capacity.
        See :ref:`vault-morpho-unattributed` for the verified Solidity source.

        :return: Remaining capacity in the denomination token's smallest units,
            with zero when assets reach or exceed the cap.
        """
        block = self.default_block_identifier if self.default_block_identifier is not None else self.web3.eth.block_number
        functions = self.vault_contract.functions
        return max(0, functions.tvlCap().call(block_identifier=block) - functions.totalAssets().call(block_identifier=block))

    def fetch_deposit_closed_reason(self) -> str | None:
        """Expose a full cap even when the standard maximum still permits deposits.

        :return: Explanation when :meth:`fetch_deposit_capacity` is zero;
            otherwise the inherited ERC-4626 assessment, which may be ``None``.
        """
        if self.fetch_deposit_capacity() == 0:
            return "VaultMorpho tvlCap reached"
        return super().fetch_deposit_closed_reason()

    def get_deposit_manager(self) -> VaultMorphoDepositManager:
        """Ensure callers receive the manager that enforces this implementation's cap.

        :return: :class:`VaultMorphoDepositManager` bound to this vault.
        """
        return VaultMorphoDepositManager(self)
