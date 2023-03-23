"""High level interface to read Enzyme vaults.

See :py:class:`Vault`.
"""
from dataclasses import dataclass
from functools import cached_property
from typing import Collection

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.token import TokenDetails, fetch_erc20_details


@dataclass
class Vault:
    """Enzyme vault wrapper.

    - Vaults are Enzyme Protocol "funds" where you have investors and assets

    - Investors have ownership of vault assets with a share token

    - Each vault has its denomiation asset, e.g. USDC that you use for the buy in

    - You buy-in to a vault using `buyShares`

    - Redemption is "in-kind" and you swap your share tokens to the tokens
      of underlying open positions and other assets

    - A separate vault owner (fund owner) can make the vault to perform trades

    Vault in Enzyme are presented by two smart contracts

    - Vault contract

    - Comptroller contract

    - `Vaults are upgradeable <https://specs.enzyme.finance/architecture/persistent>`__

    - `See Enzyme documentation for general information about vaults <https://docs.enzyme.finance/managers/setup/fund-basics>`__.

    - `See Enzyme spec for technical information about vaults <https://specs.enzyme.finance/>`__.
    """

    #: Vault smart contract
    #:
    #: The VaultLib contract contains the storage layout, event signatures, and logic for VaultProxy instances that are attached to this release.
    vault: Contract

    #: Comptroller smart contract
    #:
    #: A ComptrollerProxy is deployed per-fund, and it is the canonical contract for interacting with a fund in this release. It stores core release-level configuration and is attached to a VaultProxy via the latter's accessor role.
    #:
    #: Emits important events like `SharesBought`, `SharesRedeemed`
    comptroller: Contract

    @property
    def web3(self) -> Web3:
        """Web3 connection.

        Used for reading JSON-RPC calls
        """
        return self.vault.w3

    @cached_property
    def denomination_token(self) -> TokenDetails:
        """Get the denominator token for withdrawal/deposit.

        - Read the token on-chain details.

        - Cache the results for the future calls

        :return:
            Usually ERC-20 details for USDC

        """
        return fetch_erc20_details(self.web3, self.get_denomination_asset())

    @cached_property
    def shares_token(self) -> TokenDetails:
        """Get the shares token for withdrawal/deposit.

        - Read the token on-chain details.

        - Cache the results for the future calls

        :return:
            ERC-20 details for a token with the fund name/symbol and 18 decimals.

        """
        return fetch_erc20_details(self.web3, self.get_shares_asset())

    def get_owner(self) -> HexAddress:
        """Who is the vault owner.

        Vault owner has special priviledges like calling the adapters.

        See `IVaultCore.sol`.
        """
        return self.vault.functions.getOwner()

    def get_name(self) -> str:
        """Get the name of the share token.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.name().call()

    def get_symbol(self) -> str:
        """Get the symbol of share tokens.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.symbol().call()

    def get_total_supply(self) -> int:
        """Get the number of share tokens.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.totalSupply().call()

    def get_decimals(self) -> int:
        """Get the ERC-20 decimals of the shares.

        See `SharesTokenBase.sol`.
        """
        return self.vault.functions.decimals().call()

    def get_denomination_asset(self) -> HexAddress:
        """Get the reserve ERC-20 asset for this vault."""
        return self.comptroller.functions.getDenominationAsset().call()

    def get_shares_asset(self) -> HexAddress:
        """Get the shares ERC-20 token for this vault.

        Enzyme vault acts as ERC-20 contract as well.
        """
        return self.vault.address

    def get_tracked_assets(self) -> Collection[HexAddress]:
        """Get the list of assets this vault tracks.

        :return:
            List of ERC-20 addresses
        """
        return self.vault.functions.getTrackedAssets().call()

    def get_gross_asset_value(self) -> int:
        """Calculate the gross asset value (GAV) of the fund.

        Call the Solidity function that does this on the smart contract side.

        See `ComptrollerLib.sol`.

        :return:
            TODO - no idea
        """
        return self.comptroller.functions.calcGav().call()

    def get_share_gross_asset_value(self) -> int:
        """Calculate the one share unit gross asset value (GAV) on the smart contract side.

        Call the Solidity function that does this on the smart contract side.

        See `ComptrollerLib.sol`.

        :return:
            TODO - no idea
        """
        return self.comptroller.functions.calcGrossShareValue().call()

    def get_share_count_for_user(self, user: HexAddress) -> int:
        """How mayn shares a user has.

        :return:
            Raw token amount
        """
        return self.vault.functions.balanceOf(user).call()
