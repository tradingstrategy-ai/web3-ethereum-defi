Nova Markets
------------

`Nova Markets <https://nova.markets/>`__ operates the USDC Conviction Pool on
Arbitrum. Depositors provide pooled counterparty liquidity for Nova's perpetual
markets, receive trading and borrowing fees, and bear trader profit-and-loss
risk. The `pool documentation <https://docs.nova.markets/#/conviction-pool>`__
describes the strategy and Conviction Points, which are not guaranteed cash
income. A separately dated launch announcement for this pool was not verified
in the 28 October 2026 review.

The `contract disclosures <https://docs.nova.markets/#/contracts>`__ identify
``0x9ca80193712f372e2ff33a00127292884f6f8598`` on Arbitrum One (42161) as Nova's
independently administered fork of Gains Network's contracts. The classifier
uses that exact chain and address to return only ``nova_markets_like``; it does
not attribute the pool to Gains Network. The adapter reuses the compatible
Gains-family price reader and epoch deposit/redemption manager.

The separate `NLP vault <https://docs.nova.markets/#/nlp-vault>`__ uses Lagoon on
HyperEVM for order-book market making on Hyperliquid. It remains a Lagoon vault
and is not included in this hardcoded classification.

Withdrawals and integration
~~~~~~~~~~~~~~~~~~~~~~~~~~

Deposits are synchronous. Redemptions require an epoch-gated withdrawal request
and a subsequent claim. Nova currently uses three-day epochs and a wait of one
to three epochs depending on collateralisation. The adapter reads the complete
epoch duration from the initial window plus all oracle sampling intervals;
``requestsEvery`` alone is only the sampling interval. Consult the
`current pool mechanics <https://docs.nova.markets/#/pool-mechanics>`__ before
submitting a transaction.

The guarded fork test exercises approval, deposit, request, permissionless epoch
settlement and redemption through GuardV0. The pool was deployed after the
canonical July Arbitrum fork block, so its shared fork uses fixed block
518876926 with a committed warm RPC-cache seed.

Use the `official liquidity-pools app
<https://app.nova.markets/earn/liquidity-pools>`__ for the frontend. Public
protocol metadata, logo and the existing Nova Markets curator share the
``nova-markets`` identity. Technical risk and fee mode await human review.

Existing deployments
~~~~~~~~~~~~~~~~~~~~

After deploying this code, run
the numbered ``002-nova-markets-vaults.py`` migration through ``vault-migrate`` to repair the existing cached
row. It defaults to dry run, validates the exact deployment, backs up the
original metadata pickle and replaces it atomically. It preserves event counts,
first-seen boundaries, leads, historical prices and reader state. See
``eth_defi/erc_4626/vault_protocol/nova_markets/README-Nova-Markets.md`` for the
production procedure and real integration-test result.

API
~~~

See :doc:`../../api/nova_markets/index`.

See the `migration operations guide <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/.claude/docs/migration-scripts.md>`__ for the mandatory production launcher and daemon guard.
