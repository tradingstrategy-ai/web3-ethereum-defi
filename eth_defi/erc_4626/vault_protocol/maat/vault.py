"""MAAT strategies accept deposits only from registered token vaults."""

from web3 import Web3
from eth_typing import BlockIdentifier
from eth_defi.vault.fee import VaultFeeMode

from eth_defi.erc_4626.vault import ERC4626Vault


class MaatVault(ERC4626Vault):
    """Identify MAAT strategies that accept deposits only from registered vaults.

    The MAAT address-provider contract maintains this registration. An ordinary
    wallet cannot deposit merely because the strategy uses an ERC-4626 interface.
    """

    def is_whitelisted_deposit(self) -> bool:
        """Mark deposits as restricted because ``onlyTokenVault`` requires registration.

        :return: ``True`` because MAAT's provider must register the caller.
        """
        return True

    def is_account_whitelisted(self, address: str) -> bool:
        """Check registration in the provider used by MAAT's deposit restriction.

        Read the provider address from the strategy, then ask its ``isVault``
        function about the depositing account. Both reads use the adapter's
        selected block so historical checks use the corresponding registration.

        :param address: Account submitting the deposit.
        :return: Whether the provider recognises this account as a token vault.
        """
        provider = self.web3.eth.call({"to": self.address, "data": Web3.keccak(text="MaatAddressProvider()")[:4]}, block_identifier=self._get_block_identifier())
        if len(provider) != 32 or not int.from_bytes(provider, "big"):
            raise NotImplementedError(f"Unreadable MAAT address provider for {self.address}")
        provider_address = Web3.to_checksum_address(provider[-20:])
        result = self.web3.eth.call({"to": provider_address, "data": Web3.keccak(text="isVault(address)")[:4] + bytes.fromhex(address.removeprefix("0x")).rjust(32, b"\x00")}, block_identifier=self._get_block_identifier())
        if len(result) != 32:
            raise NotImplementedError(f"Unreadable MAAT admission for {self.address}")
        return bool(int.from_bytes(result, "big"))

    def get_fee_mode(self) -> VaultFeeMode:
        """MAAT collects performance fees by minting strategy shares.

        :return: Fee mode where newly minted shares dilute existing holders.
        """
        return VaultFeeMode.internalised_minting

    def get_management_fee(self, block_identifier: BlockIdentifier = "latest") -> float:
        """MAAT's reviewed Strategy charges no management fee.

        :param block_identifier: Unused for the fixed zero management fee.
        :return: Zero annual management fee.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier = "latest") -> float:
        """Convert the stored performance fee using MAAT's declared scale.

        Divide ``performanceFee`` by ``feePrecision`` from the same requested
        block rather than assuming that the stored number is a percentage.

        :param block_identifier: Contract state snapshot.
        :return: Performance fee as a fraction.
        """
        values = [int.from_bytes(self.web3.eth.call({"to": self.address, "data": Web3.keccak(text=name)[:4]}, block_identifier=block_identifier), "big") for name in ("performanceFee()", "feePrecision()")]
        return values[0] / values[1]
