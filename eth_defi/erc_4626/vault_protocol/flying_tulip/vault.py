"""Flying Tulip sftUSD vault adapter and contextual historical reader.

sftUSD is an ERC-4626 vault with separately claimable FT rewards. The
contextual reader emits a non-redeemable, reinvested
``share_price_equivalence`` history while ordinary live ERC-4626 calls retain
the contract-reported principal accounting.

Only the reviewed chain/address pairs in the deployment registry instantiate
this adapter. The scanner does not use contract selectors to discover or map
Flying Tulip vault equivalents.
"""

import datetime
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_FT_BY_CHAIN, FLYING_TULIP_NOTES, FLYING_TULIP_SFTUSD_BY_CHAIN, FLYING_TULIP_SHORT_DESCRIPTION, FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.flying_tulip.historical_context import FlyingTulipHistoricalContextStore, FlyingTulipSharePriceObservation
from eth_defi.erc_4626.vault_protocol.flying_tulip.tags import STRATEGY_TAGS
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManager, VaultDepositManagerCapability
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags
from eth_defi.vault.vaultdb import get_pipeline_data_dir

#: Stable explanation returned until the circuit-breaker queue transaction
#: lifecycle is implemented and verified on a shared Anvil fork.
FLYING_TULIP_UNSUPPORTED_FLOW_REASON = "Flying Tulip redemption may return a circuit-breaker queue ID; generic ERC-4626 transactions are unsupported until the queued lifecycle is implemented"


def get_flying_tulip_historical_context_path() -> Path:
    """Return the shared Flying Tulip contextual-cache path.

    :return:
        Common protocol-owned DuckDB cache filename.
    """

    return get_pipeline_data_dir() / "vault-historical-context.duckdb"


class FlyingTulipHistoricalReader(VaultHistoricalReader):
    """Read replayed FT distribution-adjusted share-price equivalents."""

    @property
    def uses_contextual_history(self) -> bool:
        """Select the source-event history path rather than static multicalls.

        :return:
            Always ``True``.
        """

        return True

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Return no static calls because the price series is epoch sourced.

        :return:
            Empty iterable.
        """

        return ()

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Reject the generic fixed-block history path.

        :param block_number:
            Unused static-call block.
        :param timestamp:
            Unused static-call timestamp.
        :param call_results:
            Unused static-call results.
        :return:
            Never returns.
        :raises RuntimeError:
            Always, because contextual replay owns this history.
        """

        del block_number, timestamp, call_results
        raise RuntimeError("Flying Tulip historical prices come from cached EpochSettled rewards and FT/ftUSD prices")

    def _create_share_price_read(self, observation: FlyingTulipSharePriceObservation) -> VaultHistoricalRead:
        """Convert one deterministic replay result to the common row type.

        :param observation:
            Reward-adjusted source replay result.
        :return:
            Synthetic performance-value row, explicitly not a redemption quote.
        """

        timestamp = datetime.datetime.fromtimestamp(observation.block_timestamp, tz=datetime.UTC).replace(tzinfo=None)
        return VaultHistoricalRead(
            vault=self.vault,
            block_number=observation.block_number,
            timestamp=timestamp,
            share_price=observation.share_price,
            total_assets=observation.total_assets,
            total_supply=observation.total_supply,
            performance_fee=None,
            management_fee=None,
            errors=None,
            deposits_open=None,
            redemption_open=None,
        )

    def fetch_contextual_historical_reads(
        self,
        start_block: int,
        end_block: int,
        step: int,
    ) -> Iterable[VaultHistoricalRead]:
        """Yield stored price-equivalence rows for the common Parquet writer.

        :param start_block:
            Inclusive caller block boundary.
        :param end_block:
            Exclusive caller block boundary.
        :param step:
            Common scanner's approximate block bucket width.
        :return:
            Sparse, reward-adjusted historical rows.
        """

        with FlyingTulipHistoricalContextStore(self.vault.historical_context_path) as store:
            observations = store.iter_share_price_observations(
                chain_id=self.vault.chain_id,
                vault_address=self.vault.vault_address,
                asset_decimals=self.vault.denomination_token.decimals,
                reward_decimals=self.vault.reward_token.decimals,
                start_block=start_block,
                end_block=end_block,
                step=step,
            )
            yield from (self._create_share_price_read(observation) for observation in observations)


