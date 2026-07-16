Maseer One
==========

`Maseer <https://maseer.finance/>`__ provides a modular framework for
tokenised real-world assets. A Maseer One deployment is an ERC-20 token with
immutable references to its purchase token, oracle, market gate, issuer,
compliance module and settlement conduit. The initial adapter supports Wren
Staked tGBP (wstGBP) on Ethereum.

Maseer One tokens are not ERC-4626 vaults. They expose a ``gem()`` purchase
token and WAD-scaled ``navprice()`` instead of ``asset()`` and
``convertToAssets()``. Minting and redemption use bespoke, compliance-gated
``mint()`` and ``redeem()`` methods. The adapter therefore inherits directly
from ``VaultBase`` and does not construct generic investment transactions.

Historical prices
-----------------

The historical reader multicalls the primary Maseer One token at each selected
block:

- ``totalSupply()`` for outstanding wstGBP shares
- ``navprice()`` for NAV/share in the ``gem()`` denomination

TVL is calculated as ``totalSupply * navprice`` in the real ERC-20
denomination. For wstGBP, the denomination is tGBP. The reader also records
whether the market gate had minting and redemption open at that block.

Fees and limitations
--------------------

``mintcost()`` and ``burncost()`` express the current entry and exit spreads
relative to NAV/share. The adapter reports these as externalised deposit and
withdrawal fees. It does not model compliance permissions, queued redemptions
or partial settlement, so generic deposits and redemptions are intentionally
reported as unavailable.

.. autosummary::
   :toctree: _autosummary_maseer_one
   :recursive:

   eth_defi.maseer_one.vault
   eth_defi.maseer_one.historical
   eth_defi.maseer_one.constants
