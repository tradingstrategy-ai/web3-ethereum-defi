Theo
====

`Theo <https://theo.xyz/>`__ provides tokenised financial products that connect
on-chain capital to global markets. This integration covers the canonical
Ethereum thBILL token, a basket of institutional-grade tokenised U.S. Treasury
bills using Theo's multi-asset iToken structure.

thBILL is not a conventional single-asset ERC-4626 vault. Its iToken accounting
uses a basket of approved assets, so the adapter tracks the ERC-20 share supply
but deliberately leaves NAV/share and TVL unavailable until a reviewed,
historical basket valuation source is configured.

Eligibility and dealing
-----------------------

Theo's `thBILL documentation <https://docs.theo.xyz/thbill>`__ states that direct
minting and redemption require KYC approval. Redemption is issuer-serviced and
settles in USDC, so the adapter does not expose public deposit, redemption or
generic flow managers. The fixed canonical Ethereum address comes from Theo's
`deployment registry <https://docs.theo.xyz/technical-reference/deployments>`__;
cross-chain OFT representations are not treated as independent fund products.

Fees and curator attribution
----------------------------

No universal fee schedule was found in the public thBILL product documentation,
so fee data remains unavailable rather than estimated. The canonical Ethereum
token is attributed to Theo as an address-scoped protocol-managed curator: Theo
operates the iToken and the documented KYC and servicing workflow.

.. autosummary::
   :toctree: _autosummary_theo
   :recursive:

   eth_defi.tokenised_fund.theo.constants
   eth_defi.tokenised_fund.theo.vault
   eth_defi.tokenised_fund.theo.historical
