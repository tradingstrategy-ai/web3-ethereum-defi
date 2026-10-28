"""Refresh short and full descriptions for reviewed AlphaPing vaults.

Purpose: Refresh short and full descriptions for reviewed AlphaPing vaults.
Scope: 36 frozen deployments; only _description and _short_description.
Branch: explain-alpha-usdc-forex-v2-vault-descriptions
PR title: feat: add AlphaPing vault descriptions and PR cleanup skill
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1686
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.alphaping import migrate_alphaping_descriptions

#: Reviewed deployment inventory frozen on 2026-10-28.
TARGETS = (
    VaultSpec(1, "0x0bf0164d17469241b6e086da4016dcc54feaa334"),
    VaultSpec(1, "0x153bd1abe60104bd46aa05a27fa12d1346d64a57"),
    VaultSpec(1, "0x29d322dd088e9b9d1416f43188954f08748fafbb"),
    VaultSpec(1, "0x35cbe8542e70fa2f7f9cdf129f19e593f4b4f560"),
    VaultSpec(1, "0x3c124fb80e6eedd88d108c2f04013ae5cd571abc"),
    VaultSpec(1, "0x47fe8ab9ee47dd65c24df52324181790b9f47efc"),
    VaultSpec(1, "0x52317a47585a6acdfbd7a29b494c3e2baae96abc"),
    VaultSpec(1, "0x610f5b68bd1eed68af649a3fd3dc2caa1ee4ae7e"),
    VaultSpec(1, "0x6619f92861c760ad11ba0d56e8ed63a33ecce22b"),
    VaultSpec(1, "0x6701957aaa39e9352a42729cc65436a4c945cb90"),
    VaultSpec(1, "0x76dedaf4ec78777716bdeba484037c9d5fbf5d11"),
    VaultSpec(1, "0x874e7fb1abb4c27a7bbeeefa39e888b63181939c"),
    VaultSpec(1, "0x89610f2125a54547a7fb2a1b7926be734a632255"),
    VaultSpec(1, "0x951a9f4a2ce19b9dea6b37e691d076a345b6c0f8"),
    VaultSpec(1, "0x9f39b13bb472126d6937bf25a39338e664eefa82"),
    VaultSpec(1, "0xa79a5fb6f619da3ed71087fb5cd33b16a5b666b5"),
    VaultSpec(1, "0xb0f05e4de970a1aaf77f8c2f823953a367504ba9"),
    VaultSpec(1, "0xb4790cfdf1df71c2e4ac2f5c43dbcb975ad447ae"),
    VaultSpec(1, "0xb530a1b5259a71187f1d69acf0488f102637a3ed"),
    VaultSpec(1, "0xbb5ad5d522684de9dd3f545fa6d6ac5bbca4710e"),
    VaultSpec(1, "0xce22b5fb17ccbc0c5d87dc2e0df47dd71e3adc0a"),
    VaultSpec(1, "0xd0634bf57921ef4068f284f5e78a3f2623e20129"),
    VaultSpec(1, "0xd8cd72a2eb7a4312404c00aed5172c0026da0c27"),
    VaultSpec(1, "0xda4d1a3187eba9ecaa137867bd4541f78d51638e"),
    VaultSpec(1, "0xeb6719c14ba01bf1d6bfb78a65f63f12fabf202c"),
    VaultSpec(1, "0xf1ca44eea3a4effcb195a970a2f1d8553f76f9a1"),
    VaultSpec(1, "0xf9b85b19f6885511165505299648e6d052e8d4c8"),
    VaultSpec(1, "0xfa7ed49eb24a6117d8a3168eee69d26b45c40c63"),
    VaultSpec(999, "0x242572d6f1af7111bca807ecdd0f74108ceaed5d"),
    VaultSpec(8453, "0x2365975ca06f99d5da5acfc70f2581b8d194f8aa"),
    VaultSpec(8453, "0x44dd77d51629987d9555c762bf09903928c70206"),
    VaultSpec(8453, "0x4a4de4757d2a04d1e12bcc21068288caad6e19a7"),
    VaultSpec(8453, "0xea47a35aaa60302b4816d920856ddc708ad181f6"),
    VaultSpec(42161, "0x0f841bd670eaeb6be0a173c06efefbad09abb0f4"),
    VaultSpec(42161, "0x23faff4f253b85834d9d37f97ea807ec46d1ec9e"),
    VaultSpec(42161, "0x4c289073eb6679393890b85eec9d604271c18c28"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    database = context.metadata()
    if not any(spec in database.rows for spec in TARGETS):
        raise ValueError("No reviewed AlphaPing rows exist")
    result = migrate_alphaping_descriptions(database, dry_run=False, targets=TARGETS)
    context.report(inspected_rows=result.inspected_rows, missing_count=len(result.missing_specs), missing=[spec.as_string_id() for spec in result.missing_specs])
    context.commit_metadata()
