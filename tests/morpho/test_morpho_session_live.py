"""Minimal end-to-end public Morpho API check, without RPC or credentials."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.morpho import offchain_metadata
from eth_defi.erc_4626.vault_protocol.morpho.offchain_metadata import MorphoVaultAPIStatus
from eth_defi.erc_4626.vault_protocol.morpho.session import create_morpho_session


def test_morpho_session_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetch a real indexed vault's warning data through the paced session.

    Only session configuration is replaced: the HTTP request, response parsing
    and caching are real. There is no retry after a rate limit or other failure.

    :param tmp_path: Isolated accounting, cooldown and response-cache paths.
    :param monkeypatch: Inject session paths without changing production state.
    """
    session = create_morpho_session(tmp_path / "state")
    monkeypatch.setattr(offchain_metadata, "create_morpho_session", lambda: session)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    address = HexAddress("0xdd0f28e19C1780eb6396170735D45153D261490d")
    result = offchain_metadata.fetch_morpho_vault_api_result(web3, address, cache_path=tmp_path / "cache")
    assert result.status is MorphoVaultAPIStatus.found
    assert result.data is not None
    assert isinstance(result.data["vault_warnings"], list)
    assert isinstance(result.data["market_warnings"], list)
