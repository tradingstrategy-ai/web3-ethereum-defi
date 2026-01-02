"""Term Finance vault support.

Term Finance is a noncustodial fixed-rate liquidity protocol modelled on tri-party
repo arrangements common in traditional finance (TradFi). Liquidity suppliers and
takers are matched through a unique weekly auction process where liquidity takers
submit bids and suppliers submit offers to the protocol.

The protocol determines a "market clearing rate" that matches supply and demand.
Bidders who bid more than the clearing rate receive loans and lenders asking less
than the clearing rate supply liquidity.

Key features:

- Fixed-rate DeFi lending and borrowing via auctions
- Scalable transactions with no spread, no slippage, and low fees
- Collateral sits in isolated noncustodial smart contracts (repoLocker)
- No rehypothecation - collateral cannot be lent to other borrowers
- Audited by Sigma Prime with DeFi Safety score of 93%

Term Finance vaults use the Yearn V3 tokenised strategy pattern as a base,
with fees internalised in the share price via the "discount rate markup".

- Homepage: https://www.term.finance/
- App: https://app.term.finance/
- Documentation: https://developers.term.finance
- GitHub: https://github.com/term-finance/term-finance-contracts
- Twitter: https://x.com/term_labs

Example vault contracts:

- Ethereum: https://etherscan.io/address/0xa10c40f9e318b0ed67ecc3499d702d8db9437228
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class TermFinanceVault(ERC4626Vault):
    """Term Finance vault.

    Term Finance provides fixed-rate DeFi lending via auction-based matching
    of liquidity suppliers and takers. Vaults use the Yearn V3 tokenised
    strategy pattern with fees internalised in the share price.

    - Homepage: https://www.term.finance/
    - App: https://app.term.finance/
    - Documentation: https://developers.term.finance
    - GitHub: https://github.com/term-finance/term-finance-contracts
    - Twitter: https://x.com/term_labs
    """

    def has_custom_fees(self) -> bool:
        """Term Finance vaults use internalised fee structure."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalised in the share price."""
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalised in the share price."""
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Term Finance vaults have no lock-up period."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the Term Finance vault page.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the vault on Term Finance app.
        """
        chain_id = self.spec.chain_id
        address = self.vault_address.lower()
        return f"https://app.term.finance/vaults/{address}/{chain_id}"
