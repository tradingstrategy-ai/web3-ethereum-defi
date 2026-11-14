"""Reviewed Veranta deployment identities and source provenance."""

from eth_defi.types import Percent

#: Unified avUSDC vault; retired tranche addresses are intentionally excluded.
VERANTA_VAULT_ADDRESS = "0x944766f715b51967e56afde5f0aa76ceacc9e7f9"
#: Base mainnet.
VERANTA_CHAIN_ID = 8453
#: Reviewed implementations: canonical Base fork and current deployment.
VERANTA_PUBLIC_IMPLEMENTATIONS = frozenset({"0xc3769e440e3515ba374372837f76aafdc4fba26d", "0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89"})
#: Reviewed reward-distributing manager proxy; other wiring needs a new review.
VERANTA_VAULT_MANAGER_ADDRESS = "0xe9fb8c70af1b99f2baaa07aa926fcf3d237348dd"
#: Source-reviewed (vault implementation, manager implementation) pairs with
#: no separate direct LP management or performance charges. Upstream trading
#: revenue allocation is already reflected in pool income.
#: See ``eth_defi/abi/veranta/README.md`` for source provenance.
VERANTA_ZERO_LP_FEE_IMPLEMENTATIONS = frozenset(
    {
        ("0xc3769e440e3515ba374372837f76aafdc4fba26d", "0xf34e3a38280a7e600cb4aa6d8c2a160d8e16127f"),
        ("0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89", "0xf0207407beb82902186321e3dd84a7050add275a"),
    }
)
#: ERC-1967 implementation storage slot.
IMPLEMENTATION_SLOT = 0x360894A13BA1A3210667C828492DB98DCA3E2076CC3735A920A3CA505D382BBC
#: Veranta fee rates use five decimal places, rather than ordinary basis points.
VERANTA_FEE_DENOMINATOR = 100_000
#: Fixed published withdrawal rate, requested for fee displays and net-return
#: estimates. Actual execution fees remain dynamic and use contract previews.
VERANTA_REPORTED_WITHDRAWAL_FEE: Percent = 0.005
