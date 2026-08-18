---
name: categorise-vault-strategy
description: Categorise a DeFi vault's investment strategy from its address, link, name, and published strategy context. Use when adding or reviewing StrategyTag classifications for VaultBase adapters or native perpetual DEX vault exports, including their maintained tags.py mappings.
---

# Categorise vault strategy

Assign evidence-based strategy tags to a known vault. Keep the maintained
address-to-tag mapping next to its vault protocol adapter.

## Workflow

1. Identify the vault and its adapter.

   - Resolve the chain and lowercase contract address from the supplied link,
     address, or name. Do not classify a same-address deployment on a different
     chain without confirming it is the same product.
   - For an ERC-4626 adapter, locate
     `eth_defi/erc_4626/vault_protocol/{slug}/vault.py` and read its
     description, `short_description`, nearby address overlays, and protocol
     metadata. Use the vault's official documentation or announcement to
     corroborate material claims.
   - Treat ApeX, Hyperliquid, GRVT, Hibachi, and Lighter as native perpetual DEX
     integrations, not `VaultBase` adapters. Their vaults are materialised by
     `eth_defi/{slug}/vault_data_export.py` with either an address, a platform
     vault ID, or a synthetic address.

2. Select tags from `eth_defi.vault.strategy_tag.StrategyTag`.

   - Assign every supported tag that the documented strategy warrants; tags
     are additive and are not mutually exclusive.
   - Prefer the most specific tag and include a relevant parent tag where it
     conveys useful search or reporting context. For example, an RWA lending
     vault normally receives both `rwa` and `rwa_lending`.
   - Do not infer a tag merely from a token symbol, protocol name, or generic
     yield marketing. When the strategy is not documented well enough, leave
     the address out of the mapping: `get_strategy_tags()` then returns
     `None`, meaning the information is missing. Use `unknown` only when a
     researched classification explicitly establishes that the strategy is
     unknown.

3. Create or update the protocol tag mapping.

   For a `VaultBase` adapter, use
   `eth_defi/erc_4626/vault_protocol/{slug}/tags.py` with lowercase
   `HexAddress` keys:

   ```python
   """Maintained strategy classifications for {Protocol} vaults."""

   from eth_typing import HexAddress

   from eth_defi.vault.strategy_tag import StrategyTag

   STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
       #: Vault: Example RWA lending vault.
       #: Added: 2026-08-17.
       #: Decision material: The issuer describes this product as lending
       #: against real-world assets.
       #: Sources:
       #: - https://issuer.example/vaults/example
       HexAddress("0x..."): {StrategyTag.rwa, StrategyTag.rwa_lending},
   }
   ```

   Keep addresses lowercase and scope every entry to an individual vault. Put
   a Sphinx line-comment block immediately above every address-specific entry.
   It must include the vault name, the UTC date the tagging entry was added,
   and the decision material used to assign the tags. Include every page URL
   consulted during the decision directly in the comment; cite repository
   files by their path. Do not add an entry where the source material does not
   support the selected tags. Do not use a bundled or historical research
   snapshot as the sole evidence for a current address classification; verify
   the current metadata row or a current official protocol source before adding
   the entry.

   Aave, Euler, and Morpho adapters automatically add the generic
   `StrategyTag.lending` tag to every vault. Their `STRATEGY_TAGS` mappings are
   additive and should contain only additional address-specific classifications.

   For a native perpetual DEX, use `eth_defi/{slug}/tags.py` with string keys,
   because ApeX, GRVT vault IDs and Hibachi/Lighter synthetic addresses are not
   EVM addresses. Its resolver normally combines the maintained address-specific
   tags with `StrategyTag.perpetual_futures`. If the source description
   explicitly identifies a product as a non-perpetual RWA or fund product,
   omit that default for the address by adding its lowercase identifier to the
   protocol's `NON_PERPETUAL_VAULTS` set and documenting the exception in the
   entry comments; do not infer perpetual-futures exposure from the platform
   name alone.

4. Wire the classification into the correct data path.

   For a `VaultBase` adapter, ensure the corresponding vault class reads the
   mapping:

   ```python
   from eth_defi.erc_4626.vault_protocol.{slug}.tags import STRATEGY_TAGS
   from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

   def get_strategy_tags(self) -> set[StrategyTag] | None:
       """Return maintained strategy tags for this vault."""
       return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)
   ```

   Return a copy so callers cannot mutate the maintained mapping. Preserve
   `None` for an address with no mapping entry.

   For a native perpetual DEX, import the resolver in its
   `vault_data_export.py` module and save its return value in the synthetic
   `VaultRow` as `_strategy_tags`. Do not add a fictional vault class or use
   `HexAddress` for a non-EVM identifier.

5. Add or update focused no-RPC coverage. For a `VaultBase` adapter, check the
   known address returns the exact tag set and an unmapped address returns
   `None`. For a native perpetual DEX, check both the default
   `perpetual_futures` tag and an address-specific tag added by its mapping.
   Format modified Python files and run the focused test.

## Chat output

When reporting a completed categorisation, output one entry per vault with:

- Vault name
- Vault address
- Tags added
