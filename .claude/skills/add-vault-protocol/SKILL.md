---
name: add-vault-protocol
description: Add support for a new ERC-4626 vault protocol. Use when the user wants to integrate a new vault protocol like IPOR, Plutus, Morpho, etc. Requires vault smart contract address, protocol name, and protocol slug as inputs.
---

# Add vault protocol

This skill guides you through adding support for a new ERC-4626 vault protocol to the eth_defi library.

## Required inputs

Before starting, gather the following information from the user:

1. **Vault smart contract address** - The address of an example vault contract on a blockchain
2. **Protocol name** - Human-readable name (e.g., "Plutus", "IPOR", "Morpho")
3. **Protocol slug** - Snake_case identifier for code (e.g., "plutus", "ipor", "morpho")
4. **Chain** - Which blockchain (Ethereum, Arbitrum, Base, etc.)
5. **Block explorer URL** - To fetch the ABI (e.g., Etherscan, Arbiscan, Basescan)
6. **Single vault protocol**: Some protocols, especially ones issuing out their own stablecoin, are know to have only a single vault for the stablecoin staking. Example protocols are like like Spark, Ethena, Cap. In this case use `HARDCODED_PROTOCOLS` classification later, as there is no point to create complex vault smart contract detection patterns if the protocol does not need it.
7. **Risk level**: Optional. If not given, set to `None`

## Completion requirements

A new vault protocol integration is not complete unless it includes:

- Protocol detection or hardcoded address classification
- Vault class and `create_vault_instance()` wiring
- Deposit manager and public deposit/redemption flow capability, backed by a guarded fork transaction test
- Risk and fee matrix entries
- Protocol metadata YAML under `eth_defi/data/vaults/metadata/`
- Original and post-processed protocol logos
- Vault documentation and API documentation entries
- Focused tests for the new protocol

## Step-by-step implementation

### Step 1: Download and store the ABI

1. Fetch the vault smart contract ABI from the blockchain explorer
2. **Important**: If the contract is a proxy, you need the implementation ABI, not the proxy ABI
   - Check if the contract has a `implementation()` function or similar
   - Use the explorer's "Read as Proxy" feature to get the implementation address
   - Download the implementation contract's ABI
3. Create the ABI directory and file:
   ```
   eth_defi/abi/{protocol_slug}/
   eth_defi/abi/{protocol_slug}/{ContractName}.json
   ```
4. Use `eth_defi/abi/lagoon/` as a reference for structure

### Step 2: Create the vault class

Create `eth_defi/erc_4626/vault_protocol/{protocol_slug}/vault.py` following the patterns in:

- `eth_defi/erc_4626/vault_protocol/plutus/vault.py` - Simple vault with hardcoded fees
- `eth_defi/erc_4626/vault_protocol/ipor/vault.py` - Complex vault with custom fee reading and multicall support

The vault class should:

```python
"""Module docstring describing the protocol."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class {ProtocolName}Vault(ERC4626Vault):
    """Protocol vault support.

    One line description of the protocol.

    - Add links to protocol documentation
    - Add links to example contracts on block explorers
    - Add links to github
    - If fee information is documented or available as Github source code, link into it
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        return None

    def get_link(self, referral: str | None = None) -> str:
        return f"https://protocol-url.com/vault/{self.vault_address}"
```

For `get_link()` check the protocol website to find a direct link URL pattern to its vault. Usual formats:

- By address
- By chain id and address - for example Ethereum chain id is 1
- By chain name and address - use `get_chain_name(chain_id).lower()` or simiar
- Can be special for protocols just with one vault, it can be a single link with no pattern
- If you fail to figure this out, just link to the protocol homepage

### Step 3: Add protocol feature enum

Edit `eth_defi/erc_4626/core.py` and add a new enum member to `ERC4626Feature`:

```python
#: {Protocol Name}
#:
#: {Protocol URL}
{protocol_slug}_like = "{protocol_slug}_like"
```

Also update `get_vault_protocol_name()` to return the protocol name:

