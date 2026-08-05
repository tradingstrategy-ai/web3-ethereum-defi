Accountable API
---------------

`Accountable Capital <https://www.accountable.capital/>`__ integration.

Accountable Capital develops blockchain-based financial verification technology
that enables organisations and investors to demonstrate solvency, liquidity,
and compliance through transparent, verifiable attestations. The platform
combines cryptographic proofs with auditable financial data to enhance trust
across Web3 and traditional finance.

Accountable vaults implement the ERC-7540 async redemption pattern with a queue
system for processing withdrawal requests. The protocol is primarily deployed
on Monad blockchain.

Deposits are synchronous standard ERC-4626 calls. Redemptions first use
``requestRedeem`` and are claimed later with ``redeem`` once the strategy emits
``RedeemClaimable``. Accountable tracks the pending and claimable share amounts
per controller, so the integration allows only one outstanding redemption per
owner. If another authorised actor claims between status checking and broadcast,
read the status again and build a fresh claim transaction.
The public manager only auto-claims self-controlled redemptions to their share
owner. This avoids routing a controller-level aggregate to a custom receiver;
delegated-controller historical requests are discovered but not auto-claimed.

Deposit permissions
~~~~~~~~~~~~~~~~~~~

The `verified Accountable vault source <https://monadscan.com/address/0x7Cd231120a60F500887444a9bAF5e1BD753A5e59#code>`__
defines three constructor-selected permission levels. ``None`` is
permissionless, ``KYC`` verifies an Accountable-signed payload appended to each
call, and ``Whitelist`` checks persistent ``allowed(address)`` membership.
When the share receiver and controller differ, the vault checks both accounts.
The Hyperithm Delta Neutral deployment uses ``None``. Its strategy capacity,
loan state and minimum amount are separate lifecycle constraints and must not
be reported as KYC.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/accountable>`__
- `Homepage <https://www.accountable.capital/>`__
- `Twitter <https://x.com/AccountableData>`__
- `LinkedIn <https://www.linkedin.com/company/accountablecapital/>`__

Notes
~~~~~

- No canonical public source repository is linked from the verified deployment
- Smart contract source is verified on MonadScan
- Fee terms are read from the deployment's Accountable fee-manager contract

.. autosummary::
   :toctree: _autosummary_accountable
   :recursive:

   eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata
   eth_defi.erc_4626.vault_protocol.accountable.vault
   eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem
   eth_defi.erc_4626.vault_protocol.accountable.settlement
