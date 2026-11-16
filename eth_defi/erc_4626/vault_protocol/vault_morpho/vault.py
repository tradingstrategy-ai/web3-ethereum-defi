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
from web3.types import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault, ERC4626HistoricalReader
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.exception import UnsupportedVaultProtocol


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
    """Reject new investment while retaining the existing redemption parser.

    Support for reading prices or recovering a legacy position does not establish
    a supported deposit lifecycle. All deposit construction paths are blocked.
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

    def can_create_deposit_request(self, owner: HexAddress) -> bool:
        """Block new deposits while leaving redemption/recovery available.

        :param owner: Prospective depositing account.
        :raises UnsupportedVaultProtocol: Always.
        """
        raise UnsupportedVaultProtocol("VaultMorpho protocol is unsupported")

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int:
        """Prevent deposit construction from bypassing the support decision.

        :param owner: Prospective depositing account.
        :raises UnsupportedVaultProtocol: Always.
        """
        raise UnsupportedVaultProtocol("VaultMorpho protocol is unsupported")


class VaultMorphoVault(ERC4626Vault):
    """Read an unattributed capped vault without inheriting Morpho protocol metadata.

    The implementation uses Morpho Blue, but its operator and upstream source
    repository are unidentified. Generic metadata retains unknown fees and
    withdrawal timing instead of borrowing canonical Morpho defaults. The integration
    remains unsupported for new investment on all deployments.

    In the 15 November 2026 simulation investigation, four proxies had reviewed
    implementation source and some completed fork trades. This established only
    observed contract behaviour: the operator/upstream attribution and maintained
    protocol integration were still missing. We therefore mark the whole family
    ``unsupported_protocol`` instead of certifying selected implementation addresses.
    See `the VaultMorpho support decision and affected-vault results
    <https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1782>`_.

    Consumers must preserve this status and exclude new investment as they do
    for permissioned vaults, regardless of owner, chain, cap or a successful probe.
    It is not evidence of an account whitelist and must not fall back to unknown
    or permissionless. Keep existing holdings visible for valuation and recovery;
    redemption availability still requires its own checks. Lifting this exclusion
    requires a properly supported integration, not another implementation exception.
    """

    #: Family-wide support decision, including previously successful fork cases.
    #: Block new investment without RPC; do not restore per-implementation exceptions.
    #: Rationale and evidence: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1782
    unsupported_protocol_reason = "VaultMorpho is an unsupported, unattributed protocol"

    def is_whitelisted_deposit(self) -> bool:
        """Report this unattributed protocol as unsupported on every chain.

        Source inspection of selected implementations is not a maintained
        protocol integration. No implementation-address exception makes it
        investable. The scanner exports ``unsupported_protocol`` and consumers
        exclude it in the same way as a permissioned vault, without claiming
        an actual account whitelist exists.

        :raises UnsupportedVaultProtocol: Always; this is a data-only adapter.
        """
        raise UnsupportedVaultProtocol("VaultMorpho is an unsupported, unattributed protocol")

    def is_account_whitelisted(self, address: HexAddress) -> bool:
        """Refuse investment regardless of the proposed custody owner.

        :param address: Prospective depositor.
        :raises UnsupportedVaultProtocol: Always.
        """
        raise UnsupportedVaultProtocol("VaultMorpho protocol is unsupported")

    def is_permissionless(self, owner: HexAddress) -> bool:
        """Keep unsupported integration distinct from proven permissionless policy.

        :param owner: Prospective custody owner.
        :raises UnsupportedVaultProtocol: Always.
        """
        raise UnsupportedVaultProtocol("VaultMorpho protocol is unsupported")

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