```python
elif ERC4626Feature.{protocol_slug}_like in features:
    return "{Protocol Name}"
```

### Step 4: Add protocol identification probes

Edit `eth_defi/erc_4626/classification.py`:

1. In `create_probe_calls()`, add a probe call that uniquely identifies this protocol:
   - Analyse the ABI and the vault implementation smart contract source code to find a function unique to this protocol
   - Look for functions like `getProtocolSpecificData()`, custom role constants, etc. and compare them to what is already implemented in `create_probe_calls()`
   - Make sure this call does not conflict with already configured protocols
   - You can also use blockchain explorer's Contract > Read contract or Contract Read contract as proxy to figure out good ABI calls to detect this particular type of smart contracts
   - If the protocol is a single vault protocol, use `HARDCODED_PROTOCOLS` in classification.py instead

If you cannot find a such accessor function in the ABI or vault smart contract source, interrupt the skill and ask for user intervention.

```python
# {Protocol Name}
# {Block explorer link}
{protocol_slug}_call = EncodedCall.from_keccak_signature(
    address=address,
    signature=Web3.keccak(text="uniqueFunction()")[0:4],
    function="uniqueFunction",
    data=b"",
    extra_data=None,
)
yield {protocol_slug}_call
```

2. In `identify_vault_features()`, add detection logic:

```python
if calls["uniqueFunction"].success:
    features.add(ERC4626Feature.{protocol_slug}_like)
```

### Step 5: Update create_vault_instance()

In `eth_defi/erc_4626/classification.py`, add a case for the new protocol in `create_vault_instance()`:

```python
elif ERC4626Feature.{protocol_slug}_like in features:
    from eth_defi.erc_4626.vault_protocol.{protocol_slug}.vault import {ProtocolName}Vault

    return {ProtocolName}Vault(web3, spec, token_cache=token_cache, features=features)
```

### Step 6: Certify deposit and redemption flows

Every vault adapter must explicitly declare whether it supports deposits and
redemptions. Do not treat ERC-4626 interface detection alone as permission to
advertise deposit-manager support: public support requires a complete tested
lifecycle.

1. Determine the flow from the vault contract and protocol documentation:
   - **Synchronous**: the user approves the denomination token and directly calls
     ERC-4626 `deposit()` / `mint()` and `withdraw()` / `redeem()`.
   - **Asynchronous**: the vault uses a request, queue, epoch, settlement, claim,
     cooldown, or redemption-delay flow. Implement a protocol-specific deposit
     manager instead of certifying the generic manager.
   - **Unsupported**: do not expose a partial manager. Leave the public capability
     as `None` until both directions are implemented and tested.
2. For a standard synchronous ERC-4626 adapter, certify the inherited
   `ERC4626DepositManager` by adding the exact fully-qualified class name to
   `CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES` in
   `eth_defi/erc_4626/vault.py`:

   ```python
   "eth_defi.erc_4626.vault_protocol.{protocol_slug}.vault.{ProtocolName}Vault",
   ```

   The inherited `get_deposit_manager()` then returns `ERC4626DepositManager`, and
   `get_deposit_manager_capability()` exports the public fields:

   ```python
   {
       "can_deposit": True,
       "can_redeem": True,
       "deposit_flow": "synchronous",
       "redemption_flow": "synchronous",
   }
   ```

3. Add a guarded Anvil fork test that uses an unlocked token holder to transfer the
   denomination token to an Anvil account, approves the vault, deposits through
   `vault.get_deposit_manager()`, and redeems the exact minted share balance. Assert
   that the manager is `ERC4626DepositManager`, both flow methods are synchronous,
   the public capability fields match the schema above, and the final share balance
   is zero.
4. Add or update a no-RPC unit test for the exact-class allowlist. This prevents a
   future refactor from silently removing the advertised capability when RPC-backed
   tests are skipped.

Reference implementations:

- Generic manager and capability implementation:
  `eth_defi/erc_4626/deposit_redeem.py` and `eth_defi/erc_4626/vault.py`
