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

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/accountable>`__
- `Homepage <https://www.accountable.capital/>`__
- `Twitter <https://x.com/AccountableData>`__
- `LinkedIn <https://www.linkedin.com/company/accountablecapital/>`__

Notes
~~~~~

- No public GitHub repository available for smart contracts
- Smart contracts are verified via Sourcify on Monad block explorers
- Fee information is not publicly exposed on-chain

.. autosummary::
   :toctree: _autosummary_accountable
   :recursive:

   eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata
   eth_defi.erc_4626.vault_protocol.accountable.vault
   eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem
   eth_defi.erc_4626.vault_protocol.accountable.settlement
