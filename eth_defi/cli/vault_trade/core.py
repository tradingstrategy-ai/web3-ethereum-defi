"""Standalone vault test-trade controller helpers.

The command intentionally has no strategy module.  It obtains Lagoon topology
from the deployment artefact and builds a small trading universe for one vault
attempt at a time.
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_defi.cctp.constants import CHAIN_ID_TO_CCTP_DOMAIN
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonConfig,
    LagoonDeploymentParameters,
    deploy_multichain_lagoon_vault,
)
from eth_defi.provider.anvil import fund_erc20_on_anvil, set_balance
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.vault.base import VaultSpec
from tradingstrategy.chain import ChainId
logger = logging.getLogger(__name__)


# Keep this mapping local: the vault tester may run without importing the
# trade-executor Web3 configuration module. The values mirror the chain slugs
# expected by the JSON_RPC_* CLI options.
_CHAIN_SLUG_OVERRIDES: dict[ChainId, str] = {
    ChainId.hyperliquid: "hyperliquid",
    ChainId.hyperliquid_testnet: "hyperliquid_testnet",
}


def get_chain_slug(chain_id: ChainId) -> str:
    """Get the RPC option slug for a chain."""

    return _CHAIN_SLUG_OVERRIDES.get(chain_id, chain_id.get_slug())


def get_rpc_env_var_name(chain_id: ChainId) -> str:
    """Get the ``JSON_RPC_*`` environment variable name for a chain."""

    if chain_id == ChainId.hypercore:
        chain_id = ChainId.hyperliquid
    return f"JSON_RPC_{get_chain_slug(chain_id).upper()}"


#: Anvil default account #0. Simulated deployments must not need production
#: signing material, and Web3Config-created forks expose this account.
SIMULATED_LAGOON_PRIVATE_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)


class _SimulatedWhitelistVault(ERC4626Vault):
    """Address-only ERC-4626 descriptor used while deploying the test guard.

    Do not probe the adapter here. Incomplete or unsupported vault adapters are
    a diagnostic result of the later per-vault test, not a reason to abort the
    shared simulated Lagoon deployment.
    """

    def __init__(self, web3, spec: VaultSpec, name: str):
        """Initialise an address-only vault descriptor with a display name."""

        super().__init__(web3, spec)
        self._simulated_name = name

    @property
    def name(self) -> str:
        """Return the downloaded vault name without probing the adapter."""

        return self._simulated_name

    @property
    def symbol(self) -> str:
        """Return a stable placeholder symbol for Lagoon guard deployment."""

        return "VTEST"


def filter_rpc_kwargs_for_vault_specs(
    rpc_kwargs: dict, vault_specs: list[VaultSpec]
) -> dict:
    """Keep only JSON-RPC connections needed by an explicit simulated batch."""

    selected_keys = {
        get_rpc_env_var_name(ChainId(spec.chain_id)).lower() for spec in vault_specs
    }
    return {
        key: value if key in selected_keys else None
        for key, value in rpc_kwargs.items()
    }


def _create_simulated_lagoon_chain_config(
    *,
    chain_id: ChainId,
    primary_chain_id: ChainId,
    selected_chain_ids: list[ChainId],
    web3: Any,
    vault_specs: list[VaultSpec],
    vault_universe: Any,
    account_address: str,
    safe_salt_nonce: int,
) -> LagoonConfig:
    """Build one chain's guard whitelist and optional CCTP permissions."""

    # Use address-only wrappers so guard deployment does not probe incomplete
    # adapters before their individual diagnostic attempt.
    whitelist_vaults = []
    for spec in vault_specs:
        vault = vault_universe.get_by_vault_spec(
            (spec.chain_id, spec.vault_address)
        )
        display_name = getattr(vault, "name", None) or spec.vault_address
        whitelist_vaults.append(_SimulatedWhitelistVault(web3, spec, display_name))

    # The hub may send to all supported satellites; a satellite needs only its
    # return route to the hub.  Unsupported chains still get a local Lagoon Safe
    # so their vault adapters can be tested without a cross-chain route.
    cctp_deployment = None
    if (
        chain_id.value in CHAIN_ID_TO_CCTP_DOMAIN
        and primary_chain_id.value in CHAIN_ID_TO_CCTP_DOMAIN
    ):
        if chain_id == primary_chain_id:
            destinations = [
                destination.value
                for destination in selected_chain_ids
                if destination != primary_chain_id
                and destination.value in CHAIN_ID_TO_CCTP_DOMAIN
            ]
        else:
            destinations = [primary_chain_id.value]
        if destinations:
            cctp_deployment = CCTPDeployment.create_for_chain(
                chain_id=chain_id.value,
                allowed_destinations=destinations,
            )

    return LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=USDC_NATIVE_TOKEN[chain_id.value],
            name="Vault test simulated Lagoon",
            symbol="VTS",
            managementRate=0,
            performanceRate=0,
        ),
        asset_managers=[account_address],
        safe_owners=[account_address],
        safe_threshold=1,
        safe_salt_nonce=safe_salt_nonce,
        cctp_deployment=cctp_deployment,
        any_asset=True,
        erc_4626_vaults=whitelist_vaults,
        satellite_chain=chain_id != primary_chain_id,
        between_contracts_delay_seconds=0,
    )