- Full synchronous approval/deposit/redeem fork flow:
  `tests/erc_4626/test_4626_deposit_redeem.py`
- Protocol-specific certified generic manager example:
  `tests/erc_4626/vault_protocol/test_kiln.py`
- No-RPC allowlist capability test:
  `tests/erc_4626/test_deposit_probe.py`
- Custom or non-generic manager examples:
  `eth_defi/erc_4626/vault_protocol/gains/` and
  `eth_defi/erc_4626/vault_protocol/upshift/vault.py`

### Step 7: Update risk and fee information

Update `eth_defi/vault/risk.py` with the protocol stub.

Set the initial risk level for the protocol in `VAULT_PROTOCOL_RISK_MATRIX`.
USe `None` if not given and this will be later updated by human judgement.

Update `eth_defi/vault/fee.py` with the protocol stub.

Set `VAULT_PROTOCOL_FEE_MATRIX` to `None` for newly added protocol.

Match `get_vault_protocol_name()` for the protocol name spelling.

### Step 8: Add protocol metadata YAML

Create `eth_defi/data/vaults/metadata/{protocol-slug}.yaml`.

- Use the slug with dashes for metadata filenames if the protocol slug contains multiple words
- Include name, slug, short description, long description, fee description, links, and example smart contracts
- Use `eth_defi/data/vaults/README.md` as the schema reference
- Include `trading_strategy` and `integration_documentation` links even if the Trading Strategy listing is not live yet

Validate that the metadata can be parsed:

```shell
poetry run python - <<'PY'
from pathlib import Path
from eth_defi.vault.protocol_metadata import build_metadata_json
print(build_metadata_json(Path("eth_defi/data/vaults/metadata/{protocol-slug}.yaml"), "https://example.invalid")["name"])
PY
```

### Step 9: Extract and post-process protocol logos

Protocol logos are required for vault protocol metadata and frontend listings.
Do not skip this step unless no official or defensible logo source can be found
after following the logo extraction workflow; if skipped, document why in the
final response and in the logo README.

1. Use the repo-local `extract-vault-protocol-logo` skill.
   - Read `.claude/skills/extract-vault-protocol-logo/SKILL.md`
   - Use the homepage from `eth_defi/data/vaults/metadata/{protocol-slug}.yaml`
   - Save original logos under `eth_defi/data/vaults/original_logos/{protocol-slug}/`
   - Add a `README.md` in the original logo folder documenting sources and choices
2. Use the repo-local `post-process-logo` skill.
   - Read `.claude/skills/post-process-logo/SKILL.md`
   - Create post-processed 256x256 PNG logos under `eth_defi/data/vaults/formatted_logos/{protocol-slug}/`
   - Produce at least `light.png` and `dark.png` when the source supports both; otherwise produce the best available variant and explain the limitation
3. Verify the metadata exporter sees the logos:

```shell
poetry run python - <<'PY'
from pathlib import Path
from eth_defi.vault.protocol_metadata import build_metadata_json
metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/{protocol-slug}.yaml"), "https://example.invalid")
print(metadata["logos"])
PY
```

### Step 10: Create test file

First the latest block number for the selected chain using `get-block-number` skill.

Create `tests/erc_4626/vault_protocol/test_{protocol_slug}.py` following the pattern in `tests/erc_4626/vault_protocol/test_plutus.py` and. `tests/erc_4626/vault_protocol/test_goat.py`:

