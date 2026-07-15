"""Aave ERC-4626 vault support."""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)

UINT256_RETURN_SIZE = 32


class AaveVault(ERC4626Vault):
    """Aave V3 ATokenVault and V4 Tokenization Spoke vaults.

    An Aave V3 ``ATokenVault`` wraps an interest-bearing Aave aToken into
    ERC-4626 shares and may charge a fee on earned yield. Aave v4 reorganises
    liquidity around a central :term:`Liquidity Hub` and modular :term:`Spokes`.
    A ``TokenizationSpoke`` tokenises a deposit of a Hub asset into fungible
    ``wa{Hub}{Asset}`` ERC-20 shares (e.g. ``waCoreUSDC``, ``waPrimeWETH``).
    Spoke yield accrues in the share price without a spoke-level fee.

    - Aave: https://aave.com/
    - Aave V3 ATokenVault: https://github.com/aave/aave-vault
    - Aave v4 docs: https://aave.com/docs/aave-v4
    - Tokenization Spoke audit (ChainSecurity, Feb 2026):
      https://github.com/aave/aave-v4/blob/main/audits/2026-02-10_TokenizationSpoke_ChainSecurity.pdf
    - Smart contracts: https://github.com/aave/aave-v4
    - Address registry (bgd-labs): https://github.com/bgd-labs/aave-address-book
    - Example ``CORE_USDC`` spoke:
      https://etherscan.io/address/0x531E90a2376902DE8915789Fcc1075e3B0c153E7

    V3 ATokenVault deployments are identified through a chain-aware address
    registry. V4 spokes are identified through the spoke-specific
    ``SPOKE_REVISION()`` accessor in
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
        """Check whether this Aave V3 vault exposes a yield-fee accessor.

        V4 spokes have no explicit vault-level fees. ATokenVault exposes
        ``getFee()``, returning the share of earned yield charged as fees.
        """
        return self._fetch_atoken_vault_fee("latest") is not None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No explicit management fee at the Aave vault level.

        ATokenVault fees apply only to earned yield. Aave V4 protocol revenue is
        taken as a reserve factor on Hub borrow interest.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the ATokenVault yield fee, or zero for a V4 spoke."""
        return self._fetch_atoken_vault_fee(block_identifier) or 0.0

    def _fetch_atoken_vault_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Read the Aave V3 ATokenVault yield fee when the accessor is available.

        The Aave V3 ``getFee()`` value uses a 1e18 scale and applies only to
        yield accrued by the wrapped aToken. V4 Tokenization Spokes do not
        implement the accessor, in which case this returns ``None``.
        """
        try:
            raw_fee = self.web3.eth.call(
                {
                    "to": Web3.to_checksum_address(self.spec.vault_address),
                    "data": self.web3.keccak(text="getFee()")[:4],
                },
                block_identifier=block_identifier,
            )
        except ValueError:
            return None

        if len(raw_fee) != UINT256_RETURN_SIZE:
            return None

        return int.from_bytes(raw_fee, "big") / 10**18

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Redemptions are liquid, subject to available Hub liquidity (no fixed lock-up)."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to the Aave markets app.

        Aave does not expose a stable deep link for every V3 vault or V4 spoke,
        so link to the markets overview.
        """
        return "https://app.aave.com/markets/"
