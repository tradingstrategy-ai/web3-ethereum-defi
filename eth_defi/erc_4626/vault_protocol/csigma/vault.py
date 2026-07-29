"""cSigma Finance vault support.

cSigma Finance is a blockchain-based protocol that connects global borrowers and lenders
by standardising and streamlining the commercial lending process. The protocol offers
crypto-uncorrelated yield opportunities to stablecoin holders through tokenised real-world
assets (RWA) and risk-adjusted DeFi strategies.

csUSD allows users to deposit stablecoins and earn yield from two sources:
- RWA credit markets
- Onchain DeFi yield strategies

The protocol dynamically allocates between these sources based on market conditions
to optimise yield performance.

- Homepage: https://csigma.finance
- csUSD vault: https://www.csigma.finance/csusd
- Documentation: https://csigma.medium.com/
- Twitter: https://x.com/csigmafinance
- Contract: https://etherscan.io/address/0xd5d097f278a735d0a3c609deee71234cac14b47e
"""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.csigma.deposit_redeem import CSUPERIOR_V2_POOL_ADDRESS, CsigmaDepositManager
from eth_defi.vault.deposit_redeem import VaultDepositManager, VaultDepositManagerCapability

logger = logging.getLogger(__name__)

#: Seconds in cSigma's UTC day calculation.
SECONDS_PER_DAY = 86_400


#: cSigma V2 pool with verified synchronous ERC-4626 lifecycle support.
CSIGMA_V2_POOL_ADDRESS: HexAddress = CSUPERIOR_V2_POOL_ADDRESS

#: Ethereum mainnet, where the representative V2 pool was verified.
CSIGMA_V2_POOL_CHAIN_ID = 1

#: cSigma deployments that use the reserve-limited synchronous ERC-4626
#: lifecycle (deposit + ``redeem`` up to ``maxRedeem`` capacity; the excess is
#: queued off-chain and reverts ``WithdrawalPending`` onchain — see
#: :class:`~eth_defi.erc_4626.vault_protocol.csigma.deposit_redeem.CsigmaDepositManager`).
#:
#: The full deposit + redeem lifecycle is fork-proven for the first two pools at
#: block 21,900,000 (``tests/erc_4626/vault_protocol/test_csigma.py``). cSigma
#: USD (``0xd5d097f2…``) uses the same ``CsigmaV2Pool`` contract model and is
#: included so it gets the capacity-aware manager (its reported raw ``Max
#: redeem`` assertion becomes a typed ``VaultFlowUnavailable``). Its lifecycle
#: cannot be fork-proven at a fixed block the way the others are — it was not
#: yet deployed at 21,900,000 and its deposits are currently ``Pausable``-paused
#: — so it is covered by the capacity-preflight test instead of a full
#: deposit/redeem round.
CSIGMA_SYNCHRONOUS_POOL_ADDRESSES = frozenset(
    {
        CSIGMA_V2_POOL_ADDRESS,
        "0x50d59b785df23728d9948804f8ca3543237a1495",
        "0xd5d097f278a735d0a3c609deee71234cac14b47e",  # cSigma USD (csUSD)
    }
)


