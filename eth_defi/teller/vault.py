"""Teller protocol vault support.

Teller Protocol is a decentralised lending protocol that enables
long-tail lending pools where liquidity providers can deposit assets
and earn yield from borrower interest payments.

Teller's unique architecture separates lending and borrowing into
isolated pools with specific collateral/lending token pairs. Each pool
has pre-set terms including collateralisation ratio, APR range, and
maximum loan duration.

Key features:

- Long-tail lending pools: Each pool is isolated to a specific lending
  token and collateral token pair
- Time-based loans: All loans on Teller are time-based instead of
  price-based. Price will never cause a loan to default, only expiration
- Liquidation auctions: On default, collateral is transferred to a 24-hour
  Dutch auction where it is purchased to pay off the loan
- ERC-4626 compliant: Lenders receive vault shares representing their stake
- TWAP pricing: Uses Uniswap V3 TWAP for collateral price oracles

The protocol is built on top of TellerV2 which handles the core lending
mechanics, with LenderCommitmentGroup_Pool_V2 providing the ERC-4626
vault interface.

- Homepage: https://www.teller.org/
- Vault page: https://app.teller.org/base/earn
- Documentation: https://docs.teller.org/teller-v2
- GitHub: https://github.com/teller-protocol/teller-protocol-v2
- Twitter: https://x.com/useteller
- Example vault: https://basescan.org/address/0x13cd7cf42ccbaca8cd97e7f09572b6ea0de1097b
"""

import datetime
import logging
from functools import cached_property

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.chain import get_chain_name

logger = logging.getLogger(__name__)


class TellerVault(ERC4626Vault):
    """Teller protocol long-tail lending pool vault.

    Teller's LenderCommitmentGroup_Pool_V2 is an ERC-4626 compliant vault that
    enables pool-style lending on top of Teller's OTC loan infrastructure.

    Lenders deposit principal tokens (e.g. USDC) to earn yield from borrower
    interest payments. Borrowers post specific collateral tokens and agree to
    time-based loan terms. If a loan defaults (expires without repayment),
    the collateral goes to a 24-hour Dutch auction.

    Key characteristics:

    - No deposit/withdrawal fees at the smart contract level
    - Yield comes from borrower interest payments
    - Time-based defaults (not price-based liquidations)
    - Each pool is isolated to specific lending/collateral token pair
    - Uses Uniswap V3 TWAP for collateral pricing

    - Homepage: https://www.teller.org/
    - Documentation: https://docs.teller.org/teller-v2
    - GitHub: https://github.com/teller-protocol/teller-protocol-v2
    - Twitter: https://x.com/useteller
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="teller/LenderCommitmentGroup_Pool_V2.json",
        )

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Teller pools do not charge deposit/withdrawal fees at the smart
        contract level. Yield comes from borrower interest payments.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Teller does not charge management fees. Yield comes directly from
        borrower interest payments.

        :return:
            0.1 = 10%
        """
        # Generated: Human can add details later if fees are discovered
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Teller pools may have performance fees set by the pool owner.
        This information is not readily available on-chain in the current
        implementation.

        :return:
            0.1 = 10%
        """
        # Generated: Human can add details later if fees are discovered
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Teller pools may have a withdraw delay time configured by the pool
        owner. This can be read from the withdrawDelayTime() function.
        """
        try:
            delay_seconds = self.vault_contract.functions.withdrawDelayTime().call()
            return datetime.timedelta(seconds=delay_seconds)
        except Exception:
            return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        Teller vaults can be accessed via the Teller app at their earn page.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Teller app earn page for this chain.
        """
        chain_id = self.web3.eth.chain_id
        chain_name = get_chain_name(chain_id).lower()
        return f"https://app.teller.org/{chain_name}/earn"
