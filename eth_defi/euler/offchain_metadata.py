"""Euler vault labelling

- Euler has put vault names offchain in Github, because of course Solidity programmers would do something like this
- ``name()`` accessor in Euler vault returns just a running counter
"""

import datetime
import json
from pathlib import Path
from typing import TypedDict
import logging
from urllib.error import HTTPError

import requests

from web3 import Web3
from eth_typing import HexAddress
from eth_defi.compat import native_datetime_utc_now, native_datetime_utc_fromtimestamp

#: Where we copy files from Euelr Github repo
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "euler"


logger = logging.getLogger(__name__)


class EulerVaultMetadata(TypedDict):
    """Metadata about an Euler vault from offchain source.

    https://raw.githubusercontent.com/euler-xyz/euler-labels/refs/heads/master/130/vaults.json
    """

    name: str
    entity: str
    description: str


def fetch_euler_vaults_file_for_chain(
    chain_id: int,
    cache_path=DEFAULT_CACHE_PATH,
    github_base_url="https://raw.githubusercontent.com/euler-xyz/euler-labels/refs/heads/master",
    now_=None,
    max_cache_duration=datetime.timedelta(days=2),
) -> dict:
    """Fetch and cache Euler offchain vault metadata for a given chain.

    - One JSON file per chain
    """

    assert type(chain_id) is int, "chain_id must be integer"
    assert isinstance(cache_path, Path), "cache_path must be Path instance"

    cache_path.mkdir(parents=True, exist_ok=True)
    file = cache_path / f"euler_vaults_chain_{chain_id}.json"

    if not now_:
        now_ = native_datetime_utc_now()

    if not file.exists() or (now_ - native_datetime_utc_fromtimestamp(file.stat().st_mtime)) > max_cache_duration:
        url = f"{github_base_url}/{chain_id}/vaults.json"

        # Fetch and save the file

        response = requests.get(url)

        try:
            response.raise_for_status()  # Raises exception for HTTP errors

            with open(file, "w", encoding="utf-8") as f:
                # Check Github file looks valud
                if json.load(open(file, "rt")):
                    f.write(response.text)
                else:
                    # TODO: Not sure what happened here, added more logs
                    logger.warning("Euler vaults file for chain %d is empty, writing empty JSON object", chain_id)
                    f.write("{}")
        except HTTPError as e:
            logger.warning("Euler vault file missing for chain %d is empty, writing empty JSON object, url %s, error %s", chain_id, url, e)
            f.write("{}")

        return json.load(open(file, "rt"))


def fetch_euler_vault_metadata(web3: Web3, vault_address: HexAddress) -> EulerVaultMetadata | None:
    """Fetch vault metadata from offchain source."""
    chain_id = web3.eth.chain_id
    vaults = fetch_euler_vaults_file_for_chain(chain_id)

    vault_address = web3.to_checksum_address(vault_address)
    return vaults.get(vault_address)
