---
name: categorise-vault-strategy
description: Categorise a DeFi vault's investment strategy from its address, link, name, and published strategy context. Use when adding or reviewing StrategyTag classifications, including maintaining a vault protocol's tags.py mapping and its vault adapter's get_strategy_tags() implementation.
---

# Categorise vault strategy

Assign evidence-based strategy tags to a known vault. Keep the maintained
address-to-tag mapping next to its vault protocol adapter.

## Workflow

1. Identify the vault and its adapter.

   - Resolve the chain and lowercase contract address from the supplied link,
     address, or name. Do not classify a same-address deployment on a different
     chain without confirming it is the same product.
   - Locate `eth_defi/erc_4626/vault_protocol/{slug}/vault.py` and read its
     description, `short_description`, nearby address overlays, and protocol
     metadata. Use the vault's official documentation or announcement to
     corroborate material claims.

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

3. Create or update `eth_defi/erc_4626/vault_protocol/{slug}/tags.py`.

   ```python
   """Maintained strategy classifications for {Protocol} vaults."""

   from eth_typing import HexAddress

   from eth_defi.vault.strategy_tag import StrategyTag

   STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
       HexAddress("0x..."): {StrategyTag.rwa, StrategyTag.rwa_lending},
   }
   ```

   Keep addresses lowercase and scope every entry to an individual vault. Add
   an explanatory Sphinx comment when a tag needs non-obvious context.

4. Ensure the corresponding vault class reads the mapping.

   ```python
   from eth_defi.erc_4626.vault_protocol.{slug}.tags import STRATEGY_TAGS
   from eth_defi.vault.strategy_tag import StrategyTag

   def get_strategy_tags(self) -> set[StrategyTag] | None:
       """Return maintained strategy tags for this vault."""
       tags = STRATEGY_TAGS.get(HexAddress(str(self.vault_address).lower()))
       return tags.copy() if tags is not None else None
   ```

   Return a copy so callers cannot mutate the maintained mapping. Preserve
   `None` for an address with no mapping entry.

5. Add or update a focused no-RPC test that checks the known address returns
   the exact tag set and an unmapped address returns `None`. Format modified
   Python files and run the focused test.
