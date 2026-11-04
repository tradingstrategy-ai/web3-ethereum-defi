"""Reviewed Avantis deployment identities and source provenance."""

#: Unified avUSDC vault; retired tranche addresses are intentionally excluded.
AVANTIS_VAULT_ADDRESS = "0x944766f715b51967e56afde5f0aa76ceacc9e7f9"
#: Base mainnet.
AVANTIS_CHAIN_ID = 8453
#: Reviewed implementations: canonical Base fork and current deployment.
AVANTIS_PUBLIC_IMPLEMENTATIONS = frozenset({"0xc3769e440e3515ba374372837f76aafdc4fba26d", "0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89"})
#: ERC-1967 implementation storage slot.
IMPLEMENTATION_SLOT = 0x360894A13BA1A3210667C828492DB98DCA3E2076CC3735A920A3CA505D382BBC
#: Avantis fee rates use five decimal places, rather than ordinary basis points.
AVANTIS_FEE_DENOMINATOR = 100_000
