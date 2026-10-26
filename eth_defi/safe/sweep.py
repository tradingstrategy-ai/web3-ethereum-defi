"""Return the remaining assets of a single-owner Safe to its owner.

Manual test scripts deploy temporary Lagoon vaults owned by the test
deployer. After the final redemption, rounding residue or stray transfers can
remain in the Safe. :py:func:`sweep_safe_to_owner` moves every listed ERC-20
balance and any native ETH back to the owner with direct Safe transactions,
signed by the owner and executed outside the asset-manager guard.

Only use this on a Safe that holds no assets backing outstanding investor
claims: check pending and claimable Lagoon requests and the share supply
first.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress
from safe_eth.safe.safe import Safe
from web3 import Web3

from eth_defi.gas import estimate_gas_price
from eth_defi.hotwallet import HotWallet
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: Gas limit for one swept ERC-20 or ETH transfer executed by the Safe.
SAFE_SWEEP_GAS_LIMIT = 150_000


@dataclass(slots=True, frozen=True)
class SafeSweepTransfer:
    """One asset moved from the Safe to its owner."""

    #: Token symbol, or ``ETH`` for native ETH.
    symbol: str

    #: Human-readable amount moved.
    amount: Decimal

    #: Executed Safe transaction hash.
    tx_hash: str


def sweep_safe_to_owner(
    web3: Web3,
    safe_address: HexAddress | str,
    owner: HotWallet,
    tokens: Iterable[TokenDetails],
    include_eth: bool = True,
) -> list[SafeSweepTransfer]:
    """Move all listed token balances and native ETH from a Safe to its sole owner.

    Each non-zero balance is sent with its own Safe transaction. The Safe must
    have a signing threshold of one and ``owner`` must be one of its owners.

    :param web3:
        Connection to the Safe's chain.
    :param safe_address:
        Safe to empty.
    :param owner:
        Sole signing owner, also the receiver and the transaction sender.
    :param tokens:
        ERC-20 tokens to sweep.
    :param include_eth:
        Also sweep native ETH held by the Safe.
    :return:
        Executed transfers, empty when the Safe held nothing.
    """
    safe_address = Web3.to_checksum_address(safe_address)
    safe = Safe(safe_address, create_safe_ethereum_client(web3))
    info = safe.retrieve_all_info()
    if info.threshold != 1 or Web3.to_checksum_address(owner.address) not in [Web3.to_checksum_address(address) for address in info.owners]:
        raise ValueError(f"Safe {safe_address} needs threshold 1 with {owner.address} as an owner to sweep")

    transfers = []
    calls = []
    for token in tokens:
        balance = token.fetch_balance_of(safe_address)
        if balance > 0:
            data = token.contract.functions.transfer(owner.address, token.convert_to_raw(balance))._encode_transaction_data()
            calls.append((token.symbol, balance, token.address, 0, bytes.fromhex(data[2:])))
    if include_eth:
        eth_raw = web3.eth.get_balance(safe_address)
        if eth_raw > 0:
            calls.append(("ETH", Decimal(eth_raw) / Decimal(10**18), owner.address, eth_raw, b""))

    for symbol, amount, target, value, data in calls:
        safe_tx = safe.build_multisig_tx(target, value, data)
        safe_tx.sign(owner.private_key.hex())
        owner.sync_nonce(web3)
        tx_hash, _tx = execute_safe_tx(
            safe_tx,
            tx_sender_private_key=owner.private_key.hex(),
            tx_gas=SAFE_SWEEP_GAS_LIMIT,
            hot_wallet=owner,
            gas_fee=estimate_gas_price(web3),
        )
        assert_transaction_success_with_explanation(web3, tx_hash)
        logger.info("Swept %s %s from Safe %s to %s", amount, symbol, safe_address, owner.address)
        transfers.append(SafeSweepTransfer(symbol=symbol, amount=amount, tx_hash=Web3.to_hex(tx_hash)))
    return transfers