class CsigmaVault(ERC4626Vault):
    """cSigma Finance vault support.

    cSigma Finance is a DeFi protocol focused on providing fixed-rate, real-world yields
    for stablecoins through tokenised RWA private credit. The protocol has tokenised
    over $80 million in business loans from mid-market companies.

    - Homepage: https://csigma.finance
    - csUSD vault: https://www.csigma.finance/csusd
    - Medium: https://csigma.medium.com/
    - Twitter: https://x.com/csigmafinance
    - Contract: https://etherscan.io/address/0xd5d097f278a735d0a3c609deee71234cac14b47e
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the verified implementation ABI to known V2 pool proxies.

        The generic ERC-4626 interface omits cSigma custom errors and the
        queue and pause-state methods required by protocol preflights. Known V2
        deployments therefore use the verified implementation ABI while other
        cSigma deployments retain the generic binding.

        :return:
            Verified cSigma V2 contract for known synchronous pools, otherwise
            the generic ERC-4626 binding.
        """
        if self.chain_id == CSIGMA_V2_POOL_CHAIN_ID and self.address.lower() in CSIGMA_SYNCHRONOUS_POOL_ADDRESSES:
            return get_deployed_contract(self.web3, "csigma/CsigmaV2Pool.json", self.address)
        return super().vault_contract

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        cSigma does not charge deposit/withdrawal fees at the smart contract level.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return the management fee represented by this adapter.

        cSigma does not expose a separate management fee through the integrated
        vault interface.

        :return:
            Zero.
        """
        del block_identifier
        return 0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return the performance fee represented by this adapter.

        cSigma does not expose a separate performance fee through the
        integrated vault interface.

        :return:
            Zero.
        """
        del block_identifier
        return 0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        cSigma uses a First-In-First-Out queue for redemptions when vault reserves
        are depleted. The lock-up period depends on RWA credit market liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not supported by cSigma currently).

        :return:
            Link to the csUSD vault page.
        """
        del referral
        return "https://edge.csigma.finance/"

    def get_deposit_manager(self) -> VaultDepositManager:
        """Create the manager appropriate for this cSigma deployment.

        :return:
            Capacity-aware manager for a verified synchronous pool; otherwise
            the unadvertised generic ERC-4626 manager inherited from the base
            class.
        """
        if self.chain_id == CSIGMA_V2_POOL_CHAIN_ID and self.address.lower() in CSIGMA_SYNCHRONOUS_POOL_ADDRESSES:
            return CsigmaDepositManager(self)
        return super().get_deposit_manager()

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability | None:
        """Declare support only for verified synchronous cSigma pools.

        :return:
            Static support metadata for a verified pool, otherwise ``None``.
        """
        if self.chain_id == CSIGMA_V2_POOL_CHAIN_ID and self.address.lower() in CSIGMA_SYNCHRONOUS_POOL_ADDRESSES:
            return self.get_synchronous_deposit_manager_capability()
        return super().get_deposit_manager_capability()

    def can_check_deposit(self) -> bool:
        """Disable generic ``maxDeposit(address(0))`` closure detection.

        cSigma uses ``maxDeposit`` for current capacity, so a zero value can be
        an amount/liquidity restriction rather than a paused vault. The explicit
        :meth:`fetch_deposit_closed_reason` implementation below reads the
        protocol's pause state instead.

        :return:
            Always ``False`` to select cSigma's pause reader.
        """
        return False

    def fetch_deposit_closed_reason(self) -> str | None:
        """Return a closure reason only when the cSigma pool is paused.

        cSigma's verified V2 implementation inherits OpenZeppelin ``Pausable``
        and enforces a separate configurable daily pause window. This mirrors
        ``_requireNotInDefaultPauseWindow()`` from the `verified implementation
        <https://etherscan.io/address/0xa5b7555775a33ca79818702f63b34b14dc9aec4d#code>`__,
        including its inclusive, non-wrapping seconds-of-day comparison. Either
        pause is suitable for non-broadcast Guard validation; an ordinary
        ``maxDeposit`` shortfall remains capacity and never enters that path.

        :return:
            Pause reason, or ``None`` when the pool is not paused or the view
            cannot be read.
        """
        if self.chain_id != CSIGMA_V2_POOL_CHAIN_ID or self.address.lower() not in CSIGMA_SYNCHRONOUS_POOL_ADDRESSES:
            return None

        pause_state_contract = self.vault_contract
        try:
            if pause_state_contract.functions.paused().call():
                return "cSigma deposits paused by governance"
        except (BadFunctionCallOutput, ContractLogicError, ValueError):
            logger.debug("Could not read cSigma paused() state for %s", self.address)

        try:
            pause_start_time = int(pause_state_contract.functions.pauseStartTime().call())
            pause_duration = int(pause_state_contract.functions.pauseDuration().call())
            current_second = int(self.web3.eth.get_block("latest")["timestamp"]) % SECONDS_PER_DAY
            if pause_start_time <= current_second <= pause_start_time + pause_duration:
                return "cSigma deposits paused during daily window"
        except (BadFunctionCallOutput, ContractLogicError, ValueError):
            logger.debug("Could not read cSigma daily pause window for %s", self.address)
        return None
