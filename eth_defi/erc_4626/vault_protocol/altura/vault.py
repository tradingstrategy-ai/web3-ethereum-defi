"""Altura vault protocol support.

Altura is a multi-strategy yield protocol built on HyperEVM (Hyperliquid) that
democratises access to institutional-grade trading strategies. Users deposit USDT0
into a single vault, and Altura allocates capital across diversified yield sources
including arbitrage, funding rate capture, staking yield, and liquidity provision.

- Homepage: https://altura.trade
- App: https://app.altura.trade
- Documentation: https://docs.altura.trade
- Github: https://github.com/AlturaTrade
- Twitter: https://twitter.com/alturax

Example vault:
- NavVault on HyperEVM: https://hyperevmscan.io/address/0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29

Fee structure:
- Instant withdrawal fee: 0.01% (1 basis point) when withdrawal amount <= vault's liquid balance
- Epoch withdrawal fee: 0% when withdrawal request exceeds available liquidity
- Exit fee is configurable via smart contract (exitFeeBps), capped at 200 bps (2%)

Audits:
- Vault audit by Adevarlabs (December 2025): https://github.com/AlturaTrade/docs/blob/V2/VaultAudit.pdf
- Predeposit audit by Adevarlabs (December 2025): https://github.com/AlturaTrade/docs/blob/V2/PredepositAudit.pdf
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class AlturaVault(ERC4626Vault):
    """Altura NavVault support.

    A multi-strategy yield vault on HyperEVM that uses oracle-backed NAV pricing.

    - Homepage: https://altura.trade
    - Documentation: https://docs.altura.trade
    - Github: https://github.com/AlturaTrade/contracts
    - Audit: https://github.com/AlturaTrade/docs/blob/V2/VaultAudit.pdf

    Key features:
    - ERC-4626 compliant with NAV oracle pricing
    - Withdrawal queue system with epoch-based claiming
    - Exit fees on instant withdrawals only
    - Role-based access control (Admin, Operator, Guardian)
    """

    def has_custom_fees(self) -> bool:
        """Altura has custom exit fees on instant withdrawals."""
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Altura does not charge management fees.

        Yield accrues via Price-Per-Share (PPS) mechanism.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Altura does not charge explicit performance fees.

        The protocol's fee structure is based on exit fees only.
        Performance is captured through the NAV oracle pricing mechanism.
        """
        return None

    def get_exit_fee(self, block_identifier: BlockIdentifier) -> float:
        """Read the exit fee from the smart contract.

        The exit fee is expressed in basis points (bps) and applies only to instant withdrawals.
        Epoch-based withdrawals have no exit fee.

        :return:
            Exit fee as a decimal (e.g., 0.0001 for 1 bps)
        """
        contract = get_deployed_contract(
            self.web3,
            "altura/NavVault.json",
            self.vault_address,
        )
        exit_fee_bps = contract.functions.exitFeeBps().call(block_identifier=block_identifier)
        return exit_fee_bps / 10_000  # Convert from basis points to decimal

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Altura uses epoch-based withdrawals with a 6-hour minimum hold period.

        For instant withdrawals (when liquidity is available), there's no lock-up.
        For epoch-based withdrawals, users must wait until the next epoch completes.
        The minimum hold period after deposit before any withdrawal is 6 hours.
        """
        return datetime.timedelta(hours=6)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the Altura app.

        Altura has a single vault, so we link directly to the app.
        """
        return "https://app.altura.trade"
