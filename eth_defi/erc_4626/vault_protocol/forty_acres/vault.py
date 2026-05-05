"""40acres cashflow lending vault support.

40acres is a cashflow lending protocol for revenue-generating on-chain assets,
primarily vote-escrowed NFTs (veNFTs) from DEXes like Aerodrome, Velodrome,
Pharaoh, and Blackhole. Users deposit USDC into ERC-4626 supply vaults to
earn organic yield sourced from real DEX trading fees and bribes.

- `Homepage <https://www.40acres.finance/>`__
- `Documentation <https://docs.40acres.finance/>`__
- `GitHub <https://github.com/40-Acres/loan-contracts>`__
- `Fee structure <https://docs.40acres.finance/fee-structure>`__
- `Security (4 Sherlock audits) <https://docs.40acres.finance/security>`__
- `DefiLlama <https://defillama.com/protocol/40-acres>`__

40acres vaults are feeless for lenders: no management fee, no performance fee.
The protocol's 5% treasury cut is taken from borrower rewards, not from depositor
principal or yield. There are no explicit fee functions on the vault contract.

The vault uses UUPS upgradeable proxy pattern with a ``_loanContract`` reference
to the protocol's lending engine.
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)

#: Human-readable vault names sourced from the 40acres API (``/api/vaults/<dex>``).
#: Keys are lowercase vault contract addresses.
#: Used to override the cryptic on-chain ``name()`` return values.
VAULT_NAMES: dict[str, str] = {
    # Aerodrome USDC vault on Base
    "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5": "Aerodrome USDC",
    # Velodrome USDC vault on Optimism
    "0x08dcdbf7bade91ccd42cb2a4ea8e5d199d285957": "Velodrome USDC",
    # Pharaoh USDC vault on Avalanche
    "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7": "Pharaoh USDC",
    # Blackhole USDC vault on Avalanche
    "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd": "Blackhole USDC",
}


class FortyAcresVault(ERC4626Vault):
    """40acres USDC supply vault.

    40acres operates a peer-to-pool lending model with ERC-4626 compliant
    USDC supply vaults. Yield is sourced from real DEX trading fees
    and bribes collected from veNFT collateral.

    - `Homepage <https://www.40acres.finance/>`__
    - `Documentation <https://docs.40acres.finance/>`__
    - `GitHub <https://github.com/40-Acres/loan-contracts>`__
    - `Fee structure <https://docs.40acres.finance/fee-structure>`__
    - `Contracts <https://docs.40acres.finance/contracts>`__
    - `Security <https://docs.40acres.finance/security>`__

    **Fee mechanism (internalised skimming)**

    Fees are internalised in the share price. The vault is a plain OpenZeppelin
    ``ERC4626Upgradeable`` with no overrides of ``deposit()``, ``withdraw()``,
    ``mint()`` or ``redeem()`` — there are no entry or exit fees.

    When veNFT collateral earns weekly rewards (trading fees + bribes),
    ``LoanV2._processFees()`` splits them:

    - **20% lender premium** — transferred as USDC directly to the vault
      via ``_asset.transfer(_vault, lenderPremium)``, increasing
      ``_asset.balanceOf(vault)`` → ``totalAssets()`` → share price.
    - **5% protocol fee** — sent to the protocol owner, never touches the vault.
    - **75% loan repayment** — repays the borrower's outstanding balance,
      reducing ``_outstandingCapital`` (tracked in ``activeAssets()``).
    - **0.8% origination fee** — deducted from borrowed amount at loan creation,
      sent to protocol owner.
    - **1% relayer fee** — infrastructure/automation cost.

    ``totalAssets()`` is defined as::

        _asset.balanceOf(vault) + _loanContract.activeAssets() - epochRewardsLocked()

    The ``epochRewardsLocked()`` mechanism linearly vests each week's lender
    premium over the 7-day epoch, preventing front-running by depositing
    just before rewards arrive.

    See `LoanV2._processFees() <https://github.com/40-Acres/loan-contracts/blob/main/src/LoanV2.sol>`__
    for the fee distribution implementation.

    Example vaults:

    - `Blackhole vault on Avalanche <https://snowtrace.io/address/0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd>`__
    - `Pharaoh vault on Avalanche <https://snowtrace.io/address/0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7>`__
    - `Velodrome vault on Optimism <https://optimistic.etherscan.io/address/0x08dCDBf7baDe91Ccd42CB2a4EA8e5D199d285957>`__
    - `Aerodrome vault on Base <https://basescan.org/address/0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5>`__
    """

    @property
    def name(self) -> str:
        """Return a human-readable vault name.

        On-chain ``name()`` returns cryptic strings like ``40op-USDC-Vault``.
        We look up the address in :py:data:`VAULT_NAMES` for the DEX-based name
        (e.g. ``"Aerodrome USDC"``), falling back to ``"40acres on <Chain>"``.
        """
        known = VAULT_NAMES.get(self.vault_address_checksumless)
        if known:
            return known
        chain = get_chain_name(self.chain_id)
        return f"40acres on {chain}"

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No management fee.

        40acres vaults charge no explicit management fee to lenders.
        The protocol's 5% treasury cut is taken from borrower rewards,
        not deducted from depositor principal or yield.

        :return:
            0.0
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """No performance fee.

        40acres vaults charge no explicit performance fee to lenders.
        Yield is delivered in full via share price appreciation.

        :return:
            0.0
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Withdrawals depend on vault utilisation.

        No explicit lock-up, but an 80% utilisation cap means 20% of reserves
        must remain accessible. When fully utilised, lenders wait for repayments.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to the 40acres app."""
        return "https://app.40acres.finance/"
