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

The vault's ``totalAssets()`` includes a ``_loanContract`` reference to the
protocol's lending engine.
"""

import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal

from eth_typing import BlockIdentifier

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626HistoricalReader, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.forty_acres.deposit_redeem import FortyAcresDepositManager
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.types import Percent
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader
from eth_defi.vault.deposit_redeem import VaultDepositManager

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


class FortyAcresVaultHistoricalReader(ERC4626HistoricalReader):
    """Read 40acres ERC-4626 state and immediately redeemable liquidity."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Add the vault's direct USDC balance to the standard ERC-4626 reads."""
        yield from self.construct_core_erc_4626_multicall()
        yield EncodedCall.from_contract_call(
            self.vault.denomination_token.contract.functions.balanceOf(self.vault.address),
            extra_data={
                "function": "idle_assets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Decode core ERC-4626 state and direct-balance redemption capacity."""
        call_by_name = self.dictify_multicall_results(block_number, call_results)
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        available_liquidity = None
        utilisation = None
        idle_result = call_by_name.get("idle_assets")
        if idle_result is not None and idle_result.success:
            idle_raw = int.from_bytes(idle_result.result[0:32], byteorder="big")
            available_liquidity = self.vault.denomination_token.convert_to_decimals(idle_raw)
            if total_assets is not None:
                if total_assets == 0:
                    utilisation = 0.0
                else:
                    total_assets_raw = self.vault.denomination_token.convert_to_raw(total_assets)
                    utilisation = (total_assets_raw - idle_raw) / total_assets_raw
        elif idle_result is not None:
            errors.append("idle_assets call failed")

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=0.0,
            management_fee=0.0,
            errors=errors,
            max_deposit=max_deposit,
            available_liquidity=available_liquidity,
            utilisation=utilisation,
        )


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

    **Immediate redemption limitation**

    The verified ``Vault.sol`` implementation only overrides ``totalAssets()``.
    It inherits OpenZeppelin ERC-4626's ``redeem()`` and ``_withdraw()`` path,
    which burns shares and directly transfers USDC from the vault address. It
    does not recall capital from ``_loanContract`` as part of redemption.

    Consequently, ``totalAssets()``, ``previewRedeem()`` and ``maxRedeem()``
    can value or authorise shares backed by outstanding loans without proving
    that the vault can transfer USDC now. Immediate redemption capacity is the
    vault's direct USDC balance converted to shares, bounded by the owner's
    shares. When that balance is insufficient, the legacy implementation
    reverts with ``ERC20: transfer amount exceeds balance``.

    The adapter performs this direct-balance preflight for every recognised
    40acres vault and reports a typed ``redemption_capacity_limited`` result
    instead of submitting a reverting transaction. Fork simulations may add
    the smallest required USDC balance on Anvil to demonstrate that the
    unchanged redemption path works; the intervention is recorded and does
    not represent live liquidity or a production redemption solution.

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

        There is no explicit lock-up or on-chain redemption queue. Immediate
        withdrawals require direct USDC held by the vault; loan-deployed assets
        require a borrower repayment before they become transferable.
        """
        return None

    def is_whitelisted_deposit(self) -> bool:  # noqa: PLR6301
        """Report the 40acres adapter family's default permission policy.

        Supported 40acres supply vaults are treated as permissionless by
        default. Utilisation and available liquidity remain independent
        economic conditions.

        :return:
            Always ``False``.
        """
        return False

    def get_link(self, referral: str | None = None) -> str:
        """Link to the 40acres app."""
        return "https://app.40acres.finance/"

    def get_deposit_manager(self) -> VaultDepositManager:
        """Create the 40acres manager with direct-balance redemption preflight.

        :return:
            The 40acres ERC-4626 manager.
        """
        return FortyAcresDepositManager(self)

    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:  # noqa: FBT001
        """Get the 40acres reader with immediate-liquidity metrics."""
        return FortyAcresVaultHistoricalReader(self, stateful)

    def fetch_available_liquidity(self, block_identifier: BlockIdentifier = "latest") -> Decimal | None:
        """Read direct USDC currently available for immediate redemption."""
        try:
            idle_raw = self.denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier)
            return self.denomination_token.convert_to_decimals(idle_raw)
        except Exception:
            return None

    def fetch_utilisation_percent(self, block_identifier: BlockIdentifier = "latest") -> Percent | None:
        """Read the share of reported assets that is not held directly by the vault."""
        try:
            total_assets = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
            idle_raw = self.denomination_token.contract.functions.balanceOf(self.address).call(block_identifier=block_identifier)
            if total_assets == 0:
                return 0.0
            return (total_assets - idle_raw) / total_assets
        except Exception:
            return None
