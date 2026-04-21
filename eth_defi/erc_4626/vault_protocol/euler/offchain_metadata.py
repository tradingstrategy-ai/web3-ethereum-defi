"""Euler vault labelling.

- Euler has put vault names offchain in Github, because of course Solidity programmers would do something like this
- ``name()`` accessor in Euler vault returns just a running counter
- Vault metadata is sourced from ``{chainId}/products.json`` in the
  `euler-xyz/euler-labels <https://github.com/euler-xyz/euler-labels>`__ repository

**Metadata structure (as of 2026-04)**

The old per-vault ``vaults.json`` was removed on 2026-04-14. Vault metadata is now
organised at the *product* level in ``products.json``.  A *product* is a branded
market grouping (e.g. *Euler Prime*, *InfiniFi*) that owns one or more vault addresses.

Each product entry contains:

- ``name`` — product brand name (e.g. ``"Euler Prime"``)
- ``description`` — long-form description of the product
- ``entity`` — list of entity slugs (resolves against ``entities.json``)
- ``vaults`` — list of active vault addresses in this product
- ``deprecatedVaults`` — formerly active vault addresses
- ``vaultOverrides`` — sparse per-vault overrides; only set where a vault
  needs a different ``name`` or ``description`` from its parent product

When building the per-vault reverse index we:

1. Use ``vaultOverrides[addr].name`` / ``.description`` when present.
2. Fall back to the parent product's ``name`` / ``description``.
3. Expose the first entity slug as ``entity`` for backward compatibility
   with the old ``vaults.json`` API; the full list is available as ``entities``.

Reference:

- `euler-xyz/euler-labels <https://github.com/euler-xyz/euler-labels>`__
- `products.json (Ethereum mainnet) <https://raw.githubusercontent.com/euler-xyz/euler-labels/refs/heads/master/1/products.json>`__
"""

import datetime
import json
from json import JSONDecodeError
from pathlib import Path
from typing import NotRequired, TypedDict
import logging
import requests
from requests.exceptions import HTTPError

from web3 import Web3
from eth_typing import HexAddress
from eth_defi.compat import native_datetime_utc_now, native_datetime_utc_fromtimestamp
from eth_defi.disk_cache import DEFAULT_CACHE_ROOT
from eth_defi.utils import wait_other_writers

#: Where we copy files from Euler Github repo
DEFAULT_CACHE_PATH = DEFAULT_CACHE_ROOT / "euler"


logger = logging.getLogger(__name__)


class EulerVaultMetadata(TypedDict):
    """Metadata about an Euler vault derived from the offchain ``products.json`` source.

    This TypedDict is the per-vault view built by :py:func:`fetch_euler_vaults_file_for_chain`
    from the product-level ``products.json`` file.

    **Backward-compatible fields** (present in the old ``vaults.json`` too):

    - ``name`` — vault display name; falls back to the parent product name when no per-vault
      override exists.
    - ``description`` — vault description; falls back to the product description.
    - ``entity`` — first entity slug (e.g. ``"euler-dao"``); kept as a plain string
      for backward compatibility with code that used the old ``vaults.json`` format.

    **New fields** (not present in the old format):

    - ``entities`` — full list of entity slugs for the product (e.g. ``["euler-dao", "gauntlet"]``).
    - ``product`` — product slug key (e.g. ``"euler-prime"``).
    - ``product_name`` — product display name (e.g. ``"Euler Prime"``).
    - ``deprecated`` — ``True`` if this vault address appears in ``deprecatedVaults``.
    - ``deprecation_reason`` — human-readable reason from ``vaultOverrides``, or ``None``.

    Reference:

    - `euler-xyz/euler-labels products.json <https://github.com/euler-xyz/euler-labels>`__
    """

    #: Vault (or product) display name.
    #:
    #: ``vaultOverrides[addr].name`` if present, otherwise ``product.name``.
    name: str

    #: Vault (or product) description.
    #:
    #: ``vaultOverrides[addr].description`` if present, otherwise ``product.description``.
    description: NotRequired[str | None]

    #: First entity slug — kept as ``str`` for backward compatibility.
    #:
    #: Example: ``"euler-dao"``.
    entity: NotRequired[str | None]

    #: Full list of entity slugs for the parent product.
    #:
    #: Example: ``["euler-dao", "gauntlet"]``.
    entities: NotRequired[list[str]]

    #: Parent product slug key in ``products.json``.
    #:
    #: Example: ``"euler-prime"``.
    product: NotRequired[str]

    #: Parent product display name.
    #:
    #: Example: ``"Euler Prime"``.
    product_name: NotRequired[str]

    #: Whether this vault address is listed under ``deprecatedVaults``.
    deprecated: NotRequired[bool]

    #: Human-readable deprecation reason from ``vaultOverrides``, or ``None``.
    deprecation_reason: NotRequired[str | None]


