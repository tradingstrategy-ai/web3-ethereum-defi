Altura API
----------

`Altura <https://altura.trade/>`__ integration.

Altura is a multi-strategy yield protocol built on HyperEVM (Hyperliquid) that
democratises access to institutional-grade trading strategies. Users deposit USDT0
into a single vault, and Altura allocates capital across diversified yield sources
including arbitrage, funding rate capture, staking yield, and liquidity provision.

The protocol targets a base 20% APY through a diversified portfolio of strategies:

- **Arbitrage & Funding (50%)**: Market-neutral trading and funding rate capture
- **Staking & Restaking (30%)**: Protocol-level staking rewards
- **Structured Liquidity Provision (20%)**: LP strategies on HyperEVM

Key features:

- Non-custodial yield protocol with full on-chain verifiability
- NAV oracle-backed pricing for accurate share valuation
- Epoch-based withdrawal system with 6-hour minimum hold period
- Role-based access control (Admin, Operator, Guardian)
- Pausable functionality for emergency situations

Fee structure:

Altura charges a minimal exit fee on instant withdrawals only:

- **Instant withdrawal fee**: 0.01% (1 basis point) when liquidity is available
- **Epoch withdrawal fee**: 0% for queued withdrawals
- **Management/Performance fees**: None (yield accrues via Price-Per-Share mechanism)

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/altura>`__
- `Homepage <https://altura.trade/>`__
- `App <https://app.altura.trade/>`__
- `Documentation <https://docs.altura.trade/>`__
- `Twitter <https://twitter.com/alturax>`__
- `GitHub <https://github.com/AlturaTrade>`__
- `Vault audit <https://github.com/AlturaTrade/docs/blob/V2/VaultAudit.pdf>`__
- `Predeposit audit <https://github.com/AlturaTrade/docs/blob/V2/PredepositAudit.pdf>`__
- `Contract on HyperEVMScan <https://hyperevmscan.io/address/0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29>`__


.. autosummary::
   :toctree: _autosummary_altura
   :recursive:

   eth_defi.erc_4626.vault_protocol.altura.vault
