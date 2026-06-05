"""Gains vault testing helpers."""

import logging
import datetime

from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
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


def force_ostium_v15_settlement(
    vault: "eth_defi.erc_4626.vault_protocol.gains.vault.OstiumVault",
    any_account: HexAddress,
    padding_seconds: int = 1,
    gas_limit: int = 3_000_000,
):
    """Force a settlement on Ostium V1.5 by advancing Anvil time and calling ``tryNewSettlement()``.

    ``tryNewSettlement()`` is public and permissionless — it executes when
    ``block.timestamp >= lastSettlementTs + maxSettlementInterval``.

    :param vault:
        Ostium V1.5 vault instance.

    :param any_account:
        Any account to pay gas for the transaction.

    :param padding_seconds:
        Extra seconds past the settlement threshold.
    """
    from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion

    assert isinstance(vault, OstiumVault), f"Expected OstiumVault, got {type(vault)}"
    assert vault.version == OstiumVersion.v1_5, f"Expected V1.5 vault, got {vault.version}"

    web3 = vault.web3
    contract = vault.vault_contract

    last_settlement_ts = contract.functions.lastSettlementTs().call()
    max_interval = contract.functions.maxSettlementInterval().call()
    last_settlement_id = contract.functions.lastSettlementId().call()

    target_ts = last_settlement_ts + max_interval + padding_seconds

    # Ensure we advance past current block time
    current_block_time = web3.eth.get_block("latest")["timestamp"]
    if current_block_time >= target_ts:
        target_ts = current_block_time + 1

    logger.info(
        "Forcing V1.5 settlement: lastSettlementId=%d, lastSettlementTs=%s, maxInterval=%ds, advancing to %s",
        last_settlement_id,
        from_unix_timestamp(last_settlement_ts),
        max_interval,
        from_unix_timestamp(target_ts),
    )

    mine(
        web3,
        timestamp=target_ts,
    )

    tx_hash = contract.functions.tryNewSettlement().transact({"from": any_account, "gas": gas_limit})
    assert_transaction_success_with_explanation(web3, tx_hash)

    new_settlement_id = contract.functions.lastSettlementId().call()
    logger.info("Settlement completed: lastSettlementId %d -> %d", last_settlement_id, new_settlement_id)