class FlyingTulipVault(ERC4626Vault):
    """Read-only Flying Tulip sftUSD adapter.

    The adapter retains regular ERC-4626 principal methods. It deliberately
    refuses generic deposits/redemptions, because a redemption can transfer
    value to Flying Tulip's circuit breaker and return a queue ID instead of
    immediately delivering ftUSD.
    """

    def __init__(self, web3: Web3, spec: VaultSpec, **kwargs) -> None:
        """Create an adapter only for a reviewed sftUSD proxy.

        :param web3:
            Web3 connection for a reviewed deployment chain.
        :param spec:
            Chain and sftUSD proxy specification.
        :param kwargs:
            Common ERC-4626 adapter keyword arguments.
        """

        expected_address = FLYING_TULIP_SFTUSD_BY_CHAIN.get(spec.chain_id)
        if expected_address is None or spec.vault_address.lower() != expected_address.lower():
            raise ValueError(f"Unsupported Flying Tulip sftUSD deployment: {spec.chain_id}:{spec.vault_address}")
        features = set(kwargs.pop("features", set()) or ()) | {ERC4626Feature.flying_tulip_like, ERC4626Feature.share_price_equivalence}
        super().__init__(web3, spec, features=features, **kwargs)
        self.historical_context_path = get_flying_tulip_historical_context_path()

    @property
    def short_description(self) -> str:
        """Return the maintained sftUSD scanner summary.

        :return:
            Concise description explaining the external reward model.
        """

        return FLYING_TULIP_SHORT_DESCRIPTION

    def fetch_reward_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Return the reviewed FT reward-token address for this deployment.

        The finite sftUSD deployment registry supplies this address directly,
        so vault identification and reward-token mapping require no ABI
        selectors. ``block_identifier`` remains accepted to preserve the
        common vault-adapter interface.

        :param block_identifier:
            Ignored historical or current state block.
        :return:
            Checksum FT token address.
        """

        del block_identifier
        return FLYING_TULIP_FT_BY_CHAIN[self.chain_id]

    @cached_property
    def reward_token(self) -> TokenDetails:
        """Fetch FT metadata on the active deployment chain.

        :return:
            Cached FT token details.
        """

        return fetch_erc20_details(self.web3, self.fetch_reward_token_address(), chain_id=self.chain_id, cache=self.token_cache, cause_diagnostics_message=f"Flying Tulip FT reward token for {self.vault_address}")

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Return the contextual external-reward history reader.

        :param stateful:
            Retained common-reader compatibility argument.
        :return:
            Flying Tulip's source-event reader.
        """

        del stateful
        return FlyingTulipHistoricalReader(self)

    def get_deposit_manager(self) -> VaultDepositManager:
        """Refuse unsafe generic ERC-4626 request construction.

        :return:
            Never returns.
        :raises NotImplementedError:
            Queue-aware lifecycle support is pending.
        """

        raise NotImplementedError(FLYING_TULIP_UNSUPPORTED_FLOW_REASON)

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability | None:
        """Hide transaction capability until both redemption outcomes are safe.

        :return:
            ``None`` so public metadata does not imply synchronous redemption.
        """

        return None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return Flying Tulip's zero direct management fee.

        :param block_identifier:
            Unused fee-read block.
        :return:
            ``0.0``.
        """

        del block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return Flying Tulip's zero direct performance fee.

        :param block_identifier:
            Unused fee-read block.
        :return:
            ``0.0``.
        """

        del block_identifier
        return 0.0

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the USDC-funded Flying Tulip vault-equivalent entry fee.

        The direct ftUSD-to-sftUSD conversion is fee-free. For consistent
        comparison with USDC-denominated vaults, the common fee field models
        the preceding USDC-to-ftUSD mint fee instead.

        :param block_identifier:
            Unused fee-read block.
        :return:
            Chain-specific externalised USDC entry fee.
        """

        del block_identifier
        return FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN[self.chain_id]

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Return the USDC-funded Flying Tulip vault-equivalent exit fee.

        The direct sftUSD-to-ftUSD conversion is fee-free. For consistent
        comparison with USDC-denominated vaults, the common fee field models
        the following ftUSD-to-USDC redemption fee instead.

        :param block_identifier:
            Unused fee-read block.
        :return:
            Chain-specific externalised USDC exit fee.
        """

        del block_identifier
        return FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN[self.chain_id]

    def get_notes(self) -> str:
        """Explain the USDC-equivalent entry and exit fee model.

        The fee fields deliberately represent entering from and returning to
        USDC so Flying Tulip can be compared with other vaults. The returned
        Markdown distinguishes those conversion fees from the fee-free direct
        sftUSD wrapping route and links to the authoritative deployment.

        :return:
            Protocol note with the fee route and operational caveats.
        """

        return FLYING_TULIP_NOTES

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return conservative, address-reviewed Flying Tulip strategy tags.

        :return:
            Tag copy, or ``None`` for an unclassified reviewed deployment.
        """

        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def get_link(self, referral: str | None = None) -> str:
        """Return Flying Tulip's ftUSD application page.

        :param referral:
            Unsupported referral code.
        :return:
            Public staking application URL.
        """

        del referral
        return "https://flyingtulip.com/ftusd/"

    def fetch_scan_record_extra_data(self) -> dict[str, object]:
        """Mark the historical curve as non-redeemable in scanner exports.

        :return:
            Common metadata plus explicit live and synthetic semantics.
        """

        return {
            **super().fetch_scan_record_extra_data(),
            "_historical_share_price_type": "share_price_equivalence",
            "_historical_share_price_redeemable": False,
            "_historical_total_assets_type": "share_price_equivalence",
            "_historical_total_assets_redeemable": False,
            "_external_reward_token": self.reward_token.address,
            "_protocol_notes": self.get_notes(),
        }
