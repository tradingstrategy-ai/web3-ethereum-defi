"""Reconstruct Securitize fund NAV from asynchronous subscription settlements.

Some Securitize funds, such as the tokenised ARK Venture Fund, sell DSTokens
through an ERC-7540-style ``AsyncFundVault``. Investors request deposits in
USDC, the requests are batched into generations, and a settler later calls
``fulfillDeposits(generationId, navPriceWAD)`` with the price at which the
generation converts into fund shares. The contract emits:

.. code-block:: solidity

    event DepositGenerationFulfilled(uint256 indexed generationId, uint256 navPrice, uint256 totalDeposits);
    event RedemptionGenerationFulfilled(uint256 indexed generationId, uint256 navPrice, uint256 fulfillmentRate, uint256 totalLiquidity);

When the vault has no onchain ``navProvider``, these settlement prices are the
only authoritative onchain fund value. The settler grosses the deposit price
up by the tokenised-route subscription fee, so the fund NAV/share is
``navPrice * (1 - subscription_fee)`` rounded to the fund's published NAV
precision. Settlements are streamed with Hypersync, never ``eth_getLogs``.

The historical multicall reader has a static call set, so it cannot look up
the latest fulfilled generation at each block from contract state. Instead the
adapter fetches the settlement timeline once per scan and picks the latest
settlement at or before each sampled block.

See the verified ``AsyncFundVault`` implementation:
https://etherscan.io/address/0x23848fc9b4da2b686358d39403d07256b51a3e9c#code
"""

import bisect
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

import eth_abi
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hypersync.hypersync_timestamp import is_hypersync_rate_limit_error
from eth_defi.tokenised_fund.securitize.description import ARKVX_ETHEREUM
from eth_defi.types import Percent
from eth_defi.vault.flow_events import IndexedVaultFlowLog, decode_indexed_event_uint, event_data_to_bytes, fetch_vault_flow_logs_hypersync, normalise_event_topic

if TYPE_CHECKING:
    from eth_defi.hypersync.session import ThrottledHypersyncClient

logger = logging.getLogger(__name__)

#: Topic of ``DepositGenerationFulfilled(uint256,uint256,uint256)``.
DEPOSIT_GENERATION_FULFILLED_TOPIC = normalise_event_topic(Web3.keccak(text="DepositGenerationFulfilled(uint256,uint256,uint256)"))

#: Topic of ``RedemptionGenerationFulfilled(uint256,uint256,uint256,uint256)``.
REDEMPTION_GENERATION_FULFILLED_TOPIC = normalise_event_topic(Web3.keccak(text="RedemptionGenerationFulfilled(uint256,uint256,uint256,uint256)"))

#: Fixed-point scale of ``navPrice`` values.
WAD = Decimal(10**18)

#: Relative settlement-to-settlement NAV move that is logged for review.
#:
#: A venture fund NAV rarely moves this much in one business day. A larger move
#: more likely means the settler changed its fee gross-up, which would shift the
#: whole reconstructed series.
SETTLEMENT_NAV_JUMP_WARNING_THRESHOLD = Decimal("0.20")

#: Hypersync attempts before a rate-limited historical settlement fetch fails.
#:
#: Bounded so a saturated API key delays the sequential tokenised-fund
#: scheduler by at most a few minutes; the item is retried on the next tick.
SETTLEMENT_FETCH_ATTEMPTS = 3

#: Seconds added to the server-stated ``resets_in`` before retrying.
#:
#: ``resets_in`` is reported in whole seconds, so wait a little past it. A
#: shared API key that another consumer saturates can still lose the race.
SETTLEMENT_FETCH_RESET_MARGIN = 5

#: Fallback wait in seconds when a rate-limit error has no ``resets_in``.
#:
#: Longer than Hypersync's 60-second rate-limit window.
SETTLEMENT_FETCH_RETRY_SLEEP = 61


class SecuritizeSettlementError(RuntimeError):
    """Raised when no settlement price is available for a block."""


@dataclass(slots=True, frozen=True)
class SecuritizeSettlementFeed:
    """One reviewed Securitize subscription vault used as a NAV source."""

    #: EVM chain hosting the DSToken and its subscription vault.
    chain_id: int

    #: Lower-case DSToken address.
    token: HexAddress

    #: Lower-case ``AsyncFundVault`` proxy address emitting settlement events.
    vault: HexAddress

    #: Block of the first fulfilled deposit generation.
    first_block: int

    #: Subscription fee the settler grosses into deposit ``navPrice`` values.
    subscription_fee: Percent

    #: Decimal places of the fund's published NAV/share.
    nav_decimals: int = 2


