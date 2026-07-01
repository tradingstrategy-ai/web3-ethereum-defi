Mellow vaults
=============

`Mellow <https://mellow.finance/>`__ provides vault infrastructure for curated
on-chain structured products and platform Earn programmes. Its current Core
Vault architecture is designed for products where the vault configuration
defines permitted assets, integrations, execution paths and risk limits.

Mellow Core Vaults are not ERC-4626 vault contracts. They use a modular
component graph with a canonical vault address, tokenised share manager, deposit
and redemption queues, oracle, fee manager, risk manager and subvaults. The
eth_defi adapter treats Mellow as a sibling EVM vault architecture and routes
factory-discovered Core Vaults through the shared scanner so Mellow vaults can
appear in the same vault metadata and price pipelines as ERC-4626 vaults.

Fees
----

Mellow fee settings live in the Core Vault FeeManager. The documented fee types
are deposit fee, redeem fee, performance fee and a time-based protocol fee. Fees
are paid in vault shares. In the shared vault model the adapter maps Mellow's
protocol fee to the management-fee field and maps performance, deposit and
redeem fees directly to their matching shared fields.

Links
-----

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/mellow>`__
- `Homepage <https://mellow.finance/>`__
- `App <https://app.mellow.finance/>`__
- `Documentation <https://docs.mellow.finance/core-vaults>`__
- `Core deployments <https://docs.mellow.finance/core-vaults/core-deployments>`__
- `GitHub <https://github.com/mellow-finance/flexible-vaults>`__
- `Twitter <https://x.com/Mellowprotocol>`__
- `Audits <https://docs.mellow.finance/security>`__
- `DefiLlama <https://defillama.com/protocol/mellow-protocol>`__
