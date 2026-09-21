"""
GMX V2 funding-fee claim execution.

Builds and broadcasts the ``ExchangeRouter.multicall([claimFundingFees(markets,
tokens, receiver)])`` transaction that releases a position holder's accrued
``CLAIMABLE_FUNDING_AMOUNT`` receipts.

The same helper serves both execution modes because it signs through whatever
wallet it is given:

- An EOA :class:`~eth_defi.hotwallet.HotWallet` signs and broadcasts the
  ``claimFundingFees`` call directly.
- A :class:`~eth_defi.gmx.lagoon.wallet.LagoonGMXTradingWallet` wraps the call
  through ``TradingStrategyModuleV0.performCall`` and signs with the asset
  manager, so the Safe is the ``msg.sender`` and the default receiver.

A funding claim is a direct ``DataStore`` -> ``receiver`` token transfer with no
GMX keeper, so no ``sendWnt`` execution fee is required and the ``performCall``
``value`` is ``0``.
"""

import logging
from collections.abc import Sequence

from eth_typing import HexAddress
from eth_utils import to_checksum_address
from hexbytes import HexBytes

from eth_defi.compat import encode_abi_compat
from eth_defi.gas import estimate_gas_fees
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract, get_exchange_router_contract
from eth_defi.gmx.core.claimable_funding_fees import GetClaimableFundingFees
from eth_defi.gmx.keys import claim_funding_fees_feature_disabled_key

logger = logging.getLogger(__name__)


#: Default gas limit for a ``claimFundingFees`` multicall.
#: The claim is a batch of ``DataStore.getUint`` reads plus up to ``N`` ERC-20
#: transfers, so usage is small and predictable. The Lagoon wallet adds its own
#: ``performCall`` buffer on top of this value.
CLAIM_FUNDING_FEES_GAS_LIMIT = 800_000


def build_claim_funding_fees_multicall(
    web3,
    chain: str,
    markets: list[HexAddress],
    tokens: list[HexAddress],
    receiver: HexAddress,
) -> bytes:
    """Encode ``ExchangeRouter.multicall([claimFundingFees(...)])`` transaction data.

    :param web3:
        Web3 connection used to bind the ExchangeRouter contract.

    :param chain:
        GMX chain name (e.g. ``"arbitrum"``).

    :param markets:
        Market addresses whose claimable funding is released.

    :param tokens:
        Token addresses to claim, aligned by index with ``markets``.

    :param receiver:
        Account that receives the claimed tokens.

    :return:
        Encoded ``multicall`` calldata, ready to place in a transaction ``data``
        field.
    """
    if len(markets) != len(tokens):
        msg = f"markets and tokens must be aligned, got {len(markets)} markets and {len(tokens)} tokens"
        raise ValueError(msg)

    exchange_router = get_exchange_router_contract(web3, chain)

    inner_hex = encode_abi_compat(
        exchange_router,
        "claimFundingFees",
        [list(markets), list(tokens), to_checksum_address(receiver)],
    )
    inner_bytes = bytes.fromhex(inner_hex[2:] if inner_hex.startswith("0x") else inner_hex)

    multicall_hex = encode_abi_compat(exchange_router, "multicall", [[inner_bytes]])
    return bytes.fromhex(multicall_hex[2:] if multicall_hex.startswith("0x") else multicall_hex)


def _is_claim_disabled(web3, chain: str, modules: Sequence[HexAddress]) -> bool:
    """Check whether GMX has paused ``claimFundingFees`` for the claiming modules.

    Reads the per-module ``CLAIM_FUNDING_FEES_FEATURE_DISABLED`` ``DataStore``
    flag (``Keys.claimFundingFeesFeatureDisabledKey(address module)``) for each
    candidate module a claim could be routed through.

    This is a fail-open guard rail: when the flag cannot be read the claim is
    allowed to proceed. A genuinely disabled claim would revert on-chain anyway,
    so a transient RPC failure must not block a valid claim.

    :param web3:
        Web3 connection.

    :param chain:
        GMX chain name (e.g. ``"arbitrum"``).

    :param modules:
        Claiming module (contract) addresses to check.

    :return:
        ``True`` only when a flag is read successfully and set.
    """
    try:
        data_store = get_datastore_contract(web3, chain)
    except Exception as e:
        # Fail open: a bind failure must never block a claim.
        logger.debug("Could not bind the GMX DataStore on %s: %s", chain, e)
        return False

    for module in modules:
        try:
            if data_store.functions.getBool(claim_funding_fees_feature_disabled_key(to_checksum_address(module))).call():
                return True
        except Exception as e:
            # Fail open: a read failure must not block the claim.
            logger.debug("Could not read the claim-disabled flag for module %s: %s", module, e)

    return False


