Bulla Network
-------------

`Bulla Network <https://www.bulla.network/>`__ provides infrastructure for
financing trade invoices and direct loan offers. Its Bulla Factoring pools use
ERC-4626 accounting for shares while funding receivables through Bulla Claim
and BullaFrendLend contracts. The public `factoring-contracts repository
<https://github.com/bulla-network/factoring-contracts>`__ contains the
contracts, including the BullaFactoring implementation.

This integration recognises Bulla Factoring vaults and supplies safe read
support. It deliberately does not advertise a generic deposit manager: Bulla
pools can restrict deposits, redemptions and factoring to separate permission
managers, and withdrawals may enter a liquidity-dependent redemption queue.
Depositors must assess pool-specific credit, collection, liquidity and
underwriter risk. Published `audit reports
<https://github.com/bulla-network/factoring-contracts/tree/main/audits>`__
are useful security evidence, but do not cover those economic risks.

Links
~~~~~

- `Bulla pools <https://banker.bulla.network/#/yield>`__
- `Documentation <https://docs.bulla.network/>`__
- `GitHub <https://github.com/bulla-network>`__
- `Twitter <https://x.com/BullaNetwork>`__
- `Example Arbitrum pool <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37>`__

.. autosummary::
   :toctree: _autosummary_bulla
   :recursive:

   eth_defi.erc_4626.vault_protocol.bulla.vault