@dataclass(slots=True, frozen=True)
class SecuritizeSettlementPrice:
    """One fulfilled deposit generation converted to fund NAV/share."""

    #: Block where the generation was fulfilled.
    block_number: int

    #: ``AsyncFundVault`` deposit generation id.
    generation_id: int

    #: Settlement price as emitted, in USD per share, including the fee gross-up.
    settlement_price: Decimal

    #: Fund NAV/share in USD after removing the subscription fee.
    share_price: Decimal

    @classmethod
    def from_wad(cls, block_number: int, generation_id: int, nav_price_wad: int, feed: "SecuritizeSettlementFeed") -> "SecuritizeSettlementPrice":
        """Create a settlement price from an emitted WAD ``navPrice``.

        :param block_number:
            Block where the generation was fulfilled.
        :param generation_id:
            Deposit generation id.
        :param nav_price_wad:
            ``navPrice`` with 18 decimals.
        :param feed:
            Reviewed settlement feed with the fee and NAV precision.
        :return:
            Settlement price with the fee-adjusted NAV/share.
        """

        return cls(
            block_number=block_number,
            generation_id=generation_id,
            settlement_price=Decimal(nav_price_wad) / WAD,
            share_price=calculate_settlement_share_price(nav_price_wad, feed),
        )


#: Reviewed Securitize products priced from subscription settlements.
SECURITIZE_SETTLEMENT_FEEDS: dict[tuple[int, HexAddress], SecuritizeSettlementFeed] = {
    (ARKVX_ETHEREUM.chain_id, ARKVX_ETHEREUM.token): SecuritizeSettlementFeed(
        chain_id=ARKVX_ETHEREUM.chain_id,
        token=ARKVX_ETHEREUM.token,
        vault=HexAddress("0xef312d033ed52e2796ba604fef12b4a56053b292"),
        # Generation 1, fulfilled 2026-09-18 14:40:59 UTC.
        first_block=26_005_046,
        # All four generations settled by 2026-09-25 reproduce ARK's published
        # NAV for the prior business day when the 2% subscription fee is removed.
        subscription_fee=ARKVX_ETHEREUM.fee_data.deposit,
    ),
}


def calculate_settlement_share_price(nav_price_wad: int, feed: SecuritizeSettlementFeed) -> Decimal:
    """Convert a deposit settlement price to fund NAV/share.

    The settler back-computes ``navPrice`` from each generation's USDC total and
    its share amount rounded to 0.001 shares, so the raw value carries rounding
    noise. Rounding half-up to the fund's published precision removes the noise
    and reproduces the published NAV.

    Example: ``61.7224302451 * 0.98 = 60.48798...`` becomes ``60.49``.

    :param nav_price_wad:
        ``navPrice`` from ``DepositGenerationFulfilled``, with 18 decimals.
    :param feed:
        Reviewed settlement feed with the fee and NAV precision.
    :return:
        Fund NAV/share in USD.
    """

    fee_multiplier = Decimal(1) - Decimal(str(feed.subscription_fee))
    quantum = Decimal(1).scaleb(-feed.nav_decimals)
    return (Decimal(nav_price_wad) / WAD * fee_multiplier).quantize(quantum, rounding=ROUND_HALF_UP)


def decode_settlement_prices(logs: Iterable[IndexedVaultFlowLog], feed: SecuritizeSettlementFeed) -> list[SecuritizeSettlementPrice]:
    """Decode settlement logs into a block-ordered NAV timeline.

    Only deposit settlements are priced. A fulfilled redemption generation logs
    a warning: its ``navPrice`` has not yet been checked against a published
    NAV, and ignoring it could leave the deposit-only series stale if deposits
    stop settling.

    :param logs:
        ``DepositGenerationFulfilled`` and ``RedemptionGenerationFulfilled``
        logs of ``feed.vault``, sorted by block and log index as returned by
        :py:func:`~eth_defi.vault.flow_events.fetch_vault_flow_logs_hypersync`.
    :param feed:
        Reviewed settlement feed.
    :return:
        Deposit settlement prices sorted by block number.
    """

    prices: list[SecuritizeSettlementPrice] = []
    for log in logs:
        topic0 = normalise_event_topic(log.topics[0])
        generation_id = decode_indexed_event_uint(log.topics[1])
        if topic0 == REDEMPTION_GENERATION_FULFILLED_TOPIC:
            logger.warning(
                "Securitize vault %s fulfilled redemption generation %d at block %d; redemption prices are not used for %s NAV until reviewed",
                feed.vault,
                generation_id,
                log.block_number,
                feed.token,
            )
            continue
        if topic0 != DEPOSIT_GENERATION_FULFILLED_TOPIC:
            continue

        nav_price_wad, _total_deposits = eth_abi.decode(["uint256", "uint256"], event_data_to_bytes(log.data))
        price = SecuritizeSettlementPrice.from_wad(log.block_number, generation_id, nav_price_wad, feed)
        if prices and prices[-1].share_price > 0:
            previous = prices[-1].share_price
            if abs(price.share_price - previous) / previous > SETTLEMENT_NAV_JUMP_WARNING_THRESHOLD:
                logger.warning(
                    "Securitize %s NAV moved from %s to %s at deposit generation %d, block %d; check whether the subscription fee changed",
                    feed.token,
                    previous,
                    price.share_price,
                    generation_id,
                    log.block_number,
                )
        prices.append(price)
    return prices


