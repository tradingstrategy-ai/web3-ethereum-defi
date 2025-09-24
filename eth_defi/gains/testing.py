"""Gains vault testing helpers."""

import logging
import datetime

from eth_defi.gains.vault import GainsVault
from eth_defi.provider.anvil import mine
from eth_typing import HexAddress

from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import to_unix_timestamp, from_unix_timestamp

logger = logging.getLogger(__name__)


def force_next_gains_epoch(
    vault: GainsVault,
    any_account: HexAddress,
    padding_seconds: int = 1,
    gas_limit=3_000_000,
):
    """Advance Gains vault to a next epoch by using Anvil hacks.

    :param any_account:
        Burn gas
    """

    assert isinstance(vault, GainsVault), f"Expected GainsVault, got {type(vault)}"

    web3 = vault.web3

    current_epoch = vault.fetch_current_epoch()

    # Full delay
    current_epoch_start = vault.vault_contract.functions.currentEpochStart().call()
    epoch_duration = vault.open_pnl_contract.functions.requestsStart().call() + (vault.open_pnl_contract.functions.requestsEvery().call() * vault.open_pnl_contract.functions.requestsCount().call())
    next_epoch = current_epoch_start + epoch_duration

    # How loong until the epoch is cooked
    unix_timestamp = next_epoch + padding_seconds

    # Handle mining old blocks
    current_block_time = web3.eth.get_block("latest")["timestamp"]
    if current_block_time >= unix_timestamp:
        unix_timestamp = current_block_time + 1

    timestamp = from_unix_timestamp(unix_timestamp)

    logger.info(
        "Current epoch: #%d (%s / %s), next epoch start at: %s, epoch duration %s",
        current_epoch,
        from_unix_timestamp(current_epoch_start),
        current_epoch_start,
        timestamp,
        datetime.timedelta(seconds=epoch_duration),
    )

    mine(
        web3,
        timestamp=int(to_unix_timestamp(timestamp)),
    )

    tx_hash = vault.open_pnl_contract.functions.forceNewEpoch().transact({"from": any_account, "gas": gas_limit})
    assert_transaction_success_with_explanation(web3, tx_hash)