def _serialise_simulated_lagoon_deployment(
    *,
    result: Any,
    chain_web3: dict[str, Any],
    primary_chain_id: ChainId,
    account_address: str,
) -> tuple["LagoonDeployment", dict]:
    """Translate eth-defi deployment output into runtime and JSON forms."""

    primary_slug = get_chain_slug(primary_chain_id)
    primary = result.deployments[primary_slug]
    satellite_modules = {
        ChainId(
            chain_web3[slug].eth.chain_id
        ): deployment.trading_strategy_module.address
        for slug, deployment in result.deployments.items()
        if deployment.is_satellite
    }
    deployment = LagoonDeployment(
        primary_chain_id=primary_chain_id,
        vault_address=primary.vault.address,
        module_address=primary.trading_strategy_module.address,
        satellite_modules=satellite_modules,
    )
    artifact = {
        "multichain": len(result.deployments) > 1,
        "simulated": True,
        "deployments": {
            slug: {
                "vault_address": deployed.vault.address
                if not deployed.is_satellite
                else None,
                "safe_address": deployed.safe_address,
                "module_address": deployed.trading_strategy_module.address,
                "asset_manager": account_address,
                "asset_managers": [account_address],
                "valuation_manager": account_address,
                "is_satellite": deployed.is_satellite,
            }
            for slug, deployed in result.deployments.items()
        },
    }
    return deployment, artifact


def deploy_simulated_lagoon_multichain(
    *,
    web3config,
    vault_specs: list[VaultSpec],
    vault_universe,
    private_key: str,
    amount: Decimal,
) -> tuple["LagoonDeployment", dict]:
    """Deploy a temporary Lagoon topology on the selected Anvil forks.

    Follows the multichain Lagoon integration-test setup: one source vault on
    the first explicitly supplied chain, deterministic satellite Safes on the
    remaining chains, per-chain transaction sequences and CCTP permissions for
    every supported source/destination route.
    """

    # The first explicit id defines the hub chain.  Preserving caller order is
    # important because all cross-chain vaults bridge through this deployment.
    assert vault_specs, "A simulated deployment needs at least one vault"
    account = Account.from_key(private_key)
    primary_chain_id = ChainId(vault_specs[0].chain_id)
    selected_chain_ids = list(
        dict.fromkeys(ChainId(spec.chain_id) for spec in vault_specs)
    )

    # Fail before deploying any contracts if one requested chain could not be
    # forked.  Partial topologies are never useful for the sequential batch.
    missing_connections = [
        chain_id.get_name()
        for chain_id in selected_chain_ids
        if chain_id not in web3config.connections
    ]
    if missing_connections:
        raise RuntimeError(
            f"Missing JSON-RPC connections for simulated Lagoon deployment: {', '.join(missing_connections)}"
        )

    # The Lagoon deployer expects slug-keyed Web3 instances.  Fund the standard
    # Anvil account independently on every fork because native balances are not
    # shared across chains.
    chain_web3 = {
        get_chain_slug(chain_id): web3config.get_connection(chain_id)
        for chain_id in selected_chain_ids
    }
    for web3 in chain_web3.values():
        set_balance(web3, account.address, 100 * 10**18)

    # Build each guard whitelist only from vaults hosted by that chain.
    specs_by_chain: dict[ChainId, list[VaultSpec]] = {}
    for spec in vault_specs:
        specs_by_chain.setdefault(ChainId(spec.chain_id), []).append(spec)

    # Reproduce the production Lagoon topology: the hub gets a real ERC-4626
    # vault and every other selected chain gets a satellite Safe and module.
    safe_salt_nonce = 42
    configs = {
        get_chain_slug(chain_id): _create_simulated_lagoon_chain_config(
            chain_id=chain_id,
            primary_chain_id=primary_chain_id,
            selected_chain_ids=selected_chain_ids,
            web3=chain_web3[get_chain_slug(chain_id)],
            vault_specs=specs_by_chain[chain_id],
            vault_universe=vault_universe,
            account_address=account.address,
            safe_salt_nonce=safe_salt_nonce,
        )
        for chain_id in selected_chain_ids
    }

    # Contract deployment is atomic at the generation level: the caller tears
    # down every fork if any chain fails here.
    result = deploy_multichain_lagoon_vault(
        chain_web3=chain_web3,
        deployer=account,
        chain_configs=configs,
    )

    # Seed the hub Safe with enough USDC for every sequential attempt.  Token
    # decimals come from the live forked contract and are never hardcoded.
    primary_slug = get_chain_slug(primary_chain_id)
    primary = result.deployments[primary_slug]
    primary_web3 = chain_web3[primary_slug]
    primary_usdc = fetch_erc20_details(
        primary_web3, USDC_NATIVE_TOKEN[primary_chain_id.value]
    )
    funding_raw = primary_usdc.convert_to_raw(max(amount * Decimal(100), Decimal(100)))
    fund_erc20_on_anvil(
        primary_web3,
        USDC_NATIVE_TOKEN[primary_chain_id.value],
        primary.safe_address,
        funding_raw,
    )

    # Convert eth-defi output into the small topology used by the executor and
    # the standard deployment JSON consumed by bootstrap.
    return _serialise_simulated_lagoon_deployment(
        result=result,
        chain_web3=chain_web3,
        primary_chain_id=primary_chain_id,
        account_address=account.address,
    )


