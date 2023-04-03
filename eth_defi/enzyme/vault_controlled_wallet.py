"""Vault owner wallet implementation.
"""
from dataclasses import dataclass
from typing import List

from eth_typing import HexAddress

from eth_defi.enzyme.vault import Vault
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce


@dataclass
class AssetDelta:
    """Spend/incoming asset information."""

    #: Change
    #:
    #: Negative for spent, positive for incoming
    raw_amount: int

    #: The ERC-20 token for this change
    asset: HexAddress


@dataclass
class EnzymeVaultTransaction:
    """Inputs needed to perform a vault transaction."""

    #: Smart contract address the vault is calling
    target_address: HexAddress

    #: Encoded
    arguments: HexAddress

    #: If this transaction results to changes in the vault balance it must be listed here.
    #:
    #:
    asset_deltas: List[AssetDelta]


class VaultControlledWallet:
    """A vault wallet.

    - Allows you to sign and broadcast transactions concerning Enzyme's vault as a vault owner.

    - Vault owner can only broadcast specific transactions allowed by Enzyme's GenericAdapter
    """

    def __init__(self,
            vault: Vault,
            hot_wallet: HotWallet):
        """Create a vault controlling wallet.

        :param hot_wallet:
            The fund deployment account as a EOA wallet.
        """
        self.vault = vault
        self.hot_wallet = hot_wallet

    @property
    def address(self) -> HexAddress:
        """Get the vault address."""
        return self.vault.address

    def sign_transaction_with_new_nonce(self, tx: EnzymeVaultTransaction) -> SignedTransactionWithNonce:
        """Signs a transaction and allocates a nonce for it.

        :param: Ethereum transaction data as a dict. This is modified in-place to include nonce.
        """

        assert isinstance(tx, EnzymeVaultTransaction)

        assert "nonce" not in tx
        tx["nonce"] = self.allocate_nonce()
        _signed = self.account.sign_transaction(tx)
        decode_signed_transaction(_signed.rawTransaction)
        signed = SignedTransactionWithNonce(
            rawTransaction=_signed.rawTransaction,
            hash=_signed.hash,
            v=_signed.v,
            r=_signed.r,
            s=_signed.s,
            nonce=tx["nonce"],
            source=tx,
        )
        return signed



