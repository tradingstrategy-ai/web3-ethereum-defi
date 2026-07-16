Frankencoin
============

`Frankencoin <https://frankencoin.com/>`__ is an over-collateralised,
oracle-free stablecoin protocol whose ZCHF token tracks the Swiss franc. The
protocol is implemented fully on-chain and makes ZCHF available across multiple
EVM networks.

The Frankencoin Savings Vaults are ERC-20 and ERC-4626 wrappers for the
Frankencoin savings module. Users deposit ZCHF and receive svZCHF shares whose
value follows savings module yield. The official `token page
<https://frankencoin.com/token/>`__ lists savings vault deployments on Ethereum,
Base and Gnosis.

Trading Strategy reports Frankencoin savings TVL from the whole savings product,
not only from the ERC-4626 wrapper account. The custom Frankencoin reader sums
ZCHF held by the underlying savings module and the svZCHF wrapper contract,
while keeping the ERC-4626 wrapper exchange rate for share-price history.

Fee model
~~~~~~~~~

Frankencoin Savings Vaults do not expose protocol-wide management, performance,
deposit, or withdrawal fees. Savings yield is reflected in the svZCHF share
price. The underlying savings contracts support an optional per-account referral
fee: when a user configures a referrer, up to 25% of earned interest can be
deducted and paid to that referrer.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/frankencoin>`__
- `Homepage <https://frankencoin.com/>`__
- `Token and savings vault page <https://frankencoin.com/token/>`__
- `Documentation <https://docs.frankencoin.com/>`__
- `GitHub <https://github.com/Frankencoin-ZCHF/Frankencoin>`__
- `Twitter <https://x.com/frankencoinzchf>`__
- `DeFiLlama <https://defillama.com/protocol/frankencoin>`__
- `Ethereum savings vault <https://etherscan.io/token/0xE5F130253fF137f9917C0107659A4c5262abf6b0>`__
- `Ethereum legacy savings vault <https://etherscan.io/token/0x637F00cAb9665cB07d91bfB9c6f3fa8faBFEF8BC>`__
- `Base savings vault <https://basescan.org/address/0xa09EBdf8A01b9ef04149319D64F83b9C01a5b585>`__
- `Gnosis savings vault <https://gnosisscan.io/token/0x6165946250dd04740ab1409217e95a4f38374fe9>`__

API
~~~

See :doc:`../../api/frankencoin/index`.