def claim_funding_fees(
    config: GMXConfig,
    wallet,
    markets: list[HexAddress],
    tokens: list[HexAddress],
    *,
    receiver: HexAddress | None = None,
    gas_limit: int = CLAIM_FUNDING_FEES_GAS_LIMIT,
    check_feature_disabled: bool = True,
) -> HexBytes | None:
    """Claim GMX V2 funding fees for ``markets``/``tokens`` to ``receiver``.

    The transaction signer must be the funding account itself (the EOA, or the
    vault Safe via :class:`LagoonGMXTradingWallet`), because GMX keys the
    claimable balance on ``msg.sender``.

    :param config:
        GMXConfig providing the Web3 connection and chain.

    :param wallet:
        Signing wallet: :class:`~eth_defi.hotwallet.HotWallet` for an EOA, or
        :class:`~eth_defi.gmx.lagoon.wallet.LagoonGMXTradingWallet` for a
        Lagoon vault. Both implement ``sync_nonce`` and
        ``sign_transaction_with_new_nonce``.

    :param markets:
        Market addresses to claim, as returned by
        :meth:`GetClaimableFundingFees.get_claim_args`.

    :param tokens:
        Token addresses aligned by index with ``markets``.

    :param receiver:
        Destination for the claimed tokens. Defaults to the wallet's address
        (for a vault, the Safe). In Lagoon mode the guard requires the receiver
        to be an allow-listed receiver, so this must normally remain the Safe.

    :param gas_limit:
        Gas limit for the claim transaction.

    :param check_feature_disabled:
        When ``True`` (default) read the per-module
        ``CLAIM_FUNDING_FEES_FEATURE_DISABLED`` ``DataStore`` flag and skip the
        transaction when GMX has paused claiming. The check fails open.

    :return:
        Transaction hash, or ``None`` when there is nothing to claim or claiming
        is disabled.
    """
    web3 = config.web3
    chain = config.chain

    markets = list(markets or [])
    tokens = list(tokens or [])

    if len(markets) != len(tokens):
        msg = f"markets and tokens must be aligned, got {len(markets)} markets and {len(tokens)} tokens"
        raise ValueError(msg)

    if not markets:
        logger.info("No claimable funding fees to claim; skipping transaction")
        return None

    account = getattr(wallet, "address", None) or config.get_wallet_address()
    if not account:
        msg = "Cannot determine the funding account: pass a wallet with .address or set config user_wallet_address"
        raise ValueError(msg)

    if receiver is None:
        receiver = account
    receiver = to_checksum_address(receiver)

    contract_addresses = get_contract_addresses(chain)

    if check_feature_disabled and _is_claim_disabled(web3, chain, (contract_addresses.exchangerouter, contract_addresses.syntheticsrouter)):
        logger.warning("GMX claimFundingFees is disabled on %s; skipping transaction", chain)
        return None

    data = build_claim_funding_fees_multicall(web3, chain, markets, tokens, receiver)

    tx = {
        "from": to_checksum_address(account),
        "to": contract_addresses.exchangerouter,
        "data": data,
        "value": 0,
        "gas": gas_limit,
        "chainId": web3.eth.chain_id,
    }

    gas_fees = estimate_gas_fees(web3)
    if gas_fees.max_fee_per_gas is not None:
        tx["maxFeePerGas"] = gas_fees.max_fee_per_gas
        tx["maxPriorityFeePerGas"] = gas_fees.max_priority_fee_per_gas
    else:
        tx["gasPrice"] = gas_fees.legacy_gas_price

    wallet.sync_nonce(web3)
    signed = wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)

    logger.info(
        "GMX funding fees claim submitted: tx=%s, account=%s, receiver=%s, pairs=%d",
        tx_hash.hex(),
        account,
        receiver,
        len(markets),
    )
    return tx_hash


def claim_all_funding_fees(
    config: GMXConfig,
    wallet,
    *,
    receiver: HexAddress | None = None,
    gas_limit: int = CLAIM_FUNDING_FEES_GAS_LIMIT,
) -> HexBytes | None:
    """Discover and claim every nonzero funding receipt for the wallet's account.

    Convenience wrapper that reads the per-account claimable funding across all
    markets and claims the whole nonzero set in a single ``multicall``.

    The funding account is always the wallet's own address, because GMX keys the
    claimable balance on ``msg.sender``: an EOA claims its own funding, and a
    Lagoon vault claims the Safe's funding (the Safe is the signer's target via
    ``performCall``).

    :param config:
        GMXConfig providing the Web3 connection and chain.

    :param wallet:
        Signing wallet (EOA ``HotWallet`` or ``LagoonGMXTradingWallet``). Its
        ``.address`` is both the funding account and the default receiver.

    :param receiver:
        Destination for the claimed tokens. Defaults to the wallet's address.

    :param gas_limit:
        Gas limit for the claim transaction.

    :return:
        Transaction hash, or ``None`` when there is nothing to claim.
    """
    account = getattr(wallet, "address", None) or config.get_wallet_address()
    if not account:
        msg = "Cannot determine the funding account: pass a wallet with .address or set config user_wallet_address"
        raise ValueError(msg)

    reader = GetClaimableFundingFees(config, account)
    markets, tokens = reader.get_claim_args()

    if not markets:
        logger.info("No claimable funding fees for account %s; skipping transaction", account)
        return None

    return claim_funding_fees(config, wallet, markets, tokens, receiver=receiver, gas_limit=gas_limit)
