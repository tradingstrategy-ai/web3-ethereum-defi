# ruff: noqa: ARG002, PLR6301

"""Contextual share-price reader for Rysk Premium option pools."""

import datetime
from collections.abc import Iterable

from eth_defi.erc_4626.vault_protocol.rysk.historical_context import RyskHistoricalContextStore
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader


class RyskPremiumHistoricalReader(VaultHistoricalReader):
    """Read final Rysk Premium epoch withdrawal prices from local context.

    The reader adapts sparse, finalised onchain epoch events to the common
    historical interface without issuing block-by-block multicalls.
    """

    @property
    def uses_contextual_history(self) -> bool:
        """Select the Rysk event-backed contextual scanner branch.

        Rysk valuation is supplied by final epoch records, so the common
        scanner must invoke :meth:`fetch_contextual_historical_reads`.

        :return:
            Always ``True``.
        """

        return True

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Return no calls because Rysk prices are event-contextual.

        The method satisfies the shared reader interface but deliberately
        contributes no static EVM calls to a historical batch.

        :return:
            Empty iterator.
        """

        return ()

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Reject the ordinary static multicall route.

        Reaching this method would mean the common scanner routed a contextual
        Rysk reader through the wrong execution branch.

        :param block_number:
            Ignored static scanner block.
        :param timestamp:
            Ignored static scanner timestamp.
        :param call_results:
            Ignored multicall results.
        :return:
            Never returns.
        :raise RuntimeError:
            Always, because prefilled context owns this history.
        """

        message = "Rysk Premium history comes from final epoch withdrawal PPS context"
        raise RuntimeError(message)

    def fetch_contextual_historical_reads(self, start_block: int, end_block: int, step: int) -> Iterable[VaultHistoricalRead]:
        """Yield final epoch withdrawal PPS observations.

        Prices are scaled with the collateral token's native decimals and
        emitted without fabricated NAV, asset or share-supply values.

        :param start_block:
            Inclusive source block boundary.
        :param end_block:
            Exclusive source block boundary.
        :param step:
            Retained common-reader compatibility parameter; Rysk epochs are sparse.
        :return:
            Rysk share-price-equivalent records.
        """

        _ = step
        collateral_token = self.vault.denomination_token
        if collateral_token is None:
            message = f"Cannot scale Rysk Premium PPS without collateral token metadata: {self.vault.address}"
            raise RuntimeError(message)
        with RyskHistoricalContextStore(self.vault.historical_context_path) as store:
            for observation in store.iter_finalised_share_prices(
                chain_id=self.vault.chain_id,
                pool_address=self.vault.address,
                start_block=start_block,
                end_block=end_block,
                collateral_decimals=collateral_token.decimals,
            ):
                timestamp = datetime.datetime.fromtimestamp(observation.block_timestamp, tz=datetime.UTC).replace(tzinfo=None)
                assert observation.withdrawal_share_price is not None
                yield VaultHistoricalRead(
                    vault=self.vault,
                    block_number=observation.block_number,
                    timestamp=timestamp,
                    share_price=observation.withdrawal_share_price,
                    # Rysk's dashboard TVL excludes marked option liabilities;
                    # no matched full NAV/supply pair is available at the epoch.
                    total_assets=None,
                    total_supply=None,
                    performance_fee=None,
                    management_fee=None,
                    errors=None,
                    # Unsupported generic transaction construction is not
                    # evidence that the protocol itself was closed.
                    deposits_open=None,
                    redemption_open=None,
                )
