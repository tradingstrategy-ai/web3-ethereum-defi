Shift
=====

`Shift <https://shiftprotocol.xyz/>`__ provides automated USDC yield strategies
using perpetual-DEX funding, basis-trading and stablecoin positions. The
reviewed vaults are custom ERC-20 ``ShiftVault`` share contracts: they do not
implement ERC-4626.

The integration uses a chain-aware hardcoded registry for the published
``ltPARA`` and ``extUSD`` Base vaults and the ``ltLLP`` Arbitrum vault. This
lets discovery track deployments that cannot be identified through ERC-4626
event or interface probes. Its historical reader combines
``getSharePrice()`` from Shift's TVL feed with ERC-20 total supply to calculate
TVL. A zero share price denotes a stale feed, not a reliable zero-TVL valuation.

Dealing and risks
-----------------

Shift deposit requests need executor approval. Withdrawals are executor-batch
resolved and are subject to a timelock measured from the withdrawal request,
so the adapter exposes no public deposit or redemption transaction manager.
Fees are read from the vault
and represented as treasury-minted fee shares. Shift's
`FAQ <https://shiftprotocol.gitbook.io/shift/resources/faq>`__ documents
strategy-dependent 0–2% annual management fees, 0–20% performance fees and
withdrawal processing that may take up to seven days.

.. autosummary::
   :toctree: _autosummary_shift
   :recursive:

   eth_defi.tokenised_fund.shift.constants
   eth_defi.tokenised_fund.shift.descriptions
   eth_defi.tokenised_fund.shift.vault
   eth_defi.tokenised_fund.shift.historical
