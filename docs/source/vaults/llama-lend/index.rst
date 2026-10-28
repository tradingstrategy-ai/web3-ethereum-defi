Llama Lend vaults
-----------------

`Llama Lend <https://www.curve.finance/llamalend/ethereum/markets>`__ is Curve's
isolated lending protocol. This integration reads V1 and V2 lender vaults.

Lenders supply the borrowed token, such as USDC, and receive ERC-4626 shares.
The share value accrues borrower interest. The collateral token, such as wstETH,
secures borrowers' loans; lenders do not receive that token's staking yield
directly.

LLAMMA (Lending Liquidating Automated Market Maker Algorithm) rebalances borrower
collateral through soft liquidations. This mechanism does not guarantee against
losses or unavailable withdrawal liquidity.

Liquidity and utilisation
~~~~~~~~~~~~~~~~~~~~~~~~~

The controller holds unborrowed lending tokens. Its outstanding debt and the
vault's assets give the following metrics:

.. code-block:: text

   available_liquidity = max(totalAssets - controller.total_debt, 0)
   utilisation = min(controller.total_debt / totalAssets, 1)

An empty vault has zero utilisation. The
`V1 vault <https://docs.curve.finance/developer/lending/contracts/vault/>`__ includes
the controller's token balance in assets. The
`V2 vault <https://docs.curve.finance/developer/llamalend-v2/vault/>`__ deducts accrued
protocol fees, so subtracting debt gives net lender liquidity in both versions.
The zero separate management and performance fees reported by the adapter do not
exclude protocol deductions already reflected in NAV.

Spot queries pin assets and debt to the same block. Historical scans use the
same accounting through multicall and export ``available_liquidity`` and
``utilisation``. Failed metric reads remain missing. The metadata cache version
is bumped to refresh stored spot observations on the next metadata scan.
Historical price rows are not automatically repaired, and the scanner retains
its existing sampling policy.

Withdrawals have no scheduled waiting period, but require sufficient market
liquidity and remain subject to protocol and account limits.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/llama-lend>`__
- `Homepage <https://curve.fi/>`__
- `LLAMMA reference <https://docs.curve.finance/crvUSD/amm/>`__
- `GitHub <https://github.com/curvefi>`__
- `Twitter <https://x.com/llamalend>`__
- `DefiLlama <https://defillama.com/protocol/curve-llamalend>`__

.. autosummary::
   :toctree: _autosummary_llama_lend
   :recursive:

   eth_defi.erc_4626.vault_protocol.llama_lend.vault
