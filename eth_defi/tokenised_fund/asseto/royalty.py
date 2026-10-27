"""Read-only Asseto RoyaltyVault support, still under verification.

`IP Vault <https://ipusd.xyz/>`__ uses ipUSD and represents a tokenised royalty
fund investment. It has ERC-4626-compatible valuation methods, but every
standard deposit/redemption entrypoint reverts. PreDeposit and RedeemQueue
implement a separate asynchronous lifecycle, which is not certified here.

The verified implementation and ABI provenance are recorded in
``eth_defi/abi/asseto/README.md``. Discovery is restricted to the Soneium proxy;
no protocol probes or Asseto API registry entries are used for this variant.
"""

# Inherited adapter signatures require positional booleans and static policy methods.
# ruff: noqa: ARG002, FBT001, FBT002, PLR0904, PLR0917, PLR6301

import datetime
from collections.abc import Iterable
from decimal import Decimal
from functools import cached_property
from typing import Any, Callable

from eth_typing import BlockIdentifier
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.tokenised_fund.asseto.royalty_constants import ROYALTY_CHAIN_ID, ROYALTY_VAULT_ADDRESS
from eth_defi.tokenised_fund.asseto.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.base import VaultBase, VaultFlowManager, VaultHistoricalRead, VaultHistoricalReader, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager, VaultDepositManagerCapability
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: ABI-encoded boolean occupies one complete EVM word.
ABI_WORD_BYTES = 32


class AssetoRoyaltyHistoricalReader(ERC4626HistoricalReader):
    """Read RoyaltyVault valuation methods without unsupported limit calls.

    Reported NAV includes in-flight settlement amounts and potentially older RWA
    valuations. It is not a measure of immediately redeemable cash.
    """

    def should_skip_call(self, function_name: str) -> bool:
        """Exclude the absent maxDeposit accessor from historical reads.

        Other methods retain the shared reader's adaptive call state.

        :param function_name: Internal reader call name.
        :return: Whether the call must be omitted.
        """
        return function_name == "maxDeposit" or super().should_skip_call(function_name)

    def get_warmup_calls(self) -> Iterable[tuple[str, Callable, Any]]:
        """Warm only the three supported valuation methods.

        RoyaltyVault has no maxDeposit method to warm or estimate.

        :return: Reader names, callables and contract calls.
        """
        functions = self.vault.vault_contract.functions
        for name, call in (
            ("total_assets", functions.totalAssets()),
            ("total_supply", functions.totalSupply()),
            ("convertToAssets", functions.convertToAssets(self.one_raw_share)),
        ):
            yield name, call.call, call

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Read valuation inputs and the held assets' oracle freshness.

        Freshness is a post-classification valuation check, never a protocol
        detection probe. Stale fund prices must not become new observations.

        :return: Valuation and oracle-status calls for the bound vault.
        """
        yield from super().construct_multicalls()
        yield EncodedCall.from_contract_call(self.vault.vault_contract.functions.oraclesFresh(), extra_data={"function": "oraclesFresh", "vault": self.vault.address}, first_block_number=self.first_block)

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Reject stale or unavailable oracle status before advancing reader state.

        The contract's lenient valuation can retain an old oracle price. Keep
        that value out of fresh price observations while preserving old rows.

        :param block_number: Historical sample block.
        :param timestamp: Sample timestamp in naive UTC.
        :param call_results: Valuation and freshness results from that block.
        :return: Decoded sample, or an unpriced sample with the failure reason.
        """
        freshness = next((r for r in call_results if r.call.extra_data["function"] == "oraclesFresh"), None)
        if freshness is None or not freshness.success or freshness.block_identifier != block_number or len(freshness.result) != ABI_WORD_BYTES or int.from_bytes(freshness.result) != 1:
            return VaultHistoricalRead(vault=self.vault, block_number=block_number, timestamp=timestamp, share_price=None, total_assets=None, total_supply=None, management_fee=None, performance_fee=None, errors=["IP Vault RWA oracle freshness is stale or unavailable"])
        return super().process_result(block_number, timestamp, call_results)


