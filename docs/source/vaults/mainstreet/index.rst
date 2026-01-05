Mainstreet Finance API
----------------------

`Mainstreet Finance <https://mainstreet.finance/>`__ integration.

Mainstreet Finance is a synthetic USD stablecoin ecosystem built on multi-asset
collateralisation on the Sonic blockchain. The protocol delivers institutional-grade
delta-neutral yield strategies through a dual-token system - msUSD (the synthetic
stablecoin) and smsUSD (the staked version that earns yield from options arbitrage
strategies).

Users deposit USDC to mint msUSD, which can then be staked into smsUSD to earn
yield. The underlying collateral is deployed into CME index box spreads and options
arbitrage strategies, with profits distributed to smsUSD holders.

Key features:

- 20% protocol fee on yields (10% to insurance fund, 10% to treasury)
- 80% of yields distributed to smsUSD holders
- Governance-configurable cooldown period for withdrawals (up to 90 days, default 7 days)
- Cross-chain functionality via LayerZero OFT standard

- `Homepage <https://mainstreet.finance/>`__
- `Documentation <https://mainstreet-finance.gitbook.io/mainstreet.finance>`__
- `GitHub <https://github.com/Mainstreet-Labs/mainstreet-core>`__
- `Twitter <https://x.com/Main_St_Finance>`__
- `smsUSD vault contract on Sonicscan <https://sonicscan.org/address/0xc7990369DA608C2F4903715E3bD22f2970536C29>`__ (the only ERC-4626 vault; msY token is a LayerZero satellite, not a vault)


.. autosummary::
   :toctree: _autosummary_mainstreet
   :recursive:

   eth_defi.erc_4626.vault_protocol.mainstreet.vault
