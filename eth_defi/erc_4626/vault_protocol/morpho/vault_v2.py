"""Morpho Vault V2 support.

Morpho Vault V2 is an upgraded version of Morpho vaults that introduces
an adapter-based architecture for flexible asset allocation across multiple yield sources.

- `Morpho V2 documentation <https://docs.morpho.org/learn/concepts/vault-v2/>`__
- `GitHub repository <https://github.com/morpho-org/vault-v2>`__
- `Example vault on Arbitrum <https://arbiscan.io/address/0xbeefff13dd098de415e07f033dae65205b31a894>`__

Key features of Morpho Vault V2:

- Adapter-based architecture for multi-protocol yield allocation
- Granular ID & Cap system for risk management
- Performance and management fees (up to 50% and 5% respectively)
- Timelocked governance with optional abdication
- Non-custodial exits via forceDeallocate
"""

import datetime
import logging

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall

logger = logging.getLogger(__name__)

#: Maximum performance fee in Morpho V2 (50%)
MAX_PERFORMANCE_FEE = 0.5

#: Maximum management fee in Morpho V2 (5% per year)
MAX_MANAGEMENT_FEE = 0.05

#: Fee denominator used in Morpho V2 contracts (1e18)
FEE_DENOMINATOR = 10**18


class MorphoV2Vault(ERC4626Vault):
    """Morpho Vault V2 support.

    Morpho Vault V2 is a newer version of Morpho vaults with an adapter-based
    architecture that allows flexible allocation across multiple yield sources.

    - `Morpho V2 documentation <https://docs.morpho.org/learn/concepts/vault-v2/>`__
    - `GitHub repository <https://github.com/morpho-org/vault-v2>`__
    - `Example vault on Arbitrum <https://arbiscan.io/address/0xbeefff13dd098de415e07f033dae65205b31a894>`__

    Key differences from Morpho V1:

    - V2 uses adapters to allocate to multiple yield sources (not just Morpho markets)
    - V2 has both performance and management fees (V1 only had performance fee)
    - V2 uses ``adaptersLength()`` function while V1 uses ``MORPHO()`` function
    - V2 has timelocked governance with curator/allocator roles

    See also :py:class:`eth_defi.erc_4626.vault_protocol.morpho.vault_v1.MorphoV1Vault`
    for the original MetaMorpho architecture.
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get Morpho V2 management fee.

        Management fee is charged on total assets (up to 5% per year).

        :return:
            Management fee as a decimal (e.g. 0.02 for 2%), or None if reading fails
        """
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="managementFee()")[0:4],
            function="managementFee",
            data=b"",
            extra_data={"vault": self.address},
        )
        try:
            data = fee_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "Management fee read reverted on Morpho V2 vault %s: %s",
                self,
                str(e),
            )
            return None

        # Management fee is stored as uint96, scaled by 1e18
        management_fee = int.from_bytes(data[0:32], byteorder="big") / FEE_DENOMINATOR
        return management_fee

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get Morpho V2 performance fee.

        Performance fee is charged on yield generated (up to 50%).

        :return:
            Performance fee as a decimal (e.g. 0.1 for 10%), or None if reading fails
        """
        fee_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="performanceFee()")[0:4],
            function="performanceFee",
            data=b"",
            extra_data={"vault": self.address},
        )
        try:
            data = fee_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "Performance fee read reverted on Morpho V2 vault %s: %s",
                self,
                str(e),
            )
            return None

        # Performance fee is stored as uint96, scaled by 1e18
        performance_fee = int.from_bytes(data[0:32], byteorder="big") / FEE_DENOMINATOR
        return performance_fee

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Morpho V2 vaults have no lock-up period.

        Users can withdraw at any time using regular withdraw or forceDeallocate.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the vault on Morpho app.

        :param referral:
            Optional referral code (not supported by Morpho)

        :return:
            URL to the vault page on app.morpho.org
        """
        chain_name = get_chain_name(self.chain_id).lower()
        return f"https://app.morpho.org/{chain_name}/vault/{self.vault_address}/"

    def get_adapters_count(self, block_identifier: BlockIdentifier = "latest") -> int | None:
        """Get the number of adapters configured for this vault.

        :return:
            Number of adapters, or None if reading fails
        """
        adapters_call = EncodedCall.from_keccak_signature(
            address=self.address,
            signature=Web3.keccak(text="adaptersLength()")[0:4],
            function="adaptersLength",
            data=b"",
            extra_data={"vault": self.address},
        )
        try:
            data = adapters_call.call(self.web3, block_identifier)
        except ValueError as e:
            logger.warning(
                "adaptersLength() read reverted on Morpho V2 vault %s: %s",
                self,
                str(e),
            )
            return None

        return int.from_bytes(data[0:32], byteorder="big")