```python
"""Test {Protocol Name} vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.{protocol_slug}.vault import {ProtocolName}Vault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.erc_4626.core import ERC4626Feature

JSON_RPC_{CHAIN} = os.environ.get("JSON_RPC_{CHAIN}")

pytestmark = pytest.mark.skipif(
    JSON_RPC_{CHAIN} is None,
    reason="JSON_RPC_{CHAIN} needed to run these tests"
)


@pytest.fixture(scope="module")
def anvil_{chain}_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_{CHAIN}, fork_block_number={block_number})
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_{chain}_fork):
    web3 = create_multi_provider_web3(anvil_{chain}_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_{protocol_slug}(
    web3: Web3,
    tmp_path: Path,
):
    """Read {Protocol Name} vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="{vault_address}",
    )

    assert isinstance(vault, {ProtocolName}Vault)
    assert vault.get_protocol_name() == "{Protocol Name}"

    # Add assertation about vault feature flags here, like:
    # assert vault.features == {ERC4626Feature.goat_like}

    # Add assertions for fee data we know
    # assert vault.get_management_fee("latest") == ...
    # assert vault.get_performance_fee("latest") == ...

    # Add assertion for the protcol risk level
    # assert vault.get_risk() == VaultTechnicalRisk.unknown

```

- Update the test file for a correct blockchain
- Use the blockchain explorer to get the latest block number using given JSON-RPC URL and Python's Web3.py `web3.eth.block_number` call
- When you run the test and if the user does not have JSON-RPC configured for this chain, interrupt the skill and tell user to update his test environment variables

After adding it, run the test module and fix any issues.

### Step 11: Add module **init**.py

Create `eth_defi/erc_4626/vault_protocol/{protocol_slug}/__init__.py`:

```python
"""{Protocol Name} protocol integration."""
```

## Step 12: Update documentation

- Add protocol to `docs/source/vaults`
- Add protocol to `docs/source/vaults/index.rst`
- Add API stub under `docs/source/api/{protocol_slug}/index.rst`
- Cross-reference the API stub in `docs/source/api/index.rst`
- Include protocol name
- Search web for a short description, two paragraph
- Add a link to the protocol home page and documentation
- Search Github/web for a Github repo link of the smart contracts
- Search protocol homepage for Twitter link and add it to the documentation
- Search protocol homepage and documentation audits page
- Search protocol homepage and documentation fees page
- Check if DefiLLama has a page for this protocol
- Add the new modules to the protocol index page TOC
- Add the protocol to the master index in `docs/source/vaults/index.rst`

Examples include

- `docs/source/vaults/plutus/index.rst`, `docs/source/vaults/truefi/index.rst`, `docs/source/api/vaults/index.rst`,

## Step 13: Run all vault protocol detection tests

Check that all ERC-4626 tests pass after adding a new vault protocol by running all testse in `tests/erc_4626/vault_protocol` folder.

Run all vault testes:

```
source .local-test.env && poetry run pytest -n auto -k vault_protocol
```

Fix any issues if found.

## Step 14: Format the codebase

Format the newly added files with `poetry run ruff format`.

## Step 15: Add feed protocol YAML entry

Create a feed YAML file at `eth_defi/data/feeds/protocols/{protocol-slug}.yaml` so the protocol's social media posts are collected by the feed scanner. For full schema documentation and collection behaviour details, see `eth_defi/feed/README-feed.md`.

Use the protocol slug with dashes (not underscores). E.g. `lagoon-finance`, `ipor-fusion`, `goat-protocol`.

The file should follow this format:

```yaml
feeder-id: {protocol-slug}
name: {Protocol Name}
role: protocol
website: {homepage URL}
twitter: {twitter handle without @}
linkedin: {linkedin company slug}
rss: {RSS or Atom feed URL}
```

To fill the fields:

1. **feeder-id**: Same slug as the filename (without `.yaml`)
2. **name**: Human-readable protocol name, matching `get_vault_protocol_name()` output
3. **role**: Always `protocol` for vault protocols
4. **website**: Protocol homepage URL (already gathered in earlier steps)
5. **twitter**: Twitter/X handle without `@` — find on the protocol homepage. If no Twitter found, omit the field.
6. **linkedin**: LinkedIn company page slug (the part after `linkedin.com/company/`). Find via web search for `"{protocol name}" site:linkedin.com/company`. If not found, omit the field.
7. **rss**: Look for an RSS/Atom feed URL. Common patterns:
   - Medium blogs: `https://medium.com/feed/@{handle}` or `https://medium.com/feed/{publication}`
   - Substack: `https://{name}.substack.com/feed`
   - Blog pages: Check for `<link rel="alternate" type="application/rss+xml">` in page source
   - If no RSS feed exists, add a comment: `# rss: not found — {reason}`

