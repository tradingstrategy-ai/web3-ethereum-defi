# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Fill early price gaps for seven Sigma Labs vaults.

Purpose: Restore the history before the scanner's first hourly price row.
Scope: Seven exact deployments on Ethereum, Base and Arbitrum. Frozen half-open
    ranges end at the first raw hourly price rows in the 20 November 2026
    daily scanner backup. Onchain share mints establish the start for vaults
    whose supply was zero at detection.
Branch: add-curator-sigmalabs
PR title: feat: complete Sigma Labs curator and vault metadata
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1804
Preserves: Metadata, reader state, prices for other vaults and existing prices
    at or after each vault's first collected block.
Expected: Fill the priced portions of gaps ranging from about three days to
    eleven weeks. Preview and apply need archive RPCs and dense timestamp
    caches covering the ranges on all three chains.

LLM-generated operational migration. Changes require human review.
Run after merge through vault-migrate with the scanner stopped.
"""

from eth_typing import HexAddress

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PriceBackfillTarget, run_price_backfill

#: Begin at detection if shares already existed, otherwise at the first verified
#: share mint. Stop before the first existing raw hourly price block.
TARGETS = (
    # stETH Redemption Carry, from detection
    PriceBackfillTarget(1, HexAddress("0x2746f31096f23670caf4043f8b30d8d02405a257"), 25_270_898, 25_370_401),
    # sigmaETH, from detection
    PriceBackfillTarget(1, HexAddress("0x6b833881a9f083ad2ccacf8c10abc0dc151cbf32"), 25_272_209, 25_322_101),
    # Cross-chain USDC Lending, from detection
    PriceBackfillTarget(1, HexAddress("0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89"), 25_411_452, 25_459_361),
    # wstETH Dynamic Leveraged, from first share mint
    PriceBackfillTarget(1, HexAddress("0x7d2bd5eb7066957c135585f0f055443451baadad"), 26_187_602, 26_374_464),
    # Stablecoin Market Making, from first share mint
    PriceBackfillTarget(1, HexAddress("0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb"), 26_294_086, 26_317_742),
    # Fixed/Variable Rate Arbitrage USDC Lending, from first share mint
    PriceBackfillTarget(8453, HexAddress("0x79238962903c62865f319f660177e75652ee4780"), 48_339_520, 51_619_408),
    # Cross-Sectional Statistical Arbitrage, from first share mint
    PriceBackfillTarget(42161, HexAddress("0x0f30d4cc24024e51bee5e7805bc7f3e6a88ea59f"), 488_371_126, 490_350_460),
)


def migrate(context: MigrationContext) -> None:
    """Backfill the reviewed ranges through the guarded hourly price writer.

    Verify that each reviewed deployment is still in the staged metadata with
    its expected protocol before scanning and validating the staged prices.

    :param context: Guarded migration attempt and shared metadata database.
    :return: None after reporting the backfill outcome.
    :raises ValueError: If a reviewed deployment is missing or has switched protocol.
    """
    database = context.metadata()
    for target in TARGETS:
        spec = target.as_spec()
        row = database.rows.get(spec)
        protocol = "Morpho" if spec.vault_address == "0x6b833881a9f083ad2ccacf8c10abc0dc151cbf32" else "Lagoon Finance"
        if row is None or row.get("Protocol") != protocol:
            raise ValueError(f"{spec.as_string_id()} is missing or has unexpected protocol metadata")
    run_price_backfill(context, TARGETS)
