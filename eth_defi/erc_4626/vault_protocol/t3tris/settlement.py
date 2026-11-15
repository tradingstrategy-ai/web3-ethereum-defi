"""T3tris deposit settlement event reader.

The settlement scanner stores these rows in the generic vault settlement
DuckDB database. Backtests read them as ``vault_settlement_at`` markers, the
measured settlement cycle that replaces the weak offchain estimate from
:py:func:`~eth_defi.erc_4626.vault_protocol.t3tris.vault.create_t3tris_withdrawal_period`
where recorded events exist.

Only ``DepositsSettled`` is recorded. T3tris curators settle with
``settle()``, ``settleDeposits()`` or ``settleRedemptions()``, and the
``vault_settlement_at`` marker does not carry a direction. Recording
``RedemptionsSettled`` too would let a redemption-only settlement complete
queued deposits in a deposit-gated backtest. Redemption timing keeps using the
offchain estimate.
"""

from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.settlement_events import build_settlement_rows_from_logs, get_event_topic
from eth_defi.erc_4626.vault_protocol.t3tris.vault import T3trisVault
from eth_defi.vault.settlement_data import VaultSettlement

#: Protocol name stored in the settlement database.
T3TRIS_PROTOCOL_NAME = "T3tris"


def get_t3tris_settlement_events_by_topic(vault: T3trisVault) -> dict[str, ContractEvent]:
    """Return the T3tris deposit settlement event keyed by topic0.

    :param vault:
        T3tris vault adapter.
    :return:
        Mapping from ``DepositsSettled`` topic0 to its event class.
    """
    event = vault.vault_contract.events.DepositsSettled
    return {get_event_topic(event): event}


def build_t3tris_settlement_rows_from_logs(
    vault: T3trisVault,
    logs: list[AttributeDict],
    event_by_topic: dict[str, ContractEvent | str] | None = None,
) -> list[VaultSettlement]:
    """Build generic settlement rows from T3tris ``DepositsSettled`` logs.

    :param vault:
        T3tris vault adapter.
    :param logs:
        Web3-compatible ``DepositsSettled`` logs for this vault.
    :param event_by_topic:
        Optional topic0 to event mapping used to populate ``event_name``.
    :return:
        Settlement rows sorted by block and log index.
    """
    return build_settlement_rows_from_logs(
        chain_id=vault.chain_id,
        address=vault.address,
        web3=vault.web3,
        protocol=T3TRIS_PROTOCOL_NAME,
        logs=logs,
        event_by_topic=event_by_topic,
    )
