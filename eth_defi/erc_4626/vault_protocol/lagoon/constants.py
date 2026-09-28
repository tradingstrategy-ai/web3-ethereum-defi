"""Reviewed address constants for Lagoon vault integrations."""

from eth_typing import HexAddress

#: Kamui's three permissioned Ethereum Lagoon vaults.
#:
#: These vaults are absent from Lagoon's paginated public catalogue and their
#: asynchronous capital flows do not emit the canonical vault-local ``Deposit``
#: events used by the generic activity gate. Keeping the addresses in one
#: chain-aware set ensures listing approval and activity-filter exemptions
#: cannot drift apart.
KAMUI_LAGOON_VAULTS: frozenset[tuple[int, HexAddress]] = frozenset(
    {
        #: Stable Vault.
        (1, HexAddress("0xcda323c2df692d989b24ba51d0acca924cf9a344")),
        #: Balanced Vault.
        (1, HexAddress("0xa5ae405242f42c47996a0c6857ff10a77f9bdee6")),
        #: Boosted Vault.
        (1, HexAddress("0x9e0db8f43bb91e2148b0db920e21370525cf3aab")),
    }
)


def is_kamui_lagoon_vault(chain_id: int, vault_address: HexAddress | str) -> bool:
    """Check whether an address is one of Kamui's reviewed Lagoon vaults.

    The match includes the chain ID and normalises checksum casing. This keeps
    address-scoped scanner exceptions from applying to an unrelated deployment
    that reuses the same address on another chain.

    :param chain_id:
        EVM chain ID of the Lagoon deployment.

    :param vault_address:
        Vault contract address in any checksum casing.

    :return:
        ``True`` only for a reviewed Kamui chain-address pair.
    """

    return (chain_id, HexAddress(vault_address.lower())) in KAMUI_LAGOON_VAULTS
