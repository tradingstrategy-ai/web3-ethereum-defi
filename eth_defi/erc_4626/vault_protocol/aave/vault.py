"""Aave (v4) Tokenization Spoke vault support."""

import datetime
import logging
from functools import cached_property

from web3.contract import Contract
from eth_typing import BlockIdentifier

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class AaveVault(ERC4626Vault):
    """Aave (v4) Tokenization Spoke vaults.

    Aave v4 reorganises liquidity around a central :term:`Liquidity Hub` and modular
    :term:`Spokes`. A ``TokenizationSpoke`` is an ERC-4626-compliant vault that tokenises
    a deposit of a Hub asset into fungible ``wa{Hub}{Asset}`` ERC-20 shares
    (e.g. ``waCoreUSDC``, ``waPrimeWETH``). Supplying liquidity mints shares; the share
    price is derived from the Hub's share price, so interest accrues in the share price
    rather than as an explicit spoke-level fee.

    - Aave: https://aave.com/
    - Aave v4 docs: https://aave.com/docs/aave-v4
    - Tokenization Spoke audit (ChainSecurity, Feb 2026):
      https://github.com/aave/aave-v4/blob/main/audits/2026-02-10_TokenizationSpoke_ChainSecurity.pdf
    - Smart contracts: https://github.com/aave/aave-v4
    - Address registry (bgd-labs): https://github.com/bgd-labs/aave-address-book
    - Example ``CORE_USDC`` spoke:
      https://etherscan.io/address/0x531E90a2376902DE8915789Fcc1075e3B0c153E7

    Identified via the spoke-specific ``SPOKE_REVISION()`` accessor in
    :py:func:`eth_defi.erc_4626.classification.identify_vault_features`.

    .. note::

        Lending available-liquidity and utilisation metrics are intentionally not
        implemented yet, so ``aave_like`` is deliberately left out of
        :py:data:`eth_defi.erc_4626.core.LENDING_PROTOCOL_FEATURES`. Withdrawals are
        subject to Hub liquidity, but the spoke does not custody the underlying token
        (it forwards deposits to the Hub via ``Hub.add()``), so the idle-balance pattern
        used by other lending vaults (``asset().balanceOf(spoke)``) would report zero
        liquidity / 100% utilisation. Correct figures require Hub-level introspection;
        until that is implemented these metrics are unsupported. This matches the
        existing ZeroLend (Aave v3 fork) integration.
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get the Tokenization Spoke deployment using the Aave spoke ABI."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="aave/TokenizationSpoke.json",
        )

    def has_custom_fees(self) -> bool:
        """No spoke-level deposit/withdrawal fees.

        Yield accrues in the Hub-derived share price.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No explicit management fee at the Tokenization Spoke level.

        Aave protocol revenue is taken as a reserve factor on Hub borrow interest,
        not as a spoke-level vault management fee, so the depositor-facing management
        fee is a known 0% (not unknown).
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """No explicit performance fee at the Tokenization Spoke level (known 0%)."""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Redemptions are liquid, subject to available Hub liquidity (no fixed lock-up)."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to the Aave markets app.

        Aave v4 does not expose a stable per-spoke deep link, so we link to the
        markets overview.
        """
        return "https://app.aave.com/markets/"
