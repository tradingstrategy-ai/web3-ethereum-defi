"""AUTO Finance Autopool vault support.

Autopool vaults (Tokemak / AUTO Finance) use a flash-accounting pattern
(similar to Uniswap v4). This means ``previewRedeem()`` reverts with a
``BalanceNotSettled()`` custom error (selector ``0x20f1d86d``) instead of
returning a value, because the vault's internal balances are not settled
outside of a flash-loan callback context.

The :py:class:`AutoPoolDepositManager` works around this by skipping
``previewRedeem()`` entirely and estimating redemption value via the share
price (``totalAssets / totalSupply``).
"""

import datetime
import logging
from decimal import Decimal
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager, ERC4626RedemptionRequest
from eth_defi.erc_4626.estimate import estimate_value_by_share_price
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.autopool.tags import STRATEGY_TAGS
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, WithdrawalPeriod
from web3.exceptions import ContractLogicError
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, extract_revert_data
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

logger = logging.getLogger(__name__)


class AutoPoolVault(ERC4626Vault):
    """Autopool vault.

    Also known as *Tokemak*, *AUTO Finance*.

    - Fees are taken by minting more shares to the fee recipient, thus diluting all other shareholders.

    - ``previewRedeem()`` reverts on all Autopool vaults due to flash-accounting
      (``BalanceNotSettled()`` error). Redemption estimation falls back to share
      price via :py:class:`AutoPoolDepositManager`.

    More information:

    - `Contract <https://arbiscan.io/address/0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c#writeProxyContract>`__
    - `Github <https://github.com/Tokemak/v2-core-pub?tab=readme-ov-file>`__
    - `Implementationn <https://arbiscan.io/address/0x12db19359159e8ab0822506adf15d4d8dbff66c3#code>`__
    - `Fee logic <https://github.com/Tokemak/v2-core-pub/blob/de163d5a1edf99281d7d000783b4dc8ade03591e/src/vault/libs/AutopoolFees.sol#L138>`__
    - `Docs <https://docs.auto.finance/>`__
    """

    def is_whitelisted_deposit(self) -> bool:
        """Autopool deposits have no account allowlist; pause and NAV checks are separate.

        :return: ``False`` for the public deposit policy.
        """
        return False

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained classifications for this reviewed deployment.

        Only source-backed address entries are classified; an unreviewed
        deployment retains the explicit missing-information result.

        :return:
            A fresh set of strategy tags, or ``None`` when information is missing.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="autopool/AutopoolETH.json",
        )

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        return datetime.timedelta(days=0)

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        return INSTANT_WITHDRAWAL_PERIOD

    def get_deposit_manager(self) -> "AutoPoolDepositManager":
        return AutoPoolDepositManager(self)


class AutoPoolDepositManager(ERC4626DepositManager):
    """Autopool (Tokemak / AUTO Finance) manager that estimates redemptions via share price.

    Autopool vaults are plain synchronous ERC-4626 vaults except for one quirk:
    they use a flash-accounting pattern (similar to Uniswap v4) so
    ``previewRedeem()`` reverts with ``BalanceNotSettled()`` (selector
    ``0x20f1d86d``) whenever it is called outside a flash-loan callback context.
    This manager keeps every standard ERC-4626 behaviour and overrides only
    redemption estimation to route around that reverting preview.

    **Deposit process**

    Synchronous, fully inherited. After ``approve()``,
    :meth:`create_deposit_request` builds a single ERC-4626 ``deposit`` call and
    :meth:`estimate_deposit` uses the standard ``previewDeposit`` path, which
    does not revert on these vaults.

    **Redemption process**

    Synchronous. :meth:`create_redemption_request` builds the inherited single
    ERC-4626 ``redeem`` call. Only :meth:`estimate_redeem` is overridden: rather
    than calling the reverting ``previewRedeem()``, it computes the estimate
    from the current share price (``totalAssets / totalSupply``) via
    :func:`estimate_value_by_share_price`.

    **Queues and settlement**

    None (synchronous). Each ``deposit`` / ``redeem`` settles in its own
    transaction; there is no request queue or ticket.

    **Lockups and cooldowns**

    None. :meth:`AutoPoolVault.get_estimated_lock_up` returns zero.

    **Whitelisting / access control**

    Permissionless. No Autopool-specific whitelist is applied beyond the
    inherited :meth:`check_deposit_whitelist` preflight.

    **Anvil settlement (force_settle)**

    The ``deposit`` and ``redeem`` calls complete in their originating
    transaction, so the inherited :meth:`force_settle` accepts ``None`` and
    performs the Anvil-validated shared no-op.
    """

    def create_redemption_request(self, owner: HexAddress, to: HexAddress | None = None, shares: Decimal | None = None, raw_shares: int | None = None, check_max_deposit: bool = True, check_enough_token: bool = True, check_max_redeem: bool = True) -> ERC4626RedemptionRequest:
        """Preflight the actual exit when flash-accounting maxRedeem is unreadable.

        :param owner: Share owner and payout receiver.
        :param to: Unsupported separate receiver, as in the standard manager.
        :param shares: Decimal shares when raw shares are not provided.
        :param raw_shares: Exact native share amount.
        :param check_max_deposit: Inherited request option.
        :param check_enough_token: Retain the normal share-balance check.
        :param check_max_redeem: Read the maximum unless its known flash view fails.
        :return: Standard request after an executable-capacity check.
        """
        if check_max_redeem:
            try:
                self.vault.vault_contract.functions.maxRedeem(owner).call()
            except (ContractLogicError, ExtraValueError) as error:
                data = extract_revert_data(error)
                if not data or bytes(data[:4]) != bytes.fromhex("8d54ba1f"):
                    raise
                raw = raw_shares if raw_shares is not None else self.vault.share_token.convert_to_raw(shares)
                try:
                    self.vault.vault_contract.functions.redeem(raw, owner, owner).call({"from": owner})
                except (ContractLogicError, ExtraValueError) as failure:
                    raise VaultFlowUnavailable("Autopool exact redemption is unavailable", protocol=self.vault.get_protocol_name(), vault_address=self.vault.address, caller=owner, direction="redeem", phase="preflight", preflight_result="redemption_unavailable", raw_revert_data=extract_revert_data(failure)) from failure
                check_max_redeem = False
        return super().create_redemption_request(owner, to, shares, raw_shares, check_max_deposit, check_enough_token, check_max_redeem)

    def estimate_redeem(self, owner: HexAddress, shares: Decimal, block_identifier: BlockIdentifier = "latest") -> Decimal:
        """Estimate redemption value using share price instead of previewRedeem().

        All Autopool vaults revert on ``previewRedeem()`` due to their
        flash-accounting design. We bypass it entirely and compute the
        value from ``totalAssets / totalSupply``.
        """
        return estimate_value_by_share_price(
            self.vault,
            shares,
            block_identifier=block_identifier,
        )