@dataclass(frozen=True)
class LagoonDeployment:
    """Runtime topology read from a Lagoon deployment artefact.

    ``primary_chain_id`` owns the ERC-4626 Lagoon vault and source Safe.
    ``satellite_modules`` maps every additional chain to its guarded execution
    module; no module discovery is performed from environment variables.
    """

    primary_chain_id: ChainId
    vault_address: str
    module_address: str
    satellite_modules: dict[ChainId, str]


def parse_vault_ids(raw_value: str | None) -> list[VaultSpec]:
    """Parse ordered comma-separated ``VAULT_ID`` input.

    Keep the command-line order; automatic modes deliberately never turn this
    list into a set.
    """

    if not raw_value or not raw_value.strip():
        raise ValueError(
            "VAULT_ID / --vault-id must contain at least one chain-address vault id"
        )

    # Collect all validation problems so a long automatic invocation reports
    # every malformed id in one error.
    result: list[VaultSpec] = []
    seen: set[str] = set()
    failures: list[str] = []
    for raw_item in raw_value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        try:
            spec = VaultSpec.parse_string(item, separator="-")
        except Exception as e:
            failures.append(f"{item!r}: {e}")
            continue
        # Use the canonical eth-defi representation for duplicate detection,
        # while preserving the original list order in ``result``.
        canonical = spec.as_string_id()
        if canonical in seen:
            failures.append(f"{item!r}: duplicate vault id")
            continue
        seen.add(canonical)
        result.append(spec)

    if failures:
        raise ValueError("Invalid VAULT_ID entries:\n - " + "\n - ".join(failures))
    if not result:
        raise ValueError("VAULT_ID / --vault-id did not contain any vault ids")
    return result


def load_lagoon_deployment(deployment_file: Path) -> LagoonDeployment:
    """Load the mandatory state-sibling deployment artefact."""

    if not deployment_file.exists():
        raise RuntimeError(
            f"Missing mandatory Lagoon deployment file: {deployment_file}"
        )

    try:
        payload = json.loads(deployment_file.read_text())
        deployments = payload["deployments"]
    except Exception as e:
        raise RuntimeError(
            f"Malformed Lagoon deployment file: {deployment_file}"
        ) from e

    # Exactly one non-satellite entry establishes the reserve chain, Safe,
    # Lagoon vault and source trading module.
    source_entries = [
        (chain_slug, entry)
        for chain_slug, entry in deployments.items()
        if not entry.get("is_satellite", False)
    ]
    if len(source_entries) != 1:
        raise RuntimeError(
            f"Deployment file must contain exactly one source deployment, got {len(source_entries)}: {deployment_file}"
        )

    source_slug, source = source_entries[0]
    vault_address = source.get("vault_address")
    module_address = source.get("module_address")
    if not vault_address or not module_address:
        raise RuntimeError(
            f"Source deployment is missing vault/module address: {deployment_file}"
        )

    primary_chain_id = ChainId.get_by_slug(source_slug)
    if primary_chain_id is None:
        raise RuntimeError(
            f"Unknown deployment chain slug {source_slug!r}: {deployment_file}"
        )

    # Satellite entries intentionally need only their module address; custody
    # Safe addresses are resolved by normal Lagoon deployment bootstrap.
    satellite_modules: dict[ChainId, str] = {}
    for chain_slug, entry in deployments.items():
        if not entry.get("is_satellite", False):
            continue
        module = entry.get("module_address")
        if not module:
            raise RuntimeError(
                f"Satellite deployment {chain_slug!r} is missing module address"
            )
        chain_id = ChainId.get_by_slug(chain_slug)
        if chain_id is None:
            raise RuntimeError(
                f"Unknown satellite deployment chain slug {chain_slug!r}"
            )
        satellite_modules[chain_id] = module

    return LagoonDeployment(
        primary_chain_id=primary_chain_id,
        vault_address=vault_address,
        module_address=module_address,
        satellite_modules=satellite_modules,
    )
