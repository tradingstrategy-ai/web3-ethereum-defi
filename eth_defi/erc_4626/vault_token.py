"""Bounded disk cache for current per-vault token relationships.

Keeps ERC-4626 vault-level state (share token address, denomination/asset
token address) out of :py:mod:`eth_defi.token`, which is reserved for ERC-20
primitives.

The cache is keyed by ``(chain_id, vault_address)`` and piggybacks on the
shared :py:class:`eth_defi.token.TokenDiskCache` sqlite file so there is no
extra config file to manage. Key prefixes ``vault-share-token-`` and
``vault-denomination-token-`` are distinct from the ERC-20 key format
``{chain_id}-{address.lower()}`` so there is no collision risk.

Relationships expire after seven days because proxies may change them. Legacy
entries without observation provenance require one fresh read. Use
``FORCE_VAULT_TOKEN_MAPPING_REFRESH=true`` to bypass the bounded cache.

See :py:meth:`eth_defi.erc_4626.vault.ERC4626Vault.fetch_share_token_address`
and :py:meth:`eth_defi.erc_4626.vault.ERC4626Vault.fetch_denomination_token_address`
for the callers.
"""

import datetime
import os
from typing import Any

from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now


def _can_reuse_cached_mapping(entry: dict | None) -> bool:
    """Require recent provenance before reusing a vault-token relationship.

    The share/asset accessors call this before doing a contract read. Timeless
    legacy entries refresh once rather than acquiring a new lifetime simply
    by being read. An operator refresh bypasses even a recent observation so
    proxy upgrades can be reflected immediately without deleting the cache.

    :param entry: Cached mapping with optional checked-at timestamp.
    :return: Whether a recent mapping can be reused without a contract read.
    """
    if not entry or os.environ.get("FORCE_VAULT_TOKEN_MAPPING_REFRESH", "false").lower() == "true":
        return False
    checked_at = entry.get("checked_at")
    return bool(checked_at) and native_datetime_utc_now() - datetime.datetime.fromisoformat(checked_at) < datetime.timedelta(days=7)


def _vault_share_token_key(chain_id: int, vault_address: HexAddress) -> str:
    assert type(chain_id) == int, f"Bad chain id: {chain_id}"
    assert vault_address.startswith("0x"), f"Bad vault address: {vault_address}"
    return f"{chain_id}-vault-share-token-{vault_address.lower()}"


def _vault_denomination_token_key(chain_id: int, vault_address: HexAddress) -> str:
    assert type(chain_id) == int, f"Bad chain id: {chain_id}"
    assert vault_address.startswith("0x"), f"Bad vault address: {vault_address}"
    return f"{chain_id}-vault-denomination-token-{vault_address.lower()}"


def get_cached_vault_share_token_address(
    cache: dict[str, Any] | None,
    chain_id: int,
    vault_address: HexAddress,
) -> HexAddress | None:
    """Return cached ERC-4626 share token address for a vault, or None.

    Works with any :py:class:`dict`-like cache, including
    :py:class:`eth_defi.token.TokenDiskCache`. ``None`` cache is accepted
    as a no-op so callers don't need ``isinstance`` gymnastics.

    :param cache:
        Any dict-like store, or ``None`` to skip the lookup.

    :param chain_id:
        EVM chain id, e.g. 8453 for Base.

    :param vault_address:
        ERC-4626 vault address (the vault itself, not the share token).

    :return:
        Cached share token address, or ``None`` if absent, expired or forced due.
    """
    if cache is None:
        return None
    entry = cache.get(_vault_share_token_key(chain_id, vault_address))
    return entry["address"] if _can_reuse_cached_mapping(entry) else None


def set_cached_vault_share_token_address(
    cache: dict[str, Any] | None,
    chain_id: int,
    vault_address: HexAddress,
    share_token_address: HexAddress,
) -> None:
    """Persist share token address for a vault in the given cache.

    Callers must only invoke this after the chain has given a **definitive**
    answer (successful call or positively-classified revert). Transient RPC
    failures (node has no block, HTTP 502) must NOT be persisted or the
    cache will be poisoned for real ERC-7575 vaults.

    :param cache:
        Any dict-like store, or ``None`` to skip the write.
    :param chain_id:
        Chain containing the vault.
    :param vault_address:
        Vault whose share-token relationship was verified.
    :param share_token_address:
        Verified share token, including the vault itself for ordinary ERC-4626.
    :return:
        None; writes the address and its naive UTC observation time.
    """
    if cache is None:
        return
    cache[_vault_share_token_key(chain_id, vault_address)] = {"address": share_token_address, "checked_at": native_datetime_utc_now().isoformat()}


def get_cached_vault_denomination_token_address(
    cache: dict[str, Any] | None,
    chain_id: int,
    vault_address: HexAddress,
) -> HexAddress | None:
    """Return cached ERC-4626 ``asset()``/denomination token address for a vault, or None.

    See :py:func:`get_cached_vault_share_token_address` for caller semantics.

    :param cache: Dict-like token cache, or None to bypass caching.
    :param chain_id: Chain containing the vault.
    :param vault_address: Vault whose asset relationship is being looked up.
    :return: Cached address, or None when a fresh contract read is required.
    """
    if cache is None:
        return None
    entry = cache.get(_vault_denomination_token_key(chain_id, vault_address))
    return entry["address"] if _can_reuse_cached_mapping(entry) else None


def set_cached_vault_denomination_token_address(
    cache: dict[str, Any] | None,
    chain_id: int,
    vault_address: HexAddress,
    denomination_token_address: HexAddress,
) -> None:
    """Persist ``asset()``/denomination token address for a vault in the given cache.

    Only persist after a definitive answer. See
    :py:func:`set_cached_vault_share_token_address` for the same caveat.

    :param cache: Dict-like token cache, or None to skip the write.
    :param chain_id: Chain containing the vault.
    :param vault_address: Vault whose asset relationship was verified.
    :param denomination_token_address: Verified asset token contract.
    :return: None; writes the address and its naive UTC observation time.
    """
    if cache is None:
        return
    cache[_vault_denomination_token_key(chain_id, vault_address)] = {"address": denomination_token_address, "checked_at": native_datetime_utc_now().isoformat()}
