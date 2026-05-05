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

Fees are embedded in the protocol mechanics: 20% of weekly veNFT rewards
go to lenders, 5% to the treasury, and 75% to borrower loan repayment.
There are no explicit management or performance fee functions on the vault contract.

The vault uses UUPS upgradeable proxy pattern with a ``_loanContract`` reference
to the protocol's lending engine.
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


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
    - `Aerodrome vault on Base <https://basescan.org/address/0xB99B6dDF96d4d5448cC0a5B3e0ef7896df9507Cf5>`__
    """

    @property
    def name(self) -> str:
        """Return a human-readable name based on the chain.

        On-chain ``name()`` returns cryptic strings like ``40op-USDC-Vault``.
        We override to produce a consistent ``40acres on <Chain>`` format.
        """
        chain = get_chain_name(self.chain_id)
        return f"40acres on {chain}"

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No explicit management fee on the vault contract.

        Fees are embedded in the protocol's reward distribution mechanics.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No explicit performance fee on the vault contract.

        The protocol takes 5% of weekly rewards as a treasury fee,
        but this is not a traditional performance fee charged to lenders.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Withdrawals depend on vault utilisation.

        No explicit lock-up, but an 80% utilisation cap means 20% of reserves
        must remain accessible. When fully utilised, lenders wait for repayments.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to the 40acres app."""
        return "https://app.40acres.finance/"
