Mainstreet Finance API
----------------------

`Mainstreet Finance <https://mainstreet.finance/>`__ integration.

Mainstreet Finance (developed by Mainstreet Labs) is a synthetic USD stablecoin
ecosystem built on multi-asset collateralisation. The protocol delivers
institutional-grade delta-neutral yield strategies through a dual-token system -
msUSD (the synthetic stablecoin) and smsUSD/Staked msUSD (the staked version that
earns yield from options arbitrage strategies).

Users deposit USDC to mint msUSD, which can then be staked into smsUSD to earn
yield. The underlying collateral is deployed into CME index box spreads and options
arbitrage strategies, with profits distributed to smsUSD holders.

Key features:

- 20% protocol fee on yields (10% to insurance fund, 10% to treasury)
- 80% of yields distributed to smsUSD holders
- Governance-configurable cooldown period for withdrawals (up to 90 days, default 7 days)
- Cross-chain functionality via LayerZero OFT standard

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/mainstreet-finance>`__
- `Homepage <https://mainstreet.finance/>`__
- `Documentation <https://mainstreet-finance.gitbook.io/mainstreet.finance>`__
- `GitHub <https://github.com/Mainstreet-Labs/mainstreet-core>`__
- `Twitter <https://x.com/Main_St_Finance>`__

Vaults:

- `Staked msUSD on Ethereum <https://etherscan.io/address/0x890a5122aa1da30fec4286de7904ff808f0bd74a>`__
- `smsUSD (legacy) on Sonic <https://sonicscan.org/address/0xc7990369DA608C2F4903715E3bD22f2970536C29>`__ (msY token is a LayerZero satellite, not a vault)


.. autosummary::
   :toctree: _autosummary_mainstreet
   :recursive:

   eth_defi.erc_4626.vault_protocol.mainstreet.vault
