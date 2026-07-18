wstGBP
======

`wstGBP <https://wstgbp.com/>`__ is a non-custodial smart-contract
wrapper for tokenised sterling. The wstGBP deployment is an ERC-20 token with
immutable references to its purchase token, oracle, market gate, issuer and
settlement conduit. The adapter supports wstGBP on Ethereum.

wstGBP is not an ERC-4626 vault. It exposes a ``gem()`` purchase
token and WAD-scaled ``navprice()`` instead of ``asset()`` and
``convertToAssets()``. Minting and redemption are permissionless through
bespoke ``mint()`` and ``redeem()`` methods. The adapter therefore inherits directly
from ``VaultBase`` and does not construct generic investment transactions.

Historical prices
-----------------

The historical reader multicalls the primary wstGBP token at each selected
block:

- ``totalSupply()`` for outstanding wstGBP shares
- ``navprice()`` for NAV/share in the ``gem()`` denomination

TVL is calculated as ``totalSupply * navprice`` in the real ERC-20
denomination. For wstGBP, the denomination is tGBP. The reader also records
whether the market gate had minting and redemption open at that block.

Fees and limitations
--------------------

``mintcost()`` and ``burncost()`` express the current entry and exit spreads
relative to NAV/share. Minting is free and instant redemption has a 25 bps fee,
with no cooldown. The adapter reports these as externalised deposit and
withdrawal fees; generic transaction construction remains unsupported because
the product uses bespoke methods.

.. autosummary::
   :toctree: _autosummary_wstgbp
   :recursive:

   eth_defi.wstgbp.vault
   eth_defi.wstgbp.historical
   eth_defi.wstgbp.constants
