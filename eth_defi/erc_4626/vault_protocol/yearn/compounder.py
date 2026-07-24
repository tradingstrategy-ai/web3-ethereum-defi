"""Yearn TokenizedStrategy compounder vault support.

Yearn compounders are Solidity ERC-4626 vaults that delegate their strategy
logic to a Yearn ``TokenizedStrategy`` implementation.  Their performance fee
is configured per vault in basis points and charged when a strategy report is
processed, diluting the share price rather than charging an investor directly.

- `Yearn TokenizedStrategy source <https://github.com/yearn/tokenized-strategy>`__
- `Verified TokenizedStrategy implementation <https://etherscan.io/address/0xD377919FA87120584B21279a491F82D5265A139c#code>`__
"""

import datetime

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

#: Yearn TokenizedStrategy fee precision: 10_000 basis points is 100%.
PERFORMANCE_FEE_DENOMINATOR = 10_000

#: Minimal ABI for the Yearn TokenizedStrategy fee accessor.
_PERFORMANCE_FEE_ABI = [
    {
        "inputs": [],
        "name": "performanceFee",
        "outputs": [{"internalType": "uint16", "name": "", "type": "uint16"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class YearnCompounderVault(ERC4626Vault):
    """Read fee data from Yearn TokenizedStrategy compounder vaults.

    These vaults expose a performance-fee percentage but no annual management,
    deposit, or standard-withdrawal fee.  The performance fee is accrued during
    strategy reporting and therefore is already reflected in the share price.
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:  # noqa: PLR6301
        """Return the absent Yearn compounder management fee.

        Yearn's TokenizedStrategy interface has no management-fee accessor and
        the supported compounder implementations charge only the configured
        performance fee.

        :param block_identifier:
            Block number or ``"latest"``. Ignored because the fee is absent.

        :return:
            Always ``0.0``.
        """
        del block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Read the strategy-report performance fee as a fraction.

        ``performanceFee()`` returns a ``uint16`` basis-point value.  The fee
        is internalised when Yearn processes a strategy report.

        :param block_identifier:
            Block number or ``"latest"`` at which to read the configuration.

        :return:
            Decimal performance-fee fraction, e.g. ``0.2`` for 20%.
        """
        contract = self.web3.eth.contract(address=self.vault_address, abi=_PERFORMANCE_FEE_ABI)
        raw_bps = contract.functions.performanceFee().call(block_identifier=block_identifier)
        return raw_bps / PERFORMANCE_FEE_DENOMINATOR

    def get_estimated_lock_up(self) -> datetime.timedelta:  # noqa: PLR6301
        """Return the immediate-redemption availability of compounder vaults.

        The supported Yearn compounder contracts use synchronous ERC-4626
        redemption and do not publish a lock-up period.

        :return:
            A zero-length lock-up.
        """
        return datetime.timedelta(0)

    def get_link(self, referral: str | None = None) -> str:
        """Return the direct Yearn vault page.

        :param referral:
            Optional referral identifier, unsupported by Yearn.

        :return:
            Yearn vault URL for this chain and vault address.
        """
        del referral
        return f"https://yearn.fi/v3/{self.chain_id}/{self.vault_address}"
