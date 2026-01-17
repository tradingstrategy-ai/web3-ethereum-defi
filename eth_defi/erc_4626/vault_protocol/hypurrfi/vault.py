"""HypurrFi vault support.

HypurrFi is a lending market built on Hyperliquid's HyperEVM that enables users
to deposit and borrow native Hyperliquid assets for leveraged yield.

- Homepage: https://www.hypurr.fi/
- App: https://app.hypurr.fi/
- Documentation: https://docs.hypurr.fi/
- Twitter: https://twitter.com/hypurrfi
- GitHub: https://github.com/lastdotnet
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class HypurrFiVault(ERC4626Vault):
    """HypurrFi lending vault support.

    HypurrFi is a lending market on HyperEVM (Hyperliquid) that enables:

    - Deposit and borrow native Hyperliquid assets
    - Leveraged yield strategies
    - Pooled and isolated lending markets

    - `Homepage <https://www.hypurr.fi/>`__
    - `App <https://app.hypurr.fi/>`__
    - `Documentation <https://docs.hypurr.fi/>`__
    - `Example vault on HyperEVMScan <https://hyperevmscan.io/address/0x8001e1e7b05990d22dd8cdb9737f9fe6589827ce>`__

    Vault naming pattern: Names start with "hy" and end with a hyphen followed by a digit (e.g. "hyUSDXL (Purr) - 2").
    """

    def has_custom_fees(self) -> bool:
        """HypurrFi fees are internalised into the share price."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """HypurrFi fees are internalised.

        Fee information not publicly documented.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """HypurrFi fees are internalised.

        Fee information not publicly documented.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """No lock-up period for HypurrFi vaults."""
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Return link to HypurrFi app.

        HypurrFi uses pooled and isolated market views.
        """
        return "https://app.hypurr.fi/markets/pooled"
