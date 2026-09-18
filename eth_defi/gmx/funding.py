"""GMX trader funding-fee discovery, claiming and receipt parsing.

GMX stores claimable trader funding per ``(market, token, account)`` tuple.
Claims must repeat a market when both its long and short token have accrued
funding.  The ExchangeRouter accepts these parallel arrays and emits one
``FundingFeesClaimed`` event for every claimed tuple.

Vault integrations should submit the claim through
``ExchangeRouter.multicall(bytes[])``.  This is both the standard GMX entry
point and the call shape understood by the Lagoon vault guard.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from eth_typing import HexAddress
from eth_utils import to_checksum_address
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.types import BlockIdentifier, TxReceipt

from eth_defi.event_reader.multicall_batcher import get_multicall_contract
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_datastore_contract, get_exchange_router_contract
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.events import find_events_by_name
from eth_defi.gmx.keys import claimable_funding_amount_key


@dataclass(frozen=True, slots=True)
class ClaimableFundingFee:
    """A non-zero GMX funding-fee balance.

    ``amount`` is expressed in the token's raw integer units.
    """

    market: HexAddress
    token: HexAddress
    amount: int


@dataclass(frozen=True, slots=True)
class ClaimedFundingFee:
    """A decoded GMX ``FundingFeesClaimed`` event."""

    market: HexAddress
    token: HexAddress
    account: HexAddress
    receiver: HexAddress
    amount: int
    next_pool_value: int | None = None


def fetch_claimable_funding_fees(
    web3: Web3,
    account: str,
    *,
    token_addresses: Iterable[str] | None = None,
    block_identifier: BlockIdentifier = "latest",
) -> list[ClaimableFundingFee]:
    """Fetch all non-zero trader funding fees for an account.

    The reads are combined into one Multicall3 request.  A market is repeated
    for its long and short token where these differ, matching the argument
    shape expected by ``claimFundingFees()``.

    :param web3:
        Web3 connection for Arbitrum or Avalanche.
    :param account:
        GMX trading account whose funding has accrued.
    :param token_addresses:
        Optional token allow-list.  Use this to claim only an accounting
        reserve token such as USDC.
    :param block_identifier:
        Block at which the DataStore balances are read.
    :return:
        Non-zero claims in deterministic market/token order.
    """
    account = to_checksum_address(account)
    token_filter = None
    if token_addresses is not None:
        token_filter = {to_checksum_address(address) for address in token_addresses}

    config = GMXConfig(web3)
    market_data = Markets(config).get_available_markets()
    datastore = get_datastore_contract(web3, config.chain)
    multicall = get_multicall_contract(web3)

    pairs: list[tuple[HexAddress, HexAddress]] = []
    seen: set[tuple[str, str]] = set()
    calls: list[tuple[str, bool, bytes]] = []

    for market, data in market_data.items():
        market = to_checksum_address(market)
        for token_key in ("long_token_address", "short_token_address"):
            token = to_checksum_address(data[token_key])
            pair_key = (market.lower(), token.lower())
            if pair_key in seen or (token_filter is not None and token not in token_filter):
                continue
            seen.add(pair_key)
            pairs.append((market, token))
            key = claimable_funding_amount_key(market, token, account)
            calldata = bytes.fromhex(
                datastore.encode_abi(
                    abi_element_identifier="getUint",
                    args=[key],
                )[2:]
            )
            calls.append((datastore.address, False, calldata))

    if not calls:
        return []

    raw_results = multicall.functions.aggregate3(calls).call(
        block_identifier=block_identifier,
    )

    claims: list[ClaimableFundingFee] = []
    for (market, token), (success, raw_amount) in zip(pairs, raw_results, strict=True):
        if not success:
            raise RuntimeError(f"Could not read GMX funding for {market}/{token}")
        amount = int.from_bytes(raw_amount, byteorder="big")
        if amount:
            claims.append(
                ClaimableFundingFee(
                    market=market,
                    token=token,
                    amount=amount,
                )
            )

    return claims


def build_claim_funding_fees_call(
    web3: Web3,
    claims: Iterable[ClaimableFundingFee],
    receiver: str,
) -> ContractFunction:
    """Build a guarded GMX funding claim transaction.

    Returns an outer ``ExchangeRouter.multicall()`` bound function containing
    one ``claimFundingFees()`` call.  Signing and broadcasting are deliberately
    left to the caller so hot-wallet and vault transaction builders can use the
    same helper.

    :raises ValueError:
        If there are no claims or a market/token tuple is repeated.
    """
    receiver = to_checksum_address(receiver)
    config = GMXConfig(web3)
    exchange_router = get_exchange_router_contract(web3, config.chain)

    markets: list[HexAddress] = []
    tokens: list[HexAddress] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims:
        market = to_checksum_address(claim.market)
        token = to_checksum_address(claim.token)
        pair_key = (market.lower(), token.lower())
        if pair_key in seen:
            raise ValueError(f"Duplicate GMX funding claim tuple: {market}/{token}")
        seen.add(pair_key)
        markets.append(market)
        tokens.append(token)

    if not markets:
        raise ValueError("Cannot build an empty GMX funding claim")

    inner_data = exchange_router.encode_abi(
        abi_element_identifier="claimFundingFees",
        args=[markets, tokens, receiver],
    )
    return exchange_router.functions.multicall([bytes.fromhex(inner_data[2:])])


def extract_claimed_funding_fees(
    web3: Web3,
    receipt: TxReceipt | dict,
    *,
    account: str | None = None,
    receiver: str | None = None,
    token_addresses: Iterable[str] | None = None,
) -> list[ClaimedFundingFee]:
    """Decode and filter ``FundingFeesClaimed`` events from a receipt."""
    account_filter = to_checksum_address(account) if account else None
    receiver_filter = to_checksum_address(receiver) if receiver else None
    token_filter = None
    if token_addresses is not None:
        token_filter = {to_checksum_address(address) for address in token_addresses}

    claims: list[ClaimedFundingFee] = []
    for event in find_events_by_name(web3, receipt, "FundingFeesClaimed"):
        market = event.get_address("market")
        token = event.get_address("token")
        event_account = event.get_address("account")
        event_receiver = event.get_address("receiver")
        amount = event.get_uint("amount")

        if None in (market, token, event_account, event_receiver, amount):
            continue

        market = to_checksum_address(market)
        token = to_checksum_address(token)
        event_account = to_checksum_address(event_account)
        event_receiver = to_checksum_address(event_receiver)
        if account_filter is not None and event_account != account_filter:
            continue
        if receiver_filter is not None and event_receiver != receiver_filter:
            continue
        if token_filter is not None and token not in token_filter:
            continue

        claims.append(
            ClaimedFundingFee(
                market=market,
                token=token,
                account=event_account,
                receiver=event_receiver,
                amount=amount,
                next_pool_value=event.get_uint("nextPoolValue"),
            )
        )

    return claims
