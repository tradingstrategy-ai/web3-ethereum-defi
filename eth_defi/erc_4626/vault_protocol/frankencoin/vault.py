"""Frankencoin savings vault support.

Frankencoin is an over-collateralised, oracle-free Swiss franc stablecoin
protocol. Its Savings Vaults are ERC-4626 wrappers for the Frankencoin savings
module, allowing users to deposit ZCHF and receive svZCHF shares.

The vaults do not have protocol-wide management, performance, deposit, or
withdrawal fees. They do support an optional per-account referral fee that is
deducted from earned interest and paid to the configured referrer.

- Homepage: https://frankencoin.com/
- Token and savings vault page: https://frankencoin.com/token/
- Documentation: https://docs.frankencoin.com/
- GitHub: https://github.com/Frankencoin-ZCHF/Frankencoin
- Savings module source: https://github.com/Frankencoin-ZCHF/Frankencoin/blob/main/contracts/minting/v2/SavingsV2.sol
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)

#: Frankencoin Savings Vault on Ethereum.
#:
#: https://etherscan.io/token/0xE5F130253fF137f9917C0107659A4c5262abf6b0
FRANKENCOIN_ETHEREUM_SAVINGS_VAULT = "0xe5f130253ff137f9917c0107659a4c5262abf6b0"

#: Frankencoin Savings Vault on Base.
#:
#: https://basescan.org/address/0xa09EBdf8A01b9ef04149319D64F83b9C01a5b585
FRANKENCOIN_BASE_SAVINGS_VAULT = "0xa09ebdf8a01b9ef04149319d64f83b9c01a5b585"

#: Frankencoin Savings Vault on Gnosis.
#:
#: https://gnosisscan.io/token/0x6165946250dd04740ab1409217e95a4f38374fe9
FRANKENCOIN_GNOSIS_SAVINGS_VAULT = "0x6165946250dd04740ab1409217e95a4f38374fe9"

#: Official Frankencoin Savings Vault addresses across supported chains.
FRANKENCOIN_SAVINGS_VAULTS = frozenset(
    {
        FRANKENCOIN_ETHEREUM_SAVINGS_VAULT,
        FRANKENCOIN_BASE_SAVINGS_VAULT,
        FRANKENCOIN_GNOSIS_SAVINGS_VAULT,
    }
)

#: Maximum optional referral fee in the Frankencoin savings module.
#:
#: Source: ``AbstractSavings.setReferrer()`` rejects values above 250,000 ppm.
MAX_REFERRAL_FEE = 0.25


class FrankencoinVault(ERC4626Vault):
    """Frankencoin ERC-4626 savings vault support.

    Frankencoin Savings Vaults tokenise deposits into the Frankencoin savings
    module. The underlying contract source documents an interest delay of up to
    three days before deposits start earning yield.
    """

    def has_custom_fees(self) -> bool:
        """Frankencoin has an optional per-account referral fee.

        Frankencoin does not charge fixed vault-level management, performance,
        deposit, or withdrawal fees. However, a user can configure a referrer
        that receives up to 25% of the earned interest, which is account-level
        fee data outside the shared protocol fee fields.
        """
        _ = self.vault_address
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return the vault management fee.

        Frankencoin Savings Vaults do not charge a protocol-wide management fee
        at the vault layer. Yield comes from the Frankencoin savings module.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Management fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return the vault performance fee.

        Frankencoin Savings Vaults do not charge a protocol-wide performance
        fee at the vault layer. A separate optional referral fee can skim up to
        25% of earned interest for accounts that configure a referrer.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Performance fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Return the savings module interest delay as a lock-up estimate.

        The verified ``SavingsV2`` source documents that saved ZCHF is subject
        to a lock-up of up to three days before it starts earning interest.

        :return:
            Estimated savings delay.
        """
        _ = self.vault_address
        return datetime.timedelta(days=3)

    def get_link(self, referral: str | None = None) -> str:
        """Return the Frankencoin token and savings vault page."""
        _ = self.vault_address, referral
        return "https://frankencoin.com/token/#svzchf"