class AssetoRoyaltyVault(ERC4626Vault):
    """Read the hardcoded IP Vault as an Asseto product under verification.

    The shared valuation reader is reusable, but generic transaction and flow
    managers are explicitly unsupported. Asseto's AoABT risk and fee assessment
    does not apply to this separate implementation.
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
        require_denomination_token: bool = False,
    ):
        """Bind only the known Soneium vault proxy.

        The implementation address and same-address deployments on other
        chains cannot be used with this adapter.

        :param web3: Configured Soneium connection.
        :param spec: Chain and proxy address.
        :param token_cache: Shared token metadata cache.
        :param features: Detected RoyaltyVault routing feature.
        :param default_block_identifier: Default metadata read block.
        :param require_denomination_token: Fail if ipUSD metadata is unavailable.
        """
        assert spec.chain_id == ROYALTY_CHAIN_ID and spec.vault_address.lower() == ROYALTY_VAULT_ADDRESS, "Unsupported Asseto RoyaltyVault deployment"
        super().__init__(web3, spec, token_cache=token_cache, features=features or {ERC4626Feature.asseto_royalty_like}, default_block_identifier=default_block_identifier, require_denomination_token=require_denomination_token)

    @cached_property
    def vault_contract(self) -> Contract:
        return get_deployed_contract(self.web3, "asseto/RoyaltyVault.json", self.vault_address)

    @property
    def short_description(self) -> str:
        return "IP Vault invests in a tokenised royalty fund through Asseto. Still under verification."

    @property
    def description(self) -> str:
        return "[IP Vault](https://ipusd.xyz/) accepts ipUSD for shares in a royalty investment portfolio. [Soneium announced the product with Asseto](https://x.com/soneium/status/2114999606400692706) on 27 October 2026. Asseto tokenises the underlying Partners Group Diversified Royalties Opportunities Evergreen fund. [Partners Group manages that fund but does not endorse IP Vault](https://ipusd.gitbook.io/ipusd-docs/general/the-underlying-fund-and-strategy). Holding ipUSD alone earns no royalty returns. [Withdrawals](https://ipusd.gitbook.io/ipusd-docs/user-guide/exiting-the-ip-vault) depend on available liquidity: instant withdrawals have a documented 5% fee, while standard withdrawals can take three to six months in low-liquidity conditions. IP Vault is still under verification."

    def get_link(self, referral: str | None = None) -> str:
        return "https://ipusd.xyz/"

    def get_flags(self) -> set[VaultFlag]:
        return VaultBase.get_flags(self) | {VaultFlag.review_needed}

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        return None

    def has_custom_fees(self) -> bool:
        return True

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def can_check_deposit(self) -> bool:
        return False

    def can_check_redeem(self) -> bool:
        return False

    def supports_generic_deposit_manager(self) -> bool:
        return False

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability | None:
        return None

    def get_deposit_manager(self) -> VaultDepositManager:
        message = "Asseto IP Vault queued deposits and redemptions are still under verification"
        raise NotImplementedError(message)

    def get_flow_manager(self) -> VaultFlowManager:
        message = "Asseto IP Vault custom flow-event accounting is not implemented"
        raise NotImplementedError(message)

    def has_block_range_event_support(self) -> bool:
        return False

    def has_deposit_distribution_to_all_positions(self) -> bool:
        return False

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        return AssetoRoyaltyHistoricalReader(self, stateful=stateful)

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """Read the contract's share conversion when its held oracles are fresh.

        RoyaltyVault specifies a one-to-one initial conversion at zero supply.
        Use that same conversion in current reads and the historical reader.

        :param block_identifier: Block at which to read valuation and freshness.
        :return: ipUSD per share, or None when the RWA valuation is stale.
        """
        if not self.vault_contract.functions.oraclesFresh().call(block_identifier=block_identifier):
            return None
        shares = self.share_token.convert_to_raw(Decimal(1))
        assets = self.vault_contract.functions.convertToAssets(shares).call(block_identifier=block_identifier)
        return self.denomination_token.convert_to_decimals(assets)
