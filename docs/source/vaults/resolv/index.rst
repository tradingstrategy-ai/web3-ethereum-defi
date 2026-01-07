Resolv API
----------

`Resolv <https://resolv.xyz/>`__ integration.

Resolv is a protocol that maintains USR, a stablecoin fully backed by ETH and BTC
and pegged to the US Dollar. The stablecoin's delta-neutral design ensures price
stability, and is backed by an innovative insurance pool (RLP) to provide additional
security and overcollateralisation.

The wstUSR (Wrapped stUSR) vault is an ERC-4626 compliant wrapper around the rebasing
stUSR (staked USR) token. stUSR is a yield-bearing token that automatically compounds
returns generated from the basis trade. wstUSR provides a non-rebasing representation
of stUSR, making it suitable for DeFi integrations that don't support rebasing tokens.

Key features:

- ERC-4626 wrapper around rebasing stUSR token
- No deposit/withdrawal fees at the smart contract level
- Yield accrues through the underlying stUSR rebasing mechanism
- Instant deposits and withdrawals

- `Homepage <https://resolv.xyz/>`__
- `Documentation <https://docs.resolv.xyz/>`__
- `Twitter <https://x.com/ResolvLabs>`__
- `Contract on Etherscan <https://etherscan.io/address/0x1202f5c7b4b9e47a1a484e8b270be34dbbc75055>`__


.. autosummary::
   :toctree: _autosummary_resolv
   :recursive:

   eth_defi.erc_4626.vault_protocol.resolv.vault
