Pear vaults
-----------

`Pear <https://www.pear.garden/>`__ offers USDC trading vaults on HyperEVM.
Vault managers trade long and short perpetual futures on HyperCore. Each
vault has a reserve for immediate withdrawals and a queue for exits that need
capital returned from HyperCore.

Discovery uses the standard ERC-4626 deposit event. The classifier checks
``factory()`` on HyperEVM against the factory observed in all four live
deployment receipts, ``0x038b9e0eef0f926682d3a9b1918611efec9cc741``. The
address in the `published contract list <https://docs.pear.garden/smart-contracts/smart-contract-addresses>`__
differed from the factory on those deployments when checked on 20 November 2026.

Pear's NAV depends on live HyperCore precompile state. Scheduled price scans
use the standard ERC-4626 reader at ``latest`` and store the execution block
and the UTC timestamp seen by that call. Each new head sampled by the scanner
adds a point to the series. Live contract metadata also uses ``latest``.

Migration 097 imports earlier observations for the four reviewed vaults from
the `public Pear API <https://pro-gateway.pearprotocol.io/vaults>`__. It takes
the last funded observation in each UTC hour before the first hour already
covered by a verified price. Each imported row retains the source timestamp
and block. The raw price file identifies API observations as ``pear_api``
and onchain head observations as ``latest``. Scheduled scans read NAV onchain.

Curator assignments and vault descriptions come from the reviewed mandates.
The adapter reads performance and withdrawal fees using two onchain getters.
The adapter supports read-only access. Withdrawals can
require queued settlement, and account admission is recorded as unknown.

- `Vault app <https://vaults.pear.garden/>`__
- `Protocol documentation <https://docs.pear.garden/vaults>`__
- `Social updates <https://x.com/pear_protocol>`__
- `Shieldify vault audit <https://github.com/shieldify-security/audits-portfolio/blob/main/reports/PearProtocol-Vault-V2-Upgrade-Security-Review.pdf>`__
- `HyperEVM historical RPC support <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/json-rpc>`__

Price units
~~~~~~~~~~~

For the four reviewed deployments, one share contains one million raw share
units. The NAV endpoint returns assets and supply in raw units and share price
scaled by 10\ :sup:`18`. On 20 November 2026 the four API prices at block
51,765,275 agreed with their reported assets divided by supply to within
0.000001 USDC per share. Onchain reads 29–30 blocks later were within
0.00005 USDC per share of those API observations. These checks compare units
across the two sources. Trading can change the valuation between blocks.
