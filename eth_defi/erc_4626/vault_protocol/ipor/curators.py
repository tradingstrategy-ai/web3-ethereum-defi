"""IPOR Fusion atomist metadata.

IPOR Fusion calls vault managers ``atomists`` in its frontend.  The public
IPOR metrics API does not currently expose atomists, so we keep a committed
overlay of ``(chain_id, vault_address)`` to atomist display names.
"""

import json
from functools import lru_cache
from json import JSONDecodeError
from pathlib import Path

from eth_typing import HexAddress

#: Committed IPOR atomist overlay.
IPOR_VAULT_ATOMISTS_PATH = Path(__file__).parents[3] / "data" / "feeds" / "ipor" / "vault_atomists.json"


def _normalise_vault_key(chain_id: int, vault_address: str) -> tuple[int, str]:
    """Normalise an IPOR vault key for overlay lookup.

    :param chain_id:
        EVM chain id.

    :param vault_address:
        Vault address. May be checksummed or lower case.

    :return:
        Tuple with a lower-case vault address.
    """
    assert isinstance(chain_id, int), f"Expected int chain id, got {chain_id!r}"
    assert isinstance(vault_address, str), f"Expected str vault address, got {vault_address!r}"
    return chain_id, vault_address.lower()


@lru_cache(maxsize=8)
def _load_ipor_vault_atomists_cached(path: str) -> dict[tuple[int, str], str]:
    """Load IPOR vault atomist overlay from a JSON file.

    :param path:
        JSON file path as a string for :py:func:`functools.lru_cache`.

    :return:
        Dict keyed by ``(chain_id, lower-case vault address)``.
    """
    json_path = Path(path)
    if not json_path.exists():
        return {}

    try:
        raw = json.loads(json_path.read_text())
    except JSONDecodeError as e:
        raise RuntimeError(f"Could not parse IPOR atomist overlay at {json_path}") from e

    result: dict[tuple[int, str], str] = {}
    for raw_key, atomist in raw.items():
        chain_id_str, vault_address = raw_key.split(":", 1)
        if not atomist:
            continue
        key = _normalise_vault_key(int(chain_id_str), vault_address)
        result[key] = atomist
    return result


def load_ipor_vault_atomists(path: Path | None = None) -> dict[tuple[int, str], str]:
    """Load IPOR vault atomist names keyed by vault.

    The load is cached by path, because vault scanning can touch many IPOR
    vaults in one process.

    :param path:
        Override JSON overlay path for tests.

    :return:
        Dict keyed by ``(chain_id, lower-case vault address)``.
    """
    if path is None:
        path = IPOR_VAULT_ATOMISTS_PATH
    return _load_ipor_vault_atomists_cached(str(path.resolve()))


def get_ipor_vault_atomist(chain_id: int, vault_address: HexAddress | str, path: Path | None = None) -> str | None:
    """Return the IPOR atomist display name for a vault.

    :param chain_id:
        EVM chain id.

    :param vault_address:
        Vault address. The value is lower-cased before lookup, so callers may
        pass checksummed addresses.

    :param path:
        Override JSON overlay path for tests.

    :return:
        Atomist display name, or ``None`` if the vault is not in the overlay.
    """
    key = _normalise_vault_key(chain_id, str(vault_address))
    return load_ipor_vault_atomists(path).get(key)
