Midas
=====

`Midas <https://midas.app/>`__ issues tokenised investment products such as
mTBILL and mBASIS. The official `Midas smart contracts registry
<https://docs.midas.app/resources/smart-contracts-registry>`__ lists each
product as a group of contracts: an ERC-20 mToken, NAV oracle, issuance vault,
redemption vault and, where applicable, bridge contracts.

Midas products are not ERC-4626 or ERC-7540 vaults. Their direct issuance and
redemption flows are implemented with bespoke Midas functions such as
``depositInstant()``, ``depositRequest()``, ``redeemInstant()`` and
``redeemRequest()``. Because of this, eth_defi supports Midas through a direct
``VaultBase`` adapter instead of the ERC-4626 adapter stack.

Historical prices
-----------------

The Midas adapter reads historical share price using the product datafeed
contract. For each block, the historical reader multicalls:

- ``mToken.totalSupply()`` for outstanding shares
- ``dataFeed.getDataInBase18()`` for NAV/share

TVL is calculated as ``totalSupply * NAV/share`` in the product denomination.
The initial implementation supports Ethereum mTBILL and mBASIS, both
USD-denominated products.

Limitations
-----------

Active issuance and redemption are not implemented. The adapter intentionally
marks generic deposits and redemptions as unavailable because Midas flows are
not ERC-4626/ERC-7540 compatible and may involve eligibility checks.

.. autosummary::
   :toctree: _autosummary_midas
   :recursive:

   eth_defi.midas.vault
   eth_defi.midas.historical
