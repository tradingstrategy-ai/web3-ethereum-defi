"""Refresh links, notes, tags and curator attribution for reviewed Gains vaults.

Purpose: Refresh links, notes, tags and curator attribution for reviewed Gains vaults.
Scope: 24 frozen deployments; Nova is explicitly excluded.
Branch: improve-gtrade-vault-notes-curator
PR title: feat: separate Nova Markets vaults and refresh gTrade metadata
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1688
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.gtrade import transform_gtrade

#: Reviewed deployment inventory frozen on 2026-10-28.
TARGETS = (
    VaultSpec(56, "0x1e98b6143a4eaf78ab63de8ea8186eec3dbe5edc"),
    VaultSpec(56, "0x28e1afcd2d91a7f0ea49e81192599fbe1e700169"),
    VaultSpec(56, "0xd796a9e7e30bfc1b1a9380f501430f681c31eb78"),
    VaultSpec(56, "0xfb34af2138280e13b0759fd322fe63fccc7508a6"),
    VaultSpec(137, "0x1544e1ff1a6f6bdbfb901622c12bb352a43464fb"),
    VaultSpec(137, "0x29019fe2e72e8d4d2118e8d0318bef389ffe2c81"),
    VaultSpec(137, "0x91993f2101cc758d0deb7279d41e880f7defe827"),
    VaultSpec(999, "0x31297b564fb8ec52a7d84cc2dee437e0992ef2b8"),
    VaultSpec(4326, "0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d"),
    VaultSpec(8453, "0xad20523a7dc37babc1cc74897e4977232b3d02e5"),
    VaultSpec(8453, "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6"),
    VaultSpec(8453, "0xd78bd3aef2e8aa7820fea8ffb33eddc4f13fa933"),
    VaultSpec(42161, "0x4beef1113f968326905224d2ca272f3032a9a9f4"),
    VaultSpec(42161, "0x5977a9682d7af81d347cfc338c61692163a2784c"),
    VaultSpec(42161, "0x992eb7040b66b13abea94e2621d4e61d5ce608bd"),
    VaultSpec(42161, "0x9b9469a68b3d94446c4c466bf8015bdaef971cf4"),
    VaultSpec(42161, "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7"),
    VaultSpec(42161, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"),
    VaultSpec(42161, "0xd85e038593d7a098614721eae955ec2022b9b91b"),
    VaultSpec(42161, "0xf40808f50b8d858f3ac6d10c441bb61da4564d53"),
    VaultSpec(42161, "0xfc7c19509e96415f32d80f47a08cc9ff84e7580d"),
    VaultSpec(42161, "0xfe3e29b3328026003a15bf0846846b03af86b537"),
    VaultSpec(80094, "0x6a6e4ad4a5ca14b940cd6949b1a90f947ae21c19"),
    VaultSpec(80094, "0xdd560bc2c98bc3fa39fcafe256249707f9b83b3c"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    result = transform_gtrade(context.metadata(), targets=TARGETS)
    context.report(inspected_rows=result.inspected_rows, missing_count=len(result.missing), missing=[spec.as_string_id() for spec in result.missing])
    context.commit_metadata()
