"""3Jane protocol vault support.

`3Jane <https://www.3jane.xyz/>`__ is a decentralised, credit-based money market
on Ethereum that facilitates uncollateralised stablecoin lending. Depositors
supply USDC and receive the protocol's ERC-4626 vault tokens — ``USD3`` (the
senior tranche) or, by staking ``USD3``, ``sUSD3`` (the junior tranche). The
pooled capital is lent across uncollateralised USDC credit lines to
crypto-native borrowers and funding conduits to U.S. fintech lenders.

Yield is internalised in the ERC-4626 share price: ``USD3`` appreciates against
USDC as interest accrues, and ``sUSD3`` captures a higher proportion of pool
yield in exchange for absorbing losses first in the senior/junior waterfall.

The vaults are Yearn V3 TokenizedStrategy contracts (``apiVersion`` 3.0.4), so
the senior/junior yield split is implemented through the strategy's on-chain
``performanceFee()`` (basis points out of 10000): ``USD3`` reads ``1000`` (10%)
whose ``performanceFeeRecipient`` is the ``sUSD3`` vault itself, i.e. it is the
``TRANCHE_SHARE_VARIANT`` waterfall routing senior yield to the junior tranche,
**not** a 3Jane protocol fee. ``sUSD3`` reads ``0``.

3Jane is a single-protocol issuer of its own vaults, so the vaults are detected
via :py:data:`eth_defi.erc_4626.classification.HARDCODED_PROTOCOLS` rather than
an on-chain probe call.

- Homepage: https://www.3jane.xyz/
- Docs: https://docs.3jane.xyz/
- USD3 (senior): https://etherscan.io/address/0x056B269Eb1f75477a8666ae8C7fE01b64dD55eCc
- sUSD3 (junior): https://etherscan.io/address/0xf689555121e529Ff0463e191F9Bd9d1E496164a7
"""

import datetime
import logging

from web3.types import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)

#: sUSD3 (junior tranche) vault address on Ethereum.
#:
#: https://etherscan.io/address/0xf689555121e529Ff0463e191F9Bd9d1E496164a7
SUSD3_ADDRESS = "0xf689555121e529ff0463e191f9bd9d1e496164a7"

#: sUSD3 junior-tranche withdrawal lock.
#:
#: 3Jane's docs and protocol config (``SUSD3_LOCK_DURATION``) set a one-month
#: cooldown on junior-tranche redemptions; the senior tranche (USD3) has none.
#: https://docs.3jane.xyz/
SUSD3_LOCK_DURATION = datetime.timedelta(days=30)

#: Yearn V3 TokenizedStrategy fee precision: fees are basis points, 10000 = 100%.
PERFORMANCE_FEE_DIVISOR = 10_000

#: Minimal ABI for the Yearn V3 ``performanceFee()`` accessor.
_PERFORMANCE_FEE_ABI = [
    {"name": "performanceFee", "outputs": [{"type": "uint16"}], "inputs": [], "stateMutability": "view", "type": "function"},
]


class ThreeJaneVault(ERC4626Vault):
    """3Jane credit-market vault (USD3 senior / sUSD3 junior tranche).

    Yearn V3 TokenizedStrategy ERC-4626 vaults whose yield is internalised in the
    share price.

    3Jane charges no explicit management, deposit, withdrawal or redemption fee —
    suppliers receive the net pool interest. The on-chain ``performanceFee()`` is
    **not** a 3Jane protocol fee: on the ``USD3`` senior tranche it is the
    ``TRANCHE_SHARE_VARIANT`` waterfall (its ``performanceFeeRecipient`` is the
    ``sUSD3`` vault), so the 10% it reads is the senior→junior yield share. We
    surface it through :py:meth:`get_performance_fee` because, from a USD3 holder's
    point of view, that share is still skimmed from their yield.

    .. note ::

        No *management* fee percentage is published (no accessor, no docs figure),
        so we report 0.0 for it. The performance fee is read live from
        ``performanceFee()`` rather than hard-coded.

    - Suppliers: https://docs.3jane.xyz/usd3-susd3/suppliers
    - FAQ (redemption fees): https://docs.3jane.xyz/resources/faq
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No explicit management fee; yield is the net pool interest."""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Read the Yearn V3 ``performanceFee()`` (bps) and return it as a fraction.

        For ``USD3`` this is the senior→junior tranche share (10%, recipient is the
        ``sUSD3`` vault), not a 3Jane protocol fee; ``sUSD3`` returns 0.0. See the
        class docstring.

        :param block_identifier:
            Block to read the fee at.

        :return:
            Performance fee as a fraction (e.g. ``0.1`` for 10%).
        """
        contract = self.web3.eth.contract(address=self.vault_address, abi=_PERFORMANCE_FEE_ABI)
        raw_bps = contract.functions.performanceFee().call(block_identifier=block_identifier)
        return raw_bps / PERFORMANCE_FEE_DIVISOR

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Junior-tranche sUSD3 has a one-month redemption lock; senior USD3 has none.

        :return:
            :py:data:`SUSD3_LOCK_DURATION` for the sUSD3 vault, otherwise
            ``timedelta(0)`` (USD3 redemptions are not time-locked).
        """
        if self.vault_address.lower() == SUSD3_ADDRESS:
            return SUSD3_LOCK_DURATION
        return datetime.timedelta(0)

    def get_link(self, referral: str | None = None) -> str:
        return "https://www.3jane.xyz/"
