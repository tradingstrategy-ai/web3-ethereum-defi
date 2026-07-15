"""Kiln OmniVault support.

Kiln OmniVault contracts expose both end-user fees on-chain:

- ``depositFee()`` is a fixed amount in the denomination token's raw units
- ``rewardFee()`` is a percentage of generated rewards, scaled by
  ``100 * 10 ** asset_decimals``

The reward fee is collected by minting vault shares. A deposit fee, when configured,
is deducted from the deposited asset amount. The mixed model cannot be represented by
the protocol-wide :class:`~eth_defi.vault.fee.VaultFeeMode` enum.

- `Kiln administration documentation <https://docs.kiln.fi/v1/kiln-products/omnivaults/how-to-integrate/administration>`__
- `Kiln contract ABI <https://docs.kiln.fi/v1/kiln-products/omnivaults/how-to-integrate/smart-contract-interactions>`__
- `Example vault on Arbiscan <https://arbiscan.io/address/0x19A0F016Ac3989e754ab8216810beD8503bDA37e>`__
"""

import datetime
from decimal import Decimal

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault


class KilnVault(ERC4626Vault):
    """Read Kiln OmniVault fees.

    Kiln lets each partner configure fees on individual vaults. Reward fees are
    returned as a ratio, such as ``0.20`` for 20%, while the fixed deposit fee
    is exposed separately in denomination-token units.
    """

    def _fetch_uint256(self, function_signature: str, block_identifier: BlockIdentifier) -> int:
        """Read a no-argument ``uint256`` function without a local ABI file.

        Kiln publishes the complete ABI with its integration documentation. These
        two view methods have stable selectors and the minimal call avoids copying
        a generated ABI JSON file into the repository.

        :param function_signature:
            Canonical Solidity signature of the no-argument function.

        :param block_identifier:
            Block at which to read the fee configuration.

        :return:
            The unscaled ``uint256`` returned by the vault.
        """
        result = self.web3.eth.call(
            {
                "to": self.vault_address,
                "data": Web3.keccak(text=function_signature)[:4],
            },
            block_identifier=block_identifier,
        )
        return int.from_bytes(result, byteorder="big")

    def has_custom_fees(self) -> bool:  # noqa: PLR6301
        """Report Kiln's fixed deposit fee as outside the common percentage model.

        :return:
            Always ``True`` because a fixed asset fee cannot be represented as a
            percentage without choosing a deposit size.
        """
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301, ARG002
        """Return Kiln's management fee.

        Kiln OmniVaults do not configure an annual management fee. The underlying
        lending protocol may have an independent interest-rate spread.

        :param block_identifier:
            Unused because the management fee is structurally zero.

        :return:
            Always ``0.0``.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Read the reward fee as a ratio of generated rewards.

        Kiln stores ``rewardFee()`` using the vault asset's percentage scale of
        ``100 * 10 ** asset_decimals``. The fee is collected by minting shares.

        :param block_identifier:
            Block at which to read the active reward-fee configuration.

        :return:
            A ratio such as ``0.20`` for a 20% reward fee.
        """
        reward_fee = self._fetch_uint256("rewardFee()", block_identifier)
        denomination_token = self.denomination_token
        assert denomination_token is not None, "Kiln vault denomination token is required for reward fee scaling"
        return reward_fee / (100 * 10**denomination_token.decimals)

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301, ARG002
        """Return ``None`` because Kiln's deposit fee is not percentage based.

        Use :py:meth:`get_deposit_fee_amount` to obtain its exact denomination
        token amount.

        :param block_identifier:
            Unused because the common percentage field is not applicable.

        :return:
            Always ``None``.
        """
        return None

    def get_deposit_fee_amount(self, block_identifier: BlockIdentifier) -> Decimal:
        """Read the fixed deposit fee in denomination-token units.

        :param block_identifier:
            Block at which to read the active deposit-fee configuration.

        :return:
            Fixed fee in human-readable denomination-token units.
        """
        deposit_fee = self._fetch_uint256("depositFee()", block_identifier)
        denomination_token = self.denomination_token
        assert denomination_token is not None, "Kiln vault denomination token is required for deposit fee conversion"
        return denomination_token.convert_to_decimals(deposit_fee)

    def get_estimated_lock_up(self) -> datetime.timedelta:  # noqa: PLR6301
        """Return Kiln's contract-level redemption delay.

        Kiln's published OmniVault ABI has synchronous ``deposit``, ``withdraw``
        and ``redeem`` methods, with no request, queue, cooldown, or withdrawal
        delay methods. This does not guarantee an immediate redemption when the
        underlying lending protocol lacks liquidity or the vault is paused.

        :return:
            Zero, because Kiln does not impose a protocol-level lock-up.
        """
        return datetime.timedelta(0)

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301, ARG002
        """Return Kiln's DeFi product page.

        :param referral:
            Optional referral code, not supported by Kiln's public page.

        :return:
            Kiln's DeFi product URL.
        """
        return "https://www.kiln.fi/defi"