Example (simple):

```yaml
feeder-id: plutus
name: Plutus
role: protocol
website: https://plutus.fi/
twitter: plutus_fi_x
rss: https://medium.com/feed/@plutus.fi
```

Example (no RSS):

```yaml
feeder-id: lagoon-finance
name: Lagoon Finance
role: protocol
website: https://lagoon.finance/
twitter: lagoon_finance
linkedin: lagoon-finance
# rss: not found — blog is at lagoon.finance/blog but has no RSS feed
```

## Step 16: Verification checklist

After implementation, verify:

- [ ] ABI file is correctly placed in `eth_defi/abi/{protocol_slug}/`, or the protocol is intentionally using `HARDCODED_PROTOCOLS`
- [ ] Vault class inherits from `ERC4626Vault`
- [ ] `ERC4626Feature` enum has the new protocol
- [ ] `get_vault_protocol_name()` returns the correct name
- [ ] `create_probe_calls()` has a unique probe for the protocol, or the protocol is intentionally using `HARDCODED_PROTOCOLS`
- [ ] `identify_vault_features()` or `HARDCODED_PROTOCOLS` correctly identifies the protocol
- [ ] `create_vault_instance()` creates the correct vault class
- [ ] Test file runs successfully with: `source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_{protocol_slug}.py -v`
- [ ] Metadata YAML parses successfully
- [ ] Original logos are saved under `eth_defi/data/vaults/original_logos/{protocol-slug}/`
- [ ] Post-processed logos are saved under `eth_defi/data/vaults/formatted_logos/{protocol-slug}/`
- [ ] Metadata exporter sees the post-processed logos
- [ ] API documents have been updated
- [ ] Check that homepage link in the API documentation takes to the correct homepage
- [ ] Check that Twitter link in the API documentation works and takes to the same Twitter account as listed on the protocol homepage
- [ ] Feed YAML file exists at `eth_defi/data/feeds/protocols/{protocol-slug}.yaml`

If there are problems with the checklist, ask for human assistance.

## Step 17: Changelog

- Update changelog line in `CHANGELOG.md` and add a note of added new protocol

## Step 18: Pull request (optional)

After everything is done, open a pull request, but only if the user asks you to.

```shell
gh pr create \
  --title "Add new vault protocol: {protocol name}" \
  --body  $'Protocol: {protocok name}\nHomepage: {homepage link}\nGithub: {github link}\nDocs: {docs link}\nExample contract: {blockchain explorer link}" \
  --base master
```

## Finding unique protocol identifiers

To find a function that uniquely identifies the protocol:

1. Read the ABI and look for:

   - Protocol-specific role constants (e.g., `SAY_TRADER_ROLE()` for Plutus)
   - Custom getter functions (e.g., `getPerformanceFeeData()` for IPOR)
   - Protocol registry calls (e.g., `MORPHO()` for Morpho)
   - Unique configuration functions

2. Verify the function is truly unique by checking it doesn't exist in other protocols

3. Some protocols may need name-based detection if no unique function exists:
   ```python
   name = calls["name"].result
   if name:
       name = name.decode("utf-8", errors="ignore")
       if "ProtocolName" in name:
           features.add(ERC4626Feature.{protocol_slug}_like)
   ```

## Example ABI structure

The ABI JSON file should contain the contract's ABI array. Example:

```json
{
  "abi": [
    {
      "inputs": [],
      "name": "totalAssets",
      "outputs": [{ "type": "uint256" }],
      "stateMutability": "view",
      "type": "function"
    }
  ]
}
```

Or just the array directly:

```json
[
  {
    "inputs": [],
    "name": "totalAssets",
    "outputs": [{ "type": "uint256" }],
    "stateMutability": "view",
    "type": "function"
  }
]
```
