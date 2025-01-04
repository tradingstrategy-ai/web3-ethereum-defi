"""Analyse Lagoon protocol events."""
import datetime
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress
from hexbytes import HexBytes

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails


@dataclass(slots=True, frozen=True)
class LagoonSettlementEvent:
    """Capture Lagoon vault flow when it is settled.

    - Use to adjust vault treasury balances for internal accounting
    - We do not capture individual users
    """

    #: Chain we checked
    chain_id: int

    #: When the settlement was done
    block_number: int

    #: When the settlement was done
    timestamp: datetime.datetime

    #: Vault address
    vault: LagoonVault

    #: Under
    underlying: TokenDetails

    #: How much new underlying was added to the vault
    deposited: Decimal

    #: How much was redeemed successfully
    redeemed: Decimal

    def get_serialiable_diagnostics_data(self) -> dict:
        """JSON serialisable diagnostics data for logging"""
        return {
            "chain_id": self.chain_id,
            "block_number": self.block_number,
            "timestamp": self.timestamp,
            "vault": self.vault.vault_address,
            "underlying": self.underlying.address,
            "deposited": self.deposited,
            "redeemed": self.redeemed,
        }

def analyse_vault_flow_in_settlement(
    vault: LagoonVault,
    tx_hash: HexBytes,
) -> LagoonSettlementEvent:
    """Extract deposit and redeem events from a settlement transaction"""
    web3 = vault.web3
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt is not None, f"Cannot find tx: {tx_hash}"