def fetch_settlement_prices(
    hypersync_client: "ThrottledHypersyncClient",
    feed: SecuritizeSettlementFeed,
    end_block: int,
    attempts: int = SETTLEMENT_FETCH_ATTEMPTS,
) -> list[SecuritizeSettlementPrice]:
    """Fetch the settlement NAV timeline with Hypersync.

    Hypersync is a hard requirement: there is no RPC or archive-state
    fallback. The repository's Hypersync client disables internal retries, so
    a rate-limited request is retried here. Each wait lasts until the
    server-stated ``resets_in``, because a shared API key's quota is usually
    only available right after its 60-second window resets.

    :param hypersync_client:
        Hypersync client for ``feed.chain_id``, created with
        :py:func:`eth_defi.hypersync.utils.configure_hypersync_from_env`.
    :param feed:
        Reviewed settlement feed.
    :param end_block:
        Inclusive last block to read.
    :param attempts:
        Maximum Hypersync attempts when rate limited.
    :return:
        Deposit settlement prices sorted by block number.
    :raises RuntimeError:
        If Hypersync stays rate limited after all attempts, or
        If Hypersync returns no deposit settlement although ``end_block`` is at
        or after the known first settlement. An incomplete index must abort
        the scan rather than rewrite priced history as unpriced rows.
    """

    assert hypersync_client is not None, f"Securitize settlement NAV for {feed.token} requires a Hypersync client"
    if end_block < feed.first_block:
        return []
    for attempt in range(1, attempts + 1):
        try:
            logs = fetch_vault_flow_logs_hypersync(
                hypersync_client=hypersync_client,
                vault_address=feed.vault,
                topic0_list=[DEPOSIT_GENERATION_FULFILLED_TOPIC, REDEMPTION_GENERATION_FULFILLED_TOPIC],
                start_block=feed.first_block,
                end_block=end_block,
            )
            break
        except RuntimeError as e:
            if not is_hypersync_rate_limit_error(e):
                raise
            if attempt == attempts:
                logger.error("Hypersync settlement fetch for %s still rate limited after %d attempts", feed.vault, attempts, exc_info=True)
                raise
            resets_in = re.search(r"resets_in=(\d+)s", str(e))
            wait = int(resets_in.group(1)) + SETTLEMENT_FETCH_RESET_MARGIN if resets_in else SETTLEMENT_FETCH_RETRY_SLEEP
            logger.warning("Hypersync rate limited fetching settlements for %s (attempt %d/%d); retrying in %d s", feed.vault, attempt, attempts, wait)
            time.sleep(wait)
    prices = decode_settlement_prices(logs, feed)
    if not prices:
        raise RuntimeError(f"Hypersync returned no deposit settlements for {feed.vault} in blocks {feed.first_block:,} - {end_block:,}, although the first settlement is at block {feed.first_block:,}")
    logger.info("Fetched %d Securitize settlement prices for %s up to block %d", len(prices), feed.token, end_block)
    return prices


def find_settlement_price_at(prices: Sequence[SecuritizeSettlementPrice], block_number: int) -> SecuritizeSettlementPrice | None:
    """Find the latest settlement at or before a block.

    A settlement's NAV stays in force until the next settlement, so rows
    between settlements repeat the last struck value.

    :param prices:
        Settlement prices sorted by block number.
    :param block_number:
        Sampled block.
    :return:
        Latest settlement no later than ``block_number``, or ``None`` before
        the first settlement.
    """

    index = bisect.bisect_right(prices, block_number, key=lambda price: price.block_number)
    return prices[index - 1] if index else None
