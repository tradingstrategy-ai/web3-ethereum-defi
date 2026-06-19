Aave API
--------

`Aave <https://aave.com/>`__ v4 ERC-4626 vault integration.

Aave is one of the largest and most established decentralised lending protocols. Suppliers earn
interest by providing liquidity, while borrowers take over-collateralised loans against it.

Aave v4 (live on Ethereum mainnet since 30 March 2026) reorganises liquidity around a central
Liquidity Hub and modular Spokes. Each supplyable asset is exposed through a Tokenization Spoke —
an ERC-4626 compliant vault that tokenises a Hub deposit into fungible ``wa{Hub}{Asset}`` shares
(e.g. ``waCoreUSDC``, ``waPrimeWETH``). v4 debuts with three Liquidity Hubs: Core, Prime and Plus.
Yield accrues in the Hub-derived share price; there is no explicit spoke-level fee.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/aave>`__
- `Homepage <https://aave.com/>`__
- `App <https://app.aave.com/markets/>`__
- `Documentation <https://aave.com/docs/aave-v4>`__
- `GitHub <https://github.com/aave/aave-v4>`__
- `Audits <https://github.com/aave/aave-v4/tree/main/audits>`__
- `DefiLlama <https://defillama.com/protocol/aave>`__
- `Twitter <https://x.com/aave>`__

.. autosummary::
   :toctree: _autosummary_aave
   :recursive:

   eth_defi.erc_4626.vault_protocol.aave.vault
