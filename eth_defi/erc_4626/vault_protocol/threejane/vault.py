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

from eth_typing import BlockIdentifier

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


class ThreeJaneVault(ERC4626Vault):
    """3Jane credit-market vault (USD3 senior / sUSD3 junior tranche).

    Standard ERC-4626 vaults whose yield is internalised in the share price.

    3Jane charges no explicit management, performance, deposit, withdrawal or
    redemption fees — suppliers receive the net pool interest, and the
    protocol's economics run through the borrower/lender interest spread
    (internalised in the share price). Redemptions are documented as "No fee".

    - Suppliers: https://docs.3jane.xyz/usd3-susd3/suppliers
    - FAQ (redemption fees): https://docs.3jane.xyz/resources/faq
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No explicit management fee; yield is the net pool interest."""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """No explicit performance fee; the protocol cut is the interest spread."""
        return 0.0

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