def _build_vault_index(products: dict) -> dict[str, EulerVaultMetadata]:
    """Build a flat ``vault_address → EulerVaultMetadata`` index from ``products.json``.

    :param products:
        Parsed contents of ``products.json`` — a dict keyed by product slug.

    :return:
        Dict keyed by checksummed vault address.
    """
    result: dict[str, EulerVaultMetadata] = {}

    for product_slug, product in products.items():
        product_name: str = product.get("name", "")
        product_description: str | None = product.get("description")
        raw_entity = product.get("entity", [])

        # entity is a list in products.json; collapse to a single string for backward compat
        if isinstance(raw_entity, list):
            entities: list[str] = raw_entity
            entity_str: str | None = raw_entity[0] if raw_entity else None
        else:
            # Defensive — old format had a plain string
            entities = [raw_entity] if raw_entity else []
            entity_str = raw_entity or None

        deprecated_set: set[str] = set(product.get("deprecatedVaults", []))
        overrides: dict = product.get("vaultOverrides", {})
        all_vaults: list[str] = product.get("vaults", []) + list(deprecated_set)

        for addr in all_vaults:
            override = overrides.get(addr, {})
            is_deprecated = addr in deprecated_set

            result[addr] = EulerVaultMetadata(
                name=override.get("name", product_name),
                description=override.get("description", product_description),
                entity=entity_str,
                entities=entities,
                product=product_slug,
                product_name=product_name,
                deprecated=is_deprecated,
                deprecation_reason=override.get("deprecationReason"),
            )

    return result


def fetch_euler_vaults_file_for_chain(
    chain_id: int,
    cache_path=DEFAULT_CACHE_PATH,
    github_base_url="https://raw.githubusercontent.com/euler-xyz/euler-labels/refs/heads/master",
    now_=None,
    max_cache_duration=datetime.timedelta(days=2),
) -> dict[str, EulerVaultMetadata]:
    """Fetch and cache Euler offchain vault metadata for a given chain.

    Fetches ``{chainId}/products.json`` from the ``euler-xyz/euler-labels`` GitHub
    repository, builds a flat per-vault reverse index via :py:func:`_build_vault_index`,
    and writes the result to a local JSON cache file.

    The cache is per-chain and expires after *max_cache_duration* (default 2 days).
    The function is multiprocess-safe via :py:func:`~eth_defi.utils.wait_other_writers`.

    :param chain_id:
        EVM chain ID (e.g. ``1`` for Ethereum mainnet).

    :param cache_path:
        Directory for the local JSON cache files.

    :param github_base_url:
        Base URL for the ``euler-xyz/euler-labels`` raw GitHub files.

    :param now_:
        Override for "current time" used in cache-expiry checks (useful in tests).

    :param max_cache_duration:
        How long to keep a cached file before re-fetching from GitHub.

    :return:
        Dict mapping checksummed vault address → :py:class:`EulerVaultMetadata`.
        Returns an empty dict when the chain has no products file.
    """
    assert type(chain_id) is int, "chain_id must be integer"
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    # Use a separate filename from the old earn-vaults cache to force a fresh fetch
    file = cache_path / f"euler_products_chain_{chain_id}.json"
    file = file.resolve()

    file_size = file.stat().st_size if file.exists() else 0

    if not now_:
        now_ = native_datetime_utc_now()

    # When running multiprocess vault scan, we have competition over this file write and
    # if we do not wait the race condition may try to read zero-bytes file
    with wait_other_writers(file):
        if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration or file_size == 0:
            logger.info("Re-fetching Euler products file for chain %d from %s", chain_id, github_base_url)
            with file.open("wt") as f:
                url = f"{github_base_url}/{chain_id}/products.json"

                response = requests.get(url)

                logger.info("Got response code %d for Euler products file for chain %d from %s", response.status_code, chain_id, url)

                try:
                    response.raise_for_status()

                    products = json.loads(response.text)
                    logger.info("Fetched Euler products file for chain %d from %s, %d products", chain_id, url, len(products))

                    content = _build_vault_index(products)
                    f.write(json.dumps(content))
                    logger.info("Wrote %s", file.resolve())

                except (HTTPError, JSONDecodeError) as e:
                    logger.warning(
                        "Euler products file missing for chain %d, url %s, error %s",
                        chain_id,
                        url,
                        e,
                    )
                    f.write("{}")
                    content = {}

            assert file.stat().st_size > 0, f"File {file} is empty after writing"
            return content

        else:
            timestamp = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=None)
            ago = now_ - timestamp
            logger.info("Using cached Euler products file for chain %d from %s, last fetched %s ago", chain_id, file, ago)

            if file_size == 0:
                return {}

            try:
                return json.load(open(file, "rt"))
            except JSONDecodeError as e:
                content = open(file, "rt").read()
                raise RuntimeError(f"Could not parse Euler products file for chain {chain_id} at {file}, content starts with {content[:100]!r}") from e


def fetch_euler_vault_metadata(web3: Web3, vault_address: HexAddress) -> EulerVaultMetadata | None:
    """Fetch vault metadata from the offchain ``products.json`` source.

    Uses a two-level cache: an in-process dict and a disk cache populated by
    :py:func:`fetch_euler_vaults_file_for_chain`.

    :param web3:
        Connected Web3 instance (used to determine chain ID and checksum the address).

    :param vault_address:
        Vault contract address.

    :return:
        :py:class:`EulerVaultMetadata` for the vault, or ``None`` if no metadata
        is available (unrecognised vault or chain has no ``products.json``).
    """
    global _cached_vaults

    chain_id = web3.eth.chain_id

    if chain_id not in _cached_vaults:
        vaults = fetch_euler_vaults_file_for_chain(chain_id)
        _cached_vaults[chain_id] = vaults

    vaults = _cached_vaults[chain_id]
    if vaults:
        vault_address = web3.to_checksum_address(vault_address)
        return vaults.get(vault_address)

    return None


#: In-process cache of fetched vaults
_cached_vaults: dict[int, dict[HexAddress, EulerVaultMetadata]] = {}
