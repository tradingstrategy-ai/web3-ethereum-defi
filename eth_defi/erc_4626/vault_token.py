"""Disk cache for immutable per-vault state.

Keeps ERC-4626 vault-level state (share token address, denomination/asset
token address) out of :py:mod:`eth_defi.token`, which is reserved for ERC-20
primitives.

The cache is keyed by ``(chain_id, vault_address)`` and piggybacks on the
shared :py:class:`eth_defi.token.TokenDiskCache` sqlite file so there is no
extra config file to manage. Key prefixes ``vault-share-token-`` and
``vault-denomination-token-`` are distinct from the ERC-20 key format
``{chain_id}-{address.lower()}`` so there is no collision risk.

Rationale: ERC-4626 share/asset token addresses are fixed at deployment time,
so the lead scanner (:py:mod:`eth_defi.erc_4626.lead_scan_core`) can skip the
``share()``/``asset()`` eth_calls on every loop iteration after the first.

See :py:meth:`eth_defi.erc_4626.vault.ERC4626Vault.fetch_share_token_address`
and :py:meth:`eth_defi.erc_4626.vault.ERC4626Vault.fetch_denomination_token_address`
for the callers.
"""

from typing import Any

from eth_typing import HexAddress


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
        Cached share token address, or ``None`` if not cached.
    """
    if cache is None:
        return None
    entry = cache.get(_vault_share_token_key(chain_id, vault_address))
    return entry["address"] if entry else None


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
    """
    if cache is None:
        return
    cache[_vault_share_token_key(chain_id, vault_address)] = {"address": share_token_address}


def get_cached_vault_denomination_token_address(
    cache: dict[str, Any] | None,
    chain_id: int,
    vault_address: HexAddress,
) -> HexAddress | None:
    """Return cached ERC-4626 ``asset()``/denomination token address for a vault, or None.

    See :py:func:`get_cached_vault_share_token_address` for caller semantics.
    """
    if cache is None:
        return None
    entry = cache.get(_vault_denomination_token_key(chain_id, vault_address))
    return entry["address"] if entry else None


def set_cached_vault_denomination_token_address(
    cache: dict[str, Any] | None,
    chain_id: int,
    vault_address: HexAddress,
    denomination_token_address: HexAddress,
) -> None:
    """Persist ``asset()``/denomination token address for a vault in the given cache.

    Only persist after a definitive answer. See
    :py:func:`set_cached_vault_share_token_address` for the same caveat.
    """
    if cache is None:
        return
    cache[_vault_denomination_token_key(chain_id, vault_address)] = {"address": denomination_token_address}
