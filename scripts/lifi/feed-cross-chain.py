"""Prefund a deployer with native gas and optional Ethereum USDC using LI.FI.

By default, distribute Arbitrum native gas to Ethereum and HyperEVM when their
balances are below USD 5. The default source spend is USD 20 per low-gas chain.
Required private inputs are ``PRIVATE_KEY`` and the selected ``JSON_RPC_*`` URLs.
Load these from a private environment; do not put keys in shell history.

``ETHEREUM_USDC_AMOUNT=5`` additionally spends 5 native source-chain USDC to
receive native Ethereum USDC at the SAME wallet address, even if gas is already
sufficient. Fees reduce the received amount; review the quoted minimum. This is
an explicit additional transfer on EVERY invocation, not a balance target.
It is independent of ``SOURCE_TOKEN``, which controls gas funding only.

After loading your private environment::

    # Quotes only: no approvals or transactions.
    DRY_RUN=true poetry run python scripts/lifi/feed-cross-chain.py
    DRY_RUN=true ETHEREUM_USDC_AMOUNT=5 poetry run python scripts/lifi/feed-cross-chain.py

    # Review quotes, then answer the execution prompt.
    ETHEREUM_USDC_AMOUNT=5 poetry run python scripts/lifi/feed-cross-chain.py

    # USDC only; do not prepare native gas top-ups.
    TARGET_CHAINS='' ETHEREUM_USDC_AMOUNT=5 poetry run python scripts/lifi/feed-cross-chain.py

Optional ``SOURCE_CHAIN`` defaults to ``arbitrum`` and ``TARGET_CHAINS`` to
``ethereum,hyperliquid``. Native source gas is required even when spending USDC.
Leave the hub subscription and unwind gas available after distribution.
This script does not prefund satellite Safes or subscribe to Lagoon.
HyperEVM USDC must be prefunded separately; the USDC option targets Ethereum.

See :ref:`lifi-feed-crosschain` and :py:mod:`eth_defi.lifi.top_up` for details.
The companion ``scripts/lagoon/lagoon-multichain-core-roundtrip.py`` requires
25 USDC on Arbitrum, 1 USDC on each satellite and native gas on all three chains.
"""

from eth_defi.lifi.top_up import perform_top_up


def main() -> None:
    """Run the shared funding workflow with environment settings.

    Dry run displays balances and quotes; live mode asks before submitting.
    """
    perform_top_up()


if __name__ == "__main__":
    main()
