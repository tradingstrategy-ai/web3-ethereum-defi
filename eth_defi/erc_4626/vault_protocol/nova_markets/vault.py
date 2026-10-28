"""Nova Markets Conviction Pool, an independently operated Gains fork.

Reuses the compatible epoch reader and deposit/redemption manager while
maintaining an independent protocol identity. Nova documents the exact
deployment, administrative control and unchanged Gains code in its
`contract disclosures <https://docs.nova.markets/#/contracts>`__.
"""

import datetime

from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_MARKETS_CHAIN_ID, NOVA_POOL_APP, NOVA_POOL_NOTES
from eth_defi.erc_4626.vault_protocol.nova_markets.tags import STRATEGY_TAGS
from eth_defi.vault.fee import FeeData
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags


class NovaMarketsVault(GainsVault):
    """Nova's USDC counterparty pool on Arbitrum, with epoch withdrawals.

    This adapter covers the exact pool published at
    https://docs.nova.markets/#/contracts. Nova's separate NLP vault on
    HyperEVM continues to use its Lagoon adapter.
    """

    @property
    def name(self) -> str:
        return "Nova Conviction Pool"

    @property
    def short_description(self) -> str:
        return "USDC counterparty liquidity for Nova's perpetual markets on Arbitrum."

    @property
    def description(self) -> str:
        return "Nova Markets operates the Conviction Pool, which supplies USDC liquidity to its perpetual markets and earns trading and borrowing fees while bearing trader PnL. Withdrawals require an epoch-based request and claim."

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301 - protocol adapter interface
        """Return Nova's official pool app.

        The destination is published by Nova's pool documentation.

        :param referral: Unused optional referral code.
        :return: Nova's liquidity-pools page.
        """
        del referral
        return NOVA_POOL_APP

    def get_notes(self) -> str | None:
        """Return Nova's strategy and operator notes, preserving manual warnings.

        A matching Gains codebase does not imply Gains Network affiliation.

        :return: Markdown note with strategy, withdrawal and source context.
        """
        return super().get_notes() or NOVA_POOL_NOTES

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the reviewed counterparty-liquidity strategy tags.

        The maintained mapping covers the Conviction Pool only.

        :return: A copy of the reviewed tags, or ``None`` when unmapped.
        """
        if self.chain_id != NOVA_MARKETS_CHAIN_ID:
            return None
        return lookup_strategy_tags(STRATEGY_TAGS, self.address)

    def get_fee_data(self) -> FeeData:
        """Return the source-reviewed direct Conviction Pool fee schedule.

        The implementation reviewed on 2026-10-28 charges no management,
        performance, entry or exit fee. Trading rewards and PnL affect share
        value. Nova's separate flat platform-funding charge is documented at
        https://docs.nova.markets/#/get-started and is not a direct vault fee.
        See the protocol README for the reproducible executable-source match.

        :return: Zero depositor fees with the reviewed protocol fee mode.
        """
        return FeeData(fee_mode=self.get_fee_mode(), management=0.0, performance=0.0, deposit=0.0, withdraw=0.0, protocol=0.0)

    def fetch_epoch_duration(self) -> datetime.timedelta:
        """Read the complete Nova epoch rather than one oracle-request interval.

        Nova's pool combines an initial window and several PnL sampling
        intervals. Its current rules describe three-day epochs at
        https://docs.nova.markets/#/pool-mechanics.

        :return: Complete epoch duration from the deployed PnL feed.
        """
        functions = self.open_pnl_contract.functions
        seconds = functions.requestsStart().call() + functions.requestsEvery().call() * functions.requestsCount().call()
        return datetime.timedelta(seconds=seconds)
