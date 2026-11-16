# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Publish the reviewed D2 catalogue descriptions.

Purpose: Replace sentence fragments and explain decisions, return sources and venues.
Scope: Only _short_description and _description for the 54 reviewed chain/address
    pairs, including GlobalMacro. No new rows are created.
Branch: find-d2-vault-description-cleanup
PR title: notes: rewrite D2 vault descriptions in plain English
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1743
Continues: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1732
Preserves: All notes, flags, fees, links, unrelated metadata, leads, prices,
    reader state and timestamp caches, including descriptions outside the inventory.
Expected: At most 54 existing rows and 108 changed fields. Missing targets are
    reported. Wrong protocol attribution fails before staging any changes.

LLM-generated operational migration; changes require human review.
No network reads or price backfill are needed for this copy-only refresh.

Run after merge: inspect docker-compose.yml and its persistent mounts, update to
merged code and build the stamped image with scripts/build-container.sh. From
~/vault-scanner/web3-ethereum-defi, stop persistent writers and quiesce other
one-shot/cron writers before both preview and apply::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review all pending migrations in preview, including 045 and 046. Use the same
inspected image and mounts throughout. The runner takes the lifetime guard
before the writer lock. Compare the restarted image's GIT_VERSION_HASH.txt with
migration.json, then check descriptions after normal scanner exports/uploads.
Production has not been modified. Resolve counter collisions before merge.
"""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import D2_PROTOCOL_NAME, D2Vault
from eth_defi.vault.base import VaultSpec, normalise_vault_short_description
from eth_defi.vault_protocols.migration import MigrationContext

#: Frozen official catalogue inventory, reviewed on 2026-11-09.
TARGETS = (
    VaultSpec(1, "0x07dff4087b43c4a759f4fc69511c26d51929daf4"),  # DRV++
    VaultSpec(1, "0x09dfcf4731149582d163a763e7dd553ebc18852d"),  # ftUSDC
    VaultSpec(1, "0x1f7bae0549f9f0baf15d0d637900056bb9434c36"),  # Debasement++
    VaultSpec(1, "0x2406aacbdf8463176deb285adaa81768415b6c7e"),  # HYPE++ Ethena
    VaultSpec(10, "0x20833f2e96ab236d66d18a0064e1e8171b70e22e"),  # Kimchi Corridor
    VaultSpec(10, "0xafd240a8e564ad34663b6c42475027ed083088a2"),  # Kimchi Premium
    VaultSpec(999, "0x0f00008223b4d4c2c466b4456df1d483743cbd60"),  # HGB
    VaultSpec(999, "0x140e81f8c033c0705036e55be2f2fbce43e4595c"),  # dgnHYPE++
    VaultSpec(999, "0x195eb4d088f222c982282b5dd495e76dba4bc7d1"),  # Alpha Strategies
    VaultSpec(999, "0x208f63a7f60c319597c05fa5ec67fde41839bad6"),  # texasHedge
    VaultSpec(999, "0x3ebb11ba6a5b61c04d1a703ea10728d519945440"),  # d2HYPE
    VaultSpec(999, "0x4de611ed46aaf54a6850f97542ce7a58e7c8d0fe"),  # hKRAKEN
    VaultSpec(999, "0x6bf9345b5d6b27b5cbf2e463dc5e0b2afcedc21c"),  # dgnVengeance
    VaultSpec(999, "0x7410e69958a8ece2a51c231c8528513d4d668c7a"),  # USDT0 hRWA
    VaultSpec(999, "0x7da637df83778704464208f092dbdbf9f386df96"),  # Hype EVM++
    VaultSpec(999, "0x8ef30c5ce9a460bfae82f1f039f7c5e5427d7018"),  # hSOL
    VaultSpec(999, "0xade27c7dec9211973278876f3819aedc28cd50ca"),  # USDT0 Hyperliquid-RWA Strategies
    VaultSpec(999, "0xf44f49e6577b3934f981c6f0629d15154d2606e6"),  # hXXI BTC
    VaultSpec(1116, "0xa6c99234ebcda225ef32cefb8472363e09a51aea"),  # CoreSolvBTC Covered Call
    VaultSpec(4663, "0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8"),  # Global Macro
    VaultSpec(8453, "0x2406aacbdf8463176deb285adaa81768415b6c7e"),  # Alpha Strategies
    VaultSpec(8453, "0x6c05a7d2c24b48fc3c615d294fec2eb068548897"),  # dgnBTC v2
    VaultSpec(8453, "0xafd240a8e564ad34663b6c42475027ed083088a2"),  # Hot7
    VaultSpec(42161, "0x0178b56fea3d7b5b9f9e0cdad486522de948730f"),  # HyperBera.weETH
    VaultSpec(42161, "0x0f76de33a3679a6065d14780618b54584a3907d4"),  # dgnETH v2
    VaultSpec(42161, "0x1176c3760af6a1dbaa5bbd0cc6cda8a2ed6b785e"),  # GTxD2
    VaultSpec(42161, "0x183424d5ae5ec9fd486634bc566d0f75ad9c9109"),  # LYRA++
    VaultSpec(42161, "0x195a9e0f29f96d4ab2139ee1272380a4aa352890"),  # d2ETH
    VaultSpec(42161, "0x23f556f4df7a263ce68ee29659aa4ea632a6a5bc"),  # VERTEXMOBY++
    VaultSpec(42161, "0x27d22eb71f00495eccc89bb02c2b68e6988c6a42"),  # ETH++
    VaultSpec(42161, "0x291344fbaac4fe14632061e4c336fe3b94c52320"),  # ARB++
    VaultSpec(42161, "0x34f0fdd80a51dfd8ba42343c20f89217280d760e"),  # Dewhales v2
    VaultSpec(42161, "0x36b1939adf539a4ac94b57dbad32faecd5bcf4d0"),  # PlsDAO++ v2
    VaultSpec(42161, "0x5b49d7fae00de64779ddcd6b067c8eb046bd9a0b"),  # GEARPENDLE++
    VaultSpec(42161, "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"),  # Alpha Strategies
    VaultSpec(42161, "0x80c403807b1032d7cb19b6d612ce23f05a213d36"),  # renzoSilo++
    VaultSpec(42161, "0x907a9f69061736ad82811cccd6add9dc4a2352a9"),  # wHLP
    VaultSpec(42161, "0x999a57ae7694298126a5db2e44f778ca486b14fc"),  # xD2e
    VaultSpec(42161, "0x9aa9bd7b032e527b0820c85cee89093dc33df8bd"),  # dgnLYRA
    VaultSpec(42161, "0xab2743a3a2e06d457368e901f5f927f271fa1374"),  # GMX++
    VaultSpec(42161, "0xac75f0c46723432a2303f2a7c7769535a179ed56"),  # wHYPE
    VaultSpec(42161, "0xb0730aa7d6e880f901b5d71a971096db56895a0f"),  # iARB
    VaultSpec(42161, "0xc027ec28f76d92d4124fcbffcf6b25137a84968c"),  # HyperBera
    VaultSpec(42161, "0xd1d64daeed7504ef3eb056aa2d973bd064843a84"),  # d2USDC v2
    VaultSpec(42161, "0xeb3194d69201b04729f1a618d95519cdae24d6c0"),  # Test
    VaultSpec(42161, "0xfddd73ecd0d0d75e902a567811a70e167a262fab"),  # HyperTRUMP
    VaultSpec(80094, "0x2b8d0420996a2753ef21c25c94eae9fc0c0aed1e"),  # Kodiak++
    VaultSpec(80094, "0x36b933554782b108bb9962ac00c498acbceb706d"),  # HyperBERA.USDC
    VaultSpec(80094, "0xace42f7e3f4672607897bf1951468031f0214359"),  # hBERA
    VaultSpec(80094, "0xbe75c8a7e58c7901d2e128dc8d3b6de2481f1f79"),  # dgnBeraland
    VaultSpec(80094, "0xbf075980792f8cc89dfb74b553acf6750a7e941b"),  # dgnUpside
    VaultSpec(80094, "0xc4fee8c68293a63241b64e5a2ef07fcf89005dd3"),  # dgnOOGA
    VaultSpec(80094, "0xcd18006cc69c6d5fa4fd4eaf99910b58464fa3ae"),  # 2xBERA
    VaultSpec(80094, "0xf650ba4303ce164e1f6b215d4cbb5e212d307056"),  # HyperEuler
)


def migrate(context: MigrationContext) -> None:
    """Refresh only the two description fields for the frozen inventory.

    Validate every existing target before staging changes, then reuse the
    adapter's copy so subsequent scans agree. The runner owns backups, locks,
    preview and persistence. Missing targets are reported without creation.

    :param context: Guarded migration context and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the refresh.
    :raises ValueError: If an existing target has conflicting protocol attribution.
    """
    database = context.metadata()
    for spec in TARGETS:
        row = database.rows.get(spec)
        if row is not None and row.get("Protocol") != D2_PROTOCOL_NAME:
            raise ValueError(f"D2 description target {spec} has unexpected protocol {row.get('Protocol')!r}")

    inspected = changed_rows = updated_fields = 0
    missing_specs = []
    for spec in TARGETS:
        row = database.rows.get(spec)
        if row is None:
            missing_specs.append(spec.as_string_id())
            continue
        inspected += 1
        vault = D2Vault(None, spec, features={ERC4626Feature.d2_like})
        assert vault.description, f"Missing reviewed D2 description for {spec}"
        values = {"_short_description": normalise_vault_short_description(vault.short_description), "_description": vault.description}
        changed = {key: value for key, value in values.items() if row.get(key) != value}
        row.update(changed)
        changed_rows += bool(changed)
        updated_fields += len(changed)

    context.report(inspected_rows=inspected, changed_rows=changed_rows, updated_fields=updated_fields, missing_specs=sorted(missing_specs))
    context.commit_metadata()
