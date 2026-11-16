"""Refresh all reviewed Accountable strategy classifications.

Purpose: Fill missing categories and complete existing product classifications.
Scope: Only _strategy_tags for the 44 chain/share-address pairs below.
Branch: update-accountable-vault-strategy-tags
PR title: fix: complete Accountable vault strategy categories
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1744
Preserves: Other metadata, unrelated rows, prices, leads and reader state.
Expected: At most 44 existing rows/fields; missing deployments are reported.

Inventory reviewed on 2026-11-09 against the live Accountable loan API,
existing Travessia/Vectis mappings and the current local metadata snapshot.
Vaults without product-specific strategy evidence remain unclassified,
including public Keyrock cBTC (off-exchange settlement alone is insufficient),
test deployments and older Noon rows whose current investments are unconfirmed.
The Polygon Tauri deployment is excluded: the snapshot detects only generic
ERC-7540/7575 features, not Accountable; its protocol repair is separate.
No new vault discovery, reader changes or price-history gaps are introduced.

LLM-generated operational migration; changes require human review.
Run after deployment with the scanner and competing writers stopped for both
preview and apply. See .claude/docs/migration-scripts.md. No network reads.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Fixed reviewed share-token inventory; do not expand an applied migration.
TARGETS = (
    VaultSpec(1, "0x0434d3b6e6c0a53c04fbb2d3d2a5cc61316cbb75"),
    VaultSpec(1, "0x05ed3da1deeaaa57c8e09986e95ac271572dcf89"),
    VaultSpec(1, "0x2b5895b716311a1eac8f93fb88c56af9ec8c1a69"),
    VaultSpec(1, "0x438d84e451decd8d4b12eadd7d186ca0bda947e7"),
    VaultSpec(1, "0x4c18e2bb9942b12b28e780acf2d9ec2dda126df9"),
    VaultSpec(1, "0x5164363574b0136d60479f0a5a33cf49871f3bb4"),
    VaultSpec(1, "0x909dadbca7955614a455d9e7447ad4adb4902c8e"),
    VaultSpec(1, "0x934a801d516ef34c519c793fe0606570e732fc6d"),
    VaultSpec(1, "0x99351baed3d8ab544ccb08af96a105910fda71e7"),
    VaultSpec(1, "0xb37cc5e891d87c5a3ef750e1bdf59c960575593c"),
    VaultSpec(1, "0xb9c317cae7dd05ecb0c0925020e529934c96f84d"),
    VaultSpec(1, "0xc606d3f0e757f7c55ced29caff0cf53099b4bbea"),
    VaultSpec(1, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35"),
    VaultSpec(1, "0xe9e54004556b9b55c59a36294688ff7081671944"),
    VaultSpec(1, "0xf84a37545e313e8177d80039158ed478c7dc9079"),
    VaultSpec(56, "0x0e51a7f836f0cf058a0f13160e21eee38ab221c0"),
    VaultSpec(56, "0x40591d6ec4e7013e39b7e10fc02caace79fc6e98"),
    VaultSpec(56, "0x8c6b6a1cb41b618bc2efe2539efa9830d8b2242b"),
    VaultSpec(56, "0xe1f7c0b46c02374ef9a77e40a6cec31ca5a15313"),
    VaultSpec(143, "0x0143c3ef3a76ed825fd5201953f65b52acefd799"),
    VaultSpec(143, "0x03ef37626bc7c7b3f6cea4bcbfeb7764b51e22fb"),
    VaultSpec(143, "0x0a4afb907672279926c73dc1f77151931c2a55cc"),
    VaultSpec(143, "0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3"),
    VaultSpec(143, "0x23b148d8f389c5821739381f1ff87bb7e1162566"),
    VaultSpec(143, "0x3a2c4aaae6776dc1c31316de559598f2f952e2cb"),
    VaultSpec(143, "0x41661c2b112263c93fdd9346c4a950476265af7a"),
    VaultSpec(143, "0x4c0d041889281531ff060290d71091401caa786d"),
    VaultSpec(143, "0x58ba69b289de313e66a13b7d1f822fc98b970554"),
    VaultSpec(143, "0x721928108fa84ae8a13545bfee3e6958626cee60"),
    VaultSpec(143, "0x7cd231120a60f500887444a9baf5e1bd753a5e59"),
    VaultSpec(143, "0x899e270df335287f687fdac8a9089059b042044f"),
    VaultSpec(143, "0x8d24b48cb736addb5b7069565f192a052f42ba61"),
    VaultSpec(143, "0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365"),
    VaultSpec(143, "0x9e98bda72ced55e562bed1dcaf2b123e95eb181e"),
    VaultSpec(143, "0xaabab7598be3c4fe58c593e73c2f5934b73b573e"),
    VaultSpec(143, "0xc6de1dc0b59682be906e5bca14e385e74ee88168"),
    VaultSpec(4114, "0x69bf9d488b0cbb9288b25f83ef815c2dc14d76de"),
    VaultSpec(4663, "0x24b84023c8e4da635be228c380c09bfe5271bf9d"),
    VaultSpec(8453, "0x37153aac7cac0c9e9c0fa2b8e7abb371cbfe70d8"),
    VaultSpec(8453, "0x67e9faebcd6429817653ff5391f26325ea0714be"),
    VaultSpec(8453, "0xb37cc5e891d87c5a3ef750e1bdf59c960575593c"),
    VaultSpec(8453, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35"),
    VaultSpec(8453, "0xee381a87bd54796f471f79cea87132f2869dc37c"),
    VaultSpec(43114, "0x9c3da70cd7fedeaef528d060ea99ace133e08fd2"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed classifications using the scanner's shared resolver.

    Validate protocol detection and all proposals before staging any changes.
    The runner owns locks, previews, backups and persistence.

    :param context: Guarded migration attempt and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the changes.
    :raises ValueError: If a target has conflicting detection or cannot resolve.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.accountable_like not in detection.features:
            raise ValueError(f"Accountable target is not an Accountable vault: {spec}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve all reviewed Accountable strategy tags"
        raise ValueError(message)
    for update in updates:
        rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(
        inspected_rows=len(rows),
        changed_rows=len(updates),
        updated_fields=len(updates),
        legacy_tag_rows=invalid,
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows],
    )
    context.commit_metadata()
